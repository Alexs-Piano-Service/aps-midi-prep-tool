"""Persistent, checksummed recovery packages for physical floppy file saves."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .rename_recovery import sync_directory, sync_file, write_manifest

COMPLETED_PACKAGE_LIMIT = 5
COMPLETED_PACKAGE_MAX_AGE = datetime.timedelta(days=30)


def recovery_root():
    override = os.environ.get("APS_FLOPPY_SAVE_RECOVERY_DIR")
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "APS MIDI Prep Tool" / "floppy-save-recovery"


def digest(path):
    checksum = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def prune_completed_packages():
    """Best effort maintenance; ambiguous and unfinished packages are never removed."""
    root = recovery_root()
    now = datetime.datetime.now(datetime.timezone.utc)
    completed = []
    try:
        directories = list(root.iterdir())
    except OSError:
        return
    for directory in directories:
        try:
            if not directory.name.startswith("save-") or directory.is_symlink() or not directory.is_dir():
                continue
            manifest_path = directory / "manifest.json"
            if manifest_path.is_symlink():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("status") != "complete":
                continue
            # Older packages did not record a completion time.
            timestamp = datetime.datetime.fromisoformat(manifest.get("completed_at") or manifest["created_at"])
            if timestamp.tzinfo is None or timestamp > now:
                continue
            completed.append((timestamp, directory))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    completed.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    for index, (timestamp, directory) in enumerate(completed):
        if index < COMPLETED_PACKAGE_LIMIT and now - timestamp <= COMPLETED_PACKAGE_MAX_AGE:
            continue
        try:
            # Keep the completion marker until all payloads are gone, so a
            # locked binary does not strand an unrecognizable partial package.
            for payload in directory.iterdir():
                if payload.name == "manifest.json":
                    continue
                if payload.is_dir() and not payload.is_symlink():
                    shutil.rmtree(payload)
                else:
                    payload.unlink()
            (directory / "manifest.json").unlink()
            directory.rmdir()
        except OSError:
            # A locked or inaccessible package must not fail startup or saving.
            continue


class SaveRecoveryPackage:
    def __init__(self, drive, prepared_image=None):
        prune_completed_packages()
        root = recovery_root()
        root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="save-", dir=root))
        sync_directory(root)
        self.manifest = {
            "schema": 1, "drive": str(drive), "status": "preparing",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "originals": {}, "replacements": {}, "actions": [],
        }
        self.checkpoint()
        if prepared_image is not None:
            shutil.copyfile(prepared_image, self.directory / "prepared.img")
            sync_file(self.directory / "prepared.img")
            self.manifest["prepared_sha256"] = digest(self.directory / "prepared.img")
        self.checkpoint()

    def checkpoint(self, **fields):
        self.manifest.update(fields)
        write_manifest(self.directory, self.manifest)

    def complete(self, diagnostics):
        self.checkpoint(status="complete", diagnostics=dict(diagnostics),
                        completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        prune_completed_packages()

    def retain(self, category, name, source):
        index = len(self.manifest[category])
        destination = self.directory / f"{category}-{index:04d}.bin"
        shutil.copyfile(source, destination)
        sync_file(destination)
        record = {"file": destination.name, "size": destination.stat().st_size,
                  "sha256": digest(destination)}
        self.manifest[category][name] = record
        self.checkpoint()
        return str(destination)

    def before(self, operation, name):
        self.manifest["actions"].append({"operation": operation, "file": name, "status": "started"})
        self.checkpoint(status="writing")

    def after(self):
        self.manifest["actions"][-1]["status"] = "complete"
        self.checkpoint()
