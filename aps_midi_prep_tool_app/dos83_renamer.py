import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .rename_recovery import (
    new_transaction_directory, preflight_space, recovery_lock, recovery_root,
    sync_directory, sync_file, write_manifest,
)
from collections import defaultdict
from dataclasses import dataclass

_DOS_BASE_LENGTH = 8
_PADDING_TEXT = "DKSONG"
_DOS83_INVALID_CHARS = set('\\/:*?"<>|+,;=[]')


@dataclass(frozen=True)
class RenameResult:
    renamed: list[tuple[str, str]]
    unchanged: list[str]
    backups_created: list[str]


class RenameError(RuntimeError):
    """A rename failed, with recovery copies retained if rollback was incomplete."""

    def __init__(self, message, *, rollback_errors=(), recovery_directory=""):
        self.rollback_errors = list(rollback_errors)
        self.recovery_directory = recovery_directory
        if self.rollback_errors:
            message += " Rollback issues: " + "; ".join(self.rollback_errors)
            message += f". Recovery copies and manifest retained in: {recovery_directory}"
        super().__init__(message)


def is_dos83_filename(filename):
    """Return whether a basename follows the app's Yamaha-safe DOS 8.3 rules."""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in {".", ".."} or name.endswith(".") or " " in name:
        return False
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in name):
        return False
    if any(char in _DOS83_INVALID_CHARS for char in name):
        return False
    stem, extension = os.path.splitext(name)
    if not stem or stem.startswith(".") or "." in stem:
        return False
    return len(stem) <= _DOS_BASE_LENGTH and len(extension.lstrip(".")) <= 3


def _normalize_path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _default_backup_path(file_path):
    stem, ext = os.path.splitext(file_path)
    return f"{stem}_backup{ext}"


def _letters_only_upper(filename):
    stem = os.path.splitext(filename)[0]
    return "".join(ch for ch in stem.upper() if "A" <= ch <= "Z")


def build_dos83_filename(source_filename, counter, *, extension=None):
    if counter < 0:
        raise ValueError("Counter must be non-negative.")

    prefix = f"{counter:02d}" if counter < 100 else str(counter)
    remaining = _DOS_BASE_LENGTH - len(prefix)
    if remaining <= 0:
        raise ValueError("Too many files selected to fit DOS 8.3 names.")

    letters = _letters_only_upper(source_filename)
    while len(letters) < remaining:
        letters += _PADDING_TEXT
    shortname = letters[:remaining]
    if extension is None:
        extension = os.path.splitext(source_filename)[1].lstrip(".")
    extension = "".join(
        ch for ch in str(extension or "").upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9"
    )
    if extension == "MIDI":
        extension = "MID"
    extension = extension[:3]
    return f"{prefix}{shortname}" + (f".{extension}" if extension else "")


def build_dos83_midi_filename(source_filename, counter):
    return build_dos83_filename(source_filename, counter, extension="MID")


def build_midi_dos83_plan(file_paths):
    unique_paths = []
    seen = set()
    for file_path in file_paths:
        abs_path = os.path.abspath(file_path)
        key = _normalize_path_key(abs_path)
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(abs_path)

    if not unique_paths:
        return []

    missing = [p for p in unique_paths if not os.path.isfile(p)]
    if missing:
        pretty = ", ".join(os.path.basename(p) for p in missing[:3])
        if len(missing) > 3:
            pretty += ", ..."
        raise ValueError(f"Some selected files no longer exist: {pretty}")

    grouped = defaultdict(list)
    for abs_path in unique_paths:
        grouped[os.path.dirname(abs_path)].append(abs_path)

    plan = []
    for directory in sorted(grouped.keys(), key=lambda d: d.lower()):
        paths = sorted(grouped[directory], key=lambda p: os.path.basename(p).lower())
        for counter, source_path in enumerate(paths):
            target_name = build_dos83_midi_filename(os.path.basename(source_path), counter)
            target_path = os.path.join(directory, target_name)
            plan.append((source_path, target_path))
    return plan


