# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Verified copy engine. The source is opened only for reading."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import unicodedata

from . import APP_NAME, COMPANY_NAME, __version__
from .library import BackupPlan, CancelledError, check_cancel, is_conversion_candidate
from .paths import is_link
from ..eseq_converter import convert_eseq_bytes_to_midi_bytes
from ..helpers.output_names import create_output_directory
from ..helpers.portable_filename import is_windows_device_name
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
    decoded: int = 0
    audio_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


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


def _audio_candidates(plan):
    return [item for item in plan.files
            if Path(item.source).suffix.lower() in {'.wav', '.mp3'}
            and item.kind not in {'Metadata', 'PianoSoft package'}
            and Path(item.original_source or item.source).parts[:2] != ('songs', 'pspg')]


def _audio_working_allowance(item):
    # A 44.1 kHz MP3 can expand greatly at low bitrates. Reserve PCM decoding,
    # a clean staging WAV, the paired export's PCM copy, and new MIDI/MP3.
    return max(1024 * 1024, item.size * (144 if Path(item.source).suffix.lower() == '.mp3' else 4))


def _audio_headroom(plan):
    candidates = _audio_candidates(plan)
    retained_pairs = sum(max(64 * 1024, item.size * (8 if Path(item.source).suffix.lower() == '.mp3' else 1))
                         for item in candidates)
    return retained_pairs + max((_audio_working_allowance(item) for item in candidates), default=0)


def _validate_target(plan: BackupPlan, target: Path, *, convert_eseq=False, split_audio=False,
                     prepare_midi=False) -> Path:
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
    required = (0 if plan.source_backup else plan.total_bytes) + _manifest_allowance(plan)
    if convert_eseq:
        # All originals coexist with derivatives until each conversion is
        # verified, including when the user requests MIDI-only output.
        required += _conversion_headroom(plan)
    if split_audio:
        required += _audio_headroom(plan)
    if prepare_midi:
        required += sum(item.size + 64 * 1024 for item in plan.files if item.kind == 'MIDI')
    if shutil.disk_usage(ancestor).free < required:
        if split_audio:
            raise ValueError('Not enough free space for the backup, decoded audio pairs, and temporary PCM files.')
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


def _audio_pair_root(folder, plan, name='Paired MIDI and MP3'):
    """Reserve a separate playback tree without colliding with original assets."""
    existing = {_path_key(Path(item.destination).parts[0]) for item in plan.files}
    base, number = name, 1
    while _path_key(name) in existing or os.path.lexists(folder / name):
        number += 1
        name = f'{base} ({number})'
    return Path(name)


def _short_playback_stem(folder, parent, filename, reserved, extensions=('.mid',)):
    """Give cataloged output a matching portable DOS name, including pairs."""
    ascii_stem = unicodedata.normalize('NFKD', Path(filename).stem).encode('ascii', 'ignore').decode()
    base = re.sub(r'[^A-Z0-9_-]', '', ascii_stem.upper())[:8] or 'SONG'
    if is_windows_device_name(base):
        base = '_' + base[:7]
    stem, number = base, 1
    while any(_path_key(parent / (stem + extension)) in reserved
              or os.path.lexists(folder / parent / (stem + extension))
              for extension in extensions):
        number += 1
        suffix = f'~{number}'
        if len(suffix) >= 8:
            raise ValueError('Too many colliding Yamaha MIDI filenames.')
        stem = base[:8 - len(suffix)] + suffix
    for extension in extensions:
        reserved.add(_path_key(parent / (stem + extension)))
    return parent / stem


def _audio_pair_stem(folder, root, original, reserved, *, short_names=False,
                     extensions=('.mid', '.mp3')):
    source = Path(original)
    parent = root / source.parent
    if short_names:
        return _short_playback_stem(folder, parent, source.name, reserved, extensions)
    base, stem, number = source.stem, source.stem, 1
    while any(_path_key(parent / (stem + extension)) in reserved
              or os.path.lexists(folder / parent / (stem + extension))
              for extension in extensions):
        number += 1
        stem = f'{base} ({number})'
    for extension in extensions:
        reserved.add(_path_key(parent / (stem + extension)))
    return parent / stem


