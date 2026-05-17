import json
import math
import os
import random
import re
import subprocess
import threading
import time
from dataclasses import dataclass

from ..subprocess_utils import windows_subprocess_kwargs


class UsbFormatError(Exception):
    """Raised when a removable USB device cannot be inspected or formatted."""


class UsbFormatCancelled(UsbFormatError):
    """Raised when the user cancels a USB formatting operation."""


@dataclass(frozen=True)
class UsbContentEntry:
    name: str
    size_bytes: int = 0
    kind: str = "file"
    path: str = ""


@dataclass(frozen=True)
class UsbVolumeInfo:
    path: str = ""
    label: str = ""
    file_system: str = ""
    size_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    mountpoints: tuple[str, ...] = ()
    contents: tuple[UsbContentEntry, ...] = ()

    @property
    def display_name(self):
        parts = []
        if self.label:
            parts.append(self.label)
        if self.path:
            parts.append(self.path)
        if self.file_system:
            parts.append(self.file_system)
        mounted = ", ".join(mount for mount in self.mountpoints if mount)
        if mounted:
            parts.append(mounted)
        return " - ".join(parts) if parts else "Volume"


@dataclass(frozen=True)
class UsbDriveInfo:
    device_path: str
    display_path: str
    size_bytes: int
    model: str = ""
    vendor: str = ""
    serial: str = ""
    transport: str = ""
    partition_style: str = ""
    read_only: bool = False
    disk_number: int = -1
    volumes: tuple[UsbVolumeInfo, ...] = ()

    @property
    def mountpoints(self):
        mounts = []
        for volume in self.volumes:
            mounts.extend(mount for mount in volume.mountpoints if mount)
        return tuple(dict.fromkeys(mounts))

    @property
    def display_name(self):
        parts = [self.display_path or self.device_path]
        if self.size_bytes:
            parts.append(display_bytes(self.size_bytes))
        identity = " ".join(part for part in (self.vendor, self.model) if part).strip()
        if identity:
            parts.append(identity)
        if self.serial:
            parts.append(f"Serial: {self.serial}")
        if self.partition_style:
            parts.append(f"Layout: {self.partition_style}")
        return " - ".join(parts)


@dataclass(frozen=True)
class Fat32Layout:
    start_lba: int
    total_sectors: int
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    fat_count: int
    sectors_per_fat: int
    root_cluster: int
    cluster_count: int
    volume_id: int
    volume_label: str

    @property
    def fat_begin_lba(self):
        return self.start_lba + self.reserved_sectors

    @property
    def data_begin_lba(self):
        return self.fat_begin_lba + self.fat_count * self.sectors_per_fat

    @property
    def cluster_size(self):
        return self.bytes_per_sector * self.sectors_per_cluster

    @property
    def fat_size_bytes(self):
        return self.sectors_per_fat * self.bytes_per_sector


FAT32_LAYOUT_SUPERFLOPPY = "superfloppy"
FAT32_LAYOUT_MBR = "mbr"
FAT32_LAYOUT_LABELS = {
    FAT32_LAYOUT_SUPERFLOPPY: "FAT32 superfloppy (no partitions)",
    FAT32_LAYOUT_MBR: "MBR with one FAT32 partition",
}


@dataclass(frozen=True)
class UsbFormatJob:
    device_path: str
    layout_kind: str
    volume_label: str = "DISKLAV"
    display_path: str = ""
    size_bytes: int = 0
    model: str = ""
    vendor: str = ""
    serial: str = ""
    transport: str = ""
    partition_style: str = ""
    disk_number: int = -1
    source: str = "gui"
    requested_at: float = 0.0
    schema_version: int = 1

_BYTES_PER_SECTOR = 512
_FAT32_RESERVED_SECTORS = 32
_FAT32_FAT_COUNT = 2
_FAT32_ROOT_CLUSTER = 2
_FAT32_MIN_CLUSTERS = 65525
_FAT32_MAX_CLUSTERS = 0x0FFFFFF5
_FAT32_MAX_SECTORS = 0xFFFFFFFF
_MBR_PARTITION_START_LBA = 2048
_QUICK_WIPE_BYTES = 1024 * 1024
_MIN_USB_STICK_BYTES = 64 * 1024 * 1024


