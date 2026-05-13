import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from .subprocess_utils import windows_subprocess_kwargs


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
        return _list_windows_removable_usb_drives()
    return _list_linux_removable_usb_drives()


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

    if os.name == "nt":
        _windows_clear_disk(drive_info, cancel_callback=cancel_callback)
    else:
        _linux_unmount_drive(drive_info, cancel_callback=cancel_callback)

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

    _refresh_operating_system_disk_view(drive_info, layout_kind, cancel_callback=cancel_callback)
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


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


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
    try:
        if input_text is not None:
            process.stdin.write(input_text)
            process.stdin.close()
            process.stdin = None
        while process.poll() is None:
            _raise_if_cancelled(cancel_callback)
            time.sleep(0.1)
        stdout, stderr = process.communicate()
    except UsbFormatCancelled:
        _terminate_process(process)
        raise

    _raise_if_cancelled(cancel_callback)
    if process.returncode != 0:
        detail = (stderr or stdout or "").strip()
        raise UsbFormatError(f"{error_prefix}: {detail}" if detail else f"{error_prefix}.")
    return (stdout or "") + (stderr or "")


def _list_linux_removable_usb_drives():
    lsblk = shutil.which("lsblk")
    if not lsblk:
        return []

    result = subprocess.run(
        [
            lsblk,
            "-J",
            "-b",
            "-o",
            "NAME,PATH,SIZE,RM,HOTPLUG,RO,TYPE,TRAN,MOUNTPOINTS,FSTYPE,LABEL,MODEL,VENDOR,SERIAL,FSUSED,FSAVAIL",
        ],
        text=True,
        capture_output=True,
        check=False,
        **windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return []

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return []

    drives = []
    for device in payload.get("blockdevices", []):
        if (device.get("type") or "").lower() != "disk":
            continue
        if _parse_bool(device.get("ro")):
            continue
        path = (device.get("path") or "").strip()
        size_bytes = _parse_int(device.get("size"), 0)
        if not path or size_bytes < _MIN_USB_STICK_BYTES:
            continue
        transport = (device.get("tran") or "").strip().lower()
        removable = _parse_bool(device.get("rm"))
        if not removable:
            continue

        volumes = tuple(_linux_volume_from_lsblk_child(child) for child in (device.get("children") or []))
        volumes = tuple(volume for volume in volumes if volume is not None)
        if not volumes and ((device.get("fstype") or "").strip() or _clean_mountpoints(device.get("mountpoints"))):
            disk_volume = _linux_volume_from_lsblk_child(device)
            volumes = (disk_volume,) if disk_volume is not None else ()
        if any(mount == "/" for volume in volumes for mount in volume.mountpoints):
            continue

        drives.append(
            UsbDriveInfo(
                device_path=path,
                display_path=path,
                size_bytes=size_bytes,
                model=(device.get("model") or "").strip(),
                vendor=(device.get("vendor") or "").strip(),
                serial=(device.get("serial") or "").strip(),
                transport=transport or "removable",
                partition_style=_linux_partition_style(device, volumes),
                read_only=False,
                volumes=volumes,
            )
        )

    drives.sort(key=lambda item: (item.display_path, item.size_bytes))
    return drives


def _linux_partition_style(device, volumes):
    children = device.get("children") or []
    if not children:
        fs_type = (device.get("fstype") or "").strip()
        return f"superfloppy {fs_type}".strip() if fs_type else "no partitions"
    if len(volumes) == 1:
        return "single partition"
    return f"{len(volumes)} partitions"


def _linux_volume_from_lsblk_child(child):
    child_type = (child.get("type") or "").lower()
    if child_type not in {"part", "disk"}:
        return None
    mountpoints = _clean_mountpoints(child.get("mountpoints"))
    size_bytes = _parse_int(child.get("size"), 0)
    free_bytes = _parse_int(child.get("fsavail"), 0)
    used_bytes = _parse_int(child.get("fsused"), 0)
    if used_bytes <= 0 and free_bytes > 0 and size_bytes >= free_bytes:
        used_bytes = size_bytes - free_bytes
    contents = _content_entries_for_mountpoints(mountpoints)
    return UsbVolumeInfo(
        path=(child.get("path") or "").strip(),
        label=(child.get("label") or "").strip(),
        file_system=(child.get("fstype") or "").strip(),
        size_bytes=size_bytes,
        used_bytes=used_bytes,
        free_bytes=free_bytes,
        mountpoints=mountpoints,
        contents=contents,
    )


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


def _powershell_command():
    return shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _list_windows_removable_usb_drives():
    powershell = _powershell_command()
    if not powershell:
        return []

    script = r"""
$ErrorActionPreference = "SilentlyContinue"
$diskDrives = Get-CimInstance Win32_DiskDrive |
    Where-Object { $_.MediaType -match "Removable" }
$items = foreach ($drive in $diskDrives) {
    $number = [int]$drive.Index
    $disk = Get-Disk -Number $number -ErrorAction SilentlyContinue
    if ($disk -and ($disk.IsBoot -or $disk.IsSystem -or $disk.IsReadOnly)) { continue }
    $parts = @(Get-Partition -DiskNumber $number -ErrorAction SilentlyContinue | ForEach-Object {
        $partition = $_
        $volume = $partition | Get-Volume -ErrorAction SilentlyContinue
        [pscustomobject]@{
            PartitionNumber = $partition.PartitionNumber
            DriveLetter = $partition.DriveLetter
            Size = [int64]$partition.Size
            Type = [string]$partition.Type
            FileSystem = [string]$volume.FileSystem
            FileSystemLabel = [string]$volume.FileSystemLabel
            VolumeSize = [int64]$volume.Size
            SizeRemaining = [int64]$volume.SizeRemaining
            AccessPaths = @($partition.AccessPaths)
        }
    })
    [pscustomobject]@{
        Number = $number
        DeviceID = [string]$drive.DeviceID
        Model = [string]$drive.Model
        Serial = [string]$drive.SerialNumber
        Size = [int64]$drive.Size
        BusType = if ($disk) { [string]$disk.BusType } else { [string]$drive.InterfaceType }
        PartitionStyle = if ($disk) { [string]$disk.PartitionStyle } else { "" }
        IsReadOnly = if ($disk) { [bool]$disk.IsReadOnly } else { $false }
        Partitions = $parts
    }
}
@($items) | ConvertTo-Json -Depth 6
"""
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        text=True,
        capture_output=True,
        check=False,
        **windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]

    drives = []
    for item in payload or []:
        number = _parse_int(item.get("Number"), -1)
        size_bytes = _parse_int(item.get("Size"), 0)
        if number < 0 or size_bytes < _MIN_USB_STICK_BYTES:
            continue
        volumes = tuple(_windows_volume_from_partition(part) for part in (item.get("Partitions") or []))
        volumes = tuple(volume for volume in volumes if volume is not None)
        drives.append(
            UsbDriveInfo(
                device_path=fr"\\.\PhysicalDrive{number}",
                display_path=f"Disk {number}",
                size_bytes=size_bytes,
                model=(item.get("Model") or "").strip(),
                serial=(item.get("Serial") or "").strip(),
                transport=(item.get("BusType") or "USB").strip(),
                partition_style=(item.get("PartitionStyle") or "").strip(),
                read_only=_parse_bool(item.get("IsReadOnly")),
                disk_number=number,
                volumes=volumes,
            )
        )

    drives.sort(key=lambda item: (item.disk_number, item.display_name))
    return drives


