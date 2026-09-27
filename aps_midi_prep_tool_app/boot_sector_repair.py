"""Repair floppy boot sectors and file visibility without rebuilding file data."""

import os
import shutil
import stat
import tempfile
import zlib
from dataclasses import dataclass

from .floppy_image import (
    DISK_FORMAT_BY_SIZE,
    RAW_IMAGE_EXTENSIONS,
    FloppyImageError,
    FloppyOperationCancelled,
    _PROTECTED_FAT12_LAYOUTS,
    _build_standard_fat12_boot_sector,
    _clear_fat12_hidden_system_flags,
    _detect_protected_fat12_layout,
    _find_volume_label,
    _geometry_from_boot_sector,
    _gw_convert,
    _layout_total_size,
    _protected_layout_hint_from_boot_sector,
    _raise_if_cancelled,
    _reconstruct_yamaha_root_dir_from_pianodir,
    image_extension,
)


_UNSUPPORTED = (
    "Could not identify a supported 720 KB, 800 KB, or 1.44 MB FAT12 layout. No file was written."
)
_VERIFICATION_FAILED = "Boot-sector verification failed. No file was written."
_DAMAGED_DIRECTORY = (
    "This is a Yamaha 720 KB image with a damaged file directory. "
    "Use Recover Damaged Image; repairing only the boot sector will not make it readable. No file was changed."
)
_MAX_IMAGE_SIZE = max(_layout_total_size(layout) for layout in _PROTECTED_FAT12_LAYOUTS)
REPAIR_IMAGE_EXTENSIONS = RAW_IMAGE_EXTENSIONS | {"hfe"}
_MAX_CONTAINER_SIZE = 10 * 1024 * 1024


@dataclass(frozen=True)
class BootSectorRepair:
    source_path: str
    original: bytes
    repaired: bytes
    prepended: bool = False

    @property
    def changed(self):
        return self.original != self.repaired


def _require_raw_image_path(path):
    if image_extension(path) not in RAW_IMAGE_EXTENSIONS:
        raise FloppyImageError("Boot-sector repair supports raw IMG, IMA, BIN, and VFD images only.")


def _read_image(path):
    # Never read devices or unbounded files through this file-only utility.
    metadata = os.stat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_IMAGE_SIZE:
        raise FloppyImageError(_UNSUPPORTED)
    with open(path, "rb") as handle:
        return handle.read(_MAX_IMAGE_SIZE + 1)


def _verify_repair(repair):
    geometry = _geometry_from_boot_sector(repair.repaired[:512])
    expected_size = len(repair.original) + (512 if repair.prepended else 0)
    original_payload = repair.original if repair.prepended else repair.original[512:]
    if (
        geometry is None
        or geometry.total_size != len(repair.repaired)
        or len(repair.repaired) != expected_size
    ):
        raise FloppyImageError(_VERIFICATION_FAILED)
    # Only sector zero and hidden/system attribute bits on actual directory
    # entries may change. Verify against the original payload so unexpected
    # changes to FATs, song data, other attributes, or unused bytes are rejected.
    expected = _clear_fat12_hidden_system_flags(repair.repaired[:512] + original_payload)
    if repair.repaired != expected:
        raise FloppyImageError(_VERIFICATION_FAILED)


def inspect_boot_sector_image(source_path):
    """Plan a conservative boot and visibility repair, preserving file contents.

    Detection may identify a boot sector relocated over FAT2, but intentionally
    does not repair that FAT or attempt the directory reconstruction used by
    general image recovery. Outside sector zero, only hidden/system attribute
    bits on active file and directory entries are cleared.
    """
    source_path = os.path.abspath(os.fsdecode(source_path))
    _require_raw_image_path(source_path)
    original = _read_image(source_path)
    return _inspect_boot_sector_bytes(source_path, original)


