import json
import os
import shutil
import tempfile
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


def _rollback_rename(staged, published):
    errors = []
    # Vacate *every* published target before restoring any original. Targets
    # may themselves be original paths, including chains and complete cycles.
    for _, target, temp_path in reversed(published):
        try:
            os.replace(target, temp_path)
        except BaseException as exc:
            errors.append(f"Could not stage {target} for rollback ({exc})")
    if errors:
        # A target still holds another source's data. Keep all staging files
        # and snapshots; restoring originals here could overwrite that data.
        return errors

    for source, _, temp_path in reversed(staged):
        try:
            if os.path.lexists(source):
                raise FileExistsError(f"Original path is occupied: {source}")
            os.replace(temp_path, source)
        except BaseException as exc:
            errors.append(f"Could not restore {source} ({exc})")
    return errors


def apply_midi_dos83_plan(plan, create_backups=False, backup_path_builder=None):
    """Rename a batch, reserving safe backups and retaining copies on rollback errors."""
    plan = [
        (os.path.abspath(source), os.path.abspath(target))
        for source, target in plan
    ]
    if not plan:
        return RenameResult(renamed=[], unchanged=[], backups_created=[])

    _validate_plan(plan)

    moving = []
    unchanged = []
    for source, target in plan:
        if _normalize_path_key(source) == _normalize_path_key(target):
            unchanged.append(source)
        else:
            moving.append((source, target))

    if not moving:
        return RenameResult(renamed=[], unchanged=unchanged, backups_created=[])

    # Finish planning all backup names before creating any files.
    backup_plan = _plan_backups(
        plan, moving, backup_path_builder or _default_backup_path,
    ) if create_backups else []
    backups_created = []
    recovery_directory = ""
    retain_recovery = False
    staging_directories = {}
    temp_entries = []
    try:
        try:
            recovery_directory = tempfile.mkdtemp(prefix="aps_midi_rename_recovery_")
            records = []
            for index, (source, target) in enumerate(moving):
                directory = os.path.dirname(source)
                if directory not in staging_directories:
                    staging_directories[directory] = tempfile.mkdtemp(
                        prefix=".aps_midi_rename_", dir=directory,
                    )
                temp_path = os.path.join(staging_directories[directory], f"{index}.tmp")
                temp_entries.append((source, target, temp_path))
                snapshot = f"original-{index:04d}.bin"
                # Independent copies survive even if a later rollback cannot
                # move files. Keep them writable for cleanup on Windows.
                shutil.copyfile(source, os.path.join(recovery_directory, snapshot))
                records.append({
                    "source": source, "target": target,
                    "temporary_path": temp_path, "original_file": snapshot,
                })
            with open(os.path.join(recovery_directory, "manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "files": records}, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except Exception as exc:
            raise RuntimeError(f"Could not prepare rename recovery copies: {exc}") from exc

        for source, backup_path in backup_plan:
            try:
                _copy_backup(source, backup_path)
            except Exception as exc:
                raise RuntimeError(
                    f"Backup failed for {os.path.basename(source)}: {exc}"
                ) from exc
            backups_created.append(backup_path)

        moved_to_temp = []
        moved_to_target = []
        phase = "before finalizing names"
        # Until success or a complete rollback, cleanup must never remove
        # staging directories that may contain the only live original files.
        retain_recovery = True
        try:
            for source, target, temp_path in temp_entries:
                os.replace(source, temp_path)
                moved_to_temp.append((source, target, temp_path))
            phase = "while finalizing names"
            for source, target, temp_path in moved_to_temp:
                os.replace(temp_path, target)
                moved_to_target.append((source, target, temp_path))
        except BaseException as exc:
            rollback_errors = _rollback_rename(moved_to_temp, moved_to_target)
            retain_recovery = bool(rollback_errors)
            raise RenameError(
                f"Rename failed {phase}: {exc}.",
                rollback_errors=rollback_errors,
                recovery_directory=recovery_directory if retain_recovery else "",
            ) from exc
        retain_recovery = False
    finally:
        if not retain_recovery:
            for directory in staging_directories.values():
                try:
                    os.rmdir(directory)
                except OSError:
                    pass
            if recovery_directory:
                shutil.rmtree(recovery_directory, ignore_errors=True)

    return RenameResult(
        renamed=[(source, target) for source, target in moving],
        unchanged=unchanged,
        backups_created=backups_created,
    )


def rename_midi_files_dos83(file_paths, create_backups=False, backup_path_builder=None):
    return apply_midi_dos83_plan(
        build_midi_dos83_plan(file_paths),
        create_backups=create_backups,
        backup_path_builder=backup_path_builder,
    )
