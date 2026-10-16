"""Durable rename journals and process locks; no dependency on Qt or system TEMP."""

import contextlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

# Kept separate from the MIDI move primitive, so a failed move cannot also corrupt
# the journal describing it. Tests fault these operations independently.
_atomic_replace = os.replace

# Local Windows scanners and open handles can briefly deny replacement. Error 5
# is ambiguous (including permanent ACL/read-only failures), so retries never
# relax access checks or remove the old manifest. Only a successful replacement
# establishes that the failure was transient. Keep the total wait to one second.
_MANIFEST_REPLACE_RETRIES = 20
_MANIFEST_REPLACE_RETRY_DELAY = 0.05
_MANIFEST_REPLACE_RETRY_ERRORS = {5, 32, 33}


def recovery_root():
    override = os.environ.get("APS_MIDI_RENAME_RECOVERY_DIR")
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "APS MIDI Prep Tool" / "rename-recovery"


def sync_directory(path):
    # Windows does not expose directory fsync through os.open. File contents and
    # journals are still flushed with os.fsync; durability also depends on the FS.
    if os.name == "nt":
        return
    import errno
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise


def sync_file(path):
    # Windows FlushFileBuffers (used by _commit) needs a writable handle.
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def write_manifest(directory, manifest, *, check_cancel=None):
    """Flush and atomically publish a journal; a failed checkpoint stays fatal.

    ``check_cancel`` may raise the caller's cancellation exception while waiting
    for Windows replacement retries. The old journal and flushed staged journal
    remain available when publication fails or the wait is cancelled.
    """
    directory = Path(directory)
    staged = directory / "manifest.json.new"
    with open(staged, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(_MANIFEST_REPLACE_RETRIES + 1):
        try:
            _atomic_replace(staged, directory / "manifest.json")
            break
        except OSError as exc:
            if (getattr(exc, "winerror", None) not in _MANIFEST_REPLACE_RETRY_ERRORS
                    or attempt == _MANIFEST_REPLACE_RETRIES):
                raise
            if check_cancel is not None:
                check_cancel()
            time.sleep(_MANIFEST_REPLACE_RETRY_DELAY)
            if check_cancel is not None:
                check_cancel()
    sync_directory(directory)


@contextlib.contextmanager
def recovery_lock(root):
    root = Path(root)
    missing = []
    parent = root
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    root.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        sync_directory(created.parent)
    with open(root / ".lock", "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another APS rename or recovery is running. Try again when it finishes.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def preflight_space(root, moving):
    # Include a journal allowance and allocation overhead for many tiny files.
    required = sum(os.path.getsize(source) + 4096 for source, _ in moving) + 64 * 1024
    free = shutil.disk_usage(root).free
    if free < required:
        needed_mb = max(1, math.ceil(required / (1024 * 1024)))
        available_mb = max(0, free // (1024 * 1024))
        raise OSError(
            f"Rename needs approximately {needed_mb} MB of temporary recovery space in {root}; "
            f"only {available_mb} MB is available. Free space or choose another persistent "
            "recovery location with APS_MIDI_RENAME_RECOVERY_DIR. No files were renamed."
        )


def new_transaction_directory(root):
    directory = tempfile.mkdtemp(prefix="aps_midi_rename_recovery_", dir=root)
    sync_directory(root)
    return directory
