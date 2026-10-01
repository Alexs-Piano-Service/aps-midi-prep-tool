"""Read-only Smart PianoSoft album preparation and verified audio pairing.

MIDI snapshots and CD PCM are retained separately from the experimental
PianoSoft PlusAudio channel encoding. Current-list snapshots include staged
MIDI edits and adjust catalog selections and titles. Original source media
remains unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import wave

from .smart_pianosoft import (
    parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog,
    update_smart_pianosoft_song_catalog,
)
from .dos83_renamer import is_dos83_filename


class AlbumBuildError(RuntimeError):
    def __init__(self, message, output_directory=None):
        super().__init__(message)
        self.output_directory = output_directory


@dataclass(frozen=True)
class AlbumTrack:
    number: int
    filename: str
    title: str
    midi_path: Path
    midi_sha256: str
    metadata: object
    display_filename: str = ""


@dataclass(frozen=True)
class ListedAlbumTrack:
    catalog_filename: str
    filename: str
    title: str
    midi_bytes: bytes


@dataclass(frozen=True)
class LoadedAlbumSource:
    label: str
    original_source: Path
    song_catalog: bytes
    disk_catalog: bytes
    tracks: tuple[ListedAlbumTrack, ...]
    error: str = ""
    source_device: int | None = None


@dataclass(frozen=True)
class AlbumSnapshot:
    title: str
    source_directory: Path
    original_source: Path
    tracks: tuple[AlbumTrack, ...]
    source_device: int | None = None


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Smart PianoSoft preparation cancelled.")


def _emit(progress, phase, completed, total, message):
    if progress is not None:
        progress({"phase": phase, "completed": completed, "total": total, "message": message})


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def scan_album(source, destination, *, cancel=None, progress=None):
    """Snapshot a floppy/folder/image, then require real SPS synchronization data."""
    from .smart_pianosoft_media import read_floppy_source
    from .smart_pianosoft_sync import parse_sps_midi

    _check_cancel(cancel)
    folder = Path(read_floppy_source(
        source, destination, cancel=cancel,
        progress=lambda step, total, message: _emit(progress, "read", step, total, message),
    ))
    files = {}
    for path in folder.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        key = path.name.casefold()
        if key in files:
            raise ValueError(f"Ambiguous source filename: {path.name}")
        files[key] = path
    song_catalog = files.get("psong.mng")
    if song_catalog is None:
        raise ValueError("This is not a Smart PianoSoft floppy: PSONG.MNG is missing.")
    songs = parse_smart_pianosoft_song_catalog(song_catalog.read_bytes())
    if not songs or len(songs) > 99:
        raise ValueError("A Smart PianoSoft album must contain 1–99 cataloged songs.")
    title = Path(source).stem
    if "pdisk.mng" in files:
        title = parse_smart_pianosoft_disk_title(files["pdisk.mng"].read_bytes()) or title
    tracks, used = [], set()
    for song in songs:
        _check_cancel(cancel)
        # Catalog fields are untrusted filenames, never paths outside this copy.
        name = song.filename
        if (not name or name in {".", ".."} or any(c in name for c in '/\\:')
                or Path(name).suffix.casefold() not in {".mid", ".midi"}):
            raise ValueError(f"Unsupported Smart PianoSoft catalog filename: {name!r}")
        path = files.get(name.casefold())
        if path is None:
            # The application's bulk extractor retains the catalog, while
            # naming MIDI copies '01 - Song title.mid' in catalog order.
            pattern = re.compile(rf"^{song.track_number:02d}\s+-\s+.+\.mid(?:i)?$", re.I)
            matches = [p for p in files.values() if pattern.fullmatch(p.name)]
            if len(matches) != 1:
                raise ValueError(f"Missing or ambiguous cataloged MIDI file: {name}")
            path = matches[0]
        if path in used:
            raise ValueError(f"Duplicate MIDI entry in PSONG.MNG: {name}")
        used.add(path)
        metadata = parse_sps_midi(path)
        # parse_sps_midi validates both complete Yamaha audio fingerprints.
        # PSONG alone is also used by ordinary MIDI albums and is insufficient.
        tracks.append(AlbumTrack(song.track_number, name, song.title, path, _digest(path), metadata))
        _emit(progress, "scan", len(tracks), len(songs), song.title or name)
    return AlbumSnapshot(title, folder, Path(source).absolute(), tuple(tracks))


def scan_loaded_album(source: LoadedAlbumSource, destination, *, cancel=None, progress=None):
    """Snapshot current-list bytes without reading the original disk or folder.

    Canonical catalog identities keep their original CD track numbers. Display
    names and row order belong to the current list; removed catalog slots stay
    blank so a subset cannot silently be matched against different CD tracks.
    """
    from .smart_pianosoft_sync import parse_sps_midi

    _check_cancel(cancel)
    if not isinstance(source, LoadedAlbumSource):
        raise ValueError("Invalid current-list Smart PianoSoft source.")
    if source.error:
        raise ValueError(source.error)
    songs = parse_smart_pianosoft_song_catalog(source.song_catalog)
    if not songs or len(songs) > 99 or any(song.track_number > 99 for song in songs):
        raise ValueError("A Smart PianoSoft album must contain 1–99 cataloged songs.")
    catalog = {}
    for song in songs:
        name = song.filename
        stem = name.rsplit(".", 1)[0].upper()
        if (not is_dos83_filename(name) or any(char in name for char in '/\\:')
                or Path(name).suffix.casefold() != ".mid"
                or stem in {"CON", "PRN", "AUX", "NUL"}
                or re.fullmatch(r"(?:COM|LPT)[1-9]", stem)):
            raise ValueError(f"Unsupported Smart PianoSoft catalog filename: {name!r}")
        key = name.casefold()
        if key in catalog:
            raise ValueError(f"Ambiguous MIDI identity in PSONG.MNG: {name}")
        catalog[key] = song
    if not source.tracks:
        raise ValueError("The current list contains no Smart PianoSoft songs.")
    selected, display_names, prepared = set(), set(), []
    title_updates = {}
    for item in source.tracks:
        _check_cancel(cancel)
        key = item.catalog_filename.casefold()
        if not key or key not in catalog:
            raise ValueError(f"No PSONG.MNG song record matches the current-list MIDI: {item.filename}")
        if key in selected:
            raise ValueError(f"Duplicate current-list catalog identity: {item.catalog_filename}")
        if not item.filename or any(char in item.filename for char in '/\\\x00'):
            raise ValueError("Current-list MIDI filenames must be nonempty basenames.")
        if item.filename.casefold() in display_names:
            raise ValueError(f"Ambiguous current-list MIDI filename: {item.filename}")
        selected.add(key)
        display_names.add(item.filename.casefold())
        song = catalog[key]
        if item.title != song.title:
            title_updates[song.filename] = item.title
        prepared.append((song, item, bytes(item.midi_bytes)))

    song_catalog = bytes(source.song_catalog)
    if title_updates or selected != set(catalog):
        # The editor's existing updater validates encoding/length and restores
        # canonical CRLF offsets when an extracted catalog used LF endings.
        patched = bytearray(update_smart_pianosoft_song_catalog(song_catalog, title_updates))
        for song in songs:
            if song.filename.casefold() not in selected:
                start = 0x80 + (song.track_number - 1) * 0xB0
                patched[start:start + 0xB0] = (b" " * 14 + b"\r\n") * 11
        song_catalog = bytes(patched)
    disk_catalog = bytes(source.disk_catalog)
    title = (parse_smart_pianosoft_disk_title(disk_catalog) if disk_catalog else "") or source.label
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        tracks = []
        for song, item, payload in prepared:
            _check_cancel(cancel)
            path = folder / song.filename
            with path.open("xb") as handle:
                handle.write(payload)
            metadata = parse_sps_midi(path)
            tracks.append(AlbumTrack(song.track_number, song.filename, item.title, path,
                                     hashlib.sha256(payload).hexdigest(), metadata,
                                     display_filename=item.filename))
            _emit(progress, "scan", len(tracks), len(prepared), item.title or item.filename)
        _check_cancel(cancel)
        with (folder / "PSONG.MNG").open("xb") as handle:
            handle.write(song_catalog)
        if disk_catalog:
            with (folder / "PDISK.MNG").open("xb") as handle:
                handle.write(disk_catalog)
        _check_cancel(cancel)
        return AlbumSnapshot(title, folder, Path(source.original_source).absolute(), tuple(tracks),
                             source_device=source.source_device)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _copy_wav(source, destination, *, cancel=None):
    """Preserve a paired music file exactly; never silently resample or truncate."""
    source, destination = Path(source), Path(destination)
    with wave.open(str(source), "rb") as audio:
        if (audio.getframerate() != 44100 or audio.getsampwidth() != 2
                or audio.getnchannels() != 2 or audio.getcomptype() != "NONE"):
            raise ValueError("Paired audio must be stereo, 44.1 kHz, 16-bit PCM WAV.")
        expected = audio.getnframes() * audio.getnchannels() * 2
        actual = 0
        while data := audio.readframes(65536):
            _check_cancel(cancel)
            actual += len(data)
        if not actual or actual != expected:
            raise ValueError(f"The audio file is empty or truncated: {source.name}")
    # The output album is newly created; exclusive creation protects its files.
    created = False
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            created = True
            while chunk := incoming.read(1024 * 1024):
                _check_cancel(cancel)
                outgoing.write(chunk)
        if _digest(source) != _digest(destination):
            raise ValueError(f"Audio changed while it was copied: {source.name}")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _save_report(folder, report):
    temporary = folder / "manifest.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(folder / "manifest.json")


def _safe_title(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:70]
    return name or "Album"


def _guard_output_source(source, output_parent, *, source_device=None):
    """Never create the output on a physical source floppy or beneath its folder."""
    device_ids = set()
    if source_device is not None:
        if type(source_device) is not int or source_device < 0:
            raise ValueError("The cached source-device identity is invalid.")
        device_ids.add(source_device)
    try:
        source_info = source.stat()
    except FileNotFoundError:
        if str(source).startswith("/dev/") and source_device is None:
            raise ValueError("The source device is unavailable; output safety cannot be verified.")
    else:
        if stat.S_ISDIR(source_info.st_mode) and (output_parent == source or source in output_parent.parents):
            raise ValueError("Choose an output folder outside the source album.")
        if stat.S_ISBLK(source_info.st_mode):
            device_ids.add(source_info.st_rdev)
    if not device_ids:
        return
    output_device = output_parent.stat().st_dev
    for device_id in device_ids:
        same_device = output_device == device_id
        # A mounted partition of the selected whole disk is also source media.
        if not same_device:
            source_sys = Path(f"/sys/dev/block/{os.major(device_id)}:{os.minor(device_id)}")
            output_sys = Path(f"/sys/dev/block/{os.major(output_device)}:{os.minor(output_device)}")
            if source_sys.exists() and output_sys.exists():
                same_device = source_sys.resolve() in output_sys.resolve().parents
        if same_device:
            raise ValueError("Choose an output folder on a different device from the source floppy.")


def prepare_album(album, output_parent, *, cd_device="", audio_paths=None,
                  cancel=None, progress=None):
    """Rip, fingerprint-match and encode an album, retaining truthful completion status.

    Explicit per-song audio choices are verified too. Catalog order is only a
    search hint; a failed fingerprint match never becomes an assumed pairing.
    An interrupted output remains available for recovery with an incomplete
    manifest. Only a successful return represents a completed album.
    """
    from .disklavier_audio import encode_disklavier_wav
    from .smart_pianosoft_media import read_cd_toc, rip_cd_track
    from .smart_pianosoft_sync import synchronize

    _check_cancel(cancel)
    output_parent = Path(output_parent).expanduser().resolve()
    original = album.original_source.resolve()
    _guard_output_source(original, output_parent, source_device=album.source_device)
    audio_paths = {int(k): Path(v) for k, v in (audio_paths or {}).items()}
    known_numbers = {track.number for track in album.tracks}
    if set(audio_paths) - known_numbers:
        raise ValueError("An audio pairing refers to an unknown MIDI track.")
    if not cd_device and set(audio_paths) != known_numbers:
        raise ValueError("Insert the matching audio CD or pair every MIDI with a WAV file.")
    if not output_parent.is_dir():
        raise ValueError("Select an existing output folder.")
    # A unique directory also makes concurrent runs and retries non-destructive.
    folder = Path(tempfile.mkdtemp(prefix=f"Smart PianoSoft - {_safe_title(album.title)} - ", dir=output_parent))
    report = {"schema": 1, "album": album.title, "status": "incomplete", "beta": True,
              "hardware_verified": False, "format": "PianoSoft PlusAudio: left music, right piano data",
              "source": str(album.original_source), "tracks": [], "audio": []}
    try:
        midi_folder, wav_folder, encoded_folder = [folder / name for name in ("MIDI", "WAV", "Disklavier")]
        for child in (midi_folder, wav_folder, encoded_folder):
            child.mkdir()
        _save_report(folder, report)
        for path in album.source_directory.iterdir():
            if path.name.casefold() in {"psong.mng", "pdisk.mng"}:
                shutil.copyfile(path, midi_folder / path.name)
        for track in album.tracks:
            _check_cancel(cancel)
            if _digest(track.midi_path) != track.midi_sha256:
                raise ValueError(f"The MIDI source changed after scanning: {track.filename}")
            target = midi_folder / track.filename
            with track.midi_path.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if _digest(target) != track.midi_sha256:
                raise ValueError(f"MIDI copy verification failed: {track.filename}")

        audio_candidates, explicit = {}, {}
        toc = read_cd_toc(cd_device, cancel=cancel) if cd_device else ()
        required_bytes = sum(t.sectors * 2352 for t in toc) * 2
        required_bytes += sum(p.stat().st_size for p in audio_paths.values()) * 2
        if shutil.disk_usage(folder).free < required_bytes + 64 * 1024 * 1024:
            raise ValueError("There is not enough free space for the original and encoded WAV files.")
        for index, track in enumerate(toc):
            _check_cancel(cancel)
            destination = wav_folder / f"CD{track.number:02d}.wav"
            _emit(progress, "rip", index, len(toc), f"CD track {track.number}")
            rip_cd_track(cd_device, track, destination, cancel=cancel,
                         progress=lambda step, total, message: _emit(progress, "rip", step, total, message))
            audio_candidates[track.number] = destination
            report["audio"].append({"cd_track": track.number, "file": f"WAV/{destination.name}",
                                    "sha256": _digest(destination), "sectors": track.sectors})
            _save_report(folder, report)
        if toc and tuple(read_cd_toc(cd_device, cancel=cancel)) != tuple(toc):
            raise ValueError("The audio CD changed while it was being read.")
        for number, source in audio_paths.items():
            _check_cancel(cancel)
            destination = wav_folder / f"Paired{number:02d}.wav"
            _copy_wav(source, destination, cancel=cancel)
            explicit[number] = destination
            report["audio"].append({"paired_song": number, "file": f"WAV/{destination.name}",
                                    "sha256": _digest(destination)})

        audio_hashes = {folder / entry["file"]: entry["sha256"] for entry in report["audio"]}
        audio_identities = {path: _identity(path) for path in audio_hashes}

        def match_audio(metadata, path):
            if _identity(path) != audio_identities[path]:
                raise RuntimeError(f"Audio changed before synchronization: {path.name}")
            try:
                return synchronize(metadata, path, cancel=cancel)
            finally:
                if _identity(path) != audio_identities[path]:
                    raise RuntimeError(f"Audio changed during synchronization: {path.name}")

        pairs, used = [], set()
        for index, track in enumerate(album.tracks):
            _check_cancel(cancel)
            _emit(progress, "sync", index, len(album.tracks), track.title or track.filename)
            if track.number in explicit:
                audio = explicit[track.number]
                alignment = match_audio(track.metadata, audio)
            else:
                hint = audio_candidates.get(track.number)
                candidates = ([hint] if hint is not None else []) + [
                    candidate for candidate in audio_candidates.values() if candidate != hint
                ]
                matches = []
                for candidate in candidates:
                    _check_cancel(cancel)
                    try:
                        result = match_audio(track.metadata, candidate)
                        matches.append((candidate, result))
                    except ValueError:
                        continue
                if len(matches) != 1 or matches[0][0] in used:
                    raise ValueError(f"{track.title or track.filename}: no unique matching CD track. Pair the correct WAV file and retry.")
                audio, alignment = matches[0]
                used.add(audio)
            pairs.append((track, audio, alignment))
            report["tracks"].append({"number": track.number, "title": track.title,
                "midi": f"MIDI/{track.filename}", "midi_sha256": track.midi_sha256,
                "audio": f"WAV/{audio.name}", "alignment": asdict(alignment)})
            _save_report(folder, report)

        # All pairings must be verified before any merged file is produced.
        for index, (track, audio, alignment) in enumerate(pairs):
            _check_cancel(cancel)
            if _digest(audio) != audio_hashes[audio]:
                raise ValueError(f"Audio changed after synchronization: {audio.name}")
            output = encoded_folder / f"{track.number:02d} - {_safe_title(track.title)}.wav"
            events = [(event.time_seconds, event.data) for event in track.metadata.events]
            result = encode_disklavier_wav(
                events, audio, output, midi_offset_seconds=alignment.offset_seconds,
                midi_time_scale=alignment.time_scale, cancel=cancel,
                progress=lambda fraction, title=track.title: _emit(progress, "encode", fraction, 1, title),
            )
            if _digest(audio) != audio_hashes[audio]:
                output.unlink(missing_ok=True)
                raise ValueError(f"Audio changed during encoding: {audio.name}")
            report["tracks"][index].update({"encoded": f"Disklavier/{output.name}",
                                           "encoded_sha256": _digest(output), "encoding": result})
            _save_report(folder, report)
        _check_cancel(cancel)
        for track in album.tracks:
            if _digest(midi_folder / track.filename) != track.midi_sha256:
                raise ValueError(f"The preserved MIDI copy changed: {track.filename}")
        for audio, expected in audio_hashes.items():
            if _digest(audio) != expected:
                raise ValueError(f"The preserved audio copy changed: {audio.name}")
        report["status"] = "complete"
        _save_report(folder, report)
        _emit(progress, "complete", len(pairs), len(pairs), str(folder))
        return folder
    except Exception as exc:
        report["status"] = "cancelled" if cancel is not None and cancel.is_set() else "failed"
        report["error"] = str(exc)
        try:
            _save_report(folder, report)
        except OSError:
            pass
        raise AlbumBuildError(str(exc), folder) from exc