def display_bytes(size):
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def list_removable_usb_drives():
    if os.name == "nt":
        from .windows_disk_tools import list_removable_usb_drives as _list_drives
    else:
        from .linux_disk_tools import list_removable_usb_drives as _list_drives
    return _list_drives()


def _prepare_drive_for_format(drive_info, *, cancel_callback=None):
    if os.name == "nt":
        from .windows_disk_tools import prepare_drive_for_format
    else:
        from .linux_disk_tools import prepare_drive_for_format
    return prepare_drive_for_format(drive_info, cancel_callback=cancel_callback)


def _refresh_formatted_drive(drive_info, layout_kind, *, cancel_callback=None):
    if os.name == "nt":
        from .windows_disk_tools import refresh_operating_system_disk_view
    else:
        from .linux_disk_tools import refresh_operating_system_disk_view
    return refresh_operating_system_disk_view(drive_info, layout_kind, cancel_callback=cancel_callback)


def create_usb_format_job(drive_info, layout_kind, *, volume_label="DISKLAV", source="gui"):
    if not isinstance(drive_info, UsbDriveInfo):
        raise UsbFormatError("Invalid USB device selection.")
    if layout_kind not in FAT32_LAYOUT_LABELS:
        raise UsbFormatError("Invalid USB formatting layout.")
    return UsbFormatJob(
        device_path=drive_info.device_path,
        display_path=drive_info.display_path,
        size_bytes=int(drive_info.size_bytes or 0),
        model=drive_info.model,
        vendor=drive_info.vendor,
        serial=drive_info.serial,
        transport=drive_info.transport,
        partition_style=drive_info.partition_style,
        disk_number=int(drive_info.disk_number if drive_info.disk_number is not None else -1),
        layout_kind=layout_kind,
        volume_label=str(volume_label or "DISKLAV"),
        source=str(source or "gui"),
        requested_at=time.time(),
    )


def usb_format_job_to_dict(job):
    if not isinstance(job, UsbFormatJob):
        raise UsbFormatError("Invalid USB format job.")
    return {
        "schema_version": int(job.schema_version or 1),
        "source": str(job.source or ""),
        "requested_at": float(job.requested_at or 0.0),
        "device_path": str(job.device_path or ""),
        "display_path": str(job.display_path or ""),
        "size_bytes": int(job.size_bytes or 0),
        "model": str(job.model or ""),
        "vendor": str(job.vendor or ""),
        "serial": str(job.serial or ""),
        "transport": str(job.transport or ""),
        "partition_style": str(job.partition_style or ""),
        "disk_number": int(job.disk_number if job.disk_number is not None else -1),
        "layout_kind": str(job.layout_kind or ""),
        "volume_label": str(job.volume_label or ""),
    }


def usb_format_job_from_dict(payload):
    if isinstance(payload, UsbFormatJob):
        return payload
    if not isinstance(payload, dict):
        raise UsbFormatError("Invalid USB format job.")
    layout_kind = str(payload.get("layout_kind") or "").strip()
    if layout_kind not in FAT32_LAYOUT_LABELS:
        raise UsbFormatError("Invalid USB formatting layout.")
    device_path = str(payload.get("device_path") or "").strip()
    if not device_path:
        raise UsbFormatError("USB format job is missing a target device path.")
    return UsbFormatJob(
        device_path=device_path,
        display_path=str(payload.get("display_path") or "").strip(),
        size_bytes=_parse_int(payload.get("size_bytes"), 0),
        model=str(payload.get("model") or "").strip(),
        vendor=str(payload.get("vendor") or "").strip(),
        serial=str(payload.get("serial") or "").strip(),
        transport=str(payload.get("transport") or "").strip(),
        partition_style=str(payload.get("partition_style") or "").strip(),
        disk_number=_parse_int(payload.get("disk_number"), -1),
        layout_kind=layout_kind,
        volume_label=str(payload.get("volume_label") or "DISKLAV"),
        source=str(payload.get("source") or "helper").strip(),
        requested_at=_parse_float(payload.get("requested_at"), 0.0),
        schema_version=_parse_int(payload.get("schema_version"), 1),
    )


def write_usb_format_job(job, path):
    _write_json_file(path, usb_format_job_to_dict(job))


def read_usb_format_job(path):
    with open(path, "r", encoding="utf-8") as file:
        return usb_format_job_from_dict(json.load(file))