def _split_audio_copies(plan, folder, records, manifest, journal, *, cancel=None, progress=None,
                       generate_yamaha_catalogs=False, cataloged_sources=()):
    """Decode verified archival copies; record skipped ordinary audio in place.

    The original file's journaled audio_decode entry describes the outcome;
    successful pairs have two ordinary derivative records so Verify Backup
    checks both halves. A failed decode never retires an encoded original.
    """
    from ..disklavier_audio_decode import decode_disklavier_audio, NotEncodedAudioError
    from ..paired_audio import export_midi_mp3

    candidate_sources = {item.source for item in _audio_candidates(plan)}
    candidates = [(index, item, record) for index, (item, record) in enumerate(zip(plan.files, records))
                  if item.source in candidate_sources and record['status'] == 'verified']
    root = _audio_pair_root(folder, plan)
    reserved = set()
    decoded = skipped = failed = completed = 0
    last_emit = 0.0

    def emit(message, force=False):
        nonlocal last_emit
        now = time.monotonic()
        if progress and (force or now - last_emit > 0.1):
            progress({'event': 'audio_decode', 'message': message, 'completed': completed,
                      'total': len(candidates), 'decoded': decoded, 'audio_total': len(candidates),
                      'audio_skipped': skipped})
            last_emit = now

    for index, item, original_record in candidates:
        check_cancel(cancel)
        details = original_record['audio_decode'] = {'status': 'pending'}
        derivative_indices = []
        journal.append('files', index, original_record)
        emit(f'Decoding {item.destination}', force=True)
        try:
            original = _contained(folder, item.destination)
            if _hash(original, cancel) != original_record['sha256']:
                raise ValueError('Original backup checksum changed before audio decoding')
            if shutil.disk_usage(folder).free < _audio_working_allowance(item) + _manifest_allowance(plan):
                raise ValueError('Not enough free space for audio decoding and temporary PCM files.')
            with tempfile.TemporaryDirectory(prefix='.decode-', dir=folder) as temporary:
                music = Path(temporary) / 'music.wav'
                # A same-stem MIDI or E-SEQ file does not prove the audio is
                # clean. Inspect its channels before classifying it as ordinary.
                decoded_audio = decode_disklavier_audio(
                    original, music, cancel=cancel,
                    progress=lambda _fraction: emit(f'Decoding {item.destination}'),
                )
                check_cancel(cancel)
                if _hash(original, cancel) != original_record['sha256']:
                    raise ValueError('Original backup checksum changed during audio decoding')
                relative_stem = _audio_pair_stem(folder, root, item.destination, reserved,
                                               short_names=(generate_yamaha_catalogs or
                                                            Path(item.source).parent.as_posix() in cataloged_sources))
                paths = [Path(str(relative_stem) + extension) for extension in ('.mid', '.mp3')]
                destinations = [_contained(folder, relative.as_posix()) for relative in paths]
                destinations[0].parent.mkdir(parents=True, exist_ok=True)
                for relative, kind in zip(paths, ('MIDI', 'MP3')):
                    derivative = {'source': item.source, 'original': item.destination,
                                  'destination': relative.as_posix(), 'kind': kind,
                                  'conversion': 'Yamaha encoded audio to MIDI/MP3', 'status': 'pending'}
                    derivative_indices.append(len(manifest['derivatives']))
                    manifest['derivatives'].append(derivative)
                    journal.append('derivatives', derivative_indices[-1], derivative)
                result = export_midi_mp3(
                    decoded_audio.events, music, folder / relative_stem, cancel=cancel,
                    progress=lambda _fraction: emit(f'Preparing MIDI/MP3 for {item.destination}'),
                )
                # Publication verified the pair. Record both hashes before
                # considering a cancellation so a completed pair stays verifiable.
                for derivative_index, destination in zip(derivative_indices, destinations):
                    derivative = manifest['derivatives'][derivative_index]
                    derivative.update(status='verified', size=destination.stat().st_size,
                                      sha256=_hash(destination))
                    journal.append('derivatives', derivative_index, derivative)
                details.update(status='decoded',
                               destinations=[path.as_posix() for path in paths],
                               control_channel=decoded_audio.control_channel,
                               decode=decoded_audio.report,
                               export=result, hardware_verified=False)
                decoded += 1
        except NotEncodedAudioError as exc:
            details.update(status='skipped', reason=str(exc))
            skipped += 1
        except (CancelledError, InterruptedError) as exc:
            details['status'] = 'cancelled'
            if getattr(exc, 'retained_paths', ()) or getattr(exc, 'recovery_directory', ''):
                details.update(recovery=str(exc),
                               retained_paths=list(getattr(exc, 'retained_paths', ())),
                               recovery_directory=getattr(exc, 'recovery_directory', ''))
                manifest['errors'].append(f'{item.source}: {exc}')
            for derivative_index in derivative_indices:
                derivative = manifest['derivatives'][derivative_index]
                if derivative['status'] != 'verified':
                    derivative['status'] = 'cancelled'
                    journal.append('derivatives', derivative_index, derivative)
            raise CancelledError('Cancelled by user') from None
        except Exception as exc:
            interrupted = isinstance(getattr(exc, 'original_exception', None), InterruptedError)
            if interrupted and not getattr(exc, 'rollback_errors', ()):
                details.update(status='cancelled', recovery=str(exc),
                               retained_paths=list(getattr(exc, 'retained_paths', ())),
                               recovery_directory=getattr(exc, 'recovery_directory', ''))
                if details['retained_paths'] or details['recovery_directory']:
                    manifest['errors'].append(f'{item.source}: {exc}')
                for derivative_index in derivative_indices:
                    derivative = manifest['derivatives'][derivative_index]
                    if derivative['status'] != 'verified':
                        derivative['status'] = 'cancelled'
                        journal.append('derivatives', derivative_index, derivative)
                raise CancelledError('Cancelled by user') from None
            details.update(status='failed', error=str(exc))
            message = f'{item.source}: Audio decoding failed: {exc}'
            manifest['errors'].append(message)
            manifest['conversion_errors'].append(message)
            failed += 1
            for derivative_index in derivative_indices:
                derivative = manifest['derivatives'][derivative_index]
                if derivative['status'] != 'verified':
                    derivative.update(status='failed', error=str(exc))
                    journal.append('derivatives', derivative_index, derivative)
        finally:
            completed += 1
            journal.append('files', index, original_record)
            emit(f'Decoded {decoded} of {len(candidates)} audio files', force=True)
    return decoded, skipped, failed


