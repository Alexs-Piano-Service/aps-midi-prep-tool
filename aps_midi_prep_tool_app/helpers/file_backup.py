"""Plan collision-safe backup names and copy originals without replacing files."""

import os
import shutil


def default_backup_path(source):
    stem, extension = os.path.splitext(source)
    return f"{stem}_backup{extension}"


def _path_key(path):
    # Resolve directory aliases even when the destination does not exist yet.
    return os.path.normcase(os.path.realpath(path))


def _available_path(desired, reserved):
    desired = os.path.abspath(desired)
    candidate = desired
    stem, extension = os.path.splitext(desired)
    counter = 2
    while _path_key(candidate) in reserved or os.path.lexists(candidate):
        candidate = f"{stem}_{counter}{extension}"
        counter += 1
    return candidate


def unique_backup_path(desired, *, reserved_paths=()):
    return _available_path(desired, {_path_key(path) for path in reserved_paths})


def plan_file_backups(sources, *, reserved_paths=(), backup_path_builder=None):
    """Choose every backup before writing, reserving sources and planned outputs.

    Existing files, dangling symlinks, aliases and other planned backups are
    never reused. Callers must still use exclusive creation to handle a file
    arriving after this read-only planning step.
    """
    sources = [os.path.abspath(source) for source in sources]
    reserved = {_path_key(path) for path in (*sources, *reserved_paths)}
    builder = backup_path_builder or default_backup_path
    plan = []
    for source in sources:
        destination = _available_path(builder(source), reserved)
        reserved.add(_path_key(destination))
        plan.append((source, destination))
    return plan


def copy_file_backup(source, destination):
    """Create a backup exclusively and remove our partial copy on failure."""
    created = False
    try:
        with open(source, "rb") as source_file, open(destination, "xb") as backup_file:
            created = True
            shutil.copyfileobj(source_file, backup_file)
            backup_file.flush()
            os.fsync(backup_file.fileno())
        shutil.copystat(source, destination)
    except BaseException:
        if created:
            os.unlink(destination)
        raise
