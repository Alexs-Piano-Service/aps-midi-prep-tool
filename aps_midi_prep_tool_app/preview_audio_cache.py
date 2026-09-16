"""Disposable, bounded cache of completed audio previews."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time


_CACHE_LOCK = threading.RLock()


def file_identity(path):
    """Detect replaced SoundFonts/renderers without rereading large sample banks."""
    if not path:
        return None
    path = os.path.normcase(os.path.abspath(path))
    try:
        stat = os.stat(path)
        return [path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except OSError:
        return [path, None]


def preview_cache_key(midi_bytes, notes, duration, renderer_identity):
    digest = hashlib.sha256(bytes(midi_bytes))
    digest.update(json.dumps(
        [1, notes, duration, renderer_identity],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    return digest.hexdigest()


class PreviewAudioCache:
    def __init__(self, directory, *, max_bytes=512 * 1024 * 1024, max_entries=32):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.max_entries = max_entries

    @staticmethod
    def _valid_wav(path):
        with open(path, "rb") as handle:
            header = handle.read(12)
        return (
            len(header) == 12 and header[:4] == b"RIFF"
            and header[8:] == b"WAVE"
            and os.path.getsize(path) == int.from_bytes(header[4:8], "little") + 8
        )

    @staticmethod
    def _copy_atomically(source, destination):
        handle, staging = tempfile.mkstemp(prefix=".preview-", dir=destination.parent)
        os.close(handle)
        try:
            # A separate hard link keeps a playing file alive after cache eviction.
            os.unlink(staging)
            try:
                os.link(source, staging)
            except OSError:
                shutil.copyfile(source, staging)
            os.replace(staging, destination)
        finally:
            try:
                os.unlink(staging)
            except OSError:
                pass

    def restore(self, key, output_path):
        """Return engine label on hit; a missing/broken/unwritable cache is a miss."""
        try:
            metadata_path = self.directory / (key + ".json")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            engine_label = metadata["engine"]
            if not isinstance(engine_label, str) or not engine_label:
                return None
            source = self.directory / (key + ".wav")
            if not self._valid_wav(source) or source.stat().st_size != metadata["size"]:
                return None
            self._copy_atomically(source, Path(output_path))
            os.utime(metadata_path, None)
            return engine_label
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def store(self, key, source, engine_label):
        with _CACHE_LOCK:
            self._store(key, source, engine_label)

    def _store(self, key, source, engine_label):
        try:
            size = os.path.getsize(source)
            if size > self.max_bytes or not self._valid_wav(source):
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            self._copy_atomically(source, self.directory / (key + ".wav"))
            metadata = json.dumps({"size": size, "engine": engine_label})
            handle, staging = tempfile.mkstemp(prefix=".preview-", dir=self.directory)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as output:
                    output.write(metadata)
                os.replace(staging, self.directory / (key + ".json"))
            finally:
                try:
                    os.unlink(staging)
                except OSError:
                    pass
            self._prune()
        except (OSError, ValueError):
            # Playback must still work when the cache is full or unavailable.
            return

    def _prune(self):
        entries = []
        for audio in self.directory.glob("*.wav"):
            metadata = audio.with_suffix(".json")
            try:
                stat = audio.stat()
                try:
                    last_used = metadata.stat().st_mtime_ns
                except FileNotFoundError:
                    # Leave another app instance time to publish its metadata.
                    if time.time() - stat.st_mtime < 3600:
                        continue
                    audio.unlink()
                    continue
                entries.append((last_used, stat.st_size, audio, metadata))
            except OSError:
                continue
        entries.sort(reverse=True)
        total = 0
        for index, (_used, size, audio, metadata) in enumerate(entries):
            total += size
            if index >= self.max_entries or total > self.max_bytes:
                for path in (metadata, audio):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        for staging in self.directory.glob(".preview-*"):
            try:
                if time.time() - staging.stat().st_mtime > 3600:
                    staging.unlink()
            except OSError:
                pass