def _validate_plan(plan):
    missing = [source for source, _ in plan if not os.path.isfile(source)]
    if missing:
        pretty = ", ".join(os.path.basename(p) for p in missing[:3])
        if len(missing) > 3:
            pretty += ", ..."
        raise ValueError(f"Some selected files no longer exist: {pretty}")

    source_keys = {_normalize_path_key(source) for source, _ in plan}
    target_map = {}

    for source, target in plan:
        source_key = _normalize_path_key(source)
        target_key = _normalize_path_key(target)
        existing_source_key = target_map.get(target_key)
        if existing_source_key is not None and existing_source_key != source_key:
            raise ValueError(f"Generated duplicate target filename: {os.path.basename(target)}")
        target_map[target_key] = source_key

    for source, target in plan:
        source_key = _normalize_path_key(source)
        target_key = _normalize_path_key(target)
        if target_key == source_key:
            continue
        if os.path.exists(target) and target_key not in source_keys:
            raise FileExistsError(
                f"Cannot rename {os.path.basename(source)}: target {os.path.basename(target)} already exists."
            )


def validate_midi_dos83_plan(plan):
    normalized_plan = [
        (os.path.abspath(source), os.path.abspath(target))
        for source, target in plan
    ]
    _validate_plan(normalized_plan)


def _plan_backups(plan, moving, backup_path_builder):
    # Resolve directory aliases too, including destinations that do not exist yet.
    def path_key(path):
        return _normalize_path_key(os.path.realpath(path))

    reserved = {path_key(path) for entry in plan for path in entry}
    backups = []
    for source, _ in moving:
        desired = os.path.abspath(backup_path_builder(source))
        candidate = desired
        stem, extension = os.path.splitext(desired)
        counter = 2
        while path_key(candidate) in reserved or os.path.lexists(candidate):
            candidate = f"{stem}_{counter}{extension}"
            counter += 1
        reserved.add(path_key(candidate))
        backups.append((source, candidate))
    return backups


def _copy_backup(source, destination):
    created = False
    try:
        with open(source, "rb") as source_file, open(destination, "xb") as backup_file:
            created = True
            shutil.copyfileobj(source_file, backup_file)
        shutil.copystat(source, destination)
    except Exception:
        if created:
            os.unlink(destination)
        raise


