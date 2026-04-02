import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass

_DOS_BASE_LENGTH = 8
_PADDING_TEXT = "DKSONG"


@dataclass(frozen=True)
class RenameResult:
    renamed: list[tuple[str, str]]
    unchanged: list[str]
    backups_created: list[str]


def _normalize_path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _default_backup_path(file_path):
    stem, ext = os.path.splitext(file_path)
    return f"{stem}_backup{ext}"


def _letters_only_upper(filename):
    stem = os.path.splitext(filename)[0]
    return "".join(ch for ch in stem.upper() if "A" <= ch <= "Z")


def build_dos83_midi_filename(source_filename, counter):
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
    return f"{prefix}{shortname}.MID"


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


def _build_temp_path(directory, index):
    return os.path.join(directory, f".aps_midi_rename_{index}_{uuid.uuid4().hex}.tmp")


def rename_midi_files_dos83(file_paths, create_backups=False, backup_path_builder=None):
    plan = build_midi_dos83_plan(file_paths)
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

    backup_path_builder = backup_path_builder or _default_backup_path
    backups_created = []
    if create_backups:
        for source, _ in moving:
            backup_path = backup_path_builder(source)
            try:
                shutil.copy2(source, backup_path)
            except Exception as exc:
                raise RuntimeError(
                    f"Backup failed for {os.path.basename(source)}: {exc}"
                ) from exc
            backups_created.append(backup_path)

    temp_entries = []
    for index, (source, target) in enumerate(moving):
        temp_entries.append((source, target, _build_temp_path(os.path.dirname(source), index)))

    moved_to_temp = []
    try:
        for source, target, temp_path in temp_entries:
            os.replace(source, temp_path)
            moved_to_temp.append((source, target, temp_path))
    except Exception as exc:
        rollback_errors = []
        for original_source, _, temp_path in reversed(moved_to_temp):
            try:
                os.replace(temp_path, original_source)
            except Exception as rollback_exc:
                rollback_errors.append(
                    f"{os.path.basename(original_source)} ({rollback_exc})"
                )
        rollback_suffix = ""
        if rollback_errors:
            rollback_suffix = " Rollback issues: " + "; ".join(rollback_errors)
        raise RuntimeError(f"Rename failed before finalizing names: {exc}.{rollback_suffix}") from exc

    moved_to_target = []
    try:
        for source, target, temp_path in moved_to_temp:
            os.replace(temp_path, target)
            moved_to_target.append((source, target, temp_path))
    except Exception as exc:
        rollback_errors = []
        processed = len(moved_to_target)

        for source, _, temp_path in moved_to_temp[processed:]:
            if os.path.exists(temp_path):
                try:
                    os.replace(temp_path, source)
                except Exception as rollback_exc:
                    rollback_errors.append(
                        f"{os.path.basename(source)} ({rollback_exc})"
                    )

        for source, target, _ in reversed(moved_to_target):
            if os.path.exists(target):
                try:
                    os.replace(target, source)
                except Exception as rollback_exc:
                    rollback_errors.append(
                        f"{os.path.basename(source)} ({rollback_exc})"
                    )

        rollback_suffix = ""
        if rollback_errors:
            rollback_suffix = " Rollback issues: " + "; ".join(rollback_errors)
        raise RuntimeError(f"Rename failed while finalizing names: {exc}.{rollback_suffix}") from exc

    return RenameResult(
        renamed=[(source, target) for source, target in moving],
        unchanged=unchanged,
        backups_created=backups_created,
    )
