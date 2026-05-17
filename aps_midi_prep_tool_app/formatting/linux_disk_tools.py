"""Linux USB disk helpers used by the shared formatting core."""

import argparse
import json
import os
import shutil
import subprocess
import sys

from ..subprocess_utils import windows_subprocess_kwargs
from .usb_format_core import (
    FAT32_LAYOUT_LABELS,
    FAT32_LAYOUT_SUPERFLOPPY,
    UsbDriveInfo,
    UsbFormatError,
    UsbVolumeInfo,
    _MIN_USB_STICK_BYTES,
    _clean_mountpoints,
    _content_entries_for_mountpoints,
    _parse_bool,
    _parse_int,
    _raise_if_cancelled,
    create_usb_format_job,
    display_bytes,
    run_usb_format_job,
    usb_format_result_to_dict,
)


def list_removable_usb_drives():
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


def prepare_drive_for_format(drive_info, *, cancel_callback=None):
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


def raw_device_writer(device_path):
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
        offset = int(offset)
        length = len(data)
        try:
            self._file.seek(offset)
            self._file.write(data)
        except OSError as exc:
            raise UsbFormatError(
                f"Could not write {self.path} at offset {offset} length {length}: {exc}"
            ) from exc

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


def refresh_operating_system_disk_view(drive_info, _layout_kind, *, cancel_callback=None):
    _raise_if_cancelled(cancel_callback)
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


def _drive_to_dict(drive):
    return {
        "device_path": drive.device_path,
        "display_path": drive.display_path,
        "size_bytes": drive.size_bytes,
        "size": display_bytes(drive.size_bytes),
        "model": drive.model,
        "vendor": drive.vendor,
        "serial": drive.serial,
        "transport": drive.transport,
        "partition_style": drive.partition_style,
        "mountpoints": list(drive.mountpoints),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect or test Linux USB formatting jobs.")
    parser.add_argument("--list", action="store_true", help="List removable USB devices as JSON.")
    parser.add_argument("--device", help="Device path, such as /dev/sdX.")
    parser.add_argument("--layout", choices=sorted(FAT32_LAYOUT_LABELS), default=FAT32_LAYOUT_SUPERFLOPPY)
    parser.add_argument("--label", default="DISKLAV", help="FAT32 volume label.")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan without writing to the device.")
    args = parser.parse_args(argv)

    drives = list_removable_usb_drives()
    if args.list:
        json.dump([_drive_to_dict(drive) for drive in drives], sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if not args.device:
        parser.error("--device is required unless --list is used")
    drive = next((item for item in drives if item.device_path == args.device), None)
    if drive is None:
        raise UsbFormatError(f"{args.device} was not found in the removable USB device list.")

    job = create_usb_format_job(drive, args.layout, volume_label=args.label, source="linux-disk-tools")
    result = run_usb_format_job(job, dry_run=args.dry_run, preselected_drive=drive)
    json.dump(usb_format_result_to_dict(result), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