def _cataloged_source_folders(folder, records, cancel=None):
    """Find valid source catalogs in verified archival copies, including empty ones."""
    from ..smart_pianosoft import parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog
    from ..yamaha_catalogs import yamaha_metadata_from_pianodir_bytes

    readers = {'pdisk.mng': parse_smart_pianosoft_disk_title,
               'psong.mng': parse_smart_pianosoft_song_catalog,
               'pianodir.fil': yamaha_metadata_from_pianodir_bytes}
    result = {}
    for record in records:
        source = Path(record['source'])
        reader = readers.get(source.name.casefold())
        if record['status'] not in {'verified', 'referenced'} or reader is None:
            continue
        payload = _read_conversion_source(
            _contained(folder, record['destination']), record['sha256'], cancel,
        )
        try:
            reader(payload)
        except ValueError:
            continue
        names = ({'PDISK.MNG', 'PSONG.MNG'} if source.name.casefold() == 'pianodir.fil'
                 else {source.name.upper()})
        result.setdefault(source.parent.as_posix(), set()).update(names)
    return result


def _write_yamaha_catalogs(plan, folder, records, manifest, journal, cancel=None, *,
                           cataloged_sources=None, original_root=None):
    """Catalog only verified playback MIDI files; archive catalogs stay intact."""
    from collections import defaultdict

    from .tags import read_music_title
    from ..smart_pianosoft import (
        build_smart_pianosoft_song_catalog, build_smart_pianosoft_song_record,
        parse_smart_pianosoft_disk_title, smart_pianosoft_catalog_by_filename,
        update_smart_pianosoft_disk_title,
    )

    original_root = original_root or folder
    originals = {item.source: item for item in plan.files}
    catalogs = defaultdict(dict)
    for record in records:
        source = Path(record['source'])
        if record['status'] in {'verified', 'referenced'} and source.name.casefold() in {'pdisk.mng', 'psong.mng'}:
            catalogs[source.parent.as_posix()][source.name.casefold()] = record
    groups = defaultdict(list)
    for record in manifest['derivatives']:
        if record['status'] == 'verified' and record['kind'] == 'MIDI':
            if (cataloged_sources is not None
                    and Path(record['source']).parent.as_posix() not in cataloged_sources):
                continue
            groups[Path(record['destination']).parent].append(record)

    failed = False
    for parent, songs in groups.items():
        check_cancel(cancel)
        try:
            def track_order(record):
                value = originals[record['source']].metadata.get('track_number')
                return int(value) if str(value).isdigit() and int(value) > 0 else 1000000

            songs.sort(key=track_order)
            item = originals[songs[0]['source']]
            source_parent = Path(item.source).parent.as_posix()
            catalog_names = ({'PDISK.MNG', 'PSONG.MNG'} if cataloged_sources is None
                             else cataloged_sources[source_parent])
            templates = catalogs[source_parent]
            template_data = {}
            for name, record in templates.items():
                template_data[name] = _read_conversion_source(
                    _contained(original_root, record['destination']), record['sha256'], cancel,
                )
            source_songs = {}
            if 'psong.mng' in template_data:
                try:
                    source_songs = smart_pianosoft_catalog_by_filename(template_data['psong.mng'])
                except ValueError:
                    # An unsupported catalog is still preserved in the archive;
                    # recovered scan labels can populate a fresh playback copy.
                    template_data.pop('psong.mng')
            if 'pdisk.mng' in template_data:
                try:
                    parse_smart_pianosoft_disk_title(template_data['pdisk.mng'])
                except ValueError:
                    template_data.pop('pdisk.mng')
            song_records = []
            for derivative in songs:
                check_cancel(cancel)
                item = originals[derivative['source']]
                path = _contained(folder, derivative['destination'])
                with path.open('rb') as stream:
                    header = stream.read(14)
                if len(header) != 14 or header[:4] != b'MThd':
                    raise ValueError(f'Prepared file is not MIDI: {path.name}')
                title = item.metadata.get('title')
                if item.metadata.get('metadata_source') == 'Original filename':
                    title = read_music_title(path) or title
                if _hash(path, cancel) != derivative['sha256']:
                    raise ValueError(f'Prepared MIDI checksum changed before cataloging: {path.name}')
                source_song = source_songs.get(Path(item.source).name.casefold())
                if source_song is None:
                    # Encoded audio inherits the catalog record of its MIDI
                    # companion only when that stem has one unambiguous entry.
                    matching = [song for song in source_songs.values()
                                if Path(song.filename).stem.casefold() == Path(item.source).stem.casefold()]
                    if len(matching) == 1:
                        source_song = matching[0]
                if 'PSONG.MNG' in catalog_names:
                    song_records.append(build_smart_pianosoft_song_record(
                        path.name, title or Path(item.source).stem,
                        source_record=source_song.raw_record if source_song else b'',
                        midi_format=int.from_bytes(header[8:10], 'big'),
                    ))
            album_title = item.metadata.get('album') or parent.name
            payloads = {}
            if 'PSONG.MNG' in catalog_names:
                payloads['PSONG.MNG'] = build_smart_pianosoft_song_catalog(
                    template_data.get('psong.mng', b''), song_records)
            if 'PDISK.MNG' in catalog_names:
                payloads['PDISK.MNG'] = update_smart_pianosoft_disk_title(
                    template_data.get('pdisk.mng', b''), album_title)
            for name, payload in payloads.items():
                relative = parent / name
                original_catalog = templates.get(name.casefold(), {})
                record = {'source': source_parent,
                          'original': original_catalog.get('destination', ''),
                          'destination': relative.as_posix(), 'kind': 'Yamaha catalog',
                          'conversion': 'Yamaha MIDI titles', 'status': 'pending'}
                index = len(manifest['derivatives'])
                manifest['derivatives'].append(record)
                journal.append('derivatives', index, record)
                try:
                    checksum = _write_derivative(_contained(folder, relative.as_posix()), payload, cancel)
                    record.update(status='verified', size=len(payload), sha256=checksum)
                except CancelledError:
                    record['status'] = 'cancelled'
                    raise
                except Exception as exc:
                    record.update(status='failed', error=str(exc))
                    raise
                finally:
                    journal.append('derivatives', index, record)
        except CancelledError:
            raise
        except Exception as exc:
            message = f'{parent}: Yamaha MIDI catalog creation failed: {exc}'
            manifest['errors'].append(message)
            manifest['conversion_errors'].append(message)
            failed = True
    return failed