def _windows_volume_from_partition(partition):
    drive_letter = str(partition.get("DriveLetter") or "").strip()
    mountpoints = []
    if drive_letter:
        mountpoints.append(f"{drive_letter.upper()}:\\")
    for path in partition.get("AccessPaths") or []:
        text = str(path or "").strip()
        if text and text not in mountpoints:
            mountpoints.append(text)
    size_bytes = _parse_int(partition.get("VolumeSize"), 0) or _parse_int(partition.get("Size"), 0)
    free_bytes = _parse_int(partition.get("SizeRemaining"), 0)
    used_bytes = max(0, size_bytes - free_bytes) if free_bytes else 0
    contents = _content_entries_for_mountpoints(tuple(mountpoints))
    return UsbVolumeInfo(
        path=f"Partition {partition.get('PartitionNumber') or ''}".strip(),
        label=(partition.get("FileSystemLabel") or "").strip(),
        file_system=(partition.get("FileSystem") or partition.get("Type") or "").strip(),
        size_bytes=size_bytes,
        used_bytes=used_bytes,
        free_bytes=free_bytes,
        mountpoints=tuple(mountpoints),
        contents=contents,
    )


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
    writer.write_at(root_offset, _build_root_directory_label_entry(layout))


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
        return _WindowsPhysicalDriveWriter(device_path)
    return _PosixRawDeviceWriter(device_path)


