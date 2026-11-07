"""Publish prepared files together, restoring earlier writes after a failure."""

import json
import hashlib
import os
import shutil
import stat
import tempfile

from .atomic_file import FilePublicationCleanupError, atomic_write_bytes


class FileBatchWriteError(OSError):
    """A batch failed, with locations of any preserved rollback files."""

    def __init__(
        self, message, *, rollback_errors=None, recovery_directory="",
        original_exception=None, retained_paths=(),
    ):
        super().__init__(message)
        self.rollback_errors = list(rollback_errors or ())
        self.recovery_directory = str(recovery_directory or "")
        self.original_exception = original_exception
        self.retained_paths = tuple(retained_paths)


def _write_recovery_manifest(directory, records, rollback_errors=()):
    def publication_details(record):
        receipt = record.get("publication")
        if receipt is None:
            return None
        return {
            "destination": receipt.destination,
            "identity": receipt.identity,
            "sha256": receipt.sha256.hex(),
            "parent_identity": receipt.parent_identity,
            "ctime_ns": receipt.ctime_ns,
        }

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
                "publication": publication_details(record),
                "restored": record.get("restored", False),
                "retained_paths": record.get("retained_paths", []),
                "quarantine_directory": record.get("quarantine_directory"),
                "operation": record.get("operation", "write"),
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


