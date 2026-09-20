"""Extract dropped ZIP files into caller-owned temporary storage."""

import os
from pathlib import Path, PureWindowsPath
import stat
import unicodedata
import zipfile


MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_FILES = 10_000
MAX_SESSION_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_SESSION_FILES = 10_000
COPY_CHUNK_SIZE = 1024 * 1024

_UNSAFE_ARCHIVE = "The ZIP file contains unsafe paths or unsupported entries."
_EXTRACTION_LIMIT = "The ZIP file exceeds the extraction limits."
_SESSION_LIMIT = (
    "The ZIP import exceeds the temporary storage budget. "
    "Save or clear the current files, then try again."
)
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{number}" for number in "123456789¹²³"),
    *(f"LPT{number}" for number in "123456789¹²³"),
}


class ZipImportError(ValueError):
    """An archive cannot be safely imported."""


class ZipImportCancelled(Exception):
    """The caller cancelled extraction."""


def _check_cancelled(is_cancelled):
    if is_cancelled is not None and is_cancelled():
        raise ZipImportCancelled()


def _report_byte_progress(byte_progress, completed_bytes, total_bytes, is_cancelled):
    if byte_progress is not None:
        byte_progress(completed_bytes, total_bytes)
    _check_cancelled(is_cancelled)


def _member_parts(info):
    # ZipInfo truncates filenames at NUL; inspect the original name as well.
    original = info.orig_filename
    name = original.replace("\\", "/")
    if "\x00" in original or name.startswith("/") or PureWindowsPath(name).drive:
        raise ZipImportError(_UNSAFE_ARCHIVE)
    is_directory = name.endswith("/")
    parts = tuple(name[:-1].split("/") if is_directory else name.split("/"))
    for part in parts:
        if (
            not part or part in {".", ".."} or part.endswith((".", " "))
            or any(ord(character) < 32 or character in '<>:"|?*' for character in part)
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        ):
            raise ZipImportError(_UNSAFE_ARCHIVE)
    entry_type = stat.S_IFMT(info.external_attr >> 16)
    if entry_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ZipImportError(_UNSAFE_ARCHIVE)
    if entry_type == stat.S_IFDIR and not is_directory:
        raise ZipImportError(_UNSAFE_ARCHIVE)
    if entry_type == stat.S_IFREG and is_directory:
        raise ZipImportError(_UNSAFE_ARCHIVE)
    return parts, is_directory


def _available_budget_bytes(budget, destination):
    return MAX_SESSION_UNCOMPRESSED_BYTES - sum(
        size for root, (size, _count) in budget.items() if root != destination
    )


def _check_budget(budget, destination, total_bytes, total_files):
    if budget is None:
        return
    other_files = sum(count for root, (_size, count) in budget.items() if root != destination)
    if (
        total_bytes > _available_budget_bytes(budget, destination)
        or total_files + other_files > MAX_SESSION_FILES
    ):
        raise ZipImportError(_SESSION_LIMIT)


def _extraction_plan(archive, is_cancelled, byte_progress, budget, destination):
    paths = {}
    explicit_paths = set()
    files = []
    total_bytes = 0
    for index, info in enumerate(archive.infolist()):
        if index and index % 128 == 0:
            _report_byte_progress(byte_progress, 0, 0, is_cancelled)
        _check_cancelled(is_cancelled)
        parts, is_directory = _member_parts(info)
        if parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
            continue
        # Include implicit directories so file/directory conflicts and casing
        # differences fail identically on Linux, macOS, and Windows.
        key_parts = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
        for length in range(1, len(parts) + 1):
            key = key_parts[:length]
            original = parts[:length]
            directory = length < len(parts) or is_directory
            existing = paths.get(key)
            if existing is not None and existing != (original, directory):
                raise ZipImportError(_UNSAFE_ARCHIVE)
            paths[key] = (original, directory)
        if key_parts in explicit_paths:
            raise ZipImportError(_UNSAFE_ARCHIVE)
        explicit_paths.add(key_parts)
        if not is_directory:
            total_bytes += info.file_size
            files.append((info, parts))
            if total_bytes > MAX_UNCOMPRESSED_BYTES or len(files) > MAX_FILES:
                raise ZipImportError(_EXTRACTION_LIMIT)
            _check_budget(budget, destination, total_bytes, len(files))
    return files, total_bytes