def _inspect_boot_sector_bytes(source_path, original):
    if len(original) > _MAX_IMAGE_SIZE:
        raise FloppyImageError(_UNSUPPORTED)
    geometry = _geometry_from_boot_sector(original[:512])
    if geometry is not None:
        if geometry.total_size != len(original):
            raise FloppyImageError(_UNSUPPORTED)
        repair = BootSectorRepair(source_path, original, _clear_fat12_hidden_system_flags(original))
        _verify_repair(repair)
        return repair

    detection = _detect_protected_fat12_layout(original)
    if detection is None:
        # The full image loader can recognize this case from the catalog and
        # FAT chains, but it must also rebuild the directory. Use that evidence
        # only to explain the failure: this utility must not rebuild directories
        # or report an unreadable output as successfully repaired.
        try:
            recognized_directory_damage = _reconstruct_yamaha_root_dir_from_pianodir(original) is not None
        except FloppyImageError:
            recognized_directory_damage = False
        if recognized_directory_damage:
            raise FloppyImageError(_DAMAGED_DIRECTORY)
        raise FloppyImageError(_UNSUPPORTED)
    layout = detection["layout"]
    prepended = detection["mode"] == "prepend_sector0"
    if "boot_sector" in detection:
        boot = detection["boot_sector"]
    elif not prepended and _protected_layout_hint_from_boot_sector(original[:512]) == layout:
        # Preserve the OEM, serial, boot code, and label when only the signature
        # prevents an otherwise consistent boot sector from being recognized.
        boot = original[:510] + b"\x55\xaa"
    else:
        root_start = detection["root_offset"]
        root_end = root_start + detection["root_dir_sectors"] * 512
        label = _find_volume_label(original[root_start:root_end])
        serial = zlib.crc32(original[detection["fat1_offset"]:]) & 0xFFFFFFFF
        boot = _build_standard_fat12_boot_sector(layout, serial, label)
    repaired = _clear_fat12_hidden_system_flags(boot + (original if prepended else original[512:]))
    repair = BootSectorRepair(source_path, original, repaired, prepended)
    _verify_repair(repair)
    return repair


