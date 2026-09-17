# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Verified copy engine. The source is opened only for reading."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
import unicodedata

from . import APP_NAME, COMPANY_NAME, __version__
from .library import BackupPlan, CancelledError, check_cancel, is_conversion_candidate
from .paths import is_link
from ..eseq_converter import convert_eseq_bytes_to_midi_bytes
from ..rename_recovery import sync_directory

CHUNK_SIZE = 1024 * 1024
MANIFEST = 'manifest.json'
JOURNAL = 'manifest.journal.jsonl'


@dataclass
class BackupResult:
    folder: Path
    status: str
    copied: int
    verified: int
    errors: list[str]
    converted: int = 0


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _contained(root: Path, relative: str) -> Path:
    p = Path(relative)
    if p.is_absolute() or not p.parts or '..' in p.parts:
        raise ValueError(f'Unsafe relative path: {relative}')
    result = root / p
    if not _within(result.resolve(), root.resolve()):
        raise ValueError(f'Path escapes its root: {relative}')
    for parent in [result, *result.parents]:
        if parent == root:
            break
        if is_link(parent):
            raise ValueError(f'Symbolic links are not followed: {relative}')
    return result


def _atomic_json(path: Path, value):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    sync_directory(path.parent)


class _ManifestJournal:
    """Persist each changed record; charge compact snapshots to journal bytes.

    A fixed file-count snapshot interval would still rewrite O(N²) bytes.
    Instead, a snapshot is due only after at least its previous size has been
    appended. The journal is retained until the final snapshot, so a crash
    before or after snapshot publication can replay by sequence number.
    """

    def __init__(self, folder: Path, manifest: dict):
        self.folder = folder
        self.manifest = manifest
        self.sequence = 0
        self.pending_bytes = 0
        self.error_count = len(manifest['errors'])
        self.conversion_error_count = len(manifest['conversion_errors'])
        with (folder / JOURNAL).open('xb') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        self.snapshot()

    def snapshot(self):
        self.manifest['journal_sequence'] = self.sequence
        _atomic_json(self.folder / MANIFEST, self.manifest)
        self.snapshot_size = (self.folder / MANIFEST).stat().st_size
        self.pending_bytes = 0

    def append(self, collection: str, index: int, record: dict):
        event = {'sequence': self.sequence + 1, 'collection': collection,
                 'index': index, 'record': record,
                 'errors': self.manifest['errors'][self.error_count:],
                 'conversion_errors': self.manifest['conversion_errors'][self.conversion_error_count:]}
        payload = (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        with (self.folder / JOURNAL).open('ab') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        self.sequence += 1
        self.error_count = len(self.manifest['errors'])
        self.conversion_error_count = len(self.manifest['conversion_errors'])
        self.pending_bytes += len(payload)
        if self.pending_bytes >= self.snapshot_size:
            self.snapshot()

    def finish(self):
        self.snapshot()
        (self.folder / JOURNAL).unlink()
        sync_directory(self.folder)


def _load_manifest(folder: Path) -> dict:
    """Read a snapshot and replay durable updates from an interrupted backup."""
    with _contained(folder, MANIFEST).open(encoding='utf-8') as stream:
        manifest = json.load(stream)
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('Unrecognized backup manifest')
    if not isinstance(manifest.get('derivatives', []), list):
        raise ValueError('Unrecognized backup derivative records')
    sequence = manifest.get('journal_sequence', 0)
    journal = _contained(folder, JOURNAL)
    if not journal.exists():
        return manifest
    with journal.open('rb') as stream:
        for line in stream:
            # A killed/failed append can leave one incomplete final line.
            if not line.endswith(b'\n'):
                break
            try:
                event = json.loads(line)
                number = event['sequence']
                if not isinstance(number, int) or number < 1:
                    raise ValueError('Invalid journal sequence')
                if number <= sequence:
                    continue  # Already included in the atomic snapshot.
                collection, index, record = event['collection'], event['index'], event['record']
                if (number != sequence + 1 or collection not in {'files', 'derivatives'}
                        or not isinstance(index, int) or index < 0 or not isinstance(record, dict)
                        or not isinstance(event['errors'], list)
                        or not isinstance(event['conversion_errors'], list)):
                    raise ValueError('Invalid journal record')
                records = manifest.setdefault(collection, [])
                if index < len(records):
                    records[index] = record
                elif collection == 'derivatives' and index == len(records):
                    records.append(record)
                else:
                    raise ValueError('Invalid journal record index')
                manifest.setdefault('errors', []).extend(event['errors'])
                manifest.setdefault('conversion_errors', []).extend(event['conversion_errors'])
                sequence = number
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError('Unrecognized backup progress journal') from exc
    manifest['journal_sequence'] = sequence
    return manifest


def _hash(path: Path, cancel=None, on_chunk=None) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f'Not a regular file: {path}')
        while True:
            check_cancel(cancel)
            block = stream.read(CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
            if on_chunk:
                on_chunk(len(block))
    return digest.hexdigest()


def _manifest_allowance(plan: BackupPlan) -> int:
    return max(16 * 1024 * 1024, len(plan.files) * 8192)


def _midi_conversion_allowance(item) -> int:
    """Reserve conservative derivative space without rereading the source drive.

    Compact E-SEQ events expand into MIDI events plus variable-length deltas.
    Allow eight times the source size, plus metadata/allocation headroom. This
    is an estimate, not a guarantee against other writers filling the disk.
    """
    title_bytes = len(str(item.metadata.get('title') or '').encode('utf-8'))
    return item.size * 8 + max(64 * 1024, title_bytes + 1024)


def _conversion_headroom(plan: BackupPlan) -> int:
    return sum(_midi_conversion_allowance(item) for item in plan.files
               if (item.convertible_eseq or item.kind == 'E-SEQ')
               and item.kind not in {'Metadata', 'PianoSoft package'}
               and Path(item.source).suffix.lower() != '.pspg')


def _validate_target(plan: BackupPlan, target: Path, *, convert_eseq=False) -> Path:
    source = plan.source.resolve(strict=True)
    target = target.expanduser().resolve()
    if _within(target, source):
        raise ValueError('Choose a backup folder outside the source drive or source folder.')
    ancestor = target
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError('The destination must be a folder.')
    # Catch a second mount/bind alias of the source data filesystem too.
    if os.path.ismount(source) and ancestor.stat().st_dev == source.stat().st_dev:
        raise ValueError('The backup destination must be on a different filesystem from the source drive.')
    required = plan.total_bytes + _manifest_allowance(plan)
    if convert_eseq:
        # All originals coexist with derivatives until each conversion is
        # verified, including when the user requests MIDI-only output.
        required += _conversion_headroom(plan)
    if shutil.disk_usage(ancestor).free < required:
        if convert_eseq:
            raise ValueError('Not enough free space for the backup, MIDI conversions, and manifest.')
        raise ValueError('Not enough free space for the backup and its manifest.')
    seen = set()
    for item in plan.files:
        _contained(source, item.source)
        _contained(target, item.destination)
        key = _path_key(item.destination)
        if key in seen:
            raise ValueError(f'Duplicate backup destination: {item.destination}')
        seen.add(key)
        if Path(item.destination).parts[0].casefold() in {
                MANIFEST, MANIFEST + '.tmp', JOURNAL, 'readme.txt', '.incomplete'}:
            raise ValueError(f'Reserved backup filename: {item.destination}')
    return target


def _path_key(path: Path | str) -> str:
    return unicodedata.normalize('NFC', Path(path).as_posix()).casefold()


def _derivative_path(folder: Path, original: str, reserved: set[str]) -> Path:
    """Choose a sibling MIDI name without replacing files or planned folders."""
    base = Path(original).with_suffix('.mid')
    relative = base
    number = 2
    while _path_key(relative) in reserved or (folder / relative).exists() or is_link(folder / relative):
        relative = base.with_name(f'{base.stem} ({number}){base.suffix}')
        number += 1
    reserved.add(_path_key(relative))
    return relative


def _read_conversion_source(path: Path, expected: str, cancel=None) -> bytes:
    """Convert only the same bytes that were verified during the backup."""
    digest = hashlib.sha256()
    data = bytearray()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Conversion source is no longer a regular file')
        while True:
            check_cancel(cancel)
            block = stream.read(CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
            data.extend(block)
    if digest.hexdigest() != expected:
        raise ValueError('Original backup checksum changed before conversion')
    return bytes(data)


def _write_derivative(path: Path, payload: bytes, cancel=None) -> str:
    """Publish a complete, read-back verified derivative with an exclusive name."""
    out_fd, out_name = tempfile.mkstemp(prefix='.convert-', suffix='.partial', dir=path.parent)
    partial = Path(out_name)
    reserved = False
    try:
        with os.fdopen(out_fd, 'wb') as stream:
            for offset in range(0, len(payload), CHUNK_SIZE):
                check_cancel(cancel)
                stream.write(payload[offset:offset + CHUNK_SIZE])
            stream.flush()
            os.fsync(stream.fileno())
        expected = hashlib.sha256(payload).hexdigest()
        if _hash(partial, cancel) != expected:
            raise ValueError('Converted MIDI SHA-256 read-back verification failed')
        check_cancel(cancel)
        with path.open('xb'):
            pass
        reserved = True
        os.replace(partial, path)
        reserved = False
        sync_directory(path.parent)
        return expected
    finally:
        partial.unlink(missing_ok=True)
        if reserved:
            path.unlink(missing_ok=True)


def run_backup(plan: BackupPlan, target: Path, progress=None, cancel=None, *,
               convert_eseq: bool = False, keep_originals: bool = True) -> BackupResult:
    """Create a new uniquely named backup, copying and reading back every file.

    Completed files are never overwritten. A manifest and .incomplete marker
    distinguish a cancelled, failed, or interrupted run from a complete backup.
    Optional E-SEQ conversion runs after every original has been copied, adding
    verified MIDI siblings. If keep_originals is false, a converted E-SEQ copy
    is removed from the backup only after its MIDI and progress are saved.
    Failed conversions retain their originals. Source files are never changed.
    """
    check_cancel(cancel)
    keep_originals = not convert_eseq or bool(keep_originals)
    target = _validate_target(plan, Path(target), convert_eseq=convert_eseq)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    folder = Path(tempfile.mkdtemp(prefix=f'MarkIV-Backup-{stamp}-', dir=target))
    (folder / '.incomplete').write_text(
        'Backup has not completed. Verify Backup reads manifest.json and any\n'
        'manifest.journal.jsonl to recover saved progress.\n', encoding='utf-8')
    records = [dict(asdict(item), status='pending') for item in plan.files]
    manifest = {'schema_version': 1, 'application': {'name': APP_NAME, 'version': __version__},
                'created_at': datetime.now(timezone.utc).isoformat(),
                'source': str(plan.source), 'status': 'in_progress', 'total_bytes': plan.total_bytes,
                'albums': [asdict(album) for album in plan.albums], 'warnings': plan.warnings,
                'files': records, 'errors': list(plan.scan_errors),
                'options': {'convert_eseq': bool(convert_eseq), 'keep_originals': keep_originals},
                'derivatives': [], 'conversion_errors': []}
    errors = manifest['errors']
    copied = verified = bytes_done = converted = conversion_total = 0
    last_emit = 0.0

    def emit(message, event='progress', force=False):
        nonlocal last_emit
        now = time.monotonic()
        if progress and (force or now - last_emit > 0.1):
            progress({'event': event, 'message': message, 'completed': verified,
                      'total': len(records), 'bytes_done': bytes_done,
                      'bytes_total': plan.total_bytes * 2,
                      'converted': converted, 'conversion_total': conversion_total})
            last_emit = now

    def read_chunk(size):
        nonlocal bytes_done
        bytes_done += size
        emit('Verifying copied bytes…')

    journal = _ManifestJournal(folder, manifest)
    status = 'incomplete' if errors else 'complete'
    try:
        for index, (item, record) in enumerate(zip(plan.files, records)):
            check_cancel(cancel)
            partial = None
            reserved_destination = False
            try:
                src = _contained(plan.source, item.source)
                dst = _contained(folder, item.destination)
                dst.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(src, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
                with os.fdopen(fd, 'rb') as incoming:
                    before = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError('Source is no longer a regular file')
                    if before.st_size != item.size or (item.mtime_ns and before.st_mtime_ns != item.mtime_ns):
                        raise ValueError('Source changed since the scan; scan again')
                    # Use an unpredictable temporary name so unusual source filenames cannot collide.
                    out_fd, out_name = tempfile.mkstemp(prefix='.copy-', suffix='.partial', dir=dst.parent)
                    partial = Path(out_name)
                    digest = hashlib.sha256()
                    count = 0
                    with os.fdopen(out_fd, 'wb') as outgoing:
                        while True:
                            check_cancel(cancel)
                            block = incoming.read(CHUNK_SIZE)
                            if not block:
                                break
                            outgoing.write(block)
                            digest.update(block)
                            count += len(block)
                            bytes_done += len(block)
                            emit(f'Copying {item.destination}')
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    after = os.fstat(incoming.fileno())
                    if (count != item.size or before.st_mtime_ns != after.st_mtime_ns or
                            before.st_size != after.st_size or before.st_ctime_ns != after.st_ctime_ns):
                        raise ValueError('Source changed while it was being copied')
                expected = digest.hexdigest()
                if _hash(partial, cancel, read_chunk) != expected:
                    raise ValueError('SHA-256 read-back verification failed')
                if dst.exists() or is_link(dst):
                    raise ValueError('Destination already exists; refusing to overwrite it')
                # Reserve our name exclusively, then publish the verified bytes.
                # Unlike hard links this also works on FAT/exFAT backup disks.
                with dst.open('xb'):
                    pass
                reserved_destination = True
                os.replace(partial, dst)
                reserved_destination = False
                partial = None
                os.utime(dst, ns=(before.st_atime_ns, before.st_mtime_ns))
                sync_directory(dst.parent)
                record.update(status='verified', sha256=expected)
                copied += 1
                verified += 1
            except CancelledError:
                record['status'] = 'cancelled'
                raise
            except (OSError, ValueError) as exc:
                message = f'{item.source}: {exc}'
                record.update(status='failed', error=str(exc))
                errors.append(message)
                status = 'incomplete'
                emit(message, event='error', force=True)
            finally:
                if partial is not None:
                    partial.unlink(missing_ok=True)
                if reserved_destination:
                    dst.unlink(missing_ok=True)
                journal.append('files', index, record)
            emit(f'Verified {verified} of {len(records)} files', force=True)
        if convert_eseq:
            reserved = {_path_key(item.destination) for item in plan.files}
            reserved.update(_path_key(parent) for item in plan.files
                            for parent in Path(item.destination).parents)
            candidates = []
            for index, (item, record) in enumerate(zip(plan.files, records)):
                check_cancel(cancel)
                if record['status'] != 'verified' or Path(item.source).suffix.lower() == '.pspg':
                    continue
                original = _contained(folder, item.destination)
                # Recheck the verified copy instead of trusting the scan flag.
                if is_conversion_candidate(original, item.kind):
                    candidates.append((index, item, record))
            conversion_total = len(candidates)
            for original_index, item, original_record in candidates:
                check_cancel(cancel)
                relative = _derivative_path(folder, item.destination, reserved)
                record = {'source': item.source, 'original': item.destination,
                          'destination': relative.as_posix(), 'kind': 'MIDI',
                          'conversion': 'E-SEQ to MIDI', 'status': 'pending'}
                manifest['derivatives'].append(record)
                emit(f'Converting {item.destination}', event='conversion', force=True)
                try:
                    check_cancel(cancel)
                    if shutil.disk_usage(folder).free < _midi_conversion_allowance(item) + _manifest_allowance(plan):
                        raise ValueError('Not enough free space for the MIDI conversion and manifest.')
                    original = _contained(folder, item.destination)
                    data = _read_conversion_source(original, original_record['sha256'], cancel)
                    title = item.metadata.get('title')
                    if item.metadata.get('metadata_source') == 'Original filename':
                        title = None
                    payload = convert_eseq_bytes_to_midi_bytes(data, title_override=title)
                    check_cancel(cancel)
                    if shutil.disk_usage(folder).free < len(payload) + _manifest_allowance(plan):
                        raise ValueError('Not enough free space for the MIDI conversion and manifest.')
                    checksum = _write_derivative(_contained(folder, relative.as_posix()), payload, cancel)
                    record.update(status='verified', size=len(payload), sha256=checksum)
                    converted += 1
                except CancelledError:
                    record['status'] = 'cancelled'
                    raise
                except Exception as exc:
                    message = f'{item.source}: E-SEQ conversion failed: {exc}'
                    record.update(status='failed', error=str(exc))
                    manifest['conversion_errors'].append(message)
                    errors.append(message)
                    status = 'incomplete'
                    emit(message, event='error', force=True)
                finally:
                    journal.append('derivatives', len(manifest['derivatives']) - 1, record)
                if record['status'] == 'verified' and not keep_originals:
                    # The verified MIDI and its journal entry are already
                    # durable before removing any copied original. Cancellation
                    # or a failed conversion leaves the original in place.
                    check_cancel(cancel)
                    original_record.update(status='replacement_pending', replacement=relative.as_posix())
                    journal.append('files', original_index, original_record)
                    try:
                        _contained(folder, item.destination).unlink()
                        original_record.update(status='replaced', replacement=relative.as_posix())
                    except (OSError, ValueError) as exc:
                        original_record['status'] = 'verified'
                        original_record.pop('replacement', None)
                        message = f'{item.source}: Could not remove converted backup original: {exc}'
                        errors.append(message)
                        status = 'incomplete'
                        emit(message, event='error', force=True)
                    finally:
                        journal.append('files', original_index, original_record)
                emit(f'Converted {converted} of {conversion_total} E-SEQ files',
                     event='conversion', force=True)
        check_cancel(cancel)
    except CancelledError:
        status = 'cancelled'
    except Exception as exc:
        status = 'incomplete'
        errors.append(str(exc))
    manifest.update(status=status, completed_at=datetime.now(timezone.utc).isoformat(),
                    copied=copied, verified=verified, converted=converted)
    journal.finish()
    conversion_note = (
        'Optional E-SEQ conversions are additional MIDI files beside the originals.\n'
        if keep_originals else
        'Successfully converted E-SEQ copies are replaced by verified MIDI files.\n'
        'Unconverted originals and files whose conversion failed are retained.\n'
    )
    (folder / 'README.txt').write_text(
        f'{APP_NAME} {__version__}\nCreated by a tool from {COMPANY_NAME}.\n\n'
        f'Status: {status}\nVerified originals: {verified}/{len(records)}\n'
        f'Converted MIDI copies: {converted}\n\n'
        'Retained original-format files have unchanged contents.\n'
        + conversion_note +
        'manifest.json records original paths, recovered titles, and SHA-256 checksums.\n'
        'Its derivatives list records converted MIDI files separately from originals.\n'
        'Replaced original records identify their MIDI replacement.\n'
        '_metadata/original retains available database and catalog source files.\n'
        'PianoSoft packages and presentation assets retain their original contents.\n'
        'This is a music archive, not a bootable disk image or an automatic piano restore.\n', encoding='utf-8')
    if status == 'complete':
        (folder / '.incomplete').unlink()
    emit(f'Backup {status}: {verified} files verified', event=status, force=True)
    return BackupResult(folder, status, copied, verified, errors, converted)


def verify_backup(folder: Path, progress=None, cancel=None) -> list[str]:
    """Check original and derivative checksums, reporting every failure."""
    folder = Path(folder).expanduser().resolve(strict=True)
    manifest = _load_manifest(folder)
    errors = []
    if manifest.get('status') != 'complete' or (folder / '.incomplete').exists():
        errors.append(f'Backup is not complete (status: {manifest.get("status", "unknown")})')
    derivatives = manifest.get('derivatives', [])
    replacements = {record.get('destination'): record for record in derivatives}
    records = manifest['files'] + derivatives
    for index, record in enumerate(records):
        check_cancel(cancel)
        rel = record.get('destination', '')
        try:
            path = _contained(folder, rel)
            replacement_status = record.get('status') in {'replaced', 'replacement_pending'}
            if index < len(manifest['files']) and replacement_status:
                replacement = replacements.get(record.get('replacement'), {})
                options = manifest.get('options', {})
                if (options.get('convert_eseq') is not True
                        or options.get('keep_originals') is not False
                        or not record.get('sha256')
                        or replacement.get('original') != rel
                        or replacement.get('source') != record.get('source')
                        or replacement.get('status') != 'verified'):
                    raise ValueError('Original has no verified MIDI replacement')
                # The corresponding derivative is checked below, including
                # missing files, size, checksum, and unsafe destination paths.
                # A crash may land on either side of the original's unlink.
                if record.get('status') == 'replacement_pending' and path.exists():
                    if path.stat().st_size != record['size']:
                        raise ValueError('Size does not match')
                    if _hash(path, cancel) != record['sha256']:
                        raise ValueError('SHA-256 checksum does not match')
            else:
                if record.get('status') != 'verified' or not record.get('sha256'):
                    raise ValueError('File was not verified during backup')
                if path.stat().st_size != record['size']:
                    raise ValueError('Size does not match')
                if _hash(path, cancel) != record['sha256']:
                    raise ValueError('SHA-256 checksum does not match')
        except (OSError, ValueError) as exc:
            errors.append(f'{rel}: {exc}')
        if progress:
            progress({'event': 'verify', 'message': rel, 'completed': index + 1,
                      'total': len(records)})
    return errors
