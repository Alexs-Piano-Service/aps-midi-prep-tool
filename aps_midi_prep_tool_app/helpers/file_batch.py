"""Publish prepared files together, restoring earlier writes after a failure."""

import json
import os
import shutil
import stat
import tempfile

from .atomic_file import atomic_write_bytes


class FileBatchWriteError(OSError):
    """A batch failed; recovery files remain only when rollback was incomplete."""

    def __init__(
        self, message, *, rollback_errors=None, recovery_directory="",
        original_exception=None,
    ):
        super().__init__(message)
        self.rollback_errors = list(rollback_errors or ())
        self.recovery_directory = str(recovery_directory or "")
        self.original_exception = original_exception


def _write_recovery_manifest(directory, records, rollback_errors=()):
    manifest = {
        "version": 1,
        "files": [
            {
                "destination": record["destination"],
                "resolved_destination": record["resolved_destination"],
                "existed": record["existed"],
                "original_file": record.get("original_file"),
                "prepared_file": record.get("prepared_file"),
                "original_mode": record.get("original_mode"),
                "published": record.get("published", False),
                "restored": record.get("restored", False),
            }
            for record in records
        ],
        "rollback_errors": list(rollback_errors),
    }
    # The initial manifest is complete before publishing any output. Replace
    # it only after writing the updated recovery state successfully.
    temporary = os.path.join(directory, "manifest.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, os.path.join(directory, "manifest.json"))


def publish_file_batch(prepared_files, *, backup_callback=None, progress_callback=None):
    """Publish (staged_path, destination) pairs, or undo earlier writes.

    Staged files must already contain the complete output and remain available
    until this call returns. Existing destinations are copied to private recovery
    storage before backups or publication. The backup callback runs for every
    existing target and may return an error string or raise. Progress receives
    a one-based completed count, total, and the caller's destination.

    Destination symlinks keep their existing meaning. New targets are created
    exclusively so that a file appearing during publication is never replaced.
    Optional persistent backups are retained even when publication rolls back.
    """
    records = []
    published = []
    recovery_directory = ""
    retain_recovery = False
    try:
        destination_keys = set()
        for staged_path, destination in prepared_files:
            destination = os.fsdecode(destination)
            resolved = os.path.realpath(destination)
            key = os.path.normcase(resolved)
            if key in destination_keys:
                raise ValueError(f"Duplicate output destination: {destination}")
            destination_keys.add(key)
            records.append({
                "staged_path": os.fsdecode(staged_path),
                "destination": destination,
                "resolved_destination": resolved,
                "existed": False,
            })
        if not records:
            return

        recovery_directory = tempfile.mkdtemp(prefix="aps_file_batch_")
        for index, record in enumerate(records):
            # Freeze prepared input too: callers may prepare a rename swap
            # directly from files that are also destinations in this batch.
            record["prepared_file"] = f"prepared-{index:04d}.bin"
            shutil.copyfile(
                record["staged_path"],
                os.path.join(recovery_directory, record["prepared_file"]),
            )
            destination = record["resolved_destination"]
            try:
                destination_stat = os.stat(destination)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(destination_stat.st_mode):
                raise ValueError(f"Destination is not a regular file: {record['destination']}")
            record["existed"] = True
            record["original_mode"] = stat.S_IMODE(destination_stat.st_mode)
            record["original_file"] = f"original-{index:04d}.bin"
            # Keep the snapshot writable for cleanup on Windows even when
            # the destination itself has the read-only flag.
            shutil.copyfile(destination, os.path.join(recovery_directory, record["original_file"]))
        _write_recovery_manifest(recovery_directory, records)

        if backup_callback is not None:
            for record in records:
                if record["existed"]:
                    error = backup_callback(record["destination"])
                    if error:
                        raise OSError(str(error))

        for index, record in enumerate(records, start=1):
            with open(os.path.join(recovery_directory, record["prepared_file"]), "rb") as handle:
                payload = handle.read()
            atomic_write_bytes(
                record["resolved_destination"], payload,
                replace_existing=record["existed"],
            )
            record["published"] = True
            published.append(record)
            if progress_callback is not None:
                progress_callback(index, len(records), record["destination"])
    except BaseException as exc:
        rollback_errors = []
        for record in reversed(published):
            destination = record["resolved_destination"]
            try:
                if record["existed"]:
                    snapshot = os.path.join(recovery_directory, record["original_file"])
                    with open(snapshot, "rb") as handle:
                        atomic_write_bytes(destination, handle.read())
                else:
                    os.unlink(destination)
                record["restored"] = True
            except BaseException as rollback_error:
                rollback_errors.append(f"{record['destination']}: {rollback_error}")
        if rollback_errors:
            retain_recovery = True
            try:
                _write_recovery_manifest(recovery_directory, records, rollback_errors)
            except BaseException as manifest_error:
                rollback_errors.append(f"Could not update recovery manifest: {manifest_error}")
        raise FileBatchWriteError(
            str(exc), rollback_errors=rollback_errors,
            recovery_directory=recovery_directory if retain_recovery else "",
            original_exception=exc,
        ) from exc
    finally:
        if recovery_directory and not retain_recovery:
            shutil.rmtree(recovery_directory, ignore_errors=True)