def _checked_file_hash(path):
    """Hash a stable regular file without accepting a final-component symlink."""
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Destination is not a regular file: {path}")
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        opened = os.fstat(handle.fileno())
        # Windows path and handle queries can report different ctime values
        # for the same unchanged file. Match the file and its contents across
        # queries, then keep the complete timestamp checks within each query
        # type so a change during hashing still fails.
        if (not stat.S_ISREG(opened.st_mode) or
                identity(opened)[:-1] != identity(before)[:-1]):
            raise OSError(f"Destination changed while being checked: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        if identity(os.fstat(handle.fileno())) != identity(opened):
            raise OSError(f"Destination changed while being checked: {path}")
    after = os.lstat(path)
    if not stat.S_ISREG(after.st_mode) or identity(after) != identity(before):
        raise OSError(f"Destination changed while being checked: {path}")
    return digest.hexdigest(), identity(before)


def publish_file_batch(prepared_files, *, backup_callback=None, progress_callback=None,
                       delete_files=(), expected_hashes=None):
    """Publish (staged_path, destination) pairs, or undo earlier writes.

    Staged files must already contain the complete output and remain available
    until this call returns. Existing destinations are copied to private recovery
    storage before backups or publication. The backup callback runs for every
    existing target and may return an error string or raise. Progress receives
    a one-based completed count, total, and the caller's destination.

    Destination symlinks keep their existing meaning. New targets are created
    exclusively so that a file appearing during publication is never replaced.
    Optional persistent backups are retained even when publication rolls back.
    ``delete_files`` are retired in the same transaction, after writes. Each
    requires a SHA-256 entry in ``expected_hashes``. Optional expectations for
    writes protect approval-time contents too; None requires an absent target.
    Checked destinations must be regular files, never symlinks.
    Rollback moves writes to private quarantine before checking their identity
    and contents. Captured inodes are retained even on success to preserve
    writes through existing handles. Originals are restored exclusively;
    conflicts keep both the competing file and recovery data.
    """
    records = []
    published = []
    recovery_directory = ""
    retain_recovery = False
    try:
        if expected_hashes is not None:
            expected_hashes = {os.fsdecode(path): digest for path, digest in expected_hashes.items()}
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
                "operation": "write",
            })
        for destination in delete_files:
            destination = os.fsdecode(destination)
            resolved = os.path.realpath(destination)
            key = os.path.normcase(resolved)
            if key in destination_keys:
                raise ValueError(f"Duplicate output destination: {destination}")
            destination_keys.add(key)
            if expected_hashes is None or not expected_hashes.get(destination):
                raise ValueError(f"Deletion requires an expected file hash: {destination}")
            records.append({"destination": destination, "resolved_destination": resolved,
                            "existed": False, "operation": "delete"})
        if not records:
            return

        def check_expected(record):
            destination = record["destination"]
            if expected_hashes is None or destination not in expected_hashes:
                return
            expected = expected_hashes[destination]
            if expected is None:
                if os.path.lexists(destination):
                    raise FileExistsError(f"Output appeared after approval: {destination}")
                return
            actual, identity = _checked_file_hash(destination)
            if actual != expected or ("checked_identity" in record and
                                      record["checked_identity"] != identity):
                raise OSError(f"Output changed after approval: {destination}")
            record["checked_identity"] = identity

        recovery_directory = tempfile.mkdtemp(prefix="aps_file_batch_")
        for index, record in enumerate(records):
            check_expected(record)
            # Freeze prepared input too: callers may prepare a rename swap
            # directly from files that are also destinations in this batch.
            if record["operation"] == "write":
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
            check_expected(record)
        _write_recovery_manifest(recovery_directory, records)

        if backup_callback is not None:
            for record in records:
                if record["existed"]:
                    error = backup_callback(record["destination"])
                    if error:
                        raise OSError(str(error))

        for index, record in enumerate(records, start=1):
            check_expected(record)
            if record["operation"] == "delete":
                os.unlink(record["destination"])
                record["published"] = True
                published.append(record)
            else:
                with open(os.path.join(recovery_directory, record["prepared_file"]), "rb") as handle:
                    payload = handle.read()

                def remember_publication(receipt):
                    record["publication"] = receipt
                    record["published"] = True
                    published.append(record)

                atomic_write_bytes(
                    record["resolved_destination"], payload,
                    replace_existing=record["existed"],
                    published_callback=remember_publication,
                )
            if progress_callback is not None:
                progress_callback(index, len(records), record["destination"])
    except BaseException as exc:
        # An exclusive copy may fail before it yields a publication receipt.
        # If its own cleanup cannot be verified, keep the recovery package too.
        rollback_errors = [str(exc)] if isinstance(exc, FilePublicationCleanupError) else []
        retained_paths = list(getattr(exc, "retained_paths", ()))
        for record in reversed(published):
            destination = record["resolved_destination"]
            try:
                receipt = record.get("publication")
                if receipt is not None and record["existed"]:
                    # Do not check a publication at one resolved path while
                    # restoring a snapshot captured from another path.
                    receipt.check_unchanged(destination)
                    destination = receipt.destination
                if record["existed"]:
                    snapshot = os.path.join(recovery_directory, record["original_file"])

                    def quarantine_publication(actual_destination):
                        try:
                            paths = receipt.quarantine_if_unchanged(actual_destination)
                        except BaseException as quarantine_error:
                            # Record capture locations before atomic staging
                            # cleanup runs; its own error must not hide them.
                            paths = getattr(quarantine_error, "retained_paths", ())
                            record["retained_paths"] = list(paths)
                            record["quarantine_directory"] = getattr(quarantine_error, "recovery_directory", None)
                            retained_paths.extend(paths)
                            raise
                        record["retained_paths"] = list(paths)
                        record["quarantine_directory"] = os.path.dirname(paths[0]) if paths else None
                        retained_paths.extend(paths)

                    with open(snapshot, "rb") as handle:
                        atomic_write_bytes(destination, handle.read(),
                                           replace_existing=False, allow_copy=False,
                                           mode=record["original_mode"],
                                           before_replace=(quarantine_publication
                                                           if receipt is not None else None))
                else:
                    paths = receipt.remove_if_unchanged()
                    record["retained_paths"] = list(paths)
                    record["quarantine_directory"] = os.path.dirname(paths[0]) if paths else None
                    retained_paths.extend(paths)
                record["restored"] = True
            except BaseException as rollback_error:
                paths = list(getattr(rollback_error, "retained_paths", ()))
                if getattr(rollback_error, "recovery_directory", None):
                    record["quarantine_directory"] = rollback_error.recovery_directory
                if os.path.lexists(destination):
                    paths.append(destination)
                record["retained_paths"] = list(dict.fromkeys(record.get("retained_paths", []) + paths))
                retained_paths.extend(paths)
                rollback_errors.append(f"{record['destination']}: {rollback_error}")
        if rollback_errors or retained_paths:
            retain_recovery = True
            try:
                _write_recovery_manifest(recovery_directory, records, rollback_errors)
            except BaseException as manifest_error:
                rollback_errors.append(f"Could not update recovery manifest: {manifest_error}")
        raise FileBatchWriteError(
            str(exc), rollback_errors=rollback_errors,
            recovery_directory=recovery_directory if retain_recovery else "",
            original_exception=exc, retained_paths=dict.fromkeys(retained_paths),
        ) from exc
    finally:
        if recovery_directory and not retain_recovery:
            shutil.rmtree(recovery_directory, ignore_errors=True)