class _PosixRawDeviceWriter:
    def __init__(self, path):
        self.path = path
        self._file = None

    def __enter__(self):
        if os.name == "posix" and not os.access(self.path, os.W_OK):
            raise UsbFormatError(
                f"Could not write {self.path}: permission denied.\n\n"
                "Formatting USB sticks usually requires root permission or block-device write access."
            )
        try:
            self._file = open(self.path, "r+b", buffering=0)
        except OSError as exc:
            raise UsbFormatError(f"Could not open {self.path} for writing: {exc}") from exc
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def write_at(self, offset, data):
        try:
            self._file.seek(int(offset))
            self._file.write(data)
        except OSError as exc:
            raise UsbFormatError(f"Could not write {self.path}: {exc}") from exc

    def flush(self):
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError as exc:
            raise UsbFormatError(f"Could not finalize {self.path}: {exc}") from exc

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None


class _WindowsPhysicalDriveWriter:
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_BEGIN = 0
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self, path):
        self.path = path
        self.handle = None
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()

    def _configure_api(self):
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        self.kernel32.SetFilePointerEx.restype = wintypes.BOOL
        self.kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.WriteFile.restype = wintypes.BOOL
        self.kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        self.kernel32.FlushFileBuffers.restype = wintypes.BOOL

    def __enter__(self):
        self.handle = self.kernel32.CreateFileW(
            self.path,
            self.GENERIC_READ | self.GENERIC_WRITE,
            self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
            None,
            self.OPEN_EXISTING,
            self.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if self.handle == self.INVALID_HANDLE_VALUE:
            raise UsbFormatError(_windows_last_error_message(f"Could not open {self.path} for writing"))
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def _seek(self, offset):
        new_pos = ctypes.c_longlong()
        ok = self.kernel32.SetFilePointerEx(
            self.handle,
            int(offset),
            ctypes.byref(new_pos),
            self.FILE_BEGIN,
        )
        if not ok:
            raise UsbFormatError(_windows_last_error_message(f"Could not seek {self.path}"))

    def write_at(self, offset, data):
        self._seek(offset)
        buffer = ctypes.create_string_buffer(data)
        written = wintypes.DWORD()
        ok = self.kernel32.WriteFile(
            self.handle,
            buffer,
            len(data),
            ctypes.byref(written),
            None,
        )
        if not ok or int(written.value) != len(data):
            raise UsbFormatError(_windows_last_error_message(f"Could not write {self.path}"))

    def flush(self):
        if not self.kernel32.FlushFileBuffers(self.handle):
            raise UsbFormatError(_windows_last_error_message(f"Could not finalize {self.path}"))

    def close(self):
        if self.handle and self.handle != self.INVALID_HANDLE_VALUE:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def _windows_last_error_message(prefix):
    error_code = ctypes.get_last_error()
    if error_code:
        return f"{prefix}: {ctypes.FormatError(error_code).strip()}"
    return f"{prefix}."


def _linux_unmount_drive(drive_info, *, cancel_callback=None):
    mount_targets = []
    for volume in drive_info.volumes:
        for mountpoint in volume.mountpoints:
            if mountpoint and all(existing_mount != mountpoint for existing_mount, _path in mount_targets):
                mount_targets.append((mountpoint, volume.path or drive_info.device_path))
    if not mount_targets:
        return

    for mountpoint, block_path in sorted(mount_targets, key=lambda item: len(item[0]), reverse=True):
        _raise_if_cancelled(cancel_callback)
        umount = shutil.which("umount")
        if umount:
            result = subprocess.run(
                [umount, mountpoint],
                text=True,
                capture_output=True,
                check=False,
                **windows_subprocess_kwargs(),
            )
            if result.returncode == 0:
                continue
            detail = (result.stderr or result.stdout or "").strip()
        else:
            detail = "umount command was not found"

        udisksctl = shutil.which("udisksctl")
        if udisksctl and block_path:
            result = subprocess.run(
                [udisksctl, "unmount", "-b", block_path],
                text=True,
                capture_output=True,
                check=False,
                **windows_subprocess_kwargs(),
            )
            if result.returncode == 0:
                continue
            detail = (result.stderr or result.stdout or detail).strip()

        raise UsbFormatError(
            f"Could not unmount {mountpoint}: {detail}\n\n"
            "Close files or file-manager windows using the USB stick, unmount it, and try again."
        )


def _windows_clear_disk(drive_info, *, cancel_callback=None):
    if drive_info.disk_number < 0:
        raise UsbFormatError("The selected Windows disk number is not available.")
    diskpart = shutil.which("diskpart.exe") or shutil.which("diskpart")
    if not diskpart:
        raise UsbFormatError("Windows diskpart was not found.")
    script = (
        f"select disk {drive_info.disk_number}\n"
        "online disk noerr\n"
        "attributes disk clear readonly noerr\n"
        "clean\n"
        "exit\n"
    )
    try:
        _run_command(
            [diskpart],
            "Could not clear the selected USB disk",
            input_text=script,
            cancel_callback=cancel_callback,
        )
    except UsbFormatError as exc:
        detail = str(exc)
        if "Access is denied" in detail or "denied" in detail.lower() or "administrator" not in detail.lower():
            detail += "\n\nRun APS MIDI Prep Tool as administrator, then try the USB format again."
        raise UsbFormatError(detail) from exc


def _refresh_operating_system_disk_view(drive_info, layout_kind, *, cancel_callback=None):
    _raise_if_cancelled(cancel_callback)
    if os.name == "nt":
        powershell = _powershell_command()
        if powershell:
            subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Update-HostStorageCache"],
                text=True,
                capture_output=True,
                check=False,
                **windows_subprocess_kwargs(),
            )
        diskpart = shutil.which("diskpart.exe") or shutil.which("diskpart")
        if diskpart:
            subprocess.run(
                [diskpart],
                input="rescan\nexit\n",
                text=True,
                capture_output=True,
                check=False,
                **windows_subprocess_kwargs(),
            )
        return

    sync = shutil.which("sync")
    if sync:
        subprocess.run([sync], text=True, capture_output=True, check=False, **windows_subprocess_kwargs())
    blockdev = shutil.which("blockdev")
    if blockdev:
        subprocess.run(
            [blockdev, "--rereadpt", drive_info.device_path],
            text=True,
            capture_output=True,
            check=False,
            **windows_subprocess_kwargs(),
        )
    partprobe = shutil.which("partprobe")
    if partprobe:
        subprocess.run(
            [partprobe, drive_info.device_path],
            text=True,
            capture_output=True,
            check=False,
            **windows_subprocess_kwargs(),
        )
    udevadm = shutil.which("udevadm")
    if udevadm:
        subprocess.run(
            [udevadm, "settle"],
            text=True,
            capture_output=True,
            check=False,
            **windows_subprocess_kwargs(),
        )