def save_boot_sector_repair(repair, output_path):
    """Atomically save a verified copy, never overwriting the source image."""
    if not repair.changed:
        return
    output_path = os.path.abspath(os.fsdecode(output_path))
    _require_raw_image_path(output_path)
    if (
        os.path.normcase(os.path.realpath(output_path))
        == os.path.normcase(os.path.realpath(repair.source_path))
        or (os.path.exists(output_path) and os.path.samefile(repair.source_path, output_path))
    ):
        raise FloppyImageError("Choose a different output file to keep the source image unchanged.")
    if _read_image(repair.source_path) != repair.original:
        raise FloppyImageError("The source image changed. Select it again before repairing.")
    _verify_repair(repair)

    fd, temporary_path = tempfile.mkstemp(prefix=".aps_boot_", suffix=".tmp", dir=os.path.dirname(output_path))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(repair.repaired)
            handle.flush()
            os.fsync(handle.fileno())
        with open(temporary_path, "rb") as handle:
            if handle.read(_MAX_IMAGE_SIZE + 1) != repair.repaired:
                raise FloppyImageError(_VERIFICATION_FAILED)
        os.replace(temporary_path, output_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


@dataclass(frozen=True)
class ImageRepairResult:
    source_path: str
    output_path: str = ""
    backup_path: str = ""
    repaired: bool = False
    converted: bool = False
    error: str = ""


@dataclass(frozen=True)
class ImageRepairBatch:
    results: tuple[ImageRepairResult, ...]
    cancelled: bool = False


def _read_container(path):
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_CONTAINER_SIZE:
        raise FloppyImageError("Choose a regular IMG, IMA, BIN, VFD, or HFE image file.")
    with open(path, "rb") as handle:
        return handle.read(_MAX_CONTAINER_SIZE + 1), metadata


def _decode_source(source_path, original, temporary_dir, cancel_callback):
    extension = image_extension(source_path)
    if extension in RAW_IMAGE_EXTENSIONS:
        return _inspect_boot_sector_bytes(source_path, original)
    encoded_path = os.path.join(temporary_dir, "source.hfe")
    with open(encoded_path, "wb") as handle:
        handle.write(original)
    # Try all supported layouts, largest first. A 720K decode of an 800K disk
    # could silently omit its tenth sector; require a complete larger decode
    # before considering the smaller geometry.
    last_error = None
    layouts = sorted(_PROTECTED_FAT12_LAYOUTS, key=_layout_total_size, reverse=True)
    for index, layout in enumerate(layouts):
        _raise_if_cancelled(cancel_callback)
        candidate = os.path.join(temporary_dir, "decoded.img")
        disk_format = DISK_FORMAT_BY_SIZE[_layout_total_size(layout)]
        try:
            _gw_convert(encoded_path, candidate, disk_format.key, cancel_callback=cancel_callback)
        except FloppyOperationCancelled:
            raise
        except FloppyImageError as exc:
            sector_map = getattr(exc, "sector_map", None) or {}
            found = int(sector_map.get("found") or 0)
            if index + 1 < len(layouts):
                next_layout = layouts[index + 1]
                rows = sector_map.get("rows") or []
                sector_base = 0 if any(row["sector"] == 0 for row in rows) else 1
                extra_sectors = any(
                    row["sector"] - sector_base >= next_layout["sectors_per_track"]
                    and "." in row["statuses"] for row in rows
                )
                if found > next_layout["total_sectors"] or extra_sectors:
                    # Even one readable extra sector rules out a smaller
                    # layout, regardless of how many other sectors are missing.
                    raise
            last_error = exc
            continue
        # A complete decode establishes the geometry. Never retry a smaller
        # layout after filesystem validation fails: that could discard sectors.
        with open(candidate, "rb") as handle:
            raw = handle.read(_MAX_IMAGE_SIZE + 1)
        repair = _inspect_boot_sector_bytes(source_path, raw)
        if len(repair.repaired) != disk_format.size_bytes:
            raise FloppyImageError(_UNSUPPORTED)
        return repair
    if last_error is not None:
        raise last_error
    raise FloppyImageError(_UNSUPPORTED)


def _encode_verified(raw_path, raw_bytes, output_path, cancel_callback):
    extension = image_extension(output_path)
    if extension in RAW_IMAGE_EXTENSIONS:
        shutil.copyfile(raw_path, output_path)
    else:
        disk_format = DISK_FORMAT_BY_SIZE[len(raw_bytes)]
        _gw_convert(raw_path, output_path, disk_format.key, cancel_callback=cancel_callback)
        decoded_path = output_path + ".verify.img"
        _gw_convert(output_path, decoded_path, disk_format.key, cancel_callback=cancel_callback)
        with open(decoded_path, "rb") as handle:
            if handle.read(_MAX_IMAGE_SIZE + 1) != raw_bytes:
                raise FloppyImageError(_VERIFICATION_FAILED)
    with open(output_path, "rb") as handle:
        encoded = handle.read(_MAX_CONTAINER_SIZE + 1)
    if len(encoded) > _MAX_CONTAINER_SIZE or (extension in RAW_IMAGE_EXTENSIONS and encoded != raw_bytes):
        raise FloppyImageError(_VERIFICATION_FAILED)
    return encoded


def _write_exclusive(path, payload, mode):
    """Reserve a new path without overwriting another image or an older backup."""
    handle = open(path, "xb")
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with open(path, "rb") as saved:
            if saved.read(len(payload) + 1) != payload:
                raise FloppyImageError(_VERIFICATION_FAILED)
        os.chmod(path, stat.S_IMODE(mode))
    except BaseException:
        _remove_created_file(path)
        raise


def _remove_created_file(path):
    try:
        os.unlink(path)
    except PermissionError:
        # Windows cannot remove a newly created file with a read-only mode.
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        os.unlink(path)


def _save_backup(source_path, original, mode):
    index = 0
    while True:
        backup_path = source_path + (".bak" if index == 0 else f".bak.{index}")
        try:
            _write_exclusive(backup_path, original, mode)
            return backup_path
        except FileExistsError:
            index += 1


def apply_boot_sector_repair(source_path, *, target_format="", backup=False, cancel_callback=None):
    """Repair the selected file in place and optionally create a converted sibling.

    All outputs are verified before publication. The original is retained when
    changing format, and a failed conversion leaves it untouched. Hidden/system
    directory flags are cleared; file contents, directory structure, and pending
    song edits are not changed.
    """
    source_path = os.path.abspath(os.fsdecode(source_path))
    source_ext = image_extension(source_path)
    target_ext = str(target_format or source_ext).lower().lstrip(".")
    if source_ext not in REPAIR_IMAGE_EXTENSIONS or target_ext not in REPAIR_IMAGE_EXTENSIONS:
        raise FloppyImageError("Choose a regular IMG, IMA, BIN, VFD, or HFE image file.")
    original, metadata = _read_container(source_path)
    converted = target_ext != source_ext
    output_path = os.path.splitext(source_path)[0] + "." + target_ext if converted else source_path
    if converted and os.path.lexists(output_path):
        raise FloppyImageError("The converted image already exists. Rename or move it before trying again.")
    _raise_if_cancelled(cancel_callback)
    # Inspect raw inputs before creating anything beside the source. Rejected
    # images and valid images needing no conversion require no working copies.
    raw_repair = _inspect_boot_sector_bytes(source_path, original) if source_ext in RAW_IMAGE_EXTENSIONS else None
    if raw_repair is not None and not raw_repair.changed and not converted:
        return ImageRepairResult(source_path, source_path)
    with tempfile.TemporaryDirectory(prefix=".aps_boot_", dir=os.path.dirname(source_path)) as temporary_dir:
        repair = raw_repair if raw_repair is not None else _decode_source(source_path, original, temporary_dir, cancel_callback)
        _verify_repair(repair)
        if not repair.changed and not converted:
            return ImageRepairResult(source_path, source_path)
        raw_path = os.path.join(temporary_dir, "repaired.img")
        with open(raw_path, "wb") as handle:
            handle.write(repair.repaired)
        staged_source = os.path.join(temporary_dir, "fixed." + source_ext)
        if repair.changed:
            _encode_verified(raw_path, repair.repaired, staged_source, cancel_callback)
            with open(staged_source, "r+b") as handle:
                os.fsync(handle.fileno())
            os.chmod(staged_source, stat.S_IMODE(metadata.st_mode))
        converted_bytes = None
        if converted:
            converted_bytes = _encode_verified(
                raw_path, repair.repaired, os.path.join(temporary_dir, "converted." + target_ext), cancel_callback,
            )
        _raise_if_cancelled(cancel_callback)

        def check_source():
            current, current_metadata = _read_container(source_path)
            if current != original or (current_metadata.st_dev, current_metadata.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise FloppyImageError("The source image changed. Select it again before repairing.")

        check_source()
        backup_path = _save_backup(source_path, original, metadata.st_mode) if backup and repair.changed else ""
        published_conversion = False
        try:
            if converted:
                _write_exclusive(output_path, converted_bytes, metadata.st_mode)
                published_conversion = True
            check_source()
            if repair.changed:
                os.replace(staged_source, source_path)
        except BaseException:
            if published_conversion:
                _remove_created_file(output_path)
            raise
        return ImageRepairResult(source_path, output_path, backup_path, repair.changed, converted)


def repair_boot_sector_batch(source_path, *, directory=False, recursive=False, target_format="", backup=False,
                             progress_callback=None, result_callback=None, cancel_callback=None):
    """Snapshot directory inputs before writing; report errors without stopping other files."""
    source_path = os.path.abspath(os.fsdecode(source_path))
    if directory:
        if not os.path.isdir(source_path):
            raise FloppyImageError("Choose a folder containing image files.")
        paths = []
        for root, dirs, files in os.walk(source_path):
            _raise_if_cancelled(cancel_callback)
            dirs[:] = sorted(name for name in dirs if not name.startswith(".aps_boot_")
                             and not os.path.islink(os.path.join(root, name))) if recursive else []
            paths.extend(os.path.join(root, name) for name in sorted(files)
                         if image_extension(name) in REPAIR_IMAGE_EXTENSIONS
                         and not name.startswith(".aps_boot_") and not os.path.islink(os.path.join(root, name)))
    else:
        paths = [source_path]
    results = []
    for index, path in enumerate(paths):
        if cancel_callback and cancel_callback():
            return ImageRepairBatch(tuple(results), cancelled=True)
        if progress_callback:
            progress_callback(index, len(paths), path)
        try:
            result = apply_boot_sector_repair(path, target_format=target_format, backup=backup, cancel_callback=cancel_callback)
        except FloppyOperationCancelled:
            return ImageRepairBatch(tuple(results), cancelled=True)
        except Exception as exc:
            result = ImageRepairResult(path, error=str(exc))
        results.append(result)
        if result_callback:
            result_callback(result)
    if progress_callback:
        progress_callback(len(paths), len(paths), "")
    return ImageRepairBatch(tuple(results))
