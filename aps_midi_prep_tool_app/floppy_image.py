import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zlib
from dataclasses import dataclass

from .eseq_pianodir import (
    PIANODIR_FILENAME,
    PIANODIR_HEADER,
    PIANODIR_TARGET_FILE_SIZE,
    PIANODIR_TRACK_SIZE,
    PianodirTrackEntry,
    build_pianodir_bytes,
    build_eseq_order_key_from_path,
    is_pianodir_path,
    read_eseq_order_key_from_file,
    update_eseq_order_key,
)
from .eseq_converter import is_eseq_file
from .midi_metadata import (
    extract_eseq_title_from_file,
    has_eseq_title_metadata,
    is_midi_file,
    update_eseq_title_to_path,
    update_midi_title_to_path,
)


class FloppyImageError(Exception):
    """Raised when a floppy image cannot be loaded or edited."""


def _host_file_is_eseq(path):
    return os.path.isfile(path) and is_eseq_file(path) and has_eseq_title_metadata(path)


@dataclass(frozen=True)
class DiskFormat:
    key: str
    label: str
    size_bytes: int


@dataclass(frozen=True)
class ImageEntry:
    path: str
    size: int
    packed_size: int
    attributes: str = ""

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def directory(self):
        return os.path.dirname(self.path).replace("\\", "/")


@dataclass(frozen=True)
class ImageListing:
    entries: list[ImageEntry]
    free_space: int
    cluster_size: int


@dataclass(frozen=True)
class YamahaRepairResult:
    note: str
    changed: bool


@dataclass(frozen=True)
class Fat12Geometry:
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entries: int
    total_sectors: int
    sectors_per_fat: int

    @property
    def root_dir_sectors(self):
        return int(math.ceil((self.root_entries * 32) / self.bytes_per_sector))

    @property
    def fat_offset(self):
        return self.reserved_sectors * self.bytes_per_sector

    @property
    def fat_size(self):
        return self.sectors_per_fat * self.bytes_per_sector

    @property
    def fat_area_size(self):
        return self.num_fats * self.fat_size

    @property
    def root_offset(self):
        return (self.reserved_sectors + self.num_fats * self.sectors_per_fat) * self.bytes_per_sector

    @property
    def root_size(self):
        return self.root_dir_sectors * self.bytes_per_sector

    @property
    def data_offset(self):
        return self.root_offset + self.root_size

    @property
    def cluster_size(self):
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def total_size(self):
        return self.total_sectors * self.bytes_per_sector


@dataclass(frozen=True)
class FloppyDriveInfo:
    path: str
    size_bytes: int
    transport: str = ""
    model: str = ""
    label: str = ""
    mountpoints: tuple[str, ...] = ()

    @property
    def disk_format(self):
        return DISK_FORMAT_BY_SIZE.get(self.size_bytes)

    @property
    def display_name(self):
        parts = [self.path]
        if self.disk_format is not None:
            parts.append(self.disk_format.label)
        else:
            parts.append(display_bytes(self.size_bytes))
        if self.transport:
            parts.append(self.transport.upper())
        if self.model:
            parts.append(self.model.strip())
        if self.label:
            parts.append(f"Label: {self.label.strip()}")
        mounted = [mount for mount in self.mountpoints if mount]
        if mounted:
            parts.append(f"Mounted: {', '.join(mounted)}")
        return " - ".join(parts)


@dataclass(frozen=True)
class GreaseweazleDeviceInfo:
    path: str
    label: str = ""

    @property
    def display_name(self):
        if self.label:
            return f"{self.label} - {self.path}"
        return self.path


@dataclass(frozen=True)
class GreaseweazleFloppySource:
    device_path: str
    drive: str
    disk_format: DiskFormat
    archival_quality: bool = False
    revs: int = 0
    retries: int = 0

    @property
    def display_name(self):
        detail = self.disk_format.label
        if self.archival_quality:
            detail += ", archival SCP"
        extras = []
        if self.revs > 0:
            extras.append(f"{self.revs} revs")
        if self.retries > 0:
            extras.append(f"{self.retries} retries")
        if extras:
            detail += ", " + ", ".join(extras)
        return f"Greaseweazle {self.drive} on {self.device_path} ({detail})"


DISK_FORMATS = [
    DiskFormat("ibm.720", "IBM 720K DD", 737280),
    DiskFormat("ibm.1440", "IBM 1.44M HD", 1474560),
    DiskFormat("ibm.1200", "IBM 1.2M HD", 1228800),
    DiskFormat("ibm.360", "IBM 360K DD", 368640),
    DiskFormat("ibm.320", "IBM 320K DD", 327680),
    DiskFormat("ibm.180", "IBM 180K", 184320),
    DiskFormat("ibm.160", "IBM 160K", 163840),
    DiskFormat("ibm.2880", "IBM 2.88M ED", 2949120),
]

DISK_FORMAT_BY_SIZE = {fmt.size_bytes: fmt for fmt in DISK_FORMATS}

RAW_IMAGE_EXTENSIONS = {"bin", "img", "ima"}

SUPPORTED_IMAGE_EXTENSIONS = {
    "a2r",
    "adf",
    "ads",
    "adm",
    "adl",
    "bin",
    "ctr",
    "d1m",
    "d2m",
    "d4m",
    "d64",
    "d71",
    "d81",
    "d88",
    "dcp",
    "dim",
    "dmk",
    "do",
    "dsd",
    "dsk",
    "edsk",
    "fd",
    "fdi",
    "hdm",
    "hfe",
    "ima",
    "img",
    "imd",
    "ipf",
    "mgt",
    "msa",
    "nfd",
    "nsi",
    "po",
    "raw",
    "sf7",
    "scp",
    "ssd",
    "st",
    "td0",
    "xdf",
}

PREFERRED_OUTPUT_EXTENSIONS = [
    ("bin", "BIN (PPFBU) raw sector image"),
    ("img", "IMG (Gotek) raw sector image"),
    ("hfe", "HFE (Nalbantov) image"),
    ("ima", "IMA raw sector image"),
    ("dsk", "DSK image"),
    ("st", "ST image"),
    ("adf", "ADF image"),
    ("adm", "ADM image"),
    ("adl", "ADL image"),
    ("ads", "ADS image"),
    ("d1m", "D1M image"),
    ("d2m", "D2M image"),
    ("d4m", "D4M image"),
    ("d88", "D88 image"),
    ("dim", "DIM image"),
    ("dmk", "DMK image"),
    ("do", "DO image"),
    ("dsd", "DSD image"),
    ("edsk", "EDSK image"),
    ("fdi", "FDI image"),
    ("hdm", "HDM image"),
    ("fd", "FD image"),
    ("imd", "IMD image"),
    ("mgt", "MGT image"),
    ("msa", "MSA image"),
    ("nfd", "NFD image"),
    ("nsi", "NSI image"),
    ("po", "PO image"),
    ("raw", "RAW image"),
    ("sf7", "SF7 image"),
    ("scp", "SCP image"),
    ("ssd", "SSD image"),
    ("td0", "TD0 image"),
    ("xdf", "XDF image"),
]

MFORMAT_SIZE_OPTIONS = {
    "ibm.160": ("-f", "160"),
    "ibm.180": ("-f", "180"),
    "ibm.320": ("-f", "320"),
    "ibm.360": ("-f", "360"),
    "ibm.720": ("-f", "720"),
    "ibm.1200": ("-f", "1200"),
    "ibm.1440": ("-f", "1440"),
    "ibm.2880": ("-f", "2880"),
}

_YAMAHA_BYTES_PER_SECTOR = 512
_YAMAHA_SECTORS_PER_CLUSTER = 2
_YAMAHA_RESERVED_SECTORS = 1
_YAMAHA_NUM_FATS = 2
_YAMAHA_ROOT_ENTRIES = 112
_YAMAHA_TOTAL_SECTORS = 1440
_YAMAHA_MEDIA_DESCRIPTOR = 0xF9
_YAMAHA_SECTORS_PER_FAT = 3
_YAMAHA_SECTORS_PER_TRACK = 9
_YAMAHA_NUM_HEADS = 2
_YAMAHA_TOTAL_SIZE = _YAMAHA_TOTAL_SECTORS * _YAMAHA_BYTES_PER_SECTOR
_YAMAHA_ROOT_DIR_SECTORS = 7
_YAMAHA_BOOT_SIGNATURE = b"\x55\xAA"
_YAMAHA_FAT_SIGNATURE = b"\xF9\xFF\xFF"
_PROTECTED_FAT12_LAYOUTS = (
    {
        "label": "IBM 720K DD",
        "bytes_per_sector": 512,
        "sectors_per_cluster": 2,
        "reserved_sectors": 1,
        "num_fats": 2,
        "root_entries": 112,
        "total_sectors": 1440,
        "media_descriptor": 0xF9,
        "sectors_per_fat": 3,
        "sectors_per_track": 9,
        "num_heads": 2,
    },
    {
        "label": "IBM 1.44M HD",
        "bytes_per_sector": 512,
        "sectors_per_cluster": 1,
        "reserved_sectors": 1,
        "num_fats": 2,
        "root_entries": 224,
        "total_sectors": 2880,
        "media_descriptor": 0xF0,
        "sectors_per_fat": 9,
        "sectors_per_track": 18,
        "num_heads": 2,
    },
)


def is_supported_image_path(file_path):
    return image_extension(file_path) in SUPPORTED_IMAGE_EXTENSIONS


def image_extension(file_path):
    return os.path.splitext(file_path)[1].lower().lstrip(".")


def display_bytes(size):
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def allocated_size(size, cluster_size):
    cluster = max(1, int(cluster_size or 1))
    if size <= 0:
        return 0
    return int(math.ceil(size / cluster) * cluster)


def output_filters(default_ext):
    ordered = []
    seen = set()

    if default_ext:
        for ext, label in PREFERRED_OUTPUT_EXTENSIONS:
            if ext == default_ext:
                ordered.append((ext, label))
                seen.add(ext)
                break

    for ext, label in PREFERRED_OUTPUT_EXTENSIONS:
        if ext not in seen:
            ordered.append((ext, label))
            seen.add(ext)

    filters = [f"{label} (*.{ext})" for ext, label in ordered]
    return ";;".join(filters), ordered[0][0] if ordered else "img"


def _volume_label_for_mformat(label):
    return _normalize_label((label or "NO NAME").encode("ascii", errors="replace")).decode("ascii").strip()


def _run_command(args, error_prefix):
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            raise FloppyImageError(f"{error_prefix}: {detail}")
        raise FloppyImageError(f"{error_prefix}.")
    return result.stdout


def _run_streaming_command(args, error_prefix, *, line_callback=None, env=None):
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    output_lines = []

    try:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                stripped = line.strip()
                if stripped:
                    output_lines.append(stripped)
                    if len(output_lines) > 40:
                        output_lines = output_lines[-40:]
                if line_callback is not None:
                    line_callback(line)
        returncode = process.wait()
    finally:
        if process.stdout is not None:
            process.stdout.close()

    if returncode != 0:
        detail = "\n".join(output_lines).strip()
        if detail:
            raise FloppyImageError(f"{error_prefix}: {detail}")
        raise FloppyImageError(f"{error_prefix}.")


def _find_gw():
    return shutil.which("gw") or shutil.which("greaseweazle")


def _notify_progress(progress_callback, step, total, message):
    if progress_callback is not None:
        progress_callback(step, total, message)