def usb_format_result_to_dict(result):
    payload = dict(result or {})
    payload["ok"] = bool(payload.get("ok", not payload.get("error")))
    payload["dry_run"] = bool(payload.get("dry_run", False))
    payload["cancelled"] = bool(payload.get("cancelled", False))
    if "plan" in payload and payload["plan"] is not None:
        payload["plan"] = [str(item) for item in payload["plan"]]
    return payload


def write_usb_format_result(result, path):
    _write_json_file(path, usb_format_result_to_dict(result))


def read_usb_format_result(path):
    with open(path, "r", encoding="utf-8") as file:
        return usb_format_result_to_dict(json.load(file))


def plan_usb_format_job(job, *, drive_info=None):
    job = usb_format_job_from_dict(job)
    drive = drive_info if isinstance(drive_info, UsbDriveInfo) else _drive_info_from_job(job)
    total_sectors = int(drive.size_bytes // _BYTES_PER_SECTOR)
    if total_sectors <= 0:
        raise UsbFormatError("The selected USB device did not report a usable size.")
    if total_sectors > _FAT32_MAX_SECTORS:
        raise UsbFormatError("FAT32 formatting is limited to devices up to 2 TB.")

    if job.layout_kind == FAT32_LAYOUT_MBR:
        if total_sectors <= _MBR_PARTITION_START_LBA + 1:
            raise UsbFormatError("The selected USB device is too small for a standard MBR partition.")
        start_lba = _MBR_PARTITION_START_LBA
        volume_sectors = total_sectors - start_lba
    else:
        start_lba = 0
        volume_sectors = total_sectors

    layout = _build_fat32_layout(volume_sectors, start_lba=start_lba, volume_label=job.volume_label)
    plan = [
        f"Verify removable USB target: {drive.display_name}",
        f"Clear existing disk headers on {drive.device_path}",
    ]
    if job.layout_kind == FAT32_LAYOUT_MBR:
        plan.append(f"Write one FAT32 MBR partition starting at sector {layout.start_lba}")
    else:
        plan.append("Write FAT32 directly to the device with no partition table")
    plan.extend(
        [
            f"Create FAT32 volume label {layout.volume_label.strip() or 'DISKLAV'}",
            f"Use {layout.sectors_per_cluster * layout.bytes_per_sector} byte clusters",
            "Refresh the operating system disk view",
        ]
    )
    return tuple(plan)


def run_usb_format_job(
    job,
    *,
    dry_run=False,
    progress_callback=None,
    cancel_callback=None,
    preselected_drive=None,
):
    job = usb_format_job_from_dict(job)
    _raise_if_cancelled(cancel_callback)

    if isinstance(preselected_drive, UsbDriveInfo) and _usb_drive_matches_job(preselected_drive, job):
        drive = preselected_drive
    elif dry_run:
        drive = _find_removable_usb_drive_for_job(job, allow_missing=True) or _drive_info_from_job(job)
    else:
        drive = _find_removable_usb_drive_for_job(job, allow_missing=False)

    if dry_run:
        plan = plan_usb_format_job(job, drive_info=drive)
        _notify_progress(progress_callback, 100, 100, "USB format dry run complete.")
        return {
            "ok": True,
            "dry_run": True,
            "device": drive.display_name,
            "layout": FAT32_LAYOUT_LABELS[job.layout_kind],
            "volume_label": _normalize_volume_label(job.volume_label).strip(),
            "size_bytes": drive.size_bytes,
            "plan": plan,
        }

    result = format_usb_drive(
        drive,
        job.layout_kind,
        volume_label=job.volume_label,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    result["ok"] = True
    result["dry_run"] = False
    return result


def format_usb_drive(
    drive_info,
    layout_kind,
    *,
    volume_label="DISKLAV",
    progress_callback=None,
    cancel_callback=None,
):
    if not isinstance(drive_info, UsbDriveInfo):
        raise UsbFormatError("Invalid USB device selection.")
    if drive_info.read_only:
        raise UsbFormatError("The selected USB device is read-only.")
    if layout_kind not in FAT32_LAYOUT_LABELS:
        raise UsbFormatError("Invalid USB formatting layout.")

    label = _normalize_volume_label(volume_label)
    total_sectors = int(drive_info.size_bytes // _BYTES_PER_SECTOR)
    if total_sectors <= 0:
        raise UsbFormatError("The selected USB device did not report a usable size.")
    if total_sectors > _FAT32_MAX_SECTORS:
        raise UsbFormatError("FAT32 formatting is limited to devices up to 2 TB.")

    _notify_progress(progress_callback, 0, 100, "Preparing USB stick...")
    _raise_if_cancelled(cancel_callback)

    _prepare_drive_for_format(drive_info, cancel_callback=cancel_callback)

    _raise_if_cancelled(cancel_callback)
    _notify_progress(progress_callback, 14, 100, "Building FAT32 layout...")
    if layout_kind == FAT32_LAYOUT_MBR:
        if total_sectors <= _MBR_PARTITION_START_LBA + 1:
            raise UsbFormatError("The selected USB device is too small for a standard MBR partition.")
        start_lba = _MBR_PARTITION_START_LBA
        volume_sectors = total_sectors - start_lba
    else:
        start_lba = 0
        volume_sectors = total_sectors

    layout = _build_fat32_layout(
        volume_sectors,
        start_lba=start_lba,
        volume_label=label,
    )

    _notify_progress(progress_callback, 18, 100, "Writing FAT32 structures...")
    with _raw_device_writer(drive_info.device_path) as writer:
        _quick_wipe_device(
            writer,
            drive_info.size_bytes,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            start_progress=18,
            end_progress=28,
        )
        if layout_kind == FAT32_LAYOUT_MBR:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 29, 100, "Writing MBR partition table...")
            writer.write_at(0, _build_mbr(layout.start_lba, layout.total_sectors))
        _write_fat32_filesystem(
            writer,
            layout,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            start_progress=30,
            end_progress=94,
        )
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 95, 100, "Finalizing USB format...")
        writer.flush()

    _refresh_formatted_drive(drive_info, layout_kind, cancel_callback=cancel_callback)
    _raise_if_cancelled(cancel_callback)
    _notify_progress(progress_callback, 100, 100, "USB format complete.")
    return {
        "device": drive_info.display_name,
        "layout": FAT32_LAYOUT_LABELS[layout_kind],
        "volume_label": label.strip(),
        "size_bytes": drive_info.size_bytes,
    }


def _parse_int(value, default=0):
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _parse_float(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _clean_mountpoints(value):
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return tuple(str(mount).strip() for mount in values if str(mount or "").strip())


def _content_entries_for_mountpoints(mountpoints, *, limit=14):
    entries = []
    for mountpoint in mountpoints:
        if len(entries) >= limit:
            break
        try:
            with os.scandir(mountpoint) as iterator:
                for entry in iterator:
                    if len(entries) >= limit:
                        break
                    try:
                        stat_result = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                        entries.append(
                            UsbContentEntry(
                                name=entry.name,
                                size_bytes=0 if is_dir else int(stat_result.st_size),
                                kind="folder" if is_dir else "file",
                                path=entry.path,
                            )
                        )
                    except OSError:
                        entries.append(UsbContentEntry(name=entry.name, kind="unreadable", path=entry.path))
        except OSError:
            continue
    return tuple(entries)


def _write_json_file(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write("\n")


def _drive_info_from_job(job):
    return UsbDriveInfo(
        device_path=job.device_path,
        display_path=job.display_path or job.device_path,
        size_bytes=int(job.size_bytes or 0),
        model=job.model,
        vendor=job.vendor,
        serial=job.serial,
        transport=job.transport,
        partition_style=job.partition_style,
        disk_number=int(job.disk_number if job.disk_number is not None else -1),
    )


def _normalize_match_text(value):
    return str(value or "").strip().casefold()


def _usb_drive_matches_job(drive_info, job):
    if not isinstance(drive_info, UsbDriveInfo):
        return False

    job_disk_number = int(job.disk_number if job.disk_number is not None else -1)
    drive_disk_number = int(drive_info.disk_number if drive_info.disk_number is not None else -1)
    if job_disk_number >= 0 and drive_disk_number >= 0:
        identity_matches = job_disk_number == drive_disk_number
    else:
        identity_matches = _normalize_match_text(drive_info.device_path) == _normalize_match_text(job.device_path)
    if not identity_matches:
        return False

    if job.size_bytes and drive_info.size_bytes and int(job.size_bytes) != int(drive_info.size_bytes):
        return False
    if job.serial and drive_info.serial and _normalize_match_text(job.serial) != _normalize_match_text(drive_info.serial):
        return False
    return True


def _find_removable_usb_drive_for_job(job, *, allow_missing=False):
    matches = [drive for drive in list_removable_usb_drives() if _usb_drive_matches_job(drive, job)]
    if matches:
        return matches[0]
    if allow_missing:
        return None
    target = job.display_path or job.device_path
    raise UsbFormatError(
        f"Could not re-detect the selected USB device ({target}). "
        "Unplug and reinsert the USB stick, then refresh the device list."
    )


def _raise_if_cancelled(cancel_callback=None):
    if cancel_callback is not None and cancel_callback():
        raise UsbFormatCancelled("Operation cancelled.")


def _notify_progress(progress_callback, step, total, message):
    if progress_callback is not None:
        progress_callback(int(step or 0), int(total or 0), str(message or ""))


def _terminate_process(process):
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            pass


def _run_command(args, error_prefix, *, input_text=None, cancel_callback=None):
    if cancel_callback is None:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            **windows_subprocess_kwargs(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise UsbFormatError(f"{error_prefix}: {detail}" if detail else f"{error_prefix}.")
        return (result.stdout or "") + (result.stderr or "")

    _raise_if_cancelled(cancel_callback)
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **windows_subprocess_kwargs(),
    )
    stdout = ""
    stderr = ""
    communicate_error = None

    def _communicate():
        nonlocal stdout, stderr, communicate_error
        try:
            stdout, stderr = process.communicate(input=input_text)
        except Exception as exc:
            communicate_error = exc

    communicator = threading.Thread(target=_communicate, daemon=True)
    communicator.start()
    try:
        while communicator.is_alive():
            _raise_if_cancelled(cancel_callback)
            communicator.join(timeout=0.1)
    except UsbFormatCancelled:
        _terminate_process(process)
        communicator.join(timeout=2)
        raise

    if communicate_error is not None:
        _terminate_process(process)
        raise communicate_error

    _raise_if_cancelled(cancel_callback)
    if process.returncode != 0:
        detail = (stderr or stdout or "").strip()
        raise UsbFormatError(f"{error_prefix}: {detail}" if detail else f"{error_prefix}.")
    return (stdout or "") + (stderr or "")


def _normalize_volume_label(label):
    text = str(label or "").upper()
    text = "".join(ch if ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$%'-`@{}~!#()&" else " " for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    return (text or "DISKLAV")[:11].ljust(11)


def _preferred_cluster_candidates(total_bytes):
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    if total_bytes <= 256 * mib:
        preferred = [1, 2, 4, 8, 16, 32, 64]
    elif total_bytes <= 8 * gib:
        preferred = [8, 4, 16, 2, 32, 1, 64]
    elif total_bytes <= 16 * gib:
        preferred = [16, 8, 32, 4, 64, 2, 1]
    else:
        preferred = [64, 32, 16, 8, 4, 2, 1]
    return preferred


def _build_fat32_layout(total_sectors, *, start_lba=0, volume_label="DISKLAV"):
    total_sectors = int(total_sectors)
    if total_sectors <= 0 or total_sectors > _FAT32_MAX_SECTORS:
        raise UsbFormatError("The selected USB device is outside the supported FAT32 size range.")

    total_bytes = total_sectors * _BYTES_PER_SECTOR
    last_error = ""
    for sectors_per_cluster in _preferred_cluster_candidates(total_bytes):
        sectors_per_fat = 1
        cluster_count = 0
        for _iteration in range(32):
            data_sectors = total_sectors - _FAT32_RESERVED_SECTORS - (_FAT32_FAT_COUNT * sectors_per_fat)
            if data_sectors <= 0:
                last_error = "not enough room for FAT32 metadata"
                break
            cluster_count = data_sectors // sectors_per_cluster
            required_sectors_per_fat = int(math.ceil(((cluster_count + 2) * 4) / _BYTES_PER_SECTOR))
            if required_sectors_per_fat <= sectors_per_fat:
                break
            sectors_per_fat = required_sectors_per_fat

        data_sectors = total_sectors - _FAT32_RESERVED_SECTORS - (_FAT32_FAT_COUNT * sectors_per_fat)
        cluster_count = data_sectors // sectors_per_cluster
        if _FAT32_MIN_CLUSTERS <= cluster_count < _FAT32_MAX_CLUSTERS:
            return Fat32Layout(
                start_lba=int(start_lba),
                total_sectors=total_sectors,
                bytes_per_sector=_BYTES_PER_SECTOR,
                sectors_per_cluster=sectors_per_cluster,
                reserved_sectors=_FAT32_RESERVED_SECTORS,
                fat_count=_FAT32_FAT_COUNT,
                sectors_per_fat=sectors_per_fat,
                root_cluster=_FAT32_ROOT_CLUSTER,
                cluster_count=cluster_count,
                volume_id=random.getrandbits(32),
                volume_label=_normalize_volume_label(volume_label),
            )
        last_error = f"{cluster_count} clusters with {sectors_per_cluster * _BYTES_PER_SECTOR} byte clusters"

    raise UsbFormatError(
        "The selected USB device is too small or too unusual for a compatible FAT32 layout"
        + (f" ({last_error})." if last_error else ".")
    )


def _build_fat32_boot_sector(layout):
    boot = bytearray(_BYTES_PER_SECTOR)
    boot[0:3] = b"\xEB\x58\x90"
    boot[3:11] = b"MSWIN4.1"
    boot[11:13] = layout.bytes_per_sector.to_bytes(2, "little")
    boot[13] = layout.sectors_per_cluster
    boot[14:16] = layout.reserved_sectors.to_bytes(2, "little")
    boot[16] = layout.fat_count
    boot[17:19] = (0).to_bytes(2, "little")
    boot[19:21] = (0).to_bytes(2, "little")
    boot[21] = 0xF8
    boot[22:24] = (0).to_bytes(2, "little")
    boot[24:26] = (63).to_bytes(2, "little")
    boot[26:28] = (255).to_bytes(2, "little")
    boot[28:32] = layout.start_lba.to_bytes(4, "little")
    boot[32:36] = layout.total_sectors.to_bytes(4, "little")
    boot[36:40] = layout.sectors_per_fat.to_bytes(4, "little")
    boot[40:42] = (0).to_bytes(2, "little")
    boot[42:44] = (0).to_bytes(2, "little")
    boot[44:48] = layout.root_cluster.to_bytes(4, "little")
    boot[48:50] = (1).to_bytes(2, "little")
    boot[50:52] = (6).to_bytes(2, "little")
    boot[64] = 0x80
    boot[66] = 0x29
    boot[67:71] = layout.volume_id.to_bytes(4, "little")
    boot[71:82] = layout.volume_label.encode("ascii", errors="replace")[:11].ljust(11, b" ")
    boot[82:90] = b"FAT32   "
    boot[510:512] = b"\x55\xAA"
    return bytes(boot)


def _build_fsinfo_sector(layout):
    fsinfo = bytearray(_BYTES_PER_SECTOR)
    fsinfo[0:4] = (0x41615252).to_bytes(4, "little")
    fsinfo[484:488] = (0x61417272).to_bytes(4, "little")
    free_clusters = max(0, layout.cluster_count - 1)
    fsinfo[488:492] = free_clusters.to_bytes(4, "little")
    fsinfo[492:496] = (3).to_bytes(4, "little")
    fsinfo[508:512] = b"\x00\x00\x55\xAA"
    return bytes(fsinfo)


def _build_root_directory_label_entry(layout):
    entry = bytearray(32)
    entry[0:11] = layout.volume_label.encode("ascii", errors="replace")[:11].ljust(11, b" ")
    entry[11] = 0x08
    return bytes(entry)


def _build_root_directory_label_sector(layout):
    sector = bytearray(layout.bytes_per_sector)
    sector[0:32] = _build_root_directory_label_entry(layout)
    return bytes(sector)


def _build_mbr(start_lba, sector_count):
    if start_lba < 1 or sector_count <= 0 or start_lba > 0xFFFFFFFF or sector_count > 0xFFFFFFFF:
        raise UsbFormatError("The FAT32 partition is outside the supported MBR size range.")
    mbr = bytearray(_BYTES_PER_SECTOR)
    entry = bytearray(16)
    entry[0] = 0x00
    entry[1:4] = b"\x00\x02\x00"
    entry[4] = 0x0C
    entry[5:8] = b"\xFE\xFF\xFF"
    entry[8:12] = int(start_lba).to_bytes(4, "little")
    entry[12:16] = int(sector_count).to_bytes(4, "little")
    mbr[446:462] = entry
    mbr[510:512] = b"\x55\xAA"
    return bytes(mbr)


def _write_fat32_filesystem(
    writer,
    layout,
    *,
    progress_callback=None,
    cancel_callback=None,
    start_progress=0,
    end_progress=100,
):
    base_offset = layout.start_lba * layout.bytes_per_sector
    reserved_size = layout.reserved_sectors * layout.bytes_per_sector
    _write_zeroes(
        writer,
        base_offset,
        reserved_size,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        start_progress=start_progress,
        end_progress=start_progress + 8,
        message="Clearing FAT32 reserved sectors...",
    )

    boot = _build_fat32_boot_sector(layout)
    fsinfo = _build_fsinfo_sector(layout)
    writer.write_at(base_offset, boot)
    writer.write_at(base_offset + layout.bytes_per_sector, fsinfo)
    writer.write_at(base_offset + 6 * layout.bytes_per_sector, boot)
    writer.write_at(base_offset + 7 * layout.bytes_per_sector, fsinfo)

    fat_start_progress = start_progress + 8
    fat_end_progress = end_progress - 8
    first_fat_sector = bytearray(layout.bytes_per_sector)
    first_fat_sector[0:4] = (0x0FFFFFF8).to_bytes(4, "little")
    first_fat_sector[4:8] = (0xFFFFFFFF).to_bytes(4, "little")
    first_fat_sector[8:12] = (0x0FFFFFFF).to_bytes(4, "little")
    for fat_index in range(layout.fat_count):
        fat_offset = (layout.fat_begin_lba + fat_index * layout.sectors_per_fat) * layout.bytes_per_sector
        span_start = fat_start_progress + int(((fat_end_progress - fat_start_progress) / layout.fat_count) * fat_index)
        span_end = fat_start_progress + int(((fat_end_progress - fat_start_progress) / layout.fat_count) * (fat_index + 1))
        _write_zeroes(
            writer,
            fat_offset,
            layout.fat_size_bytes,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            start_progress=span_start,
            end_progress=span_end,
            message=f"Clearing FAT {fat_index + 1} of {layout.fat_count}...",
        )
        writer.write_at(fat_offset, bytes(first_fat_sector))

    root_offset = layout.data_begin_lba * layout.bytes_per_sector
    _write_zeroes(
        writer,
        root_offset,
        layout.cluster_size,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        start_progress=end_progress - 8,
        end_progress=end_progress,
        message="Clearing root directory...",
    )
    writer.write_at(root_offset, _build_root_directory_label_sector(layout))


def _quick_wipe_device(writer, size_bytes, *, progress_callback=None, cancel_callback=None, start_progress=0, end_progress=100):
    wipe_size = min(_QUICK_WIPE_BYTES, max(0, int(size_bytes)))
    if wipe_size <= 0:
        return
    midpoint = start_progress + ((end_progress - start_progress) // 2)
    _write_zeroes(
        writer,
        0,
        wipe_size,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        start_progress=start_progress,
        end_progress=midpoint,
        message="Clearing existing disk headers...",
    )
    if size_bytes > wipe_size:
        _write_zeroes(
            writer,
            int(size_bytes) - wipe_size,
            wipe_size,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            start_progress=midpoint,
            end_progress=end_progress,
            message="Clearing backup partition headers...",
        )


def _write_zeroes(
    writer,
    offset,
    length,
    *,
    progress_callback=None,
    cancel_callback=None,
    start_progress=0,
    end_progress=100,
    message="Writing...",
):
    length = int(length or 0)
    if length <= 0:
        return
    chunk_size = 1024 * 1024
    zero_chunk = b"\x00" * chunk_size
    written = 0
    while written < length:
        _raise_if_cancelled(cancel_callback)
        current = min(chunk_size, length - written)
        writer.write_at(offset + written, zero_chunk[:current])
        written += current
        if progress_callback is not None:
            fraction = written / length
            step = start_progress + int((end_progress - start_progress) * fraction)
            _notify_progress(progress_callback, step, 100, message)


def _raw_device_writer(device_path):
    if os.name == "nt":
        from .windows_disk_tools import raw_device_writer
    else:
        from .linux_disk_tools import raw_device_writer
    return raw_device_writer(device_path)