def _digest(path):
    if os.path.islink(path):
        raise ValueError(f"Recovery cannot safely identify a symbolic link: {path}")
    with open(path, "rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _fault_hook(phase, index):
    """Tests replace this with os._exit(), immediately after a physical move."""


class _RenameTransaction:
    def __init__(self, directory, manifest):
        self.directory = str(directory)
        self.manifest = manifest
        self.records = manifest["files"]

    def save(self):
        write_manifest(self.directory, self.manifest)

    def matches(self, path, record):
        return os.path.isfile(path) and _digest(path) == record["sha256"]

    def reserved(self, path):
        if not os.path.isfile(path) or os.path.islink(path):
            return False
        with open(path, "rb") as handle:
            return handle.read(100) == self.manifest["id"].encode("ascii")

    def resolve_pending(self):
        pending = self.manifest.get("pending")
        if pending is None:
            return
        record = self.records[pending["index"]]
        source, destination = pending["source"], pending["destination"]
        if self.matches(source, record) and (
            not os.path.lexists(destination)
            or (destination == record["temporary_path"] and self.reserved(destination))
        ):
            record["location"] = source
        elif not os.path.lexists(source) and self.matches(destination, record):
            record["location"] = destination
        else:
            raise RuntimeError(
                f"Cannot safely determine the interrupted move from {source} to {destination}. "
                f"Files and recovery copies are preserved in {self.directory}."
            )
        self.manifest["pending"] = None
        self.save()

    def move(self, index, destination, phase):
        record = self.records[index]
        source = record["location"]
        if not self.matches(source, record):
            raise RuntimeError(f"File changed or is unavailable: {source}. Recovery copies are preserved.")
        if os.path.lexists(destination) and not (
            destination == record["temporary_path"] and self.reserved(destination)
        ):
            raise FileExistsError(f"Rename destination is occupied: {destination}")
        self.manifest["pending"] = {"index": index, "source": source, "destination": destination}
        self.save()  # Write-ahead intent survives death between rename and the next journal update.
        os.replace(source, destination)
        record["location"] = destination
        _fault_hook(phase, index)
        for directory in {os.path.dirname(source), os.path.dirname(destination)}:
            sync_directory(directory)
        self.manifest["pending"] = None
        self.save()

    def verify(self):
        for record in self.records:
            snapshot = os.path.join(self.directory, record["original_file"])
            if not self.matches(snapshot, record):
                raise RuntimeError(f"Recovery copy is missing or changed: {snapshot}")
        self.resolve_pending()
        occupied = {}
        for record in self.records:
            if not self.matches(record["location"], record):
                raise RuntimeError(
                    f"Original is missing or changed: {record['location']}. "
                    f"Its independent recovery copy is in {self.directory}."
                )
            key = _normalize_path_key(record["location"])
            if key in occupied:
                raise ValueError("Recovery journal assigns two recordings to the same location.")
            occupied[key] = record
        for record in self.records:
            for path in (record["source"], record["target"], record["temporary_path"]):
                if _normalize_path_key(path) in occupied or not os.path.lexists(path):
                    continue
                if path == record["temporary_path"] and self.reserved(path):
                    continue
                raise FileExistsError(f"Recovery stopped because an unrelated file occupies {path}. No files were overwritten.")

    def cleanup(self):
        for record in self.records:
            path = record["temporary_path"]
            if os.path.lexists(path):
                if not self.reserved(path):
                    raise RuntimeError(f"Staging file retained for inspection: {path}")
                os.unlink(path)
                sync_directory(os.path.dirname(path))
        # A crash during recursive cleanup must not look like an unfinished rename.
        completed = os.path.join(os.path.dirname(self.directory), ".completed_" + self.manifest["id"])
        os.rename(self.directory, completed)
        sync_directory(os.path.dirname(completed))
        shutil.rmtree(completed, ignore_errors=True)


def _load_transaction(directory):
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        manifest_path = directory / "manifest.json.new"
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("version") != 2 or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported rename recovery journal. All recovery files have been preserved.")
    transaction_id = str(uuid.UUID(manifest["id"]))
    if transaction_id != manifest["id"]:
        raise ValueError("Invalid rename transaction ID")
    for index, record in enumerate(manifest["files"]):
        source, target = record["source"], record["target"]
        expected_temp = os.path.join(os.path.dirname(source), f".aps_midi_rename_{transaction_id}_{index}.tmp")
        if (not os.path.isabs(source) or not os.path.isabs(target)
                or record["temporary_path"] != expected_temp
                or record["original_file"] != f"original-{index:04d}.bin"
                or record["location"] not in (source, target, expected_temp)):
            raise ValueError("Invalid rename recovery paths. All files have been preserved.")
    pending = manifest.get("pending")
    if pending is not None:
        index = pending["index"]
        if not isinstance(index, int) or not 0 <= index < len(manifest["files"]):
            raise ValueError("Invalid pending rename index")
        record = manifest["files"][index]
        paths = (record["source"], record["target"], record["temporary_path"])
        if pending["source"] not in paths or pending["destination"] not in paths:
            raise ValueError("Invalid pending rename paths")
    return _RenameTransaction(directory, manifest)


def _pending_directories(root):
    return sorted(Path(root).glob("aps_midi_rename_recovery_*"))


def find_pending_midi_renames():
    """Return unfinished journal directories, including unreadable ones for the UI."""
    root = recovery_root()
    if not root.exists():
        return []
    with recovery_lock(root):
        pending = []
        for path in _pending_directories(root):
            if not path.is_dir():
                continue
            try:
                transaction = _load_transaction(path)
                if transaction.manifest["phase"] in {"committed", "restored"}:
                    transaction.verify()
                    transaction.cleanup()
                    continue
            except Exception:
                # An unreadable or conflicting journal also needs to be surfaced.
                pass
            pending.append(str(path))
        for completed in root.glob(".completed_*"):
            if completed.is_dir() and not completed.is_symlink():
                shutil.rmtree(completed, ignore_errors=True)
        return pending


def _restore_transaction(transaction):
    errors = []
    try:
        transaction.resolve_pending()
    except BaseException as exc:
        return [str(exc)]
    # Vacate every published target first: source and destination names can form cycles.
    for index in reversed(range(len(transaction.records))):
        record = transaction.records[index]
        if record["location"] == record["target"]:
            try:
                transaction.move(index, record["temporary_path"], "restore_staging")
            except BaseException as exc:
                errors.append(f"Could not stage {record['target']} for rollback ({exc})")
                try:
                    transaction.resolve_pending()
                except BaseException:
                    return errors
    if errors:
        return errors
    for index in reversed(range(len(transaction.records))):
        record = transaction.records[index]
        if record["location"] != record["source"]:
            try:
                transaction.move(index, record["source"], "restore_publication")
            except BaseException as exc:
                errors.append(f"Could not restore {record['source']} ({exc})")
                try:
                    transaction.resolve_pending()
                except BaseException:
                    return errors
    return errors


def recover_midi_dos83_transaction(directory, action="restore"):
    """Restore originals or finish the rename, even after interruption during recovery."""
    if action not in {"restore", "resume"}:
        raise ValueError("Choose restore or resume.")
    root = recovery_root()
    directory = Path(directory).absolute()
    if directory.parent != root.absolute() or not directory.name.startswith("aps_midi_rename_recovery_"):
        raise ValueError("Recovery directory is outside the configured APS recovery location.")
    resume_plan = None
    with recovery_lock(root):
        transaction = _load_transaction(directory)
        phase = transaction.manifest["phase"]
        if phase == "preparing":
            # No sources move until all recovery copies and optional backups are ready.
            if not all(os.path.isfile(r["source"]) for r in transaction.records):
                raise RuntimeError(f"A source is now missing. Recovery copies are preserved in {directory}.")
            if action == "resume":
                resume_plan = [(r["source"], r["target"]) for r in transaction.records]
                backup_plan = dict(transaction.manifest.get("backup_plan", []))
            transaction.cleanup()
        else:
            transaction.verify()  # Refuse conflicting external changes before moving anything.
            transaction.manifest["phase"] = "recovering"
            transaction.save()
            if action == "restore":
                errors = _restore_transaction(transaction)
                if errors:
                    raise RenameError("Could not finish restoring originals.", rollback_errors=errors, recovery_directory=str(directory))
            else:
                # Normalize both partial publication and partial rollback to a fully staged set.
                for index, record in enumerate(transaction.records):
                    if record["location"] != record["temporary_path"]:
                        transaction.move(index, record["temporary_path"], "recovery_staging")
                for index, record in enumerate(transaction.records):
                    transaction.move(index, record["target"], "recovery_publication")
            transaction.manifest["phase"] = "restored" if action == "restore" else "committed"
            transaction.save()
            renamed = [(r["source"], r["target"]) for r in transaction.records] if action == "resume" else []
            transaction.cleanup()
            return RenameResult(renamed, [], [])
    if resume_plan is not None:
        return apply_midi_dos83_plan(
            resume_plan, create_backups=bool(backup_plan),
            backup_path_builder=lambda source: backup_plan[source],
        )
    return RenameResult([], [], [])


def apply_midi_dos83_plan(plan, create_backups=False, backup_path_builder=None):
    """Rename with persistent snapshots, a write-ahead journal, and safe rollback."""
    plan = [(os.path.abspath(source), os.path.abspath(target)) for source, target in plan]
    if not plan:
        return RenameResult([], [], [])
    _validate_plan(plan)
    moving = [(source, target) for source, target in plan if _normalize_path_key(source) != _normalize_path_key(target)]
    unchanged = [source for source, target in plan if _normalize_path_key(source) == _normalize_path_key(target)]
    if not moving:
        return RenameResult([], unchanged, [])
    backup_plan = _plan_backups(plan, moving, backup_path_builder or _default_backup_path) if create_backups else []
    root = recovery_root()
    with recovery_lock(root):
        pending = _pending_directories(root)
        if pending:
            raise RenameError(f"An unfinished rename needs recovery first. Reopen APS to restore or resume it. Recovery: {pending[0]}", recovery_directory=str(pending[0]))
        preflight_space(root, moving)
        directory = new_transaction_directory(root)
        transaction_id = str(uuid.uuid4())
        records = [{
            "source": source, "target": target, "location": source,
            "temporary_path": os.path.join(os.path.dirname(source), f".aps_midi_rename_{transaction_id}_{index}.tmp"),
            "original_file": f"original-{index:04d}.bin",
        } for index, (source, target) in enumerate(moving)]
        transaction = _RenameTransaction(directory, {
            "version": 2, "id": transaction_id, "phase": "preparing", "pending": None,
            "files": records, "backup_plan": backup_plan,
        })
        backups_created = []
        retain_recovery = False
        try:
            try:
                transaction.save()
                for record in records:
                    # Exclusive, collision-proof filenames need no new source subdirectories.
                    with open(record["temporary_path"], "xb") as handle:
                        handle.write(transaction_id.encode("ascii"))
                        handle.flush()
                        os.fsync(handle.fileno())
                    sync_directory(os.path.dirname(record["source"]))
                    snapshot = os.path.join(directory, record["original_file"])
                    shutil.copyfile(record["source"], snapshot)
                    sync_file(snapshot)
                    record["sha256"] = _digest(snapshot)
            except Exception as exc:
                raise RuntimeError(f"Could not prepare rename recovery copies or staging files in {root}: {exc}. No files were renamed.") from exc
            for source, backup_path in backup_plan:
                try:
                    _copy_backup(source, backup_path)
                except Exception as exc:
                    raise RuntimeError(f"Backup failed for {os.path.basename(source)}: {exc}") from exc
                backups_created.append(backup_path)
            transaction.manifest["phase"] = "ready"
            transaction.save()
            retain_recovery = True
            phase = "before finalizing names"
            try:
                for index, record in enumerate(records):
                    transaction.move(index, record["temporary_path"], "staging")
                phase = "while finalizing names"
                for index, record in enumerate(records):
                    transaction.move(index, record["target"], "publication")
                transaction.manifest["phase"] = "committed"
                transaction.save()
            except BaseException as exc:
                rollback_errors = _restore_transaction(transaction)
                retain_recovery = bool(rollback_errors)
                if not retain_recovery:
                    transaction.manifest["phase"] = "restored"
                    transaction.save()
                raise RenameError(
                    f"Rename failed {phase}: {exc}.", rollback_errors=rollback_errors,
                    recovery_directory=directory if retain_recovery else "",
                ) from exc
            retain_recovery = False
        finally:
            if not retain_recovery:
                transaction.cleanup()
        return RenameResult(moving, unchanged, backups_created)


def rename_midi_files_dos83(file_paths, create_backups=False, backup_path_builder=None):
    return apply_midi_dos83_plan(
        build_midi_dos83_plan(file_paths),
        create_backups=create_backups,
        backup_path_builder=backup_path_builder,
    )