def extract_zip(
    archive_path, destination, *, progress=None, byte_progress=None, is_cancelled=None,
    budget=None,
):
    """Return extracted file paths in archive order, preserving subdirectories.

    ``destination`` must be an existing, empty, private directory. The caller
    owns its lifetime and must remove it after an error or cancellation.
    ``progress(completed_files, total_files)`` also runs between copy chunks so
    a GUI caller can process events and allow cancellation during large files.
    ``byte_progress(completed_bytes, total_bytes)`` reports ``(0, 0)`` before
    opening the archive and periodically during validation, then reports the
    uncompressed byte total before copying and after each chunk or empty file. Empty
    archives and archives containing only empty files finish with ``(0, 0)``.
    Cancellation is checked immediately after each progress callback.
    Embedded ZIP files are returned as ordinary files without further expansion.
    ``budget`` optionally shares a mapping of destination directories to retained
    ``(bytes, files)`` across imports. This includes companion files. A successful
    extraction reserves its usage until the caller removes that directory's entry
    after deleting its files; failed or cancelled extractions release it here.
    """
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ZipImportError(_UNSAFE_ARCHIVE)
    _check_cancelled(is_cancelled)
    _report_byte_progress(byte_progress, 0, 0, is_cancelled)
    budget_key = os.fspath(destination)
    try:
        return _extract_zip(
            archive_path, destination, progress, byte_progress, is_cancelled,
            budget, budget_key,
        )
    except BaseException:
        if budget is not None:
            budget.pop(budget_key, None)
        raise


def _extract_zip(archive_path, destination, progress, byte_progress, is_cancelled, budget, budget_key):
    with zipfile.ZipFile(archive_path) as archive:
        files, total_bytes = _extraction_plan(
            archive, is_cancelled, byte_progress, budget, budget_key,
        )
        total_files = len(files)
        _check_budget(budget, budget_key, total_bytes, total_files)
        if budget is not None:
            budget[budget_key] = (total_bytes, total_files)
        completed_files = 0
        actual_bytes = 0
        extracted = []
        _report_byte_progress(byte_progress, 0, total_bytes, is_cancelled)

        def report_progress():
            if progress is not None:
                progress(completed_files, total_files)
            _check_cancelled(is_cancelled)

        report_progress()
        for info, parts in files:
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                while True:
                    _check_cancelled(is_cancelled)
                    read_limit = min(COPY_CHUNK_SIZE, MAX_UNCOMPRESSED_BYTES - actual_bytes + 1)
                    if budget is not None:
                        read_limit = min(
                            read_limit, _available_budget_bytes(budget, budget_key) - actual_bytes + 1,
                        )
                    chunk = source.read(read_limit)
                    if not chunk:
                        break
                    actual_bytes += len(chunk)
                    if actual_bytes > MAX_UNCOMPRESSED_BYTES:
                        raise ZipImportError(_EXTRACTION_LIMIT)
                    _check_budget(budget, budget_key, actual_bytes, total_files)
                    if budget is not None and actual_bytes > total_bytes:
                        budget[budget_key] = (actual_bytes, total_files)
                    output.write(chunk)
                    _report_byte_progress(byte_progress, actual_bytes, total_bytes, is_cancelled)
                    report_progress()
            extracted.append(os.fspath(target))
            completed_files += 1
            if info.file_size == 0:
                # Empty members have no copy chunks to keep a busy UI alive.
                _report_byte_progress(byte_progress, actual_bytes, total_bytes, is_cancelled)
            report_progress()
        if budget is not None:
            budget[budget_key] = (actual_bytes, total_files)
        return extracted