def _list_linux_floppy_drives():
    lsblk = shutil.which("lsblk")
    if not lsblk:
        return []

    result = subprocess.run(
        [
            lsblk,
            "-J",
            "-b",
            "-o",
            "NAME,PATH,SIZE,RM,RO,TYPE,TRAN,MOUNTPOINTS,LABEL,MODEL",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return []

    drives = []
    for device in payload.get("blockdevices", []):
        if device.get("type") != "disk":
            continue

        path = device.get("path") or ""
        size_bytes = _parse_int(device.get("size"), 0)
        if size_bytes not in DISK_FORMAT_BY_SIZE:
            continue

        transport = (device.get("tran") or "").strip().lower()
        model = (device.get("model") or "").strip()
        removable = bool(device.get("rm"))
        looks_like_floppy = (
            removable
            or transport == "usb"
            or path.startswith("/dev/fd")
            or "floppy" in model.lower()
        )
        if not looks_like_floppy:
            continue

        mountpoints = tuple(
            mount
            for mount in (device.get("mountpoints") or [])
            if mount
        )
        drives.append(
            FloppyDriveInfo(
                path=path,
                size_bytes=size_bytes,
                transport=transport,
                model=model,
                label=(device.get("label") or "").strip(),
                mountpoints=mountpoints,
            )
        )

    drives.sort(key=lambda item: (item.path, item.size_bytes))
    return drives


def _windows_ctypes():
    import ctypes
    from ctypes import wintypes

    return ctypes, wintypes, ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_last_error_message(prefix):
    ctypes, _wintypes, _kernel32 = _windows_ctypes()
    error_code = ctypes.get_last_error()
    if error_code:
        return f"{prefix}: {ctypes.FormatError(error_code).strip()}"
    return f"{prefix}."


def _windows_raw_volume_path(drive_path):
    drive_path = str(drive_path or "").strip()
    if drive_path.startswith("\\\\.\\"):
        return drive_path
    drive_path = drive_path.rstrip("\\/")
    if re.fullmatch(r"[A-Za-z]:", drive_path):
        return f"\\\\.\\{drive_path.upper()}"
    if re.fullmatch(r"[A-Za-z]", drive_path):
        return f"\\\\.\\{drive_path.upper()}:"
    return drive_path


def _windows_volume_label(root_path):
    try:
        ctypes, wintypes, kernel32 = _windows_ctypes()
        kernel32.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        kernel32.GetVolumeInformationW.restype = wintypes.BOOL
        label_buffer = ctypes.create_unicode_buffer(261)
        fs_buffer = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        max_component = wintypes.DWORD()
        flags = wintypes.DWORD()
        ok = kernel32.GetVolumeInformationW(
            root_path,
            label_buffer,
            len(label_buffer),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            fs_buffer,
            len(fs_buffer),
        )
        if ok:
            return label_buffer.value.strip()
    except Exception:
        pass
    return ""


def _windows_device_io_control(handle, control_code, out_buffer=None):
    ctypes, wintypes, kernel32 = _windows_ctypes()
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    bytes_returned = wintypes.DWORD()
    out_size = ctypes.sizeof(out_buffer) if out_buffer is not None else 0
    ok = kernel32.DeviceIoControl(
        handle,
        control_code,
        None,
        0,
        ctypes.byref(out_buffer) if out_buffer is not None else None,
        out_size,
        ctypes.byref(bytes_returned),
        None,
    )
    return bool(ok)


def _windows_detect_floppy_size(raw_path):
    if os.name != "nt":
        return 0

    ctypes, wintypes, _kernel32 = _windows_ctypes()

    class _DiskGeometry(ctypes.Structure):
        _fields_ = [
            ("Cylinders", ctypes.c_longlong),
            ("MediaType", wintypes.DWORD),
            ("TracksPerCylinder", wintypes.DWORD),
            ("SectorsPerTrack", wintypes.DWORD),
            ("BytesPerSector", wintypes.DWORD),
        ]

    class _LengthInfo(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_longlong)]

    try:
        with _WindowsVolumeHandle(raw_path, write=False) as volume:
            geometry = _DiskGeometry()
            if _windows_device_io_control(volume.handle, 0x00070000, geometry):
                size = int(
                    geometry.Cylinders
                    * geometry.TracksPerCylinder
                    * geometry.SectorsPerTrack
                    * geometry.BytesPerSector
                )
                if size in DISK_FORMAT_BY_SIZE:
                    return size

            length_info = _LengthInfo()
            if _windows_device_io_control(volume.handle, 0x0007405C, length_info):
                size = int(length_info.Length)
                if size in DISK_FORMAT_BY_SIZE:
                    return size

            for disk_format in sorted(DISK_FORMATS, key=lambda item: item.size_bytes, reverse=True):
                try:
                    volume.read_at(disk_format.size_bytes - 1, 1, "floppy size probe")
                    return disk_format.size_bytes
                except FloppyImageError:
                    continue
    except FloppyImageError:
        return 0
    return 0


def _list_windows_floppy_drives():
    if os.name != "nt":
        return []
    try:
        ctypes, wintypes, kernel32 = _windows_ctypes()
        kernel32.GetLogicalDrives.restype = wintypes.DWORD
        kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetDriveTypeW.restype = wintypes.UINT
        drive_mask = int(kernel32.GetLogicalDrives())
    except Exception:
        return []

    drives = []
    DRIVE_REMOVABLE = 2
    for index in range(26):
        if not (drive_mask & (1 << index)):
            continue
        letter = chr(ord("A") + index)
        root_path = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root_path) != DRIVE_REMOVABLE:
            continue

        raw_path = _windows_raw_volume_path(f"{letter}:")
        size_bytes = _windows_detect_floppy_size(raw_path)
        if size_bytes not in DISK_FORMAT_BY_SIZE:
            continue
        drives.append(
            FloppyDriveInfo(
                path=f"{letter}:",
                size_bytes=size_bytes,
                transport="usb",
                model=f"Windows removable drive {letter}:",
                label=_windows_volume_label(root_path),
                mountpoints=(),
            )
        )

    drives.sort(key=lambda item: (item.path, item.size_bytes))
    return drives


def list_floppy_drives():
    if os.name == "nt":
        return _list_windows_floppy_drives()
    return _list_linux_floppy_drives()


