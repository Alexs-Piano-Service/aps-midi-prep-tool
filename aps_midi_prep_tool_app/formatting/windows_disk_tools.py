"""Windows USB disk helpers used by the shared formatting core."""

import ctypes
import json
import shutil
import subprocess
from ctypes import wintypes

from ..subprocess_utils import windows_subprocess_kwargs
from .usb_format_core import (
    UsbDriveInfo,
    UsbFormatError,
    UsbVolumeInfo,
    _MIN_USB_STICK_BYTES,
    _content_entries_for_mountpoints,
    _parse_bool,
    _parse_int,
    _raise_if_cancelled,
    _run_command,
)


def _powershell_command():
    return shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def list_removable_usb_drives():
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


def prepare_drive_for_format(drive_info, *, cancel_callback=None):
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
            detail += "\n\nRun the APS USB format helper as administrator, then try the USB format again."
        raise UsbFormatError(detail) from exc


def raw_device_writer(device_path):
    return _WindowsPhysicalDriveWriter(device_path)


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


def refresh_operating_system_disk_view(_drive_info, _layout_kind, *, cancel_callback=None):
    _raise_if_cancelled(cancel_callback)
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