def run_backup(plan: BackupPlan, target: Path, progress=None, cancel=None, *,
               convert_eseq: bool = False, keep_originals: bool = True,
               random_suffixes: bool = True, split_audio: bool = False,
               generate_yamaha_catalogs: bool = False, preserve_yamaha_catalogs: bool = False,
               audio_output: str | None = None) -> BackupResult:
    """Create a new archive or prepare playback files from an existing archive.

    Completed files are never overwritten. A manifest and .incomplete marker
    distinguish a cancelled, failed, or interrupted run from a complete backup.
    Optional E-SEQ conversion runs after every original has been copied, adding
    verified MIDI siblings. If keep_originals is false, a converted E-SEQ copy
    is removed from the backup only after its MIDI and progress are saved.
    Failed conversions retain their originals. Playback preparation resolves
    catalog companions and exports MIDI/MP3 pairs or encoded WAVs. Existing
    archives are verified in place and only new playback files are written.
    Legacy audio decoding adds MIDI/MP3 pairs in a separate tree. Encoded
    audio originals are always kept. Compatible Yamaha preparation adds title
    catalogs beside converted MIDI, with DOS filenames in separate playback
    trees so archival catalogs retain their original bytes. Preservation-only
    preparation adds playback catalogs only where valid source catalogs exist.
    Source files are never changed.
    """
    check_cancel(cancel)
    from ..audio_preparation import AUDIO_OUTPUT_FORMATS, MIDI_MP3

    if plan.source_backup and split_audio and audio_output is None:
        audio_output = MIDI_MP3
    if audio_output is not None and audio_output not in AUDIO_OUTPUT_FORMATS:
        raise ValueError('Choose paired MIDI/MP3 or encoded WAV playback output.')
    prepare_audio = audio_output is not None
    keep_originals = plan.source_backup or not convert_eseq or bool(keep_originals)
    target = _validate_target(plan, Path(target), convert_eseq=convert_eseq,
                              split_audio=split_audio or prepare_audio, prepare_midi=prepare_audio)
    candidates = _audio_candidates(plan)
    requires_lame = (audio_output == MIDI_MP3 or split_audio
                     or (prepare_audio and any(Path(item.source).suffix.lower() == '.mp3'
                                               for item in candidates)))
    if requires_lame and candidates:
        from ..audio_tools import find_lame_command

        if not find_lame_command():
            raise ValueError(
                'LAME is required to prepare MP3 audio. Install LAME before starting the backup.')
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    prefix = 'MarkIV-Prepared' if plan.source_backup else 'MarkIV-Backup'
    folder = create_output_directory(target, f'{prefix}-{stamp}',
                                     random_suffixes=random_suffixes)
    original_root = plan.source if plan.source_backup else folder
    (folder / '.incomplete').write_text(
        'Backup has not completed. Verify Backup reads manifest.json and any\n'
        'manifest.journal.jsonl to recover saved progress.\n', encoding='utf-8')
    records = [dict(asdict(item), status='pending') for item in plan.files]
    manifest = {'schema_version': 1, 'application': {'name': APP_NAME, 'version': __version__},
                'created_at': datetime.now(timezone.utc).isoformat(),
                'source': str(plan.source), 'status': 'in_progress', 'total_bytes': plan.total_bytes,
                'albums': [asdict(album) for album in plan.albums], 'warnings': list(plan.warnings),
                'files': records, 'errors': list(plan.scan_errors),
                'options': {'convert_eseq': bool(convert_eseq), 'keep_originals': keep_originals,
                            'split_audio': bool(split_audio),
                            'generate_yamaha_catalogs': bool(generate_yamaha_catalogs),
                            'preserve_yamaha_catalogs': bool(preserve_yamaha_catalogs)},
                'derivatives': [], 'conversion_errors': []}
    if prepare_audio:
        manifest['options']['audio_output'] = audio_output
    if plan.source_backup:
        manifest['options']['source_backup'] = True
    errors = manifest['errors']
    copied = verified = bytes_done = converted = conversion_total = 0
    decoded = audio_skipped = 0
    last_emit = 0.0

    def emit(message, event='progress', force=False):
        nonlocal last_emit
        now = time.monotonic()
        if progress and (force or now - last_emit > 0.1):
            progress({'event': event, 'message': message, 'completed': verified,
                      'total': len(records), 'bytes_done': bytes_done,
                      'bytes_total': plan.total_bytes * (1 if plan.source_backup else 2),
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
                if plan.source_backup:
                    # Existing archives are immutable inputs. Validate their
                    # recorded bytes and create only new playback derivatives.
                    if src.stat().st_size != item.size:
                        raise ValueError('Archive source size changed since the scan')
                    checksum = _hash(src, cancel, read_chunk)
                    if not item.expected_sha256 or checksum != item.expected_sha256:
                        raise ValueError('Archive source SHA-256 checksum does not match its manifest')
                    record.update(status='referenced', sha256=checksum)
                    verified += 1
                    continue
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
                if item.expected_sha256 and expected != item.expected_sha256:
                    raise ValueError('Source SHA-256 checksum does not match the archive manifest')
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
        cataloged_sources = (_cataloged_source_folders(original_root, records, cancel)
                             if preserve_yamaha_catalogs else set())
        if prepare_audio:
            from .playback import resolve_playback_pairs
            from .playback_export import prepare_playback_copies
            from .solo_export import prepare_solo_midi_copies

            if progress:
                progress({'event': 'audio_prepare', 'completed': 0, 'total': 0, 'audio_total': 0})
            resolution = resolve_playback_pairs(
                plan, cancel=cancel,
                source_paths={item.source: _contained(original_root, item.destination) for item in plan.files},
            )
            decoded, audio_skipped, audio_failed = prepare_playback_copies(
                plan, folder, original_root, records, manifest, journal, audio_output,
                cancel=cancel, progress=progress, generate_yamaha_catalogs=generate_yamaha_catalogs,
                cataloged_sources=cataloged_sources, resolution=resolution,
            )
            if audio_failed:
                status = 'incomplete'
            solos, solo_failed = prepare_solo_midi_copies(
                plan, folder, original_root, records, manifest, journal,
                cancel=cancel, progress=progress, generate_yamaha_catalogs=generate_yamaha_catalogs,
                cataloged_sources=cataloged_sources, resolution=resolution,
            )
            decoded += solos
            if solo_failed:
                status = 'incomplete'
        if convert_eseq:
            reserved = {_path_key(item.destination) for item in plan.files}
            reserved.update(_path_key(parent) for item in plan.files
                            for parent in Path(item.destination).parents)
            candidates = []
            for index, (item, record) in enumerate(zip(plan.files, records)):
                check_cancel(cancel)
                if record['status'] not in {'verified', 'referenced'} or Path(item.source).suffix.lower() == '.pspg':
                    continue
                original = _contained(original_root, item.destination)
                # Recheck the verified copy instead of trusting the scan flag.
                if is_conversion_candidate(original, item.kind):
                    candidates.append((index, item, record))
            conversion_total = len(candidates)
            midi_root = (_audio_pair_root(folder, plan, 'Yamaha MIDI')
                         if generate_yamaha_catalogs or cataloged_sources else None)
            for original_index, item, original_record in candidates:
                check_cancel(cancel)
                if generate_yamaha_catalogs or Path(item.source).parent.as_posix() in cataloged_sources:
                    stem = _short_playback_stem(
                        folder, midi_root / Path(item.destination).parent, Path(item.source).name, reserved,
                    )
                    relative = stem.with_suffix('.mid')
                else:
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
                    original = _contained(original_root, item.destination)
                    data = _read_conversion_source(original, original_record['sha256'], cancel)
                    title = item.metadata.get('title')
                    if item.metadata.get('metadata_source') == 'Original filename':
                        title = None
                    payload = convert_eseq_bytes_to_midi_bytes(
                        data, filename=original.name, title_override=title,
                    )
                    check_cancel(cancel)
                    if shutil.disk_usage(folder).free < len(payload) + _manifest_allowance(plan):
                        raise ValueError('Not enough free space for the MIDI conversion and manifest.')
                    destination = _contained(folder, relative.as_posix())
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    checksum = _write_derivative(destination, payload, cancel)
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
        if split_audio and not prepare_audio:
            decoded, audio_skipped, audio_failed = _split_audio_copies(
                plan, folder, records, manifest, journal, cancel=cancel, progress=progress,
                generate_yamaha_catalogs=generate_yamaha_catalogs, cataloged_sources=cataloged_sources,
            )
            if audio_failed:
                status = 'incomplete'
        if (generate_yamaha_catalogs or cataloged_sources) and _write_yamaha_catalogs(
                plan, folder, records, manifest, journal, cancel,
                cataloged_sources=None if generate_yamaha_catalogs else cataloged_sources,
                original_root=original_root):
            status = 'incomplete'
        check_cancel(cancel)
    except CancelledError:
        status = 'cancelled'
    except Exception as exc:
        status = 'incomplete'
        errors.append(str(exc))
    # The audio helper can be interrupted after completing earlier pairs.
    # Recover counts from the journaled per-original outcomes in that case.
    if prepare_audio:
        decoded = sum(record.get('playback_prepare', {}).get('status') == 'prepared' for record in records)
        audio_skipped = sum(record.get('playback_prepare', {}).get('status') == 'skipped' for record in records)
    elif split_audio:
        decoded = sum(record.get('audio_decode', {}).get('status') == 'decoded' for record in records)
        audio_skipped = sum(record.get('audio_decode', {}).get('status') == 'skipped' for record in records)
    manifest.update(status=status, completed_at=datetime.now(timezone.utc).isoformat(),
                    copied=copied, verified=verified, converted=converted,
                    decoded=decoded, audio_skipped=audio_skipped)
    journal.finish()
    conversion_note = (
        ('Cataloged E-SEQ conversions are additional MIDI files in Yamaha MIDI; other conversions stay beside the originals.\n'
         if generate_yamaha_catalogs or preserve_yamaha_catalogs else
         'Optional E-SEQ conversions are additional MIDI files beside the originals.\n')
        if keep_originals else
        'Successfully converted E-SEQ copies are replaced by verified MIDI files.\n'
        'Unconverted originals and files whose conversion failed are retained.\n'
    )
    (folder / 'README.txt').write_text(
        f'{APP_NAME} {__version__}\nCreated by a tool from {COMPANY_NAME}.\n\n'
        f'Status: {status}\nVerified originals: {verified}/{len(records)}\n'
        f'Converted MIDI copies: {converted}\n'
        f'Playback format: {audio_output or (MIDI_MP3 if split_audio else "originals")}\n'
        f'Prepared playback songs: {decoded}; audio files left unchanged: {audio_skipped}\n\n'
        + (f'Originals remain in the source archive: {plan.source}\n'
           'This folder contains new playback files and a record of their verified sources.\n'
           if plan.source_backup else '') +
        'Retained original-format files have unchanged contents.\n'
        + conversion_note +
        'manifest.json records original paths, recovered titles, and SHA-256 checksums.\n'
        'Its derivatives list records converted MIDI files separately from originals.\n'
        'Yamaha MIDI contains cataloged E-SEQ conversions when compatible preparation is selected.\n'
        'PDISK.MNG and PSONG.MNG beside playback MIDI preserve recovered album and song titles.\n'
        'Playback files are in Paired MIDI and MP3 or Encoded WAV (or a numbered variant).\n'
        'Standalone MIDI performances are in MIDI, with the same album organization.\n'
        'For ENSPIRE USB playback, copy each desired album folder from that tree;\n'
        'keep the matching .mid and .mp3 filenames together. Audio originals stay in the archive.\n'
        'The control channel is removed only from successfully decoded music copies.\n'
        'Hardware timing and MP3 decoder delay handling still require a piano playback check.\n'
        'Replaced original records identify their MIDI replacement.\n'
        '_metadata/original retains available database and catalog source files.\n'
        'PianoSoft packages and presentation assets retain their original contents.\n'
        'This is a music archive, not a bootable disk image or an automatic piano restore.\n', encoding='utf-8')
    if status == 'complete':
        (folder / '.incomplete').unlink()
    emit(f'Backup {status}: {verified} files verified', event=status, force=True)
    return BackupResult(folder, status, copied, verified, errors, converted, decoded, audio_skipped,
                        manifest['warnings'])


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
            if index < len(manifest['files']) and record.get('status') == 'referenced':
                if (manifest.get('options', {}).get('source_backup') is not True
                        or not re.fullmatch(r'[0-9a-fA-F]{64}', str(record.get('sha256', '')))):
                    raise ValueError('Invalid original archive reference')
                # Portable playback exports record original provenance. Verify
                # their local derivatives below without requiring the archive.
            elif index < len(manifest['files']) and replacement_status:
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
