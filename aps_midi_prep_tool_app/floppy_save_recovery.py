"""Persistent, checksummed recovery packages for physical floppy file saves."""

import datetime
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .rename_recovery import sync_directory, sync_file, write_manifest


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


class SaveRecoveryPackage:
    def __init__(self, drive, prepared_image):
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
        shutil.copyfile(prepared_image, self.directory / "prepared.img")
        sync_file(self.directory / "prepared.img")
        self.manifest["prepared_sha256"] = digest(self.directory / "prepared.img")
        self.checkpoint()

    def checkpoint(self, **fields):
        self.manifest.update(fields)
        write_manifest(self.directory, self.manifest)

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
