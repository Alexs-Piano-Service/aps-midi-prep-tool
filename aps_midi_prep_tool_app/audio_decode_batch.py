"""Decode selected Yamaha audio files into a separate ENSPIRE playback tree."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unicodedata

from . import audio_tools, disklavier_audio_decode, paired_audio
from .helpers.atomic_file import atomic_write_bytes
from .helpers.output_names import create_output_directory


REPORT_NAME = "decode-report.json"
_AUDIO_SUFFIXES = {".wav", ".mp3"}


@dataclass(frozen=True)
class BatchDecodeRecord:
    source: Path
    status: str
    midi_path: Path | None = None
    mp3_path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class BatchDecodeResult:
    output_directory: Path | None
    records: tuple[BatchDecodeRecord, ...]
    cancelled: bool = False

    @property
    def converted(self):
        return sum(record.status == "converted" for record in self.records)

    @property
    def skipped(self):
        return sum(record.status == "skipped" for record in self.records)

    @property
    def failed(self):
        return sum(record.status == "failed" for record in self.records)


@dataclass(frozen=True)
class _Candidate:
    source: Path
    root: Path | None
    identity: tuple


def _check_cancel(cancel):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise InterruptedError("Audio decoding cancelled.")


def _key(path):
    return unicodedata.normalize("NFC", str(path)).casefold()


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _has_link(path):
    # An explicitly selected child of a linked directory must not bypass the
    # recursive scanner's no-links rule. Junctions receive the same treatment.
    return any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
               for part in (path, *path.parents))


def _scan(sources, records, cancel, emit):
    roots = []
    seen = set()
    for value in sources:
        _check_cancel(cancel)
        path = Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        emit("scanning", path)
        try:
            if _has_link(path):
                records.append(BatchDecodeRecord(path, "skipped", error="Symbolic links are not followed."))
                continue
            info = path.stat()
            if stat.S_ISDIR(info.st_mode):
                roots.append((path, True))
            elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in _AUDIO_SUFFIXES:
                roots.append((path, False))
            else:
                records.append(BatchDecodeRecord(path, "skipped", error="Select WAV or MP3 files, or directories."))
        except InterruptedError:
            raise
        except OSError as exc:
            records.append(BatchDecodeRecord(path, "failed", error=str(exc)))

    # Prefer the enclosing directory regardless of selection order, so an
    # explicitly added song never flattens its album's relative hierarchy.
    directories = [path for path, is_directory in roots if is_directory]
    roots = [(path, is_directory) for path, is_directory in roots
             if not any(parent != path and path.is_relative_to(parent) for parent in directories)]
    candidates = []

    def visit(path, root):
        _check_cancel(cancel)
        emit("scanning", path)
        try:
            if _has_link(path):
                if path.suffix.lower() in _AUDIO_SUFFIXES:
                    records.append(BatchDecodeRecord(path, "skipped", error="Symbolic links are not followed."))
                return
            info = path.stat()
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    children = sorted((Path(entry.path) for entry in entries), key=lambda item: (_key(item.name), item.name))
                for child in children:
                    visit(child, root)
            elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in _AUDIO_SUFFIXES:
                candidates.append(_Candidate(path, root, _identity(info)))
        except InterruptedError:
            raise
        except OSError as exc:
            records.append(BatchDecodeRecord(path, "failed", error=str(exc)))

    for path, is_directory in sorted(roots, key=lambda item: (_key(item[0]), str(item[0]))):
        visit(path, path if is_directory else None)
    return candidates


def _output_stems(candidates):
    """Reserve whole paths, including directories, for case-insensitive disks."""
    used = {_key(REPORT_NAME)}
    directories = {}
    stems = []

    def directory(source, parent, name):
        if source in directories:
            return directories[source]
        number, candidate = 1, parent / name
        while _key(candidate) in used:
            number += 1
            candidate = parent / f"{name} ({number})"
        used.add(_key(candidate))
        directories[source] = candidate
        return candidate

    for item in candidates:
        parent = Path()
        if item.root is not None:
            parent = directory(item.root, parent, item.root.name or "Audio")
            original_parent = item.root
            for component in item.source.relative_to(item.root).parts[:-1]:
                original_parent /= component
                parent = directory(original_parent, parent, component)
        name, number = item.source.stem, 1
        stem = parent / name
        while any(_key(str(stem) + suffix) in used for suffix in (".mid", ".mp3")):
            number += 1
            stem = parent / f"{name} ({number})"
        used.update(_key(str(stem) + suffix) for suffix in (".mid", ".mp3"))
        stems.append(stem)
    return stems


def _error_details(exc):
    details = str(exc) or type(exc).__name__
    recovery = getattr(exc, "recovery_directory", "")
    retained = tuple(str(path) for path in getattr(exc, "retained_paths", ()))
    if recovery and str(recovery) not in details:
        details += f"\nPrepared recovery files: {recovery}"
    if retained:
        details += "\nRetained rollback files: " + ", ".join(retained)
    return details


def _write_report(result, total, *, finished):
    document = {
        "schema_version": 1,
        "output_directory": str(result.output_directory),
        "finished": finished,
        "cancelled": result.cancelled,
        "total_audio_files": total,
        "converted": result.converted,
        "skipped": result.skipped,
        "failed": result.failed,
        "files": [{"source": str(record.source), "status": record.status,
                   "midi_path": str(record.midi_path) if record.midi_path else None,
                   "mp3_path": str(record.mp3_path) if record.mp3_path else None,
                   "error": record.error} for record in result.records],
    }
    try:
        atomic_write_bytes(result.output_directory / REPORT_NAME,
                           (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError as exc:
        failures = "\n".join(f"{record.source}: {record.error}" for record in result.records
                             if record.status == "failed" and record.error)
        message = (f"Could not save the decoding report in {result.output_directory}. "
                   f"Completed pairs are kept in that output folder. {_error_details(exc)}")
        if failures:
            # Publication recovery folders can live outside the output tree.
            # If the report cannot be written, preserve their only remaining
            # user-visible locations in the error delivered to the caller.
            message += "\nFile errors and recovery locations:\n" + failures
        raise ValueError(message) from exc


def run_audio_decode_batch(sources, output_parent, *, random_suffixes=True,
                           cancel=None, progress=None):
    """Inspect files or recursive trees and export each validated control stream.

    Sources are read only. An enclosing selected directory takes precedence
    over individual selections within it, and links are never traversed. Scan
    completion and the LAME preflight precede any output-directory creation.
    Every successful pair has its own collision-safe shared basename. Ordinary
    audio is skipped; failed files are reported without stopping the batch.
    Cancellation retains all completed pairs and any publication recovery data.
    """
    sources = tuple(sources)
    if not sources:
        raise ValueError("Select at least one audio file or directory.")
    if not str(output_parent).strip():
        raise ValueError("Choose an output directory before starting audio decoding.")
    records = []
    total = completed = 0
    last_progress = 0.0
    last_stage = None

    def emit(phase, source, portion=0.0):
        nonlocal last_progress, last_stage
        if progress is not None:
            now = time.monotonic()
            stage = (phase, completed)
            if stage == last_stage and now - last_progress < 0.05:
                return
            last_progress, last_stage = now, stage
            progress({"phase": phase, "source": str(source), "completed": completed,
                      "total": total, "fraction": min(1.0, (completed + portion) / total) if total else 0.0})

    try:
        candidates = _scan(sources, records, cancel, emit)
        _check_cancel(cancel)
    except InterruptedError:
        return BatchDecodeResult(None, tuple(records), cancelled=True)
    if not candidates:
        return BatchDecodeResult(None, tuple(records))
    if not audio_tools.find_lame_command():
        raise ValueError("LAME is required to create paired MIDI/MP3 files. Install LAME before starting audio decoding.")
    target = Path(output_parent).expanduser().absolute()
    if target.exists() and not target.is_dir():
        raise ValueError("The output location must be a directory.")
    try:
        _check_cancel(cancel)
    except InterruptedError:
        return BatchDecodeResult(None, tuple(records), cancelled=True)
    target.mkdir(parents=True, exist_ok=True)
    folder = create_output_directory(target, "ENSPIRE-MIDI-MP3-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
                                     random_suffixes=random_suffixes)
    total = len(candidates)
    cancelled = False
    _write_report(BatchDecodeResult(folder, tuple(records)), total, finished=False)
    for item, relative_stem in zip(candidates, _output_stems(candidates)):
        try:
            _check_cancel(cancel)
            emit("decoding", item.source)
            if _has_link(item.source) or _identity(item.source.stat()) != item.identity:
                raise ValueError("The source changed after scanning; it was not decoded.")
            with tempfile.TemporaryDirectory(prefix=".decode-", dir=folder) as temporary:
                music = Path(temporary) / "music.wav"
                decoded = disklavier_audio_decode.decode_disklavier_audio(
                    item.source, music, cancel=cancel,
                    progress=lambda fraction: emit("decoding", item.source, 0.7 * fraction))
                _check_cancel(cancel)
                if _has_link(item.source) or _identity(item.source.stat()) != item.identity:
                    raise ValueError("The source changed during decoding; no pair was exported.")
                stem = folder / relative_stem
                stem.parent.mkdir(parents=True, exist_ok=True)
                paired_audio.export_midi_mp3(
                    decoded.events, music, stem, cancel=cancel,
                    progress=lambda fraction: emit("exporting", item.source, 0.7 + 0.3 * fraction))
                # Record a published pair before observing a late cancellation.
                records.append(BatchDecodeRecord(item.source, "converted",
                                                 Path(str(stem) + ".mid"), Path(str(stem) + ".mp3")))
        except disklavier_audio_decode.NotEncodedAudioError as exc:
            records.append(BatchDecodeRecord(item.source, "skipped", error=str(exc)))
        except Exception as exc:
            interrupted = isinstance(exc, InterruptedError) or isinstance(getattr(exc, "original_exception", None), InterruptedError)
            recovery = getattr(exc, "recovery_directory", "") or getattr(exc, "retained_paths", ())
            if not interrupted or recovery or getattr(exc, "rollback_errors", ()):
                records.append(BatchDecodeRecord(item.source, "failed", error=_error_details(exc)))
            if interrupted:
                cancelled = True
                break
        completed += 1
        emit("decoding", item.source)
        _write_report(BatchDecodeResult(folder, tuple(records)), total, finished=False)
    result = BatchDecodeResult(folder, tuple(records), cancelled)
    _write_report(result, total, finished=True)
    return result
