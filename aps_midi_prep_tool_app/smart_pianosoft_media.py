"""Read-only source snapshots and secure Linux audio-CD ingestion.

These functions copy media; they do not certify a PSONG catalog as authentic
Smart PianoSoft. The caller must validate the MIDI synchronization messages.
All destination paths must be new. Progress callbacks receive (step, total,
message); cancellation accepts a callback or threading.Event and raises
FloppyOperationCancelled.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import wave

from .floppy_image import (
    DISK_FORMAT_BY_SIZE, FloppyDriveInfo, FloppyImageError, FloppyImageSession,
    FloppyOperationCancelled, _terminate_process,
)
from .subprocess_utils import windows_subprocess_kwargs
from .helpers.atomic_file import publish_new_file


MEDIA_HELPER_ARG = "--aps-smart-pianosoft-media-helper"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
CD_QUERY_TIMEOUT = 30.0
FLOPPY_TIMEOUT = 180.0


class SmartPianoSoftMediaError(FloppyImageError):
    pass


@dataclass(frozen=True)
class CDTrack:
    number: int
    start_sector: int
    sectors: int
    pre_emphasis: bool = False

    @property
    def duration_seconds(self):
        return self.sectors / 75.0


def _cancel_callback(cancel):
    return cancel.is_set if hasattr(cancel, "is_set") else cancel


def _check_cancel(cancel):
    callback = _cancel_callback(cancel)
    if callback is not None and callback():
        raise FloppyOperationCancelled("Operation cancelled.")


def _progress(progress, step, total, message, cancel=None):
    _check_cancel(cancel)
    if progress is not None:
        progress(step, total, message)
    _check_cancel(cancel)


def _linux_required():
    if not sys.platform.startswith("linux"):
        raise SmartPianoSoftMediaError(
            "Physical CD and floppy ingestion is currently available on Linux only. "
            "Choose a saved floppy image or folder and pair existing audio files instead."
        )


def discover_cd_drives():
    """Return [(device_path, display_label)] without opening optical media."""
    if not sys.platform.startswith("linux"):
        return []
    drives = []
    for entry in sorted(Path("/sys/class/block").glob("*")):
        try:
            if (entry / "device/type").read_text().strip() != "5":
                continue
            device = Path("/dev") / entry.name
            if not stat.S_ISBLK(device.stat().st_mode):
                continue
            label = " ".join(
                (entry / "device" / field).read_text().strip()
                for field in ("vendor", "model")
            ).strip()
            drives.append((str(device), f"{device} — {label}" if label else str(device)))
        except OSError:
            continue
    return drives


def _device_identity(device):
    path = Path(device).resolve(strict=True)
    info = path.stat()
    if not stat.S_ISBLK(info.st_mode):
        raise SmartPianoSoftMediaError(f"The selected source is not a block device: {device}")
    sys_device = Path("/sys/class/block") / path.name
    return str(path), info.st_dev, info.st_ino, info.st_rdev, str(sys_device.resolve())


def _cdparanoia():
    command = shutil.which("cdparanoia")
    if command is None:
        raise SmartPianoSoftMediaError(
            "Secure CD reading requires cdparanoia. Install it or pair existing audio files."
        )
    return command


def _run_command(args, *, timeout, cancel=None, progress=None, output=None, total=0):
    """Bound helpers and keep potentially large diagnostics out of pipe buffers."""
    _check_cancel(cancel)
    env = dict(os.environ, LC_ALL="C", LANG="C")
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            list(map(os.fspath, args)), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, env=env,
            start_new_session=os.name != "nt",
            **windows_subprocess_kwargs(),
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                _check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise SmartPianoSoftMediaError(
                        f"Media reading did not finish within {timeout:g} seconds."
                    )
                if progress is not None and output is not None:
                    try:
                        completed = max(0, Path(output).stat().st_size - 44)
                    except FileNotFoundError:
                        completed = 0
                    _progress(progress, min(completed, total), total,
                              "Reading audio CD with secure error correction…", cancel)
                time.sleep(0.1)
            _check_cancel(cancel)
            log.seek(0)
            text = log.read(1024 * 1024).decode("utf-8", errors="replace")
            if process.returncode:
                raise SmartPianoSoftMediaError(
                    f"Media reader failed (exit {process.returncode}): {text[-6000:].strip()}"
                )
            return text
        except BaseException:
            if os.name != "nt":
                try:
                    # A floppy helper may have started mtools children. Stop
                    # its entire owned session before removing staging files.
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            _terminate_process(process)
            raise


def _parse_cd_toc(text):
    # cdparanoia excludes data tracks from this table, retaining the original
    # track numbers. Do not infer that missing numbers are audio tracks.
    if "Table of contents (audio tracks only):" not in text:
        raise SmartPianoSoftMediaError("The drive did not return an audio-CD table of contents.")
    tracks = []
    pattern = re.compile(
        r"^\s*(\d+)\.\s+(\d+)\s+\[[^\]]+\]\s+(-?\d+)\s+\[[^\]]+\]"
        r"\s+(yes|no)\s+(yes|no)\s+(\d+)\s*$"
    )
    table = text.split("Table of contents (audio tracks only):", 1)[1]
    for line in table.splitlines():
        if not re.match(r"\s*\d+\.", line):
            continue
        match = pattern.fullmatch(line)
        if match is None:
            raise SmartPianoSoftMediaError("Unrecognized audio-CD track information.")
        number, sectors, start = map(int, match.group(1, 2, 3))
        if not 1 <= number <= 99 or sectors <= 0 or start < 0:
            raise SmartPianoSoftMediaError("Invalid audio-CD track boundaries.")
        if int(match.group(6)) != 2:
            raise SmartPianoSoftMediaError("Only two-channel audio-CD tracks are supported.")
        if tracks and (number <= tracks[-1].number
                       or start < tracks[-1].start_sector + tracks[-1].sectors):
            raise SmartPianoSoftMediaError("Audio-CD tracks overlap or are out of order.")
        tracks.append(CDTrack(number, start, sectors, match.group(5) == "yes"))
    if not tracks:
        raise SmartPianoSoftMediaError("The disc has no readable audio tracks.")
    return tuple(tracks)


def read_cd_toc(device, cancel=None):
    """Read audio tracks only; pre-emphasis is exposed and never removed silently."""
    cancel = _cancel_callback(cancel)
    _linux_required()
    _check_cancel(cancel)
    identity = _device_identity(device)
    text = _run_command([_cdparanoia(), "-Q", "-d", device],
                        timeout=CD_QUERY_TIMEOUT, cancel=cancel)
    if _device_identity(device) != identity:
        raise SmartPianoSoftMediaError("The selected CD drive changed while reading its track list.")
    return _parse_cd_toc(text)


def _new_destination(destination):
    destination = Path(os.path.abspath(os.fspath(destination)))
    if os.path.lexists(destination):
        raise FileExistsError(f"The destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _validate_cd_wav(path, track, cancel=None):
    try:
        with wave.open(str(path), "rb") as audio:
            expected_frames = track.sectors * 588
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                    audio.getcomptype(), audio.getnframes()) != (
                        2, 2, 44100, "NONE", expected_frames):
                raise SmartPianoSoftMediaError(
                    "The CD reader did not produce the complete 44.1 kHz, 16-bit stereo track."
                )
            frames = 0
            while data := audio.readframes(16384):
                _check_cancel(cancel)
                if len(data) % 4:
                    raise SmartPianoSoftMediaError("The ripped WAV contains an incomplete audio frame.")
                frames += len(data) // 4
            if frames != expected_frames:
                raise SmartPianoSoftMediaError("The ripped WAV audio is truncated.")
    except (wave.Error, EOFError) as exc:
        raise SmartPianoSoftMediaError(f"The CD reader produced an invalid WAV: {exc}") from exc


def _publish_new_file(staged_path, destination, cancel=None):
    publish_new_file(staged_path, destination, check_cancel=lambda: _check_cancel(cancel))


def rip_cd_track(device, track, destination, cancel=None, progress=None):
    """Securely read one CD track and publish a checked PCM WAV without overwriting.

    Pre-emphasized discs need an explicit de-emphasis workflow and are rejected.
    The drive identity and the complete audio TOC must match before and after
    ripping. Identical TOCs cannot distinguish different pressings of an album;
    the synchronization layer must still match the audio to the MIDI fingerprint.
    """
    cancel = _cancel_callback(cancel)
    _linux_required()
    _check_cancel(cancel)
    if not isinstance(track, CDTrack) or not 1 <= track.number <= 99 or track.sectors <= 0:
        raise SmartPianoSoftMediaError("Select a valid audio-CD track.")
    if track.pre_emphasis:
        raise SmartPianoSoftMediaError(
            "This CD track uses pre-emphasis. Automatic ripping cannot apply verified "
            "de-emphasis yet; pair a correctly de-emphasized audio file instead."
        )
    destination = _new_destination(destination)
    identity = _device_identity(device)
    toc = read_cd_toc(device, cancel=cancel)
    if track not in toc:
        raise SmartPianoSoftMediaError("The audio CD changed. Read its track list again.")
    descriptor, temporary = tempfile.mkstemp(prefix=".aps_cd_", suffix=".wav", dir=destination.parent)
    os.close(descriptor)
    try:
        total = track.sectors * 2352
        _progress(progress, 0, total, f"Reading CD track {track.number}…", cancel)
        _run_command(
            [_cdparanoia(), "-w", "-X", "-z", "-d", device, str(track.number), temporary],
            timeout=max(180.0, min(3600.0, track.duration_seconds * 12)),
            cancel=cancel, progress=progress, output=temporary, total=total,
        )
        _validate_cd_wav(temporary, track, cancel)
        if _device_identity(device) != identity or read_cd_toc(device, cancel=cancel) != toc:
            raise SmartPianoSoftMediaError("The audio CD changed during ripping; its output was discarded.")
        _check_cancel(cancel)
        # Windows requires write access for fsync; preserve the validated bytes.
        with open(temporary, "r+b") as handle:
            os.fsync(handle.fileno())
        _publish_new_file(temporary, destination, cancel)
        _progress(progress, total, total, f"CD track {track.number} read and verified.")
        return destination
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _source_file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _copy_checked(source, destination, cancel=None):
    if source.is_symlink() or not source.is_file():
        raise SmartPianoSoftMediaError(f"Source files must be regular files, not links: {source}")
    identity = _source_file_identity(source.stat())
    if identity[2] > MAX_SOURCE_BYTES:
        raise SmartPianoSoftMediaError("A source file exceeds the floppy snapshot size limit.")
    digest = hashlib.sha256()
    copied = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        if _source_file_identity(os.fstat(incoming.fileno())) != identity:
            raise SmartPianoSoftMediaError("A source file changed before it could be copied.")
        while chunk := incoming.read(1024 * 1024):
            _check_cancel(cancel)
            copied += len(chunk)
            if copied > MAX_SOURCE_BYTES:
                raise SmartPianoSoftMediaError("A source file grew beyond the snapshot size limit.")
            digest.update(chunk)
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
        if _source_file_identity(os.fstat(incoming.fileno())) != identity:
            raise SmartPianoSoftMediaError("A source file changed while it was being copied.")
    _check_cancel(cancel)
    if copied != identity[2] or _source_file_identity(source.stat()) != identity:
        raise SmartPianoSoftMediaError("A source file changed while it was being copied.")
    verified = hashlib.sha256()
    with destination.open("rb") as incoming:
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            _check_cancel(cancel)
            verified.update(chunk)
    if verified.digest() != digest.digest():
        raise SmartPianoSoftMediaError("The source snapshot did not pass checksum verification.")
    return copied, identity


def _wanted_file(name):
    return name.upper() in {"PSONG.MNG", "PDISK.MNG"} or Path(name).suffix.lower() in {".mid", ".midi"}


def _copy_folder(source, destination, cancel=None, progress=None):
    selected = sorted((entry for entry in source.iterdir() if _wanted_file(entry.name)),
                      key=lambda entry: entry.name.casefold())
    if not selected:
        raise SmartPianoSoftMediaError("The source contains no MIDI files or Yamaha catalogs.")
    if len(selected) > 1001 or len({entry.name.casefold() for entry in selected}) != len(selected):
        raise SmartPianoSoftMediaError("The source contains ambiguous or excessive catalog/MIDI files.")
    total_bytes = 0
    identities = {}
    for index, entry in enumerate(selected):
        _progress(progress, index, len(selected), f"Copying {entry.name}…", cancel)
        copied, identity = _copy_checked(entry, destination / entry.name, cancel)
        total_bytes += copied
        identities[entry] = identity
        if total_bytes > MAX_SOURCE_BYTES:
            raise SmartPianoSoftMediaError("The source exceeds the floppy snapshot size limit.")
    if {entry.name for entry in source.iterdir() if _wanted_file(entry.name)} != {entry.name for entry in selected}:
        raise SmartPianoSoftMediaError("The source folder changed during the snapshot.")
    for entry, identity in identities.items():
        if _source_file_identity(entry.stat()) != identity:
            raise SmartPianoSoftMediaError("The source folder changed during the snapshot.")


def _copy_session(source, destination, cancel=None, progress=None):
    loader = FloppyImageSession.load_floppy if isinstance(source, FloppyDriveInfo) else FloppyImageSession.load
    session = loader(source, cancel_callback=cancel, progress_callback=progress)
    try:
        entries = [entry for entry in session.list_entries().entries
                   if not entry.is_directory and _wanted_file(entry.name)]
        if not entries or len(entries) > 1001 or sum(entry.size for entry in entries) > MAX_SOURCE_BYTES:
            raise SmartPianoSoftMediaError("The image has no MIDI/catalog files or exceeds the snapshot limit.")
        names = set()
        for index, entry in enumerate(entries):
            relative = PurePosixPath(entry.path.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or str(relative).casefold() in names:
                raise SmartPianoSoftMediaError("The image contains unsafe or ambiguous filenames.")
            names.add(str(relative).casefold())
            _progress(progress, index, len(entries), f"Extracting {entry.name}…", cancel)
            extracted = Path(session.extract_file(entry.path))
            _copy_checked(extracted, destination.joinpath(*relative.parts), cancel)
        if not isinstance(source, FloppyDriveInfo):
            session._assert_source_unchanged()
    finally:
        session.cleanup()


def _floppy_info(source):
    if isinstance(source, FloppyDriveInfo):
        return source
    path = Path(source).resolve(strict=True)
    size = int((Path("/sys/class/block") / path.name / "size").read_text().strip()) * 512
    return FloppyDriveInfo(str(path), size, transport="usb")


def _helper_command(source, destination):
    arguments = [MEDIA_HELPER_ARG, source.path, str(source.size_bytes), str(destination)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    entry = Path(__file__).resolve().parent.parent / "aps_midi_prep_tool.py"
    return [sys.executable, str(entry), *arguments]


def read_floppy_source(source, destination, cancel=None, progress=None):
    """Snapshot unchanged catalog/MIDI bytes from one album folder, image, or USB floppy.

    Album folders are intentionally non-recursive. Existing long MIDI names
    remain intact for the caller's catalog/name reconciliation. No repair or
    mutation is ever committed to the source. Raw device reads run in a helper
    with a deadline, so an unresponsive driver cannot block the caller forever.
    """
    cancel = _cancel_callback(cancel)
    _check_cancel(cancel)
    path = Path(source.path if isinstance(source, FloppyDriveInfo) else source)
    physical = isinstance(source, FloppyDriveInfo) or stat.S_ISBLK(path.stat().st_mode)
    destination = Path(os.path.abspath(os.fspath(destination)))
    if path.is_dir() and destination.resolve().is_relative_to(path.resolve()):
        raise SmartPianoSoftMediaError("The snapshot destination must be outside its source folder.")
    destination = _new_destination(destination)
    with tempfile.TemporaryDirectory(prefix=".aps_source_", dir=destination.parent) as temporary:
        snapshot = Path(temporary) / "snapshot"
        snapshot.mkdir()
        if physical:
            _linux_required()
            identity = _device_identity(path)
            info = _floppy_info(source)
            if info.size_bytes not in DISK_FORMAT_BY_SIZE:
                raise SmartPianoSoftMediaError("Select a supported floppy-size device.")
            _progress(progress, 0, 0, f"Reading floppy {info.path}…", cancel)
            _run_command(_helper_command(info, snapshot), timeout=FLOPPY_TIMEOUT, cancel=cancel)
            if _device_identity(path) != identity:
                raise SmartPianoSoftMediaError("The selected floppy drive changed during reading.")
        elif path.is_dir():
            _copy_folder(path, snapshot, cancel, progress)
        else:
            if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
                raise SmartPianoSoftMediaError("Select a supported floppy image or album folder.")
            _copy_session(str(path), snapshot, cancel, progress)
        _check_cancel(cancel)
        if os.path.lexists(destination):
            raise FileExistsError(f"The destination already exists: {destination}")
        os.rename(snapshot, destination)
    _progress(progress, 1, 1, "Floppy source copied and verified.")
    return destination


def run_media_helper_from_argv(argv):
    """Early application dispatch for the bounded, read-only floppy helper."""
    if len(argv) < 2 or argv[1] != MEDIA_HELPER_ARG:
        return None
    if len(argv) != 5:
        return 2
    try:
        _linux_required()
        source = FloppyDriveInfo(argv[2], int(argv[3]), transport="usb")
        _device_identity(source.path)
        if source.size_bytes not in DISK_FORMAT_BY_SIZE:
            raise SmartPianoSoftMediaError("Unsupported floppy capacity.")
        destination = Path(argv[4])
        if not destination.is_dir() or any(destination.iterdir()):
            raise SmartPianoSoftMediaError("The helper requires an empty staging directory.")
        # Keep the session's temporary image inside the parent's staging tree,
        # including when a stalled helper must be killed before its finally block.
        tempfile.tempdir = str(destination.parent)
        _copy_session(source, destination)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