def list_greaseweazle_devices():
    devices = []
    seen_paths = set()

    serial_dir = "/dev/serial/by-id"
    if os.path.isdir(serial_dir):
        for entry in sorted(os.listdir(serial_dir)):
            if "greaseweazle" not in entry.lower():
                continue
            symlink_path = os.path.join(serial_dir, entry)
            real_path = os.path.realpath(symlink_path)
            if real_path in seen_paths:
                continue
            seen_paths.add(real_path)
            devices.append(GreaseweazleDeviceInfo(path=real_path, label=entry))

    if devices:
        return devices

    gw = _find_gw()
    if not gw:
        return []

    result = subprocess.run([gw, "info"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return []

    match = re.search(r"^\s*Port:\s*(\S+)", result.stdout or "", re.MULTILINE)
    if not match:
        return []

    default_path = match.group(1).strip()
    return [GreaseweazleDeviceInfo(path=default_path, label="Default Greaseweazle")]


def _require_command(command_name):
    path = shutil.which(command_name)
    if not path:
        raise FloppyImageError(f"Required command not found: {command_name}")
    return path


def _mformat_args_for_disk_format(disk_format):
    if not isinstance(disk_format, DiskFormat):
        raise FloppyImageError("Invalid disk format.")
    args = MFORMAT_SIZE_OPTIONS.get(disk_format.key)
    if not args:
        raise FloppyImageError(f"Unsupported disk format for image creation: {disk_format.label}")
    return list(args)


def _write_image_direct(source_img, output_path, output_ext, disk_format):
    output_ext = output_ext.lower().lstrip(".")
    if output_ext in RAW_IMAGE_EXTENSIONS:
        shutil.copy2(source_img, output_path)
        return
    if output_ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise FloppyImageError(f"Unsupported output image type: {output_ext.upper()}")
    _gw_convert(source_img, output_path, disk_format.key)


def create_blank_floppy_image(output_path, disk_format, volume_label="NO NAME"):
    mformat = _require_command("mformat")
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path):
        os.remove(output_path)

    _run_command(
        [
            mformat,
            "-C",
            "-i",
            output_path,
            "-v",
            _volume_label_for_mformat(volume_label),
            *_mformat_args_for_disk_format(disk_format),
            "::",
        ],
        f"Could not create a blank {disk_format.label} image",
    )
    read_image_listing(output_path)
    return output_path


def _copy_host_file_into_image(target_img, host_path, image_path):
    if not os.path.isfile(host_path):
        raise FloppyImageError(f"File to add no longer exists: {host_path}")
    mcopy = _require_command("mcopy")
    _run_command(
        [mcopy, "-i", target_img, host_path, mtools_path(image_path)],
        f"Could not add {os.path.basename(host_path)} to image",
    )


def _is_image_capacity_error(exc):
    message = str(exc).lower()
    return "disk full" in message or "no directory slots" in message


def create_floppy_images_from_files(
    file_specs,
    output_path,
    output_ext,
    disk_format,
    *,
    volume_label="NO NAME",
    progress_callback=None,
):
    if not file_specs:
        raise FloppyImageError("There are no files to save into an image.")

    output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    total_files = len(file_specs)
    temp_dir = tempfile.mkdtemp(prefix="aps_new_floppy_image_")
    raw_images = []
    current_img = None
    current_count = 0

    def start_new_image(image_number):
        raw_path = os.path.join(temp_dir, f"part_{image_number:03d}.img")
        _notify_progress(
            progress_callback,
            max(0, min(total_files, len(raw_images))),
            total_files,
            f"Preparing blank {disk_format.label} image {image_number}...",
        )
        return create_blank_floppy_image(raw_path, disk_format, volume_label=volume_label)

    try:
        current_img = start_new_image(1)

        for index, spec in enumerate(file_specs, start=1):
            if isinstance(spec, dict):
                host_path = spec["host_path"]
                image_path = spec["image_path"]
                display_name = spec.get("display_name") or os.path.basename(image_path)
            else:
                host_path, image_path = spec[:2]
                display_name = spec[2] if len(spec) > 2 else os.path.basename(image_path)

            _notify_progress(
                progress_callback,
                index - 1,
                total_files,
                f"Packing {display_name} into image {len(raw_images) + 1}...",
            )

            try:
                _copy_host_file_into_image(current_img, host_path, image_path)
                current_count += 1
                continue
            except FloppyImageError as exc:
                if not _is_image_capacity_error(exc):
                    raise

            if current_count == 0:
                raise FloppyImageError(
                    f"'{display_name}' is too large to fit on a {disk_format.label} image."
                )

            raw_images.append(current_img)
            current_img = start_new_image(len(raw_images) + 1)
            current_count = 0

            try:
                _copy_host_file_into_image(current_img, host_path, image_path)
                current_count = 1
            except FloppyImageError as exc:
                if _is_image_capacity_error(exc):
                    raise FloppyImageError(
                        f"'{display_name}' is too large to fit on a {disk_format.label} image."
                    ) from exc
                raise

        if current_img is not None:
            raw_images.append(current_img)

        base_path, _ = os.path.splitext(output_path)
        total_images = len(raw_images)
        digits = max(2, len(str(total_images)))
        written_paths = []

        for index, raw_img in enumerate(raw_images, start=1):
            if total_images == 1:
                final_path = output_path
            else:
                final_path = f"{base_path}_{index:0{digits}d}.{output_ext.lower().lstrip('.')}"

            _notify_progress(
                progress_callback,
                index,
                total_images,
                f"Writing image {index} of {total_images}...",
            )

            temp_output = os.path.join(
                output_dir,
                f".{os.path.basename(final_path)}.aps_{uuid.uuid4().hex}.{output_ext.lower().lstrip('.')}",
            )
            try:
                _write_image_direct(raw_img, temp_output, output_ext, disk_format)
                os.replace(temp_output, final_path)
            finally:
                if os.path.exists(temp_output):
                    os.remove(temp_output)

            written_paths.append(final_path)

        return written_paths
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _gw_convert(input_path, output_path, disk_format):
    gw = _find_gw()
    if not gw:
        raise FloppyImageError("Greaseweazle CLI was not found; cannot convert this image format.")
    if os.path.exists(output_path):
        os.remove(output_path)
    _run_command(
        [gw, "convert", f"--format={disk_format}", input_path, output_path],
        "Image conversion failed",
    )


def _parse_gw_track_values(set_spec):
    values = []
    seen = set()

    for chunk in (set_spec or "").split(","):
        part = chunk.strip()
        if not part:
            continue

        if "-" in part:
            start_text, end_text = part.split("-", 1)
        else:
            start_text = part
            end_text = part

        try:
            start = int(start_text.strip())
            end = int(end_text.strip())
        except ValueError:
            continue

        step = 1 if end >= start else -1
        for value in range(start, end + step, step):
            if value in seen:
                continue
            seen.add(value)
            values.append(value)

    return values


def _extract_gw_track_total(track_spec):
    cyl_values = []
    head_values = []

    for segment in (track_spec or "").split(":"):
        part = segment.strip()
        if part.startswith("c="):
            cyl_values = _parse_gw_track_values(part[2:])
        elif part.startswith("h="):
            head_values = _parse_gw_track_values(part[2:])

    if not cyl_values or not head_values:
        return 0
    return len(cyl_values) * len(head_values)


def _gw_short_status(status_text):
    status = (status_text or "").strip()
    if not status:
        return ""
    if " from " in status:
        status = status.split(" from ", 1)[0].strip()
    return status


def _handle_gw_read_progress_line(progress_callback, state, line):
    clean_line = (line or "").strip()
    if not clean_line:
        return
    if clean_line.startswith("*** "):
        return

    header_match = re.match(r"^Reading\s+(?P<trackspec>.+?)\s+revs=\d+$", clean_line)
    if header_match:
        total_tracks = _extract_gw_track_total(header_match.group("trackspec"))
        state["total_tracks"] = total_tracks
        state["seen_tracks"] = set()
        if total_tracks > 0:
            _notify_progress(
                progress_callback,
                0,
                total_tracks,
                f"Reading floppy via Greaseweazle (0/{total_tracks} tracks)...",
            )
        else:
            _notify_progress(progress_callback, 0, 0, clean_line)
        return

    if clean_line.startswith("Format "):
        return

    track_match = re.match(r"^T(?P<cyl>\d+)\.(?P<head>\d+)(?:\s+<-.*)?\s*:\s*(?P<status>.*)$", clean_line)
    if track_match:
        track_key = (int(track_match.group("cyl")), int(track_match.group("head")))
        seen_tracks = state.setdefault("seen_tracks", set())
        seen_tracks.add(track_key)
        completed_tracks = len(seen_tracks)
        total_tracks = state.get("total_tracks", 0)
        track_label = f"T{track_match.group('cyl')}.{track_match.group('head')}"
        status = _gw_short_status(track_match.group("status"))
        if total_tracks > 0:
            message = f"Reading {track_label} ({completed_tracks}/{total_tracks})..."
            if status:
                message = f"{message} {status}"
            _notify_progress(progress_callback, completed_tracks, total_tracks, message)
        else:
            _notify_progress(progress_callback, 0, 0, clean_line)
        return

    total_tracks = state.get("total_tracks", 0)
    completed_tracks = len(state.get("seen_tracks", set()))
    if total_tracks > 0:
        _notify_progress(
            progress_callback,
            completed_tracks,
            total_tracks,
            clean_line,
        )
    else:
        _notify_progress(progress_callback, 0, 0, clean_line)


def _gw_read_floppy(source, output_path, progress_callback=None):
    gw = _find_gw()
    if not gw:
        raise FloppyImageError("Greaseweazle CLI was not found; cannot read from floppy drive.")
    if os.path.exists(output_path):
        os.remove(output_path)

    args = [
        gw,
        "read",
        f"--drive={source.drive}",
    ]
    if source.archival_quality:
        args.append("--raw")
    else:
        args.append(f"--format={source.disk_format.key}")
    if source.revs > 0:
        args.append(f"--revs={source.revs}")
    if source.retries > 0:
        args.append(f"--retries={source.retries}")
    if source.device_path:
        args.append(f"--device={source.device_path}")
    args.append(output_path)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    progress_state = {"total_tracks": 0, "seen_tracks": set(), "command_failed": ""}

    def _progress_line_callback(line):
        clean_line = (line or "").strip()
        if clean_line.startswith("Command Failed:"):
            progress_state["command_failed"] = clean_line
        _handle_gw_read_progress_line(progress_callback, progress_state, line)

    _run_streaming_command(
        args,
        "Greaseweazle read failed",
        line_callback=_progress_line_callback,
        env=env,
    )
    if progress_state["command_failed"]:
        detail = progress_state["command_failed"].split(":", 1)[1].strip()
        raise FloppyImageError(f"Greaseweazle read failed: {detail}")


def _gw_write_floppy(source, input_path):
    gw = _find_gw()
    if not gw:
        raise FloppyImageError("Greaseweazle CLI was not found; cannot write to floppy drive.")

    args = [
        gw,
        "write",
        f"--drive={source.drive}",
        f"--format={source.disk_format.key}",
    ]
    if source.device_path:
        args.append(f"--device={source.device_path}")
    args.append(input_path)
    _run_command(args, "Greaseweazle write failed")


def _normalize_image_path(path):
    cleaned = path.replace("\\", "/")
    if cleaned.startswith("::"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


class _WindowsVolumeHandle:
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_BEGIN = 0
    FSCTL_LOCK_VOLUME = 0x00090018
    FSCTL_UNLOCK_VOLUME = 0x0009001C
    FSCTL_DISMOUNT_VOLUME = 0x00090020

    def __init__(self, path, *, write=False):
        if os.name != "nt":
            raise FloppyImageError("Windows raw volume access is only available on Windows.")
        self.path = _windows_raw_volume_path(path)
        self.write = bool(write)
        self._ctypes, self._wintypes, self._kernel32 = _windows_ctypes()
        self._configure_api()
        access = self.GENERIC_READ | (self.GENERIC_WRITE if self.write else 0)
        self.handle = self._kernel32.CreateFileW(
            self.path,
            access,
            self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
            None,
            self.OPEN_EXISTING,
            self.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if self.handle == self._ctypes.c_void_p(-1).value:
            raise FloppyImageError(_windows_last_error_message(f"Could not open floppy device {self.path}"))

    def _configure_api(self):
        self._kernel32.CreateFileW.argtypes = [
            self._wintypes.LPCWSTR,
            self._wintypes.DWORD,
            self._wintypes.DWORD,
            self._wintypes.LPVOID,
            self._wintypes.DWORD,
            self._wintypes.DWORD,
            self._wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = self._wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [self._wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = self._wintypes.BOOL
        self._kernel32.SetFilePointerEx.argtypes = [
            self._wintypes.HANDLE,
            self._ctypes.c_longlong,
            self._ctypes.POINTER(self._ctypes.c_longlong),
            self._wintypes.DWORD,
        ]
        self._kernel32.SetFilePointerEx.restype = self._wintypes.BOOL
        self._kernel32.ReadFile.argtypes = [
            self._wintypes.HANDLE,
            self._wintypes.LPVOID,
            self._wintypes.DWORD,
            self._ctypes.POINTER(self._wintypes.DWORD),
            self._wintypes.LPVOID,
        ]
        self._kernel32.ReadFile.restype = self._wintypes.BOOL
        self._kernel32.WriteFile.argtypes = [
            self._wintypes.HANDLE,
            self._wintypes.LPCVOID,
            self._wintypes.DWORD,
            self._ctypes.POINTER(self._wintypes.DWORD),
            self._wintypes.LPVOID,
        ]
        self._kernel32.WriteFile.restype = self._wintypes.BOOL
        self._kernel32.FlushFileBuffers.argtypes = [self._wintypes.HANDLE]
        self._kernel32.FlushFileBuffers.restype = self._wintypes.BOOL

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def close(self):
        handle = getattr(self, "handle", None)
        if handle and handle != self._ctypes.c_void_p(-1).value:
            self._kernel32.CloseHandle(handle)
            self.handle = None

    def _seek(self, offset, label):
        new_pos = self._ctypes.c_longlong()
        ok = self._kernel32.SetFilePointerEx(
            self.handle,
            int(offset),
            self._ctypes.byref(new_pos),
            self.FILE_BEGIN,
        )
        if not ok:
            raise FloppyImageError(_windows_last_error_message(f"Could not seek to {label}"))

    def read_at(self, offset, size, label):
        self._seek(offset, label)
        buffer = self._ctypes.create_string_buffer(int(size))
        bytes_read = self._wintypes.DWORD()
        ok = self._kernel32.ReadFile(
            self.handle,
            buffer,
            int(size),
            self._ctypes.byref(bytes_read),
            None,
        )
        if not ok:
            raise FloppyImageError(_windows_last_error_message(f"Could not read {label}"))
        if bytes_read.value <= 0:
            return b""
        return buffer.raw[:bytes_read.value]

    def lock_for_write(self):
        if not self.write:
            return
        if not _windows_device_io_control(self.handle, self.FSCTL_LOCK_VOLUME):
            raise FloppyImageError(
                _windows_last_error_message(
                    "Could not lock the floppy volume for writing. Close Explorer or other programs using the drive and try again"
                )
            )
        _windows_device_io_control(self.handle, self.FSCTL_DISMOUNT_VOLUME)

    def unlock_after_write(self):
        if self.write:
            _windows_device_io_control(self.handle, self.FSCTL_UNLOCK_VOLUME)

    def write_file(self, input_path, progress_callback=None):
        self._seek(0, "start of floppy device")
        total_size = os.path.getsize(input_path)
        written_total = 0
        chunk_size = 64 * 1024
        with open(input_path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                buffer = self._ctypes.create_string_buffer(chunk)
                bytes_written = self._wintypes.DWORD()
                ok = self._kernel32.WriteFile(
                    self.handle,
                    buffer,
                    len(chunk),
                    self._ctypes.byref(bytes_written),
                    None,
                )
                if not ok or bytes_written.value != len(chunk):
                    raise FloppyImageError(_windows_last_error_message(f"Could not write floppy device {self.path}"))
                written_total += bytes_written.value
                if progress_callback is not None and total_size > 0:
                    progress = 4 + min(1, int((written_total / total_size) * 1))
                    progress_callback(progress, 5, f"Writing USB floppy: {display_bytes(written_total)} of {display_bytes(total_size)}...")
        if not self._kernel32.FlushFileBuffers(self.handle):
            raise FloppyImageError(_windows_last_error_message(f"Could not flush floppy device {self.path}"))


def _open_block_device_for_read(device_path):
    if os.name == "nt":
        return _WindowsVolumeHandle(device_path, write=False)
    try:
        return os.open(device_path, os.O_RDONLY)
    except OSError as exc:
        raise FloppyImageError(f"Could not open floppy device {device_path}: {exc}") from exc


def _close_block_device(device):
    if hasattr(device, "close"):
        device.close()
    else:
        os.close(device)


def _read_windows_block_device_bytes(device_path, size_bytes, progress_callback=None):
    if os.name == "nt":
        if not size_bytes:
            raise FloppyImageError("Could not read Windows floppy device: unknown disk size.")
        chunks = []
        remaining = int(size_bytes)
        cursor = 0
        chunk_size = 64 * 1024
        last_progress = -1
        with _WindowsVolumeHandle(device_path, write=False) as volume:
            while remaining > 0:
                current_size = min(chunk_size, remaining)
                chunk = volume.read_at(cursor, current_size, "floppy image")
                if not chunk:
                    raise FloppyImageError("Could not read floppy device: unexpected end of device.")
                chunks.append(chunk)
                cursor += len(chunk)
                remaining -= len(chunk)
                if progress_callback is not None and size_bytes > 0:
                    progress = min(70, int((cursor / int(size_bytes)) * 70))
                    if progress > last_progress:
                        last_progress = progress
                        progress_callback(
                            progress,
                            100,
                            f"Reading floppy image: {display_bytes(cursor)} of {display_bytes(size_bytes)}...",
                        )
        return b"".join(chunks)
    raise FloppyImageError("Windows raw floppy byte reads are only available on Windows.")


def _read_block_device(device_path, output_path, size_bytes):
    if os.name == "nt":
        data = _read_windows_block_device_bytes(device_path, size_bytes)
        with open(output_path, "wb") as output:
            output.write(data)
        return

    dd = _require_command("dd")
    args = [
        dd,
        f"if={device_path}",
        f"of={output_path}",
        "bs=1024",
        "iflag=fullblock",
        "status=none",
    ]
    if size_bytes and size_bytes % 1024 == 0:
        args.append(f"count={size_bytes // 1024}")
    _run_command(args, f"Could not read floppy device {device_path}")


def _write_block_device(input_path, device_path, progress_callback=None):
    if os.name == "nt":
        permission_hint = (
            "Direct USB floppy writes on Windows require permission to lock and write the raw drive. "
            "Close Explorer windows using the drive and run the app as administrator if Windows denies access. "
            "You can also use Save As Image as a safer fallback."
        )
        try:
            with _WindowsVolumeHandle(device_path, write=True) as volume:
                volume.lock_for_write()
                try:
                    volume.write_file(input_path, progress_callback=progress_callback)
                finally:
                    volume.unlock_after_write()
            return
        except FloppyImageError as exc:
            detail = str(exc)
            if "Access is denied" in detail or "denied" in detail.lower() or "lock" in detail.lower():
                detail = f"{detail}\n\n{permission_hint}"
            raise FloppyImageError(detail) from exc

    dd = _require_command("dd")
    permission_hint = (
        "Direct USB floppy writes require write permission for the block device. "
        "On Linux, make sure the disk is not mounted and that your user has write "
        "access to the device, or run the app with appropriate elevated permissions. "
        "You can also use Save As Image as a safer fallback."
    )
    if os.name == "posix" and not os.access(device_path, os.W_OK):
        raise FloppyImageError(
            f"Could not write floppy device {device_path}: permission denied.\n\n{permission_hint}"
        )
    try:
        _run_command(
            [
                dd,
                f"if={input_path}",
                f"of={device_path}",
                "bs=1024",
                "conv=fsync",
                "status=none",
            ],
            f"Could not write floppy device {device_path}",
        )
    except FloppyImageError as exc:
        detail = str(exc)
        if "Permission denied" in detail or "Text file busy" in detail or "Device or resource busy" in detail:
            detail = f"{detail}\n\n{permission_hint}"
        raise FloppyImageError(detail) from exc


def mtools_path(path):
    return "::/" + _normalize_image_path(path)


def _parse_int(value, fallback=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _read_image_listing_with_7z(img_path):
    seven_zip = _require_command("7z")
    output = _run_command([seven_zip, "l", "-slt", img_path], "Could not read image contents")

    in_records = False
    record = {}
    entries = []
    free_space = 0
    cluster_size = 1024

    def flush_record():
        nonlocal record
        if not record:
            return
        folder = record.get("Folder")
        path = _normalize_image_path(record.get("Path", ""))
        if folder == "-" and path:
            size = _parse_int(record.get("Size"), 0)
            packed_size = _parse_int(record.get("Packed Size"), allocated_size(size, cluster_size))
            entries.append(
                ImageEntry(
                    path=path,
                    size=size,
                    packed_size=packed_size,
                    attributes=record.get("Attributes", ""),
                )
            )
        record = {}

    for raw_line in output.splitlines():
        line = raw_line.rstrip("\n")
        if line == "----------":
            in_records = True
            continue

        if not in_records:
            if line.startswith("Free Space ="):
                free_space = _parse_int(line.split("=", 1)[1])
            elif line.startswith("Cluster Size ="):
                cluster_size = max(1, _parse_int(line.split("=", 1)[1], cluster_size))
            continue

        if not line:
            flush_record()
            continue

        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        record[key] = value

    flush_record()
    entries.sort(key=lambda item: item.path.lower())
    return ImageListing(entries=entries, free_space=free_space, cluster_size=cluster_size)


def read_image_listing(img_path):
    try:
        return _read_fat12_image_listing(img_path)
    except FloppyImageError as fat_exc:
        if not shutil.which("7z"):
            raise fat_exc
        return _read_image_listing_with_7z(img_path)


def _u16le(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def _looks_like_valid_yamaha_boot_sector(sector0):
    if len(sector0) != _YAMAHA_BYTES_PER_SECTOR:
        return False
    if sector0[510:512] != _YAMAHA_BOOT_SIGNATURE:
        return False
    return (
        _u16le(sector0, 11) == _YAMAHA_BYTES_PER_SECTOR
        and sector0[13] == _YAMAHA_SECTORS_PER_CLUSTER
        and _u16le(sector0, 14) == _YAMAHA_RESERVED_SECTORS
        and sector0[16] == _YAMAHA_NUM_FATS
        and _u16le(sector0, 17) == _YAMAHA_ROOT_ENTRIES
        and _u16le(sector0, 19) == _YAMAHA_TOTAL_SECTORS
        and sector0[21] == _YAMAHA_MEDIA_DESCRIPTOR
        and _u16le(sector0, 22) == _YAMAHA_SECTORS_PER_FAT
        and _u16le(sector0, 24) == _YAMAHA_SECTORS_PER_TRACK
        and _u16le(sector0, 26) == _YAMAHA_NUM_HEADS
    )


def _fat_signature_at(data, offset, media_descriptor=_YAMAHA_MEDIA_DESCRIPTOR):
    expected = bytes([int(media_descriptor) & 0xFF, 0xFF, 0xFF])
    end = offset + len(expected)
    return 0 <= offset and end <= len(data) and data[offset:end] == expected


def _entry_name_looks_plausible(raw_name):
    allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789$%'-_@~`!(){}^#& "
    return all(byte in allowed for byte in raw_name)


def _root_dir_looks_plausible(data, offset, root_dir_sectors=_YAMAHA_ROOT_DIR_SECTORS):
    end = offset + int(root_dir_sectors) * _YAMAHA_BYTES_PER_SECTOR
    if end > len(data):
        return False

    found = 0
    for pos in range(offset, end, 32):
        entry = data[pos:pos + 32]
        if len(entry) < 32:
            return False
        first = entry[0]
        attr = entry[11]

        if first == 0x00:
            break
        if first == 0xE5:
            continue
        if attr == 0x0F:
            found += 1
            continue
        if attr & 0xC0:
            return False
        if not _entry_name_looks_plausible(entry[:11]):
            return False
        found += 1

    return found > 0


def _layout_root_dir_sectors(layout):
    return int(math.ceil((int(layout["root_entries"]) * 32) / int(layout["bytes_per_sector"])))


def _layout_fat_offset(layout):
    return int(layout["reserved_sectors"]) * int(layout["bytes_per_sector"])


def _layout_fat_size(layout):
    return int(layout["sectors_per_fat"]) * int(layout["bytes_per_sector"])


def _layout_root_offset(layout):
    return (
        int(layout["reserved_sectors"])
        + int(layout["num_fats"]) * int(layout["sectors_per_fat"])
    ) * int(layout["bytes_per_sector"])


def _layout_total_size(layout):
    return int(layout["total_sectors"]) * int(layout["bytes_per_sector"])


def _detect_protected_fat12_layout(data):
    size = len(data)
    for layout in _PROTECTED_FAT12_LAYOUTS:
        total_size = _layout_total_size(layout)
        fat1_offset = _layout_fat_offset(layout)
        fat_size = _layout_fat_size(layout)
        fat2_offset = fat1_offset + fat_size
        root_offset = _layout_root_offset(layout)
        root_dir_sectors = _layout_root_dir_sectors(layout)
        media_descriptor = layout["media_descriptor"]

        if (
            size == total_size
            and _fat_signature_at(data, fat1_offset, media_descriptor)
            and _fat_signature_at(data, fat2_offset, media_descriptor)
            and _root_dir_looks_plausible(data, root_offset, root_dir_sectors)
        ):
            return {
                "mode": "replace_sector0",
                "layout": layout,
                "fat1_offset": fat1_offset,
                "root_offset": root_offset,
                "root_dir_sectors": root_dir_sectors,
                "notes": f"sector 0 appears blank/corrupt; {layout['label']} FATs and root directory are intact",
            }

        if (
            size == total_size - int(layout["bytes_per_sector"])
            and _fat_signature_at(data, 0, media_descriptor)
            and _fat_signature_at(data, fat_size, media_descriptor)
            and _root_dir_looks_plausible(
                data,
                int(layout["num_fats"]) * fat_size,
                root_dir_sectors,
            )
        ):
            return {
                "mode": "prepend_sector0",
                "layout": layout,
                "fat1_offset": 0,
                "root_offset": int(layout["num_fats"]) * fat_size,
                "root_dir_sectors": root_dir_sectors,
                "notes": f"first sector appears omitted; image needs a {layout['label']} boot sector prepended",
            }

    return None


def _detect_yamaha_layout(data):
    size = len(data)
    fat1_offset = _YAMAHA_BYTES_PER_SECTOR
    fat2_offset = (1 + _YAMAHA_SECTORS_PER_FAT) * _YAMAHA_BYTES_PER_SECTOR
    root_offset = (1 + _YAMAHA_NUM_FATS * _YAMAHA_SECTORS_PER_FAT) * _YAMAHA_BYTES_PER_SECTOR

    if size == _YAMAHA_TOTAL_SIZE and _looks_like_valid_yamaha_boot_sector(data[:_YAMAHA_BYTES_PER_SECTOR]):
        return {
            "mode": "already_valid",
            "fat1_offset": fat1_offset,
            "root_offset": root_offset,
            "notes": "valid 720 KB FAT12 boot sector already present",
        }

    if (
        size == _YAMAHA_TOTAL_SIZE
        and _fat_signature_at(data, fat1_offset)
        and _fat_signature_at(data, fat2_offset)
        and _root_dir_looks_plausible(data, root_offset)
    ):
        return {
            "mode": "replace_sector0",
            "fat1_offset": fat1_offset,
            "root_offset": root_offset,
            "notes": "sector 0 appears blank/corrupt; FATs and root directory are intact",
        }

    if (
        size == _YAMAHA_TOTAL_SIZE - _YAMAHA_BYTES_PER_SECTOR
        and _fat_signature_at(data, 0)
        and _fat_signature_at(data, _YAMAHA_SECTORS_PER_FAT * _YAMAHA_BYTES_PER_SECTOR)
        and _root_dir_looks_plausible(
            data,
            _YAMAHA_NUM_FATS * _YAMAHA_SECTORS_PER_FAT * _YAMAHA_BYTES_PER_SECTOR,
        )
    ):
        return {
            "mode": "prepend_sector0",
            "fat1_offset": 0,
            "root_offset": _YAMAHA_NUM_FATS * _YAMAHA_SECTORS_PER_FAT * _YAMAHA_BYTES_PER_SECTOR,
            "notes": "first sector appears omitted; image needs a sector prepended",
        }

    return None


def _find_volume_label(root_dir):
    for pos in range(0, len(root_dir), 32):
        entry = root_dir[pos:pos + 32]
        if len(entry) < 32:
            break
        if entry[0] == 0x00:
            break
        if entry[0] == 0xE5:
            continue
        if entry[11] == 0x08:
            return entry[:11]
    return None


def _normalize_label(label):
    text = (label or b"NO NAME").decode("latin1", errors="replace").strip()
    if not text:
        text = "NO NAME"
    text = "".join(ch if 0x20 <= ord(ch) <= 0x7E else " " for ch in text).upper()
    return text[:11].ljust(11).encode("ascii", errors="replace")


def _build_standard_fat12_boot_sector(layout, serial, volume_label):
    bytes_per_sector = int(layout["bytes_per_sector"])
    boot = bytearray(bytes_per_sector)
    boot[0:3] = b"\xEB\x3C\x90"
    boot[3:11] = b"MSDOS5.0"
    boot[11:13] = bytes_per_sector.to_bytes(2, "little")
    boot[13] = int(layout["sectors_per_cluster"])
    boot[14:16] = int(layout["reserved_sectors"]).to_bytes(2, "little")
    boot[16] = int(layout["num_fats"])
    boot[17:19] = int(layout["root_entries"]).to_bytes(2, "little")
    total_sectors = int(layout["total_sectors"])
    if total_sectors <= 0xFFFF:
        boot[19:21] = total_sectors.to_bytes(2, "little")
    else:
        boot[19:21] = (0).to_bytes(2, "little")
        boot[32:36] = total_sectors.to_bytes(4, "little")
    boot[21] = int(layout["media_descriptor"]) & 0xFF
    boot[22:24] = int(layout["sectors_per_fat"]).to_bytes(2, "little")
    boot[24:26] = int(layout["sectors_per_track"]).to_bytes(2, "little")
    boot[26:28] = int(layout["num_heads"]).to_bytes(2, "little")
    boot[28:32] = (0).to_bytes(4, "little")
    boot[36] = 0x00
    boot[37] = 0x00
    boot[38] = 0x29
    boot[39:43] = int(serial).to_bytes(4, "little", signed=False)
    boot[43:54] = _normalize_label(volume_label)
    boot[54:62] = b"FAT12   "
    boot[510:512] = _YAMAHA_BOOT_SIGNATURE
    return bytes(boot)


def _build_standard_yamaha_boot_sector(serial, volume_label):
    return _build_standard_fat12_boot_sector(_PROTECTED_FAT12_LAYOUTS[0], serial, volume_label)


def _geometry_from_boot_sector(sector0):
    if len(sector0) < _YAMAHA_BYTES_PER_SECTOR or sector0[510:512] != _YAMAHA_BOOT_SIGNATURE:
        return None

    bytes_per_sector = _u16le(sector0, 11)
    sectors_per_cluster = sector0[13]
    reserved_sectors = _u16le(sector0, 14)
    num_fats = sector0[16]
    root_entries = _u16le(sector0, 17)
    total_sectors = _u16le(sector0, 19) or int.from_bytes(sector0[32:36], "little")
    sectors_per_fat = _u16le(sector0, 22)

    if bytes_per_sector != 512:
        return None
    if sectors_per_cluster <= 0 or reserved_sectors <= 0 or num_fats <= 0:
        return None
    if root_entries <= 0 or total_sectors <= 0 or sectors_per_fat <= 0:
        return None

    geometry = Fat12Geometry(
        bytes_per_sector=bytes_per_sector,
        sectors_per_cluster=sectors_per_cluster,
        reserved_sectors=reserved_sectors,
        num_fats=num_fats,
        root_entries=root_entries,
        total_sectors=total_sectors,
        sectors_per_fat=sectors_per_fat,
    )
    if geometry.data_offset >= geometry.total_size:
        return None
    return geometry


def _yamaha_720_geometry():
    return Fat12Geometry(
        bytes_per_sector=_YAMAHA_BYTES_PER_SECTOR,
        sectors_per_cluster=_YAMAHA_SECTORS_PER_CLUSTER,
        reserved_sectors=_YAMAHA_RESERVED_SECTORS,
        num_fats=_YAMAHA_NUM_FATS,
        root_entries=_YAMAHA_ROOT_ENTRIES,
        total_sectors=_YAMAHA_TOTAL_SECTORS,
        sectors_per_fat=_YAMAHA_SECTORS_PER_FAT,
    )


def _fat12_geometry_from_layout(layout):
    return Fat12Geometry(
        bytes_per_sector=int(layout["bytes_per_sector"]),
        sectors_per_cluster=int(layout["sectors_per_cluster"]),
        reserved_sectors=int(layout["reserved_sectors"]),
        num_fats=int(layout["num_fats"]),
        root_entries=int(layout["root_entries"]),
        total_sectors=int(layout["total_sectors"]),
        sectors_per_fat=int(layout["sectors_per_fat"]),
    )


def _read_device_exact(device, offset, size, label):
    chunks = []
    remaining = int(size)
    cursor = int(offset)
    while remaining > 0:
        try:
            if hasattr(device, "read_at"):
                chunk = device.read_at(cursor, remaining, label)
            else:
                chunk = os.pread(device, remaining, cursor)
        except OSError as exc:
            raise FloppyImageError(f"Could not read {label}: {exc}") from exc
        if not chunk:
            raise FloppyImageError(f"Could not read {label}: unexpected end of device.")
        chunks.append(chunk)
        cursor += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _try_read_device_exact(device, offset, size):
    try:
        return _read_device_exact(device, offset, size, "floppy sector")
    except FloppyImageError:
        return None


def _decode_dos_directory_name(raw_name):
    stem = raw_name[:8].decode("ascii", errors="replace").rstrip()
    ext = raw_name[8:11].decode("ascii", errors="replace").rstrip()
    stem = stem.strip()
    ext = ext.strip()
    if not stem:
        return ""
    if ext:
        return f"{stem}.{ext}"
    return stem


def _iter_root_file_entries(root_dir):
    for pos in range(0, len(root_dir), 32):
        entry = root_dir[pos:pos + 32]
        if len(entry) < 32:
            break
        first = entry[0]
        attr = entry[11]
        if first == 0x00:
            break
        if first == 0xE5 or attr == 0x0F:
            continue
        if attr & 0x08:
            continue
        if attr & 0x10:
            raise FloppyImageError("Fast USB floppy read does not support subdirectories.")

        name = _decode_dos_directory_name(entry[:11])
        if not name:
            continue
        yield {
            "name": name,
            "attr": attr,
            "cluster": _u16le(entry, 26),
            "size": int.from_bytes(entry[28:32], "little"),
        }


def _fat12_next_cluster(fat, cluster):
    index = cluster + (cluster // 2)
    if index + 1 >= len(fat):
        return 0xFFF
    if cluster & 1:
        return ((fat[index] >> 4) | (fat[index + 1] << 4)) & 0xFFF
    return (fat[index] | ((fat[index + 1] & 0x0F) << 8)) & 0xFFF


def _fat12_cluster_chain(fat, first_cluster, size, geometry):
    if size <= 0 or first_cluster < 2:
        return []

    needed_clusters = int(math.ceil(size / geometry.cluster_size))
    max_clusters = max(needed_clusters + 4, 4)
    clusters = []
    seen = set()
    cluster = first_cluster

    while 2 <= cluster < 0xFF0 and cluster not in seen:
        clusters.append(cluster)
        seen.add(cluster)
        if len(clusters) >= max_clusters:
            break
        next_cluster = _fat12_next_cluster(fat, cluster)
        if next_cluster >= 0xFF8:
            break
        if next_cluster == 0xFF7:
            raise FloppyImageError("FAT12 cluster chain contains a bad cluster marker.")
        if next_cluster < 2:
            break
        cluster = next_cluster

    if len(clusters) < needed_clusters:
        raise FloppyImageError("FAT12 cluster chain ended before the file data was complete.")
    return clusters[:needed_clusters]


def _fat12_cluster_chain_from_start(fat, first_cluster):
    clusters = []
    seen = set()
    cluster = first_cluster
    while 2 <= cluster < 0xFF0 and cluster not in seen:
        clusters.append(cluster)
        seen.add(cluster)
        next_cluster = _fat12_next_cluster(fat, cluster)
        if next_cluster >= 0xFF8:
            break
        if next_cluster == 0xFF7:
            raise FloppyImageError("FAT12 cluster chain contains a bad cluster marker.")
        if next_cluster < 2:
            break
        cluster = next_cluster
    return clusters


def _cluster_offset(geometry, cluster):
    return geometry.data_offset + ((int(cluster) - 2) * geometry.cluster_size)


def _read_cluster_chain_from_image(data, geometry, clusters, size):
    output = bytearray()
    for cluster in clusters:
        offset = _cluster_offset(geometry, cluster)
        end = offset + geometry.cluster_size
        if offset < geometry.data_offset or end > len(data):
            raise FloppyImageError("A file points outside the floppy data area.")
        output.extend(data[offset:end])
        if len(output) >= size:
            break
    return bytes(output[:size])


def _fat12_data_cluster_count(geometry):
    return max(0, (geometry.total_size - geometry.data_offset) // geometry.cluster_size)


def _iter_fat_directory_entries(directory_bytes):
    for pos in range(0, len(directory_bytes), 32):
        entry = directory_bytes[pos:pos + 32]
        if len(entry) < 32:
            break
        first = entry[0]
        attr = entry[11]
        if first == 0x00:
            break
        if first == 0xE5 or attr == 0x0F:
            continue
        name = _decode_dos_directory_name(entry[:11])
        if not name or name in {".", ".."}:
            continue
        yield {
            "name": name,
            "attr": attr,
            "cluster": _u16le(entry, 26),
            "size": int.from_bytes(entry[28:32], "little"),
        }


def _read_directory_chain_from_image(data, geometry, fat, first_cluster):
    if first_cluster < 2:
        return b""
    clusters = _fat12_cluster_chain_from_start(fat, first_cluster)
    if not clusters:
        return b""
    return _read_cluster_chain_from_image(data, geometry, clusters, len(clusters) * geometry.cluster_size)


def _collect_fat12_listing_entries(data, geometry, fat, directory_bytes, parent_path=""):
    entries = []
    for entry in _iter_fat_directory_entries(directory_bytes):
        attr = entry["attr"]
        image_path = entry["name"] if not parent_path else f"{parent_path}/{entry['name']}"
        image_path = _normalize_image_path(image_path)
        if attr & 0x08:
            continue
        if attr & 0x10:
            child_dir = _read_directory_chain_from_image(data, geometry, fat, entry["cluster"])
            entries.extend(_collect_fat12_listing_entries(data, geometry, fat, child_dir, image_path))
            continue

        cluster_chain = _fat12_cluster_chain(fat, entry["cluster"], entry["size"], geometry)
        entries.append(
            ImageEntry(
                path=image_path,
                size=entry["size"],
                packed_size=len(cluster_chain) * geometry.cluster_size,
                attributes=f"{attr:02X}",
            )
        )
    return entries


def _read_fat12_image_context(img_path):
    with open(img_path, "rb") as handle:
        data = handle.read()

    geometry = _geometry_from_boot_sector(data[:_YAMAHA_BYTES_PER_SECTOR])
    if geometry is None:
        raise FloppyImageError("Could not parse the FAT12 boot sector for this image.")
    if len(data) < geometry.total_size:
        raise FloppyImageError("The floppy image ended before the FAT12 data area was complete.")

    fat = data[geometry.fat_offset:geometry.fat_offset + geometry.fat_size]
    if len(fat) != geometry.fat_size:
        raise FloppyImageError("Could not read the FAT12 allocation table from this image.")

    root_dir = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    if len(root_dir) != geometry.root_size:
        raise FloppyImageError("Could not read the FAT12 root directory from this image.")

    return data, geometry, fat, root_dir


def _read_fat12_image_listing(img_path):
    data, geometry, fat, root_dir = _read_fat12_image_context(img_path)
    entries = _collect_fat12_listing_entries(data, geometry, fat, root_dir)
    free_clusters = sum(
        1
        for cluster in range(2, _fat12_data_cluster_count(geometry) + 2)
        if _fat12_next_cluster(fat, cluster) == 0
    )
    entries.sort(key=lambda item: item.path.lower())
    return ImageListing(
        entries=entries,
        free_space=free_clusters * geometry.cluster_size,
        cluster_size=geometry.cluster_size,
    )


def _split_image_path_components(image_path):
    return [part for part in _normalize_image_path(image_path).split("/") if part]


def _locate_fat12_entry(data, geometry, fat, directory_bytes, path_parts, *, original_path):
    if not path_parts:
        raise FloppyImageError(f"Could not extract {original_path} from image: invalid image path.")

    target_name = path_parts[0].upper()
    for entry in _iter_fat_directory_entries(directory_bytes):
        if entry["name"].upper() != target_name:
            continue
        if len(path_parts) == 1:
            return entry
        if not (entry["attr"] & 0x10):
            raise FloppyImageError(
                f"Could not extract {original_path} from image: {entry['name']} is not a directory."
            )
        child_dir = _read_directory_chain_from_image(data, geometry, fat, entry["cluster"])
        return _locate_fat12_entry(
            data,
            geometry,
            fat,
            child_dir,
            path_parts[1:],
            original_path=original_path,
        )

    raise FloppyImageError(f"Could not extract {original_path} from image: file was not found.")


def _read_fat12_file_bytes(img_path, image_path):
    normalized_path = _normalize_image_path(image_path)
    path_parts = _split_image_path_components(normalized_path)
    data, geometry, fat, root_dir = _read_fat12_image_context(img_path)
    entry = _locate_fat12_entry(
        data,
        geometry,
        fat,
        root_dir,
        path_parts,
        original_path=normalized_path,
    )
    if entry["attr"] & 0x10:
        raise FloppyImageError(f"Could not extract {normalized_path} from image: path is a directory.")
    clusters = _fat12_cluster_chain(fat, entry["cluster"], entry["size"], geometry)
    return _read_cluster_chain_from_image(data, geometry, clusters, entry["size"])


def _fat12_chain_starts(fat, geometry):
    data_clusters = _fat12_data_cluster_count(geometry)
    used = []
    referenced = set()
    for cluster in range(2, data_clusters + 2):
        next_cluster = _fat12_next_cluster(fat, cluster)
        if next_cluster == 0:
            continue
        used.append(cluster)
        if 2 <= next_cluster < 0xFF0:
            referenced.add(next_cluster)
    return [cluster for cluster in used if cluster not in referenced]


def _dos_directory_entry(name_bytes, first_cluster, size, attr=0x20):
    entry = bytearray(32)
    entry[0:11] = bytes(name_bytes)[:11].ljust(11, b" ")
    entry[11] = attr & 0xFF
    entry[26:28] = int(first_cluster).to_bytes(2, "little")
    entry[28:32] = max(0, min(int(size), 0xFFFFFFFF)).to_bytes(4, "little")
    return bytes(entry)


def _reconstruct_yamaha_root_dir_from_pianodir(data):
    if len(data) != _YAMAHA_TOTAL_SIZE:
        return None

    geometry = _yamaha_720_geometry()
    fat_area = data[geometry.fat_offset:geometry.fat_offset + geometry.fat_area_size]
    if len(fat_area) != geometry.fat_area_size:
        return None
    if not _fat_signature_at(fat_area, 0) or not _fat_signature_at(fat_area, geometry.fat_size):
        return None

    fat = fat_area[:geometry.fat_size]
    chain_starts = _fat12_chain_starts(fat, geometry)
    pianodir_cluster = None
    for cluster in chain_starts:
        offset = _cluster_offset(geometry, cluster)
        if data[offset:offset + len(PIANODIR_HEADER)] == PIANODIR_HEADER:
            pianodir_cluster = cluster
            break
    if pianodir_cluster is None:
        return None

    try:
        pianodir_chain = _fat12_cluster_chain(fat, pianodir_cluster, PIANODIR_TARGET_FILE_SIZE, geometry)
        pianodir_bytes = _read_cluster_chain_from_image(
            data,
            geometry,
            pianodir_chain,
            PIANODIR_TARGET_FILE_SIZE,
        )
    except FloppyImageError:
        return None

    entries = [_dos_directory_entry(b"PIANODIRFIL", pianodir_cluster, PIANODIR_TARGET_FILE_SIZE)]
    used_starts = {pianodir_cluster}
    max_records = (PIANODIR_TARGET_FILE_SIZE - len(PIANODIR_HEADER)) // PIANODIR_TRACK_SIZE
    for slot in range(max_records):
        record_offset = len(PIANODIR_HEADER) + slot * PIANODIR_TRACK_SIZE
        record = pianodir_bytes[record_offset:record_offset + PIANODIR_TRACK_SIZE]
        if not record or not record.strip(b"\x00"):
            continue
        name_bytes = record[0:11]
        if not name_bytes.strip():
            continue

        matched_cluster = None
        matched_size = 0
        for cluster in chain_starts:
            if cluster in used_starts:
                continue
            offset = _cluster_offset(geometry, cluster)
            if data[offset + 7:offset + 15] != b"COM-ESEQ":
                continue
            if data[offset + 0x27:offset + 0x77] != record:
                continue
            chain = _fat12_cluster_chain_from_start(fat, cluster)
            allocated = len(chain) * geometry.cluster_size
            declared_size = int.from_bytes(data[offset + 3:offset + 7], "little")
            if declared_size <= 0 or declared_size > allocated:
                declared_size = allocated
            matched_cluster = cluster
            matched_size = declared_size
            break

        if matched_cluster is None:
            continue
        entries.append(_dos_directory_entry(name_bytes, matched_cluster, matched_size))
        used_starts.add(matched_cluster)

    if len(entries) <= 1:
        return None

    root_dir = bytearray(geometry.root_size)
    cursor = 0
    for entry in entries[:geometry.root_entries]:
        root_dir[cursor:cursor + 32] = entry
        cursor += 32
    return bytes(root_dir)


def _read_floppy_device_fast_image(device_path, output_path, size_bytes, progress_callback=None):
    device = _open_block_device_for_read(device_path)
    try:
        _notify_progress(progress_callback, 0, 100, "Reading floppy directory...")
        sector0 = _try_read_device_exact(device, 0, _YAMAHA_BYTES_PER_SECTOR) or b"\x00" * _YAMAHA_BYTES_PER_SECTOR
        geometry = _geometry_from_boot_sector(sector0)
        repair_result = YamahaRepairResult("Fast USB floppy read: valid FAT12 boot sector present.", False)
        boot = sector0
        fat_area = None
        root_dir = None

        if geometry is None:
            matched_layout = None
            candidate_layouts = sorted(
                _PROTECTED_FAT12_LAYOUTS,
                key=lambda layout: (
                    0 if int(layout["total_sectors"]) * int(layout["bytes_per_sector"]) == int(size_bytes or 0) else 1,
                    int(layout["total_sectors"]) * int(layout["bytes_per_sector"]),
                ),
            )
            for layout in candidate_layouts:
                candidate_geometry = _fat12_geometry_from_layout(layout)
                if size_bytes and candidate_geometry.total_size > size_bytes:
                    continue
                candidate_fat = _try_read_device_exact(
                    device,
                    candidate_geometry.fat_offset,
                    candidate_geometry.fat_area_size,
                )
                candidate_root = _try_read_device_exact(
                    device,
                    candidate_geometry.root_offset,
                    candidate_geometry.root_size,
                )
                if candidate_fat is None or candidate_root is None:
                    continue
                media_descriptor = int(layout["media_descriptor"])
                if not _fat_signature_at(candidate_fat, 0, media_descriptor) or not _fat_signature_at(
                    candidate_fat,
                    candidate_geometry.fat_size,
                    media_descriptor,
                ):
                    continue
                if not _root_dir_looks_plausible(candidate_root, 0, candidate_geometry.root_dir_sectors):
                    continue

                geometry = candidate_geometry
                fat_area = candidate_fat
                root_dir = candidate_root
                matched_layout = layout
                break
            if geometry is None or matched_layout is None:
                raise FloppyImageError("Fast USB floppy read only supports valid FAT12 disks or Yamaha protected FAT12 disks.")
            _notify_progress(progress_callback, 10, 100, "Yamaha protected disk recognized; creating working copy...")
            serial = zlib.crc32(fat_area + root_dir) & 0xFFFFFFFF
            boot = _build_standard_fat12_boot_sector(matched_layout, serial, _find_volume_label(root_dir))
            repair_result = YamahaRepairResult(
                "Fast USB floppy read applied Yamaha copy-protection repair: sector 0 appears blank/corrupt.",
                True,
            )

        if size_bytes and geometry.total_size > size_bytes:
            raise FloppyImageError("The detected FAT12 geometry is larger than the selected floppy device.")

        if fat_area is None:
            fat_area = _read_device_exact(device, geometry.fat_offset, geometry.fat_area_size, "floppy FAT sectors")
        if root_dir is None:
            root_dir = _read_device_exact(device, geometry.root_offset, geometry.root_size, "floppy root directory")
        if not repair_result.changed:
            _notify_progress(progress_callback, 10, 100, "Reading floppy file map...")

        total_size = geometry.total_size
        image = bytearray(total_size)
        image[0:len(boot)] = boot
        image[geometry.fat_offset:geometry.fat_offset + len(fat_area)] = fat_area
        image[geometry.root_offset:geometry.root_offset + len(root_dir)] = root_dir

        fat = fat_area[:geometry.fat_size]
        file_entries = list(_iter_root_file_entries(root_dir))
        file_chains = []
        _notify_progress(progress_callback, 20, 100, f"Planning read for {len(file_entries)} file(s)...")
        for entry in file_entries:
            clusters = _fat12_cluster_chain(fat, entry["cluster"], entry["size"], geometry)
            file_chains.append((entry, clusters))

        clusters_to_read = sorted({cluster for _entry, clusters in file_chains for cluster in clusters})
        cluster_runs = []
        run_start = None
        previous = None
        for cluster in clusters_to_read:
            if run_start is None:
                run_start = previous = cluster
                continue
            if cluster == previous + 1:
                previous = cluster
                continue
            cluster_runs.append((run_start, previous))
            run_start = previous = cluster
        if run_start is not None:
            cluster_runs.append((run_start, previous))

        total_data_bytes = sum(((end - start) + 1) * geometry.cluster_size for start, end in cluster_runs)
        pass_label = "pass" if len(cluster_runs) == 1 else "passes"
        _notify_progress(
            progress_callback,
            25,
            100,
            f"Reading {display_bytes(total_data_bytes)} of file data in {len(cluster_runs)} {pass_label}...",
        )
        read_data_bytes = 0
        last_progress = 25
        chunk_size = max(geometry.cluster_size, 16 * 1024)
        for start_cluster, end_cluster in cluster_runs:
            offset = geometry.data_offset + ((start_cluster - 2) * geometry.cluster_size)
            run_size = ((end_cluster - start_cluster) + 1) * geometry.cluster_size
            if offset < geometry.data_offset or offset + run_size > total_size:
                raise FloppyImageError("A file points outside the floppy data area.")
            run_cursor = 0
            while run_cursor < run_size:
                current_size = min(chunk_size, run_size - run_cursor)
                chunk = _read_device_exact(
                    device,
                    offset + run_cursor,
                    current_size,
                    f"clusters {start_cluster}-{end_cluster}",
                )
                image[offset + run_cursor:offset + run_cursor + len(chunk)] = chunk
                run_cursor += len(chunk)
                read_data_bytes += len(chunk)
                if total_data_bytes > 0:
                    progress = 25 + int((read_data_bytes / total_data_bytes) * 70)
                    if progress > last_progress:
                        last_progress = progress
                        _notify_progress(
                            progress_callback,
                            min(progress, 95),
                            100,
                            f"Reading file data: {display_bytes(read_data_bytes)} of {display_bytes(total_data_bytes)}...",
                        )

        _notify_progress(progress_callback, 97, 100, "Preparing floppy contents...")

        with open(output_path, "wb") as handle:
            handle.write(image)
        return repair_result
    finally:
        _close_block_device(device)


def prepare_yamaha_image(input_path, output_path):
    with open(input_path, "rb") as handle:
        data = handle.read()

    return prepare_yamaha_bytes(data, output_path)


def prepare_yamaha_bytes(data, output_path):
    def write_output(payload):
        with open(output_path, "wb") as handle:
            handle.write(payload)

    detection = _detect_yamaha_layout(data)
    if detection is None:
        detection = _detect_protected_fat12_layout(data)
    if detection is None:
        reconstructed_root = _reconstruct_yamaha_root_dir_from_pianodir(data)
        if reconstructed_root is not None:
            geometry = _yamaha_720_geometry()
            detection = {
                "mode": "replace_sector0",
                "fat1_offset": geometry.fat_offset,
                "root_offset": geometry.root_offset,
                "root_dir_sectors": geometry.root_dir_sectors,
                "root_dir": reconstructed_root,
                "notes": "sector 0 and root directory were damaged; rebuilt root directory from PIANODIR.FIL and FAT chains",
            }
    if detection is None:
        write_output(data)
        return YamahaRepairResult("No Yamaha copy-protection repair needed.", False)

    if detection["mode"] == "already_valid":
        write_output(data)
        return YamahaRepairResult("Yamaha repair check: valid 720 KB FAT12 boot sector already present.", False)

    layout = detection.get("layout")
    root_dir_sectors = int(detection.get("root_dir_sectors", _YAMAHA_ROOT_DIR_SECTORS))
    bytes_per_sector = int(layout["bytes_per_sector"]) if layout else _YAMAHA_BYTES_PER_SECTOR
    root_dir = detection.get("root_dir")
    if root_dir is None:
        root_dir = data[
            int(detection["root_offset"]): int(detection["root_offset"]) + root_dir_sectors * bytes_per_sector
        ]
    serial = zlib.crc32(data[int(detection["fat1_offset"]):]) & 0xFFFFFFFF
    if layout:
        boot = _build_standard_fat12_boot_sector(layout, serial, _find_volume_label(root_dir))
        expected_size = _layout_total_size(layout)
    else:
        boot = _build_standard_yamaha_boot_sector(serial, _find_volume_label(root_dir))
        expected_size = _YAMAHA_TOTAL_SIZE

    if detection["mode"] == "prepend_sector0":
        repaired = boot + data
    else:
        repaired = boot + data[bytes_per_sector:]

    if len(repaired) != expected_size:
        raise FloppyImageError("FAT12 repair produced an unexpected image size.")

    if detection.get("root_dir") is not None:
        root_offset = int(detection["root_offset"])
        repaired_data = bytearray(repaired)
        repaired_data[root_offset:root_offset + len(root_dir)] = root_dir
        repaired = bytes(repaired_data)

    write_output(repaired)

    return YamahaRepairResult("Yamaha copy-protection repair applied: " + detection["notes"] + ".", True)


def _disk_format_for_image(img_path):
    size = os.path.getsize(img_path)
    disk_format = DISK_FORMAT_BY_SIZE.get(size)
    if not disk_format:
        raise FloppyImageError(
            f"Unsupported FAT image size: {display_bytes(size)}. "
            "This tool currently supports common IBM-compatible floppy sizes."
        )
    return disk_format


def _looks_like_editable_fat_image(img_path):
    try:
        read_image_listing(img_path)
        return True
    except FloppyImageError:
        return False


class FloppyImageSession:
    def __init__(
        self,
        source_path,
        source_ext,
        temp_dir,
        working_img_path,
        disk_format,
        repair_result,
        source_kind="image",
        source_name=None,
        drive_info=None,
        gw_source=None,
    ):
        self.source_path = source_path
        self.source_ext = source_ext
        self.temp_dir = temp_dir
        self.working_img_path = working_img_path
        self.disk_format = disk_format
        self.source_kind = source_kind
        self.source_name = source_name or os.path.basename(source_path)
        self.drive_info = drive_info
        self.gw_source = gw_source
        self.repair_note = repair_result.note
        self.repair_changed = repair_result.changed
        self.extracted_dir = os.path.join(temp_dir, "extracted")
        self.patched_dir = os.path.join(temp_dir, "patched")
        self._extracted_files = {}
        os.makedirs(self.extracted_dir, exist_ok=True)
        os.makedirs(self.patched_dir, exist_ok=True)

    @classmethod
    def load(cls, source_path, progress_callback=None):
        source_path = os.path.abspath(source_path)
        source_ext = image_extension(source_path)
        if source_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise FloppyImageError("Unsupported image type.")

        temp_dir = tempfile.mkdtemp(prefix="aps_floppy_image_")
        try:
            _notify_progress(progress_callback, 0, 4, "Preparing floppy image...")
            if source_ext in RAW_IMAGE_EXTENSIONS:
                return cls._load_raw(source_path, source_ext, temp_dir, progress_callback=progress_callback)
            return cls._load_converted(source_path, source_ext, temp_dir, progress_callback=progress_callback)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def load_floppy(cls, drive_info, progress_callback=None):
        if not isinstance(drive_info, FloppyDriveInfo):
            raise FloppyImageError("Invalid floppy drive selection.")

        temp_dir = tempfile.mkdtemp(prefix="aps_floppy_drive_")
        try:
            source_copy = os.path.join(temp_dir, "source.img")
            working_img = os.path.join(temp_dir, "working.img")
            if os.name == "nt":
                try:
                    repair_result = _read_floppy_device_fast_image(
                        drive_info.path,
                        working_img,
                        drive_info.size_bytes,
                        progress_callback=progress_callback,
                    )
                    disk_format = _disk_format_for_image(working_img)
                    read_image_listing(working_img)
                except Exception as fast_exc:
                    _notify_progress(
                        progress_callback,
                        0,
                        100,
                        f"Reading full floppy image from {drive_info.path}...",
                    )
                    raw_data = _read_windows_block_device_bytes(
                        drive_info.path,
                        drive_info.size_bytes,
                        progress_callback=progress_callback,
                    )
                    _notify_progress(progress_callback, 75, 100, "Creating working copy...")
                    repair_result = prepare_yamaha_bytes(raw_data, working_img)
                    repair_result = YamahaRepairResult(
                        repair_result.note + f" Fast USB file-level read was unavailable: {fast_exc}",
                        repair_result.changed,
                    )
                    disk_format = _disk_format_for_image(working_img)
                    _notify_progress(progress_callback, 90, 100, "Scanning floppy contents...")
                    read_image_listing(working_img)
            else:
                try:
                    repair_result = _read_floppy_device_fast_image(
                        drive_info.path,
                        working_img,
                        drive_info.size_bytes,
                        progress_callback=progress_callback,
                    )
                    disk_format = _disk_format_for_image(working_img)
                    read_image_listing(working_img)
                except Exception as fast_exc:
                    _notify_progress(
                        progress_callback,
                        0,
                        100,
                        f"Reading full floppy image from {drive_info.path}...",
                    )
                    _read_block_device(drive_info.path, source_copy, drive_info.size_bytes)
                    _notify_progress(progress_callback, 75, 100, "Creating working copy...")
                    repair_result = prepare_yamaha_image(source_copy, working_img)
                    repair_result = YamahaRepairResult(
                        repair_result.note + f" Fast USB file-level read was unavailable: {fast_exc}",
                        repair_result.changed,
                    )
                    disk_format = _disk_format_for_image(working_img)
                    _notify_progress(progress_callback, 90, 100, "Scanning floppy contents...")
                    read_image_listing(working_img)
            _notify_progress(progress_callback, 100, 100, "Opening floppy contents...")
            return cls(
                drive_info.path,
                "img",
                temp_dir,
                working_img,
                disk_format,
                repair_result,
                source_kind="floppy_usb",
                source_name=drive_info.display_name,
                drive_info=drive_info,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def load_greaseweazle(cls, gw_source, progress_callback=None):
        if not isinstance(gw_source, GreaseweazleFloppySource):
            raise FloppyImageError("Invalid Greaseweazle source selection.")

        temp_dir = tempfile.mkdtemp(prefix="aps_gw_floppy_")
        try:
            source_copy = os.path.join(temp_dir, "source.img")
            working_img = os.path.join(temp_dir, "working.img")
            source_capture = source_copy
            total_steps = 4
            progress_step = 0
            if gw_source.archival_quality:
                source_capture = os.path.join(temp_dir, "source.scp")
                total_steps = 5
            _notify_progress(
                progress_callback,
                progress_step,
                total_steps,
                f"Reading floppy via Greaseweazle drive {gw_source.drive}...",
            )
            _gw_read_floppy(gw_source, source_capture, progress_callback=progress_callback)
            progress_step += 1
            if gw_source.archival_quality:
                _notify_progress(progress_callback, progress_step, total_steps, "Converting archival SCP capture...")
                _gw_convert(source_capture, source_copy, gw_source.disk_format.key)
                progress_step += 1
            _notify_progress(progress_callback, progress_step, total_steps, "Preparing editable floppy image...")
            repair_result = prepare_yamaha_image(source_copy, working_img)
            progress_step += 1
            _notify_progress(progress_callback, progress_step, total_steps, "Detecting floppy format...")
            disk_format = _disk_format_for_image(working_img)
            if disk_format.size_bytes != gw_source.disk_format.size_bytes:
                raise FloppyImageError("Greaseweazle read did not match the selected disk size.")
            progress_step += 1
            _notify_progress(progress_callback, progress_step, total_steps, "Scanning floppy contents...")
            read_image_listing(working_img)
            return cls(
                source_copy,
                "img",
                temp_dir,
                working_img,
                disk_format,
                repair_result,
                source_kind="floppy_gw",
                source_name=gw_source.display_name,
                gw_source=gw_source,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @property
    def mode_name(self):
        return "Floppy Mode" if self.source_kind.startswith("floppy") else "Image Mode"

    @classmethod
    def _load_raw(cls, source_path, source_ext, temp_dir, progress_callback=None):
        source_copy = os.path.join(temp_dir, "source.img")
        working_img = os.path.join(temp_dir, "working.img")
        _notify_progress(progress_callback, 1, 4, "Copying raw floppy image...")
        shutil.copy2(source_path, source_copy)
        _notify_progress(progress_callback, 2, 4, "Preparing editable floppy image...")
        repair_result = prepare_yamaha_image(source_copy, working_img)
        _notify_progress(progress_callback, 3, 4, "Scanning floppy contents...")
        disk_format = _disk_format_for_image(working_img)
        read_image_listing(working_img)
        return cls(source_path, source_ext, temp_dir, working_img, disk_format, repair_result)

    @classmethod
    def _load_converted(cls, source_path, source_ext, temp_dir, progress_callback=None):
        last_error = None
        for disk_format in DISK_FORMATS:
            candidate = os.path.join(temp_dir, f"candidate_{disk_format.key.replace('.', '_')}.img")
            prepared = os.path.join(temp_dir, f"prepared_{disk_format.key.replace('.', '_')}.img")
            try:
                _notify_progress(
                    progress_callback,
                    1,
                    4,
                    f"Converting image to editable {disk_format.label}...",
                )
                _gw_convert(source_path, candidate, disk_format.key)
                _notify_progress(progress_callback, 2, 4, "Preparing editable floppy image...")
                repair_result = prepare_yamaha_image(candidate, prepared)
                _notify_progress(progress_callback, 3, 4, "Scanning floppy contents...")
                detected_format = _disk_format_for_image(prepared)
                if detected_format.size_bytes != disk_format.size_bytes:
                    raise FloppyImageError("Converted image did not match the requested disk size.")
                read_image_listing(prepared)
                working_img = os.path.join(temp_dir, "working.img")
                shutil.move(prepared, working_img)
                return cls(source_path, source_ext, temp_dir, working_img, disk_format, repair_result)
            except Exception as exc:
                last_error = exc

        detail = f" Last error: {last_error}" if last_error else ""
        raise FloppyImageError(
            "Could not convert this image into an editable FAT floppy image." + detail
        )

    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def list_entries(self):
        return read_image_listing(self.working_img_path)

    def _run_mtools(self, args, message):
        _run_command(args, message)

    def _extract_from_image(self, source_img, image_path, dest_path):
        if os.path.exists(dest_path):
            os.remove(dest_path)
        try:
            data = _read_fat12_file_bytes(source_img, image_path)
        except FloppyImageError as fat_exc:
            mcopy = shutil.which("mcopy")
            if not mcopy:
                raise fat_exc
            self._run_mtools(
                [mcopy, "-i", source_img, mtools_path(image_path), dest_path],
                f"Could not extract {image_path} from image",
            )
            return

        with open(dest_path, "wb") as handle:
            handle.write(data)

    def extract_file(self, image_path):
        normalized = _normalize_image_path(image_path)
        cached = self._extracted_files.get(normalized)
        if cached and os.path.isfile(cached):
            return cached

        filename = os.path.basename(normalized) or "image-file"
        dest_path = os.path.join(self.extracted_dir, f"{uuid.uuid4().hex}_{filename}")
        self._extract_from_image(self.working_img_path, normalized, dest_path)
        self._extracted_files[normalized] = dest_path
        return dest_path

    def _patched_metadata_path(self, source_path, image_path=None, *, new_title=None, order_key=None):
        dest_path = os.path.join(self.patched_dir, f"{uuid.uuid4().hex}_{os.path.basename(source_path)}")
        if image_path and _host_file_is_eseq(source_path):
            if new_title is not None:
                error_msg = update_eseq_title_to_path(source_path, new_title, dest_path)
            else:
                shutil.copy2(source_path, dest_path)
                error_msg = None
            if error_msg:
                raise FloppyImageError(error_msg)
            if order_key is not None:
                error_msg = update_eseq_order_key(dest_path, order_key)
                if error_msg:
                    raise FloppyImageError(error_msg)
            return dest_path

        if new_title is None:
            shutil.copy2(source_path, dest_path)
            return dest_path

        if image_path and _host_file_is_eseq(source_path):
            error_msg = update_eseq_title_to_path(source_path, new_title, dest_path)
        else:
            error_msg = update_midi_title_to_path(source_path, new_title, dest_path)
        if error_msg:
            raise FloppyImageError(error_msg)
        return dest_path

    def _write_generated_pianodir(self, target_img, pianodir_metadata=None):
        listing = read_image_listing(target_img)
        track_entries = []

        for entry in listing.entries:
            if is_pianodir_path(entry.path):
                continue

            extracted_path = os.path.join(
                self.extracted_dir,
                f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(entry.path))}",
            )
            self._extract_from_image(target_img, entry.path, extracted_path)
            if not is_eseq_file(extracted_path):
                continue

            title = extract_eseq_title_from_file(extracted_path)
            if title.startswith("Error:"):
                title = ""
            track_entries.append(
                PianodirTrackEntry(
                    image_path=entry.path,
                    local_path=extracted_path,
                    title=title,
                )
            )

        track_entries.sort(
            key=lambda item: (
                read_eseq_order_key_from_file(item.local_path)
                if os.path.isfile(item.local_path)
                else build_eseq_order_key_from_path(item.image_path, sort_last=True)
            )
        )

        pianodir_bytes = build_pianodir_bytes(track_entries, metadata=pianodir_metadata)
        generated_path = os.path.join(self.patched_dir, f"{uuid.uuid4().hex}_{PIANODIR_FILENAME}")
        with open(generated_path, "wb") as handle:
            handle.write(pianodir_bytes)

        mdel = _require_command("mdel")
        mcopy = _require_command("mcopy")
        for entry in listing.entries:
            if not is_pianodir_path(entry.path):
                continue
            self._run_mtools(
                [mdel, "-i", target_img, mtools_path(entry.path)],
                "Could not replace existing PIANODIR.FIL in image",
            )
            break

        self._run_mtools(
            [mcopy, "-i", target_img, generated_path, mtools_path(PIANODIR_FILENAME)],
            "Could not write PIANODIR.FIL into image",
        )

    def _delete_existing_pianodir(self, target_img):
        listing = read_image_listing(target_img)
        mdel = _require_command("mdel")
        for entry in listing.entries:
            if not is_pianodir_path(entry.path):
                continue
            self._run_mtools(
                [mdel, "-i", target_img, mtools_path(entry.path)],
                "Could not delete PIANODIR.FIL from image",
            )
            break

    def create_modified_image(
        self,
        renames=None,
        deletes=None,
        additions=None,
        replacements=None,
        title_edits=None,
        order_key_edits=None,
        pianodir_metadata=None,
        generate_pianodir=False,
        delete_pianodir=False,
        progress_callback=None,
    ):
        renames = renames or {}
        deletes = set(deletes or set())
        additions = additions or {}
        replacements = replacements or {}
        title_edits = title_edits or {}
        order_key_edits = order_key_edits or {}
        target_img = os.path.join(self.temp_dir, f"modified_{uuid.uuid4().hex}.img")
        shutil.copy2(self.working_img_path, target_img)

        mdel = _require_command("mdel")
        mren = _require_command("mren")
        mcopy = _require_command("mcopy")

        try:
            _notify_progress(progress_callback, 1, 4, "Applying pending changes to floppy image...")
            for image_path in sorted(deletes, key=lambda item: item.lower(), reverse=True):
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not delete {image_path} from image",
                )

            if delete_pianodir:
                self._delete_existing_pianodir(target_img)

            for image_path, new_title in sorted(title_edits.items(), key=lambda item: item[0].lower()):
                if image_path in deletes or image_path in additions or image_path in replacements:
                    continue
                extracted_path = os.path.join(
                    self.extracted_dir,
                    f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(image_path))}",
                )
                self._extract_from_image(target_img, image_path, extracted_path)
                patched_path = self._patched_metadata_path(
                    extracted_path,
                    image_path=image_path,
                    new_title=new_title,
                    order_key=order_key_edits.get(image_path),
                )
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not replace {image_path} in image",
                )
                self._run_mtools(
                    [mcopy, "-i", target_img, patched_path, mtools_path(image_path)],
                    f"Could not write updated title for {image_path} into image",
                )

            for image_path, host_path in sorted(replacements.items(), key=lambda item: item[0].lower()):
                if image_path in deletes or image_path in additions:
                    continue
                if not os.path.isfile(host_path):
                    raise FloppyImageError(f"Replacement file no longer exists: {host_path}")
                source_path = host_path
                if image_path in title_edits or image_path in order_key_edits:
                    source_path = self._patched_metadata_path(
                        host_path,
                        image_path=image_path,
                        new_title=title_edits.get(image_path),
                        order_key=order_key_edits.get(image_path),
                    )
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not replace {image_path} in image",
                )
                self._run_mtools(
                    [mcopy, "-i", target_img, source_path, mtools_path(image_path)],
                    f"Could not write converted data for {image_path} into image",
                )

            for source_path, target_path in sorted(renames.items(), key=lambda item: item[0].lower()):
                if source_path in deletes:
                    continue
                if _normalize_image_path(source_path).lower() == _normalize_image_path(target_path).lower():
                    continue
                self._run_mtools(
                    [mren, "-i", target_img, mtools_path(source_path), mtools_path(target_path)],
                    f"Could not rename {source_path} in image",
                )

            for image_path, host_path in sorted(additions.items(), key=lambda item: item[0].lower()):
                if not os.path.isfile(host_path):
                    raise FloppyImageError(f"File to add no longer exists: {host_path}")
                source_path = host_path
                if image_path in title_edits or image_path in order_key_edits:
                    source_path = self._patched_metadata_path(
                        host_path,
                        image_path=image_path,
                        new_title=title_edits.get(image_path),
                        order_key=order_key_edits.get(image_path),
                    )
                self._run_mtools(
                    [mcopy, "-i", target_img, source_path, mtools_path(image_path)],
                    f"Could not add {os.path.basename(host_path)} to image",
                )

            for image_path, order_key in sorted(order_key_edits.items(), key=lambda item: item[0].lower()):
                if image_path in deletes or image_path in additions or image_path in replacements or image_path in title_edits:
                    continue
                extracted_path = os.path.join(
                    self.extracted_dir,
                    f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(image_path))}",
                )
                self._extract_from_image(target_img, image_path, extracted_path)
                patched_path = self._patched_metadata_path(
                    extracted_path,
                    image_path=image_path,
                    order_key=order_key,
                )
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not replace {image_path} in image",
                )
                self._run_mtools(
                    [mcopy, "-i", target_img, patched_path, mtools_path(image_path)],
                    f"Could not write updated order for {image_path} into image",
                )

            if generate_pianodir:
                _notify_progress(progress_callback, 2, 4, "Generating PIANODIR.FIL...")
                self._write_generated_pianodir(target_img, pianodir_metadata=pianodir_metadata)

            _notify_progress(progress_callback, 3, 4, "Verifying updated floppy image...")
            read_image_listing(target_img)
            return target_img
        except Exception:
            if os.path.exists(target_img):
                os.remove(target_img)
            raise

    def _write_image_direct(self, source_img, output_path, output_ext):
        output_ext = output_ext.lower().lstrip(".")
        if output_ext in RAW_IMAGE_EXTENSIONS:
            shutil.copy2(source_img, output_path)
            return
        if output_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise FloppyImageError(f"Unsupported output image type: {output_ext.upper()}")
        _gw_convert(source_img, output_path, self.disk_format.key)

    def write_image(self, source_img, output_path, output_ext, progress_callback=None):
        output_path = os.path.abspath(output_path)
        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)
        temp_output = os.path.join(
            output_dir,
            f".{os.path.basename(output_path)}.aps_{uuid.uuid4().hex}.{output_ext.lower().lstrip('.')}",
        )
        try:
            if output_ext.lower().lstrip(".") in RAW_IMAGE_EXTENSIONS:
                _notify_progress(progress_callback, 4, 5, "Writing raw floppy image...")
            else:
                _notify_progress(progress_callback, 4, 5, f"Converting floppy image to {output_ext.upper()}...")
            self._write_image_direct(source_img, temp_output, output_ext)
            os.replace(temp_output, output_path)
        finally:
            if os.path.exists(temp_output):
                os.remove(temp_output)

    def export_to(
        self,
        output_path,
        output_ext,
        renames=None,
        deletes=None,
        additions=None,
        replacements=None,
        title_edits=None,
        order_key_edits=None,
        pianodir_metadata=None,
        generate_pianodir=False,
        delete_pianodir=False,
        progress_callback=None,
    ):
        modified_img = self.create_modified_image(
            renames=renames,
            deletes=deletes,
            additions=additions,
            replacements=replacements,
            title_edits=title_edits,
            order_key_edits=order_key_edits,
            pianodir_metadata=pianodir_metadata,
            generate_pianodir=generate_pianodir,
            delete_pianodir=delete_pianodir,
            progress_callback=progress_callback,
        )
        try:
            self.write_image(modified_img, output_path, output_ext, progress_callback=progress_callback)
        finally:
            if os.path.exists(modified_img):
                os.remove(modified_img)

    def commit_to_source(
        self,
        renames=None,
        deletes=None,
        additions=None,
        replacements=None,
        title_edits=None,
        order_key_edits=None,
        pianodir_metadata=None,
        generate_pianodir=False,
        delete_pianodir=False,
        progress_callback=None,
    ):
        modified_img = self.create_modified_image(
            renames=renames,
            deletes=deletes,
            additions=additions,
            replacements=replacements,
            title_edits=title_edits,
            order_key_edits=order_key_edits,
            pianodir_metadata=pianodir_metadata,
            generate_pianodir=generate_pianodir,
            delete_pianodir=delete_pianodir,
            progress_callback=progress_callback,
        )
        try:
            if self.source_kind == "floppy_usb":
                _notify_progress(progress_callback, 4, 5, f"Writing USB floppy {self.source_path}...")
                _write_block_device(modified_img, self.source_path, progress_callback=progress_callback)
            elif self.source_kind == "floppy_gw":
                drive_name = self.gw_source.drive if self.gw_source is not None else "A"
                _notify_progress(progress_callback, 4, 5, f"Writing Greaseweazle drive {drive_name}...")
                _gw_write_floppy(self.gw_source, modified_img)
            else:
                output_ext = self.source_ext if self.source_ext else "img"
                source_dir = os.path.dirname(self.source_path)
                temp_output = os.path.join(
                    source_dir,
                    f".{os.path.basename(self.source_path)}.aps_{uuid.uuid4().hex}.{output_ext}",
                )
                if output_ext.lower().lstrip(".") in RAW_IMAGE_EXTENSIONS:
                    _notify_progress(progress_callback, 4, 5, "Saving raw floppy image...")
                else:
                    _notify_progress(progress_callback, 4, 5, f"Converting image back to {output_ext.upper()}...")
                self._write_image_direct(modified_img, temp_output, output_ext)
                os.replace(temp_output, self.source_path)
            os.replace(modified_img, self.working_img_path)
            self._extracted_files.clear()
            self.repair_changed = False
            self.repair_note = "Floppy saved." if self.source_kind.startswith("floppy") else "Image saved."
        finally:
            temp_output = locals().get("temp_output")
            if temp_output and os.path.exists(temp_output):
                os.remove(temp_output)
            if os.path.exists(modified_img):
                os.remove(modified_img)
