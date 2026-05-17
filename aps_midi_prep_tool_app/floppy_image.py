import datetime
import json
import math
import ntpath
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from dataclasses import dataclass

from .eseq_pianodir import (
    ESEQ_VARIANT_CLAVINOVA,
    ESEQ_VARIANT_DISKLAVIER,
    ESEQ_ORDER_KEY_SIZE,
    MUSICDIR_FILENAME,
    PIANODIR_FILENAME,
    PIANODIR_HEADER,
    PIANODIR_TARGET_FILE_SIZE,
    PIANODIR_TRACK_SIZE,
    PIANODIR_TRACK_SOURCE_START,
    PIANODIR_TRACK_SOURCE_END,
    PianodirTrackEntry,
    build_music_dir_bytes,
    build_pianodir_bytes,
    build_eseq_order_key_from_path,
    is_clavinova_mda_file,
    is_eseq_directory_path,
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
from .additional_formats import electone_mdr_to_midi
from .subprocess_utils import windows_subprocess_kwargs


class FloppyImageError(Exception):
    """Raised when a floppy image cannot be loaded or edited."""


class FloppyOperationCancelled(FloppyImageError):
    """Raised when the user cancels a long floppy operation."""


class GreaseweazleConversionError(FloppyImageError):
    """Raised when a Greaseweazle image conversion reports sector failures."""

    def __init__(
        self,
        message,
        *,
        sector_map=None,
        disk_format=None,
        capture_path="",
        reason="",
        suggested_format=None,
        details=None,
    ):
        super().__init__(message)
        self.sector_map = sector_map or {}
        self.disk_format = disk_format
        self.capture_path = capture_path or ""
        self.reason = reason or ("sector_failure" if self.sector_map.get("has_failures") else "")
        self.suggested_format = suggested_format
        self.details = details or {}


class ConvertedImageFormatMismatchError(FloppyImageError):
    """Raised when a converted image's boot sector points to another disk format."""

    def __init__(self, message, *, suggested_format=None, hinted_label=""):
        super().__init__(message)
        self.suggested_format = suggested_format
        self.hinted_label = hinted_label or ""


class FastFloppyReadError(FloppyImageError):
    """Raised when the file-level floppy reader cannot be used."""

    def __init__(self, message, *, fallback_allowed=False):
        super().__init__(message)
        self.fallback_allowed = bool(fallback_allowed)


def _raise_if_cancelled(cancel_callback=None):
    if cancel_callback is not None and cancel_callback():
        raise FloppyOperationCancelled("Operation cancelled.")


def _host_file_is_eseq(path):
    return os.path.isfile(path) and is_eseq_file(path) and has_eseq_title_metadata(path)


def _normalized_eseq_variant(eseq_variant):
    if eseq_variant == ESEQ_VARIANT_CLAVINOVA:
        return ESEQ_VARIANT_CLAVINOVA
    return ESEQ_VARIANT_DISKLAVIER


def _eseq_directory_filename_for_variant(eseq_variant):
    return MUSICDIR_FILENAME if _normalized_eseq_variant(eseq_variant) == ESEQ_VARIANT_CLAVINOVA else PIANODIR_FILENAME


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
    modified_time: float | None = None

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
class RecoveredFile:
    image_path: str
    data: bytes
    kind: str
    source_offset: int = -1
    origin: str = ""


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
        elif self.size_bytes <= 0:
            parts.append("size unknown")
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
class FloppyRecoverySource:
    drive_info: FloppyDriveInfo
    disk_format: DiskFormat

    @property
    def display_name(self):
        return f"{self.drive_info.path} ({self.disk_format.label})"


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
    capture_save_path: str = ""
    capture_output_ext: str = ""

    @property
    def display_name(self):
        detail = self.disk_format.label
        if self.archival_quality:
            detail += ", raw SCP"
        elif self.capture_output_ext:
            detail += f", save {self.capture_output_ext.upper()}"
        extras = []
        if self.revs > 0:
            extras.append(f"{self.revs} revs")
        if self.retries > 0:
            extras.append(f"{self.retries} retries")
        if extras:
            detail += ", " + ", ".join(extras)
        return f"Greaseweazle {self.drive} on {self.device_path} ({detail})"


@dataclass(frozen=True)
class GreaseweazleCapture:
    gw_source: GreaseweazleFloppySource
    capture_path: str
    temp_dir: str
    sector_map: dict | None = None

    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


@dataclass(frozen=True)
class ImageLoadSource:
    path: str
    disk_format: DiskFormat | None = None

    @property
    def display_name(self):
        name = os.path.basename(self.path)
        if self.disk_format is None:
            return f"{name} (autodetect)"
        return f"{name} ({self.disk_format.label})"


@dataclass(frozen=True)
class ImageRecoverySource:
    path: str
    disk_format: DiskFormat | None = None

    @property
    def display_name(self):
        name = os.path.basename(self.path)
        if self.disk_format is None:
            return f"{name} (autodetect)"
        return f"{name} ({self.disk_format.label})"


DISK_FORMATS = [
    DiskFormat("ibm.720", "IBM 720K DD", 737280),
    DiskFormat("ibm.800", "IBM 800K DD", 819200),
    DiskFormat("ibm.1440", "IBM 1.44M HD", 1474560),
    DiskFormat("ibm.1200", "IBM 1.2M HD", 1228800),
    DiskFormat("ibm.360", "IBM 360K DD", 368640),
    DiskFormat("ibm.320", "IBM 320K DD", 327680),
    DiskFormat("ibm.180", "IBM 180K", 184320),
    DiskFormat("ibm.160", "IBM 160K", 163840),
    DiskFormat("ibm.2880", "IBM 2.88M ED", 2949120),
]

DISK_FORMAT_BY_SIZE = {fmt.size_bytes: fmt for fmt in DISK_FORMATS}
DISK_FORMAT_BY_KEY = {fmt.key: fmt for fmt in DISK_FORMATS}
MAX_FLOPPY_DRIVE_BYTES = 100 * 1024 * 1024

NON_FAT_GW_FORMATS = [
    DiskFormat("mac.800", "Macintosh 800K GCR/HFS", 819200),
]

GW_IMAGE_FORMATS = DISK_FORMATS + NON_FAT_GW_FORMATS
GW_FORMAT_BY_KEY = {fmt.key: fmt for fmt in GW_IMAGE_FORMATS}
NON_FAT_GW_FORMAT_BY_KEY = {fmt.key: fmt for fmt in NON_FAT_GW_FORMATS}
SCP_DISK_TYPE_MACINTOSH = 0x80
NON_FAT_GW_FORMAT_BY_SCP_TYPE = {
    SCP_DISK_TYPE_MACINTOSH: NON_FAT_GW_FORMAT_BY_KEY["mac.800"],
}

DISK_FORMAT_TRACK_LAYOUTS = {
    "ibm.160": {"cylinders": 40, "heads": 1, "sectors_per_track": 8},
    "ibm.180": {"cylinders": 40, "heads": 1, "sectors_per_track": 9},
    "ibm.320": {"cylinders": 40, "heads": 2, "sectors_per_track": 8},
    "ibm.360": {"cylinders": 40, "heads": 2, "sectors_per_track": 9},
    "ibm.720": {"cylinders": 80, "heads": 2, "sectors_per_track": 9},
    "ibm.800": {"cylinders": 80, "heads": 2, "sectors_per_track": 10},
    "ibm.1200": {"cylinders": 80, "heads": 2, "sectors_per_track": 15},
    "ibm.1440": {"cylinders": 80, "heads": 2, "sectors_per_track": 18},
    "ibm.2880": {"cylinders": 80, "heads": 2, "sectors_per_track": 36},
}

RAW_IMAGE_EXTENSIONS = {"bin", "img", "ima", "vfd"}

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
    "vfd",
    "xdf",
}

PREFERRED_OUTPUT_EXTENSIONS = [
    ("bin", "BIN raw sector image"),
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
        "label": "IBM 800K DD",
        "bytes_per_sector": 512,
        "sectors_per_cluster": 2,
        "reserved_sectors": 1,
        "num_fats": 2,
        "root_entries": 112,
        "total_sectors": 1600,
        "media_descriptor": 0xF9,
        "sectors_per_fat": 3,
        "sectors_per_track": 10,
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


def _run_command(args, error_prefix, *, cancel_callback=None):
    if cancel_callback is None:
        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            **windows_subprocess_kwargs(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                raise FloppyImageError(f"{error_prefix}: {detail}")
            raise FloppyImageError(f"{error_prefix}.")
        return (result.stdout or "") + (result.stderr or "")

    _raise_if_cancelled(cancel_callback)
    process = subprocess.Popen(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **windows_subprocess_kwargs(),
    )
    stdout = ""
    stderr = ""
    communicate_error = None

    def _communicate():
        nonlocal stdout, stderr, communicate_error
        try:
            stdout, stderr = process.communicate()
        except Exception as exc:
            communicate_error = exc

    communicator = threading.Thread(target=_communicate, daemon=True)
    communicator.start()
    try:
        while communicator.is_alive():
            _raise_if_cancelled(cancel_callback)
            communicator.join(timeout=0.1)
    except FloppyOperationCancelled:
        _terminate_process(process)
        communicator.join(timeout=2)
        raise

    if communicate_error is not None:
        _terminate_process(process)
        raise communicate_error

    _raise_if_cancelled(cancel_callback)
    if process.returncode != 0:
        detail = (stderr or stdout or "").strip()
        if detail:
            raise FloppyImageError(f"{error_prefix}: {detail}")
        raise FloppyImageError(f"{error_prefix}.")
    return (stdout or "") + (stderr or "")


def _run_streaming_command(args, error_prefix, *, line_callback=None, env=None, cancel_callback=None):
    _raise_if_cancelled(cancel_callback)
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        **windows_subprocess_kwargs(),
    )

    output_lines = []
    all_output_lines = []
    line_queue = queue.Queue()
    stream_done = object()

    def _read_output():
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line_queue.put(raw_line)
        finally:
            line_queue.put(stream_done)

    reader = threading.Thread(target=_read_output, daemon=True)
    reader.start()

    try:
        while True:
            _raise_if_cancelled(cancel_callback)
            try:
                raw_line = line_queue.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if raw_line is stream_done:
                break
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            if stripped:
                all_output_lines.append(stripped)
                output_lines.append(stripped)
                if len(output_lines) > 40:
                    output_lines = output_lines[-40:]
            if line_callback is not None:
                line_callback(line)
            _raise_if_cancelled(cancel_callback)
        returncode = process.wait()
    except FloppyOperationCancelled:
        _terminate_process(process)
        raise
    except Exception:
        _terminate_process(process)
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
        reader.join(timeout=0.2)

    _raise_if_cancelled(cancel_callback)
    if returncode != 0:
        detail = "\n".join(output_lines).strip()
        if detail:
            raise FloppyImageError(f"{error_prefix}: {detail}")
        raise FloppyImageError(f"{error_prefix}.")
    return "\n".join(all_output_lines)


def _find_gw():
    found = shutil.which("gw") or shutil.which("greaseweazle")
    if found:
        return found
    return _find_bundled_gw()


def _command_name_variants(command_name):
    name = str(command_name or "").strip()
    if not name:
        return []
    variants = [name]
    if os.name == "nt" and not os.path.splitext(name)[1]:
        variants.extend(f"{name}{suffix}" for suffix in (".exe", ".cmd", ".bat"))
    return variants


def _bundled_tool_search_dirs():
    package_dir = os.path.dirname(os.path.abspath(__file__))
    repo_or_bundle_root = os.path.dirname(package_dir)
    bases = [
        getattr(sys, "_MEIPASS", ""),
        os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else "",
        package_dir,
        repo_or_bundle_root,
    ]
    suffixes = (
        "",
        "bin",
        os.path.join("bin", "greaseweazle"),
        os.path.join("aps_midi_prep_tool_app", "bin"),
        os.path.join("aps_midi_prep_tool_app", "bin", "greaseweazle"),
    )
    dirs = []
    seen = set()
    for base in bases:
        if not base:
            continue
        for suffix in suffixes:
            path = os.path.abspath(os.path.join(base, suffix))
            normalized = os.path.normcase(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            dirs.append(path)
    return dirs


def _find_bundled_command(*command_names):
    for directory in _bundled_tool_search_dirs():
        for command_name in command_names:
            for filename in _command_name_variants(command_name):
                path = os.path.join(directory, filename)
                if os.path.isfile(path) and (os.name == "nt" or os.access(path, os.X_OK)):
                    return path
    return None


def _find_bundled_gw():
    return _find_bundled_command("gw", "greaseweazle")


def _dependency_command_message(command_name):
    command = str(command_name or "").strip()
    mtools_commands = {"mformat", "mcopy", "mdel", "mren", "mdir"}
    if command in mtools_commands:
        return (
            f"Required mtools command '{command}' was not found. "
            "Install mtools, or run an AppImage build that bundles mtools, then try again."
        )
    if command == "7z":
        return (
            "Required 7-Zip command '7z' was not found. "
            "Install 7-Zip/p7zip so this image type can be inspected."
        )
    if command == "dd":
        return (
            "Required system command 'dd' was not found. "
            "Direct floppy reads and writes on Linux need dd on PATH."
        )
    return f"Required command '{command}' was not found. Install it and make sure it is on PATH."


def _missing_greaseweazle_message(action):
    return (
        f"Greaseweazle CLI was not found, so the app cannot {action}. "
        "Install Greaseweazle or use a build that bundles gw.exe, and make sure "
        "the command is available as 'gw' or 'greaseweazle'."
    )


def _supported_image_type_hint():
    return "Supported floppy image types include IMG, BIN, IMA, and HFE."


def _unsupported_image_type_message(output_ext, *, for_output=False):
    ext = (output_ext or "").lower().lstrip(".")
    label = ext.upper() if ext else "(none)"
    action = "write" if for_output else "open"
    return f"Unsupported image type '{label}'. The app cannot {action} that format. {_supported_image_type_hint()}"


def _notify_progress(progress_callback, step, total, message):
    if progress_callback is not None:
        progress_callback(step, total, message)


FLOPPY_DRIVE_MODEL_HINTS = (
    "floppy",
    "fdd",
    "fd-05",
    "mitsumi",
    "teac",
    "uf000",
    "y-e data",
    "yedata",
)


def _model_looks_like_floppy(model):
    model_text = str(model or "").strip().lower()
    return any(hint in model_text for hint in FLOPPY_DRIVE_MODEL_HINTS)


def _size_exceeds_floppy_drive_limit(size_bytes):
    return int(size_bytes or 0) > MAX_FLOPPY_DRIVE_BYTES


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
        if device.get("type") != "disk":
            continue

        path = device.get("path") or ""
        transport = (device.get("tran") or "").strip().lower()
        model = (device.get("model") or "").strip()
        size_bytes = _parse_int(device.get("size"), 0)
        if _size_exceeds_floppy_drive_limit(size_bytes):
            continue
        supported_size = size_bytes in DISK_FORMAT_BY_SIZE
        removable = bool(device.get("rm"))
        looks_like_floppy = (
            path.startswith("/dev/fd")
            or _model_looks_like_floppy(model)
            or (supported_size and (removable or transport == "usb"))
        )
        if not looks_like_floppy:
            continue
        if size_bytes > 0 and not supported_size:
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


def _windows_filesystem_root(drive_path):
    drive_path = str(drive_path or "").strip()
    raw_match = re.fullmatch(r"\\\\\.\\([A-Za-z]):", drive_path)
    if raw_match:
        return f"{raw_match.group(1).upper()}:\\"
    match = re.fullmatch(r"([A-Za-z]):[\\/]*", drive_path)
    if match:
        return f"{match.group(1).upper()}:\\"
    return None


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


def _windows_volume_total_size(root_path):
    root_path = _windows_filesystem_root(root_path) or str(root_path or "").strip()
    if not root_path:
        return 0
    try:
        ctypes, wintypes, kernel32 = _windows_ctypes()
        kernel32.GetDiskFreeSpaceExW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        kernel32.GetDiskFreeSpaceExW.restype = wintypes.BOOL
        free_available = ctypes.c_ulonglong()
        total_bytes = ctypes.c_ulonglong()
        total_free = ctypes.c_ulonglong()
        if kernel32.GetDiskFreeSpaceExW(
            root_path,
            ctypes.byref(free_available),
            ctypes.byref(total_bytes),
            ctypes.byref(total_free),
        ):
            return int(total_bytes.value)
    except Exception:
        pass
    return 0


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
                if size > 0:
                    return size

            length_info = _LengthInfo()
            if _windows_device_io_control(volume.handle, 0x0007405C, length_info):
                size = int(length_info.Length)
                if size > 0:
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

        volume_size_bytes = _windows_volume_total_size(root_path)
        if _size_exceeds_floppy_drive_limit(volume_size_bytes):
            continue

        raw_path = _windows_raw_volume_path(f"{letter}:")
        size_bytes = _windows_detect_floppy_size(raw_path)
        if _size_exceeds_floppy_drive_limit(size_bytes):
            continue
        # Protected or empty USB floppy drives may not look filesystem-ready to
        # Windows but can still be usable once the user chooses the disk size.
        if size_bytes > 0 and size_bytes not in DISK_FORMAT_BY_SIZE and letter not in {"A", "B"}:
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


GREASEWEAZLE_USB_IDS = (
    ("1209", "4D69"),
)


def _hardware_id_looks_like_greaseweazle(text):
    normalized = str(text or "").upper()
    return any(f"VID_{vid}&PID_{pid}" in normalized for vid, pid in GREASEWEAZLE_USB_IDS)


def _normalize_windows_com_port(port_name):
    port = str(port_name or "").strip()
    if not port:
        return ""
    raw_match = re.fullmatch(r"\\\\\.\\(COM\d+)", port, flags=re.IGNORECASE)
    if raw_match:
        return raw_match.group(1).upper()
    if re.fullmatch(r"COM\d+", port, flags=re.IGNORECASE):
        return port.upper()
    return ""


def _extract_windows_com_port(text):
    match = re.search(r"\b(COM\d+)\b", str(text or ""), flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _windows_greaseweazle_device_from_info(name="", device_id="", status="", port_name=""):
    if device_id and not _hardware_id_looks_like_greaseweazle(device_id):
        return None
    port = _normalize_windows_com_port(port_name) or _extract_windows_com_port(name)
    if not port:
        return None

    label = str(name or "").strip() or "Greaseweazle USB Serial Device"
    if "greaseweazle" not in label.lower():
        label = f"Greaseweazle {label}"
    status_text = str(status or "").strip()
    if status_text and status_text.upper() != "OK":
        label = f"{label} [{status_text}]"
    return GreaseweazleDeviceInfo(path=port, label=label)


def _dedupe_greaseweazle_devices(devices):
    deduped = []
    seen_paths = set()
    for device in devices:
        if not isinstance(device, GreaseweazleDeviceInfo):
            continue
        path_key = os.path.normcase(str(device.path or "").strip())
        if not path_key or path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        deduped.append(device)
    return deduped


def _list_windows_greaseweazle_devices_from_pnp():
    if os.name != "nt":
        return []
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return []

    id_pattern = "|".join(f"VID_{vid}&PID_{pid}" for vid, pid in GREASEWEAZLE_USB_IDS)
    script = rf"""
$items = Get-CimInstance Win32_PnPEntity |
    Where-Object {{ $_.DeviceID -match '{id_pattern}' }} |
    ForEach-Object {{
        $portName = $null
        try {{
            $keyPath = 'HKLM:\SYSTEM\CurrentControlSet\Enum\' + $_.DeviceID + '\Device Parameters'
            $portName = (Get-ItemProperty -Path $keyPath -Name PortName -ErrorAction Stop).PortName
        }} catch {{}}
        [PSCustomObject]@{{
            Name = $_.Name
            DeviceID = $_.DeviceID
            Status = $_.Status
            PortName = $portName
        }}
    }}
@($items) | ConvertTo-Json -Depth 4 -Compress
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
    devices = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        device = _windows_greaseweazle_device_from_info(
            name=item.get("Name", ""),
            device_id=item.get("DeviceID", ""),
            status=item.get("Status", ""),
            port_name=item.get("PortName", ""),
        )
        if device is not None:
            devices.append(device)
    return _dedupe_greaseweazle_devices(devices)


def _winreg_query_value(key, value_name):
    try:
        import winreg

        value, _value_type = winreg.QueryValueEx(key, value_name)
    except OSError:
        return ""
    return str(value or "").strip()


def _winreg_subkey_names(key):
    try:
        import winreg
    except ImportError:
        return []

    names = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            break
        index += 1
    return names


def _list_windows_greaseweazle_devices_from_registry():
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    devices = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB") as usb_key:
            hardware_keys = _winreg_subkey_names(usb_key)
    except OSError:
        return []

    for hardware_key_name in hardware_keys:
        if not _hardware_id_looks_like_greaseweazle(hardware_key_name):
            continue
        hardware_path = rf"SYSTEM\CurrentControlSet\Enum\USB\{hardware_key_name}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hardware_path) as hardware_key:
                instance_names = _winreg_subkey_names(hardware_key)
        except OSError:
            continue

        for instance_name in instance_names:
            instance_path = rf"{hardware_path}\{instance_name}"
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, instance_path) as instance_key:
                    friendly_name = _winreg_query_value(instance_key, "FriendlyName")
                    device_desc = _winreg_query_value(instance_key, "DeviceDesc")
            except OSError:
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{instance_path}\Device Parameters") as params_key:
                    port_name = _winreg_query_value(params_key, "PortName")
            except OSError:
                port_name = ""

            device = _windows_greaseweazle_device_from_info(
                name=friendly_name or device_desc,
                device_id=rf"USB\{hardware_key_name}\{instance_name}",
                port_name=port_name,
            )
            if device is not None:
                devices.append(device)
    return _dedupe_greaseweazle_devices(devices)


def _list_windows_greaseweazle_devices():
    return (
        _list_windows_greaseweazle_devices_from_pnp()
        or _list_windows_greaseweazle_devices_from_registry()
    )


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

    if os.name == "nt":
        devices = _list_windows_greaseweazle_devices()
        if devices:
            return devices

    gw = _find_gw()
    if not gw:
        return []

    result = subprocess.run(
        [gw, "info"],
        text=True,
        capture_output=True,
        check=False,
        **windows_subprocess_kwargs(),
    )
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
        raise FloppyImageError(_dependency_command_message(command_name))
    return path


def _mformat_args_for_disk_format(disk_format):
    if not isinstance(disk_format, DiskFormat):
        raise FloppyImageError("Invalid disk format.")
    args = MFORMAT_SIZE_OPTIONS.get(disk_format.key)
    if not args:
        raise FloppyImageError(
            f"Unsupported disk format for image creation: {disk_format.label}. "
            "Choose one of the IBM floppy formats listed in the dialog."
        )
    return list(args)


def _protected_layout_for_disk_format(disk_format):
    if not isinstance(disk_format, DiskFormat):
        return None
    for layout in _PROTECTED_FAT12_LAYOUTS:
        if _layout_total_size(layout) == disk_format.size_bytes:
            return layout
    return None


def _create_blank_fat12_image_from_layout(output_path, layout, volume_label):
    total_size = _layout_total_size(layout)
    payload = bytearray(total_size)
    label_bytes = (volume_label or "NO NAME").encode("ascii", errors="replace")
    serial_seed = label_bytes + str(layout["label"]).encode("ascii", errors="replace")
    serial = zlib.crc32(serial_seed) & 0xFFFFFFFF
    payload[:_YAMAHA_BYTES_PER_SECTOR] = _build_standard_fat12_boot_sector(layout, serial, label_bytes)

    fat_signature = bytes([int(layout["media_descriptor"]) & 0xFF, 0xFF, 0xFF])
    fat_offset = _layout_fat_offset(layout)
    fat_size = _layout_fat_size(layout)
    for fat_index in range(int(layout["num_fats"])):
        offset = fat_offset + fat_index * fat_size
        payload[offset:offset + len(fat_signature)] = fat_signature

    with open(output_path, "wb") as handle:
        handle.write(payload)


def _write_image_direct(source_img, output_path, output_ext, disk_format):
    output_ext = output_ext.lower().lstrip(".")
    if output_ext in RAW_IMAGE_EXTENSIONS:
        shutil.copy2(source_img, output_path)
        return None
    if output_ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise FloppyImageError(_unsupported_image_type_message(output_ext, for_output=True))
    output = _gw_convert(source_img, output_path, disk_format.key)
    return _gw_sector_report(
        "convert",
        _parse_gw_sector_map(output, disk_format),
        title="Greaseweazle Conversion Sector Map",
        summary=f"Converted the image to {output_ext.upper()} using {disk_format.label}.",
        disk_format=disk_format,
    )


def create_blank_floppy_image(output_path, disk_format, volume_label="NO NAME", cancel_callback=None):
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path):
        os.remove(output_path)

    _raise_if_cancelled(cancel_callback)
    layout = None
    if disk_format.key not in MFORMAT_SIZE_OPTIONS:
        layout = _protected_layout_for_disk_format(disk_format)

    if layout is not None:
        _create_blank_fat12_image_from_layout(output_path, layout, volume_label)
    else:
        mformat = _require_command("mformat")
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
            cancel_callback=cancel_callback,
        )
    _raise_if_cancelled(cancel_callback)
    read_image_listing(output_path)
    return output_path


def _write_empty_pianodir_to_image(target_img, temp_dir, metadata=None):
    pianodir_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{PIANODIR_FILENAME}")
    with open(pianodir_path, "wb") as handle:
        handle.write(build_pianodir_bytes([], metadata=metadata))
    _copy_host_file_into_image(target_img, pianodir_path, PIANODIR_FILENAME)


def _copy_host_file_into_image(target_img, host_path, image_path, cancel_callback=None):
    if not os.path.isfile(host_path):
        raise FloppyImageError(f"File to add no longer exists: {host_path}")
    mcopy = _require_command("mcopy")
    _run_command(
        [mcopy, "-i", target_img, host_path, mtools_path(image_path)],
        f"Could not add {os.path.basename(host_path)} to image",
        cancel_callback=cancel_callback,
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
    sector_report_callback=None,
):
    if not file_specs:
        raise FloppyImageError("There are no files to save into an image. Add MIDI or E-SEQ files first.")

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
                    f"'{display_name}' is too large to fit on a {disk_format.label} image. "
                    "Remove the file, choose a larger disk format, or split the set across multiple images."
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
                        f"'{display_name}' is too large to fit on a {disk_format.label} image. "
                        "Remove the file, choose a larger disk format, or split the set across multiple images."
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
                report = _write_image_direct(raw_img, temp_output, output_ext, disk_format)
                os.replace(temp_output, final_path)
                if report is not None and sector_report_callback is not None:
                    sector_report_callback(report)
            finally:
                if os.path.exists(temp_output):
                    os.remove(temp_output)

            written_paths.append(final_path)

        return written_paths
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _disk_format_key(disk_format):
    if isinstance(disk_format, DiskFormat):
        return disk_format.key
    return str(disk_format or "")


def _gw_expected_sectors_per_track(disk_format):
    layout = DISK_FORMAT_TRACK_LAYOUTS.get(_disk_format_key(disk_format))
    if not layout:
        return 0
    return int(layout.get("sectors_per_track") or 0)


_GW_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _clean_gw_output_line(line):
    cleaned = _GW_ANSI_ESCAPE_RE.sub("", str(line or ""))
    return cleaned.replace("\b", "").strip()


def _track_status_line_to_counts(clean_line, expected_sectors):
    track_match = re.match(r"^T(?P<cyl>\d+)\.(?P<head>\d+)(?:\s+<-.*)?\s*:\s*(?P<status>.*)$", clean_line)
    if not track_match:
        return None

    status_text = track_match.group("status") or ""
    found = None
    total = None
    pair_match = re.search(r"\b(\d+)\s*/\s*(\d+)\s+sectors?\b", status_text, flags=re.IGNORECASE)
    if pair_match:
        found = int(pair_match.group(1))
        total = int(pair_match.group(2))
    else:
        found_match = re.search(r"\b(\d+)\s+sectors?\b", status_text, flags=re.IGNORECASE)
        if found_match:
            found = int(found_match.group(1))
            total = expected_sectors or found

    if total is None:
        total = expected_sectors
    if found is None:
        failed = re.search(r"\b(?:fail|failed|error|bad|missing|lost|no\s+flux)\b", status_text, flags=re.IGNORECASE)
        found = 0 if failed else total
    if total <= 0:
        return None

    found = max(0, min(int(found), int(total)))
    return {
        "cylinder": int(track_match.group("cyl")),
        "head": int(track_match.group("head")),
        "found": found,
        "total": int(total),
    }


def _sector_rows_from_track_counts(track_counts):
    if not track_counts:
        return []
    max_cylinder = max(item["cylinder"] for item in track_counts)
    max_sector = max(item["total"] for item in track_counts)
    heads = sorted({item["head"] for item in track_counts})
    grid = {
        (head, sector): [" "] * (max_cylinder + 1)
        for head in heads
        for sector in range(max_sector)
    }
    for item in track_counts:
        cylinder = item["cylinder"]
        head = item["head"]
        found = item["found"]
        total = item["total"]
        for sector in range(total):
            grid.setdefault((head, sector), [" "] * (max_cylinder + 1))
            grid[(head, sector)][cylinder] = "." if sector < found else "x"

    rows = []
    for head in heads:
        for sector in range(max_sector):
            statuses = "".join(grid.get((head, sector), [" "] * (max_cylinder + 1)))
            if statuses.strip():
                rows.append({"head": head, "sector": sector, "statuses": statuses})
    return rows


def _parse_gw_sector_map(output, disk_format=None):
    lines = str(output or "").splitlines()
    rows = []
    track_counts = []
    found = None
    total = None
    expected_sectors = _gw_expected_sectors_per_track(disk_format)
    for line in lines:
        clean_line = _clean_gw_output_line(line)
        found_match = re.search(r"\bFound\s+(\d+)\s+sectors\s+of\s+(\d+)", clean_line)
        if found_match:
            found = int(found_match.group(1))
            total = int(found_match.group(2))
            continue
        row_match = re.match(r"^(?P<head>\d+)\.\s*(?P<sector>\d+):\s*(?P<statuses>\S+)\s*$", clean_line)
        if row_match:
            statuses = row_match.group("statuses")
            rows.append(
                {
                    "head": int(row_match.group("head")),
                    "sector": int(row_match.group("sector")),
                    "statuses": statuses,
                }
            )
            continue
        track_count = _track_status_line_to_counts(clean_line, expected_sectors)
        if track_count is not None:
            track_counts.append(track_count)

    if not rows and track_counts:
        rows = _sector_rows_from_track_counts(track_counts)
        found = sum(item["found"] for item in track_counts)
        total = sum(item["total"] for item in track_counts)

    if not rows and found is None and total is None:
        return {}

    good = 0
    bad = 0
    for row in rows:
        for char in row["statuses"]:
            if char == ".":
                good += 1
            elif str(char).strip():
                bad += 1
    if found is not None and total is not None and found < total:
        bad = max(bad, total - found)

    return {
        "rows": rows,
        "found": found,
        "total": total,
        "good": good,
        "bad": bad,
        "has_failures": bad > 0,
    }


def _gw_sector_report(
    report_type,
    sector_map,
    *,
    title="",
    summary="",
    disk_format=None,
    allow_empty_rows=False,
):
    if not sector_map or (not allow_empty_rows and not sector_map.get("rows")):
        return None
    return {
        "type": str(report_type or "greaseweazle"),
        "title": title or "Greaseweazle Sector Map",
        "summary": summary or "",
        "sector_map": sector_map,
        "disk_format": disk_format,
        "allow_empty_rows": bool(allow_empty_rows),
    }


def _gw_sector_reports(*reports):
    return tuple(report for report in reports if report)


def _gw_recovery_sector_report(sector_map, *, summary="", disk_format=None):
    if not sector_map:
        return None
    return _gw_sector_report(
        "recover",
        sector_map,
        title="Greaseweazle Recovery Sector Map",
        summary=summary,
        disk_format=disk_format,
        allow_empty_rows=True,
    )


def _gw_recovery_no_sector_report(*, summary="", disk_format=None):
    return _gw_recovery_sector_report(
        {"rows": [], "found": None, "total": None, "good": 0, "bad": 0, "has_failures": False},
        summary=summary or "No Greaseweazle sector map was available for this recovery.",
        disk_format=disk_format,
    )


def _gw_recovery_sector_note(sector_map, disk_format):
    if not sector_map or not sector_map.get("has_failures"):
        return ""
    found = sector_map.get("found")
    total = sector_map.get("total")
    bad = int(sector_map.get("bad") or 0)
    format_label = disk_format.label if isinstance(disk_format, DiskFormat) else "the selected format"
    if found is not None and total is not None:
        return (
            f" Greaseweazle reported {found} of {total} expected sector position(s) "
            f"while converting as {format_label}; recovery continued using the partial image."
        )
    if bad:
        return (
            f" Greaseweazle reported {bad} bad or missing sector position(s) "
            f"while converting as {format_label}; recovery continued using the partial image."
        )
    return (
        f" Greaseweazle reported bad or missing sectors while converting as {format_label}; "
        "recovery continued using the partial image."
    )


def _gw_convert(input_path, output_path, disk_format, cancel_callback=None, *, allow_sector_failures=False):
    gw = _find_gw()
    if not gw:
        raise FloppyImageError(_missing_greaseweazle_message("convert this image format"))
    if os.path.exists(output_path):
        os.remove(output_path)
    try:
        output = _run_command(
            [gw, "convert", f"--format={disk_format}", input_path, output_path],
            "Image conversion failed",
            cancel_callback=cancel_callback,
        )
    except FloppyOperationCancelled:
        raise
    except FloppyImageError as exc:
        sector_map = _parse_gw_sector_map(str(exc), disk_format)
        raise GreaseweazleConversionError(
            str(exc),
            sector_map=sector_map,
            disk_format=GW_FORMAT_BY_KEY.get(_disk_format_key(disk_format)),
            capture_path=input_path,
        ) from exc

    sector_map = _parse_gw_sector_map(output, disk_format)
    if sector_map.get("has_failures") and not allow_sector_failures:
        found = sector_map.get("found")
        total = sector_map.get("total")
        summary = ""
        if found is not None and total is not None:
            summary = f" Greaseweazle found {found} of {total} expected sector(s)."
        raise GreaseweazleConversionError(
            f"Greaseweazle conversion reported unreadable or missing sectors.{summary}",
            sector_map=sector_map,
            disk_format=GW_FORMAT_BY_KEY.get(_disk_format_key(disk_format)),
            capture_path=input_path,
            reason="sector_failure",
        )
    return output


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


def _handle_gw_track_progress_line(progress_callback, state, line, *, action="Reading"):
    clean_line = _clean_gw_output_line(line)
    if not clean_line:
        return
    if clean_line.startswith("*** "):
        return

    header_match = re.match(r"^(?:Reading|Writing)\s+(?P<trackspec>.+?)(?:\s+revs=\d+)?$", clean_line)
    if header_match:
        total_tracks = _extract_gw_track_total(header_match.group("trackspec"))
        state["total_tracks"] = total_tracks
        state["seen_tracks"] = set()
        if total_tracks > 0:
            _notify_progress(
                progress_callback,
                0,
                total_tracks,
                f"{action} floppy via Greaseweazle (0/{total_tracks} tracks)...",
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
            message = f"{action} {track_label} ({completed_tracks}/{total_tracks})..."
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


def _handle_gw_read_progress_line(progress_callback, state, line):
    _handle_gw_track_progress_line(progress_callback, state, line, action="Reading")


def _handle_gw_write_progress_line(progress_callback, state, line):
    _handle_gw_track_progress_line(progress_callback, state, line, action="Writing")


def _gw_reset_device(gw, source, progress_callback=None, cancel_callback=None, *, operation_label="operation"):
    args = [gw, "reset"]
    device_path = str(getattr(source, "device_path", "") or "").strip()
    if device_path:
        args.append(f"--device={device_path}")

    _notify_progress(progress_callback, 0, 0, "Resetting Greaseweazle device...")
    try:
        _run_command(args, "Greaseweazle reset failed", cancel_callback=cancel_callback)
    except FloppyOperationCancelled:
        raise
    except FloppyImageError:
        _notify_progress(
            progress_callback,
            0,
            0,
            f"Greaseweazle reset failed; continuing with {operation_label}...",
        )
        return False
    _notify_progress(progress_callback, 0, 0, f"Greaseweazle reset complete; starting {operation_label}...")
    return True


def _gw_read_floppy(source, output_path, progress_callback=None, cancel_callback=None):
    gw = _find_gw()
    if not gw:
        raise FloppyImageError(_missing_greaseweazle_message("read from a floppy drive"))
    if os.path.exists(output_path):
        os.remove(output_path)
    _gw_reset_device(gw, source, progress_callback, cancel_callback, operation_label="read")

    raw_capture = (
        bool(source.archival_quality)
        or image_extension(output_path) == "scp"
        or str(getattr(source, "capture_output_ext", "") or "").lower().lstrip(".") == "scp"
    )
    args = [
        gw,
        "read",
        f"--drive={source.drive}",
    ]
    if raw_capture:
        args.append("--raw")
    elif source.disk_format is not None:
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
        clean_line = _clean_gw_output_line(line)
        if clean_line.startswith("Command Failed:"):
            progress_state["command_failed"] = clean_line
        _handle_gw_read_progress_line(progress_callback, progress_state, line)

    output = _run_streaming_command(
        args,
        "Greaseweazle read failed",
        line_callback=_progress_line_callback,
        env=env,
        cancel_callback=cancel_callback,
    )
    _raise_if_cancelled(cancel_callback)
    if progress_state["command_failed"]:
        detail = progress_state["command_failed"].split(":", 1)[1].strip()
        raise FloppyImageError(
            f"Greaseweazle read failed: {detail}. "
            "Check the selected drive, disk format, cable orientation, and that a readable disk is inserted."
        )
    return _parse_gw_sector_map(output, source.disk_format)


def _gw_write_floppy(source, input_path, progress_callback=None, cancel_callback=None):
    gw = _find_gw()
    if not gw:
        raise FloppyImageError(_missing_greaseweazle_message("write to a floppy drive"))
    _gw_reset_device(gw, source, progress_callback, cancel_callback, operation_label="write")

    args = [
        gw,
        "write",
        f"--drive={source.drive}",
        f"--format={source.disk_format.key}",
    ]
    if source.device_path:
        args.append(f"--device={source.device_path}")
    args.append(input_path)

    progress_state = {"total_tracks": 0, "seen_tracks": set()}

    def _progress_line_callback(line):
        _handle_gw_write_progress_line(progress_callback, progress_state, line)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    output = _run_streaming_command(
        args,
        "Greaseweazle write failed",
        line_callback=_progress_line_callback,
        env=env,
        cancel_callback=cancel_callback,
    )
    return _parse_gw_sector_map(output, source.disk_format)


def _normalize_image_path(path):
    cleaned = path.replace("\\", "/")
    if cleaned.startswith("::"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


_WINDOWS_VOLUME_METADATA_DIR_NAMES = {
    "SYSTEM VOLUME INFORMATION",
    "SYSTEM~1",
}

_WINDOWS_VOLUME_METADATA_FILE_NAMES = {
    "INDEXERVOLUMEGUID",
    "INDEXE~1",
    "WPSETTINGS.DAT",
    "WPSETT~1.DAT",
}


def _is_windows_volume_metadata_path(path):
    normalized = _normalize_image_path(str(path or ""))
    parts = [
        part.strip().rstrip(".").upper()
        for part in normalized.split("/")
        if part.strip()
    ]
    if not parts:
        return False
    if any(part in _WINDOWS_VOLUME_METADATA_DIR_NAMES for part in parts):
        return True
    return parts[-1] in _WINDOWS_VOLUME_METADATA_FILE_NAMES


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
    ERROR_INVALID_FUNCTION = 1
    ERROR_NOT_READY = 21

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

    def write_file(self, input_path, progress_callback=None, cancel_callback=None):
        self._seek(0, "start of floppy device")
        total_size = os.path.getsize(input_path)
        written_total = 0
        chunk_size = 8 * 1024
        if progress_callback is not None and total_size > 0:
            progress_callback(0, 100, f"Writing floppy: 0 B of {display_bytes(total_size)}...")
        with open(input_path, "rb") as handle:
            while True:
                _raise_if_cancelled(cancel_callback)
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
                    progress = min(98, int((written_total / total_size) * 98))
                    progress_callback(progress, 100, f"Writing floppy: {display_bytes(written_total)} of {display_bytes(total_size)}...")
        _raise_if_cancelled(cancel_callback)
        if progress_callback is not None and total_size > 0:
            progress_callback(99, 100, "Finalizing floppy write...")
        if not self._kernel32.FlushFileBuffers(self.handle):
            error_code = self._ctypes.get_last_error()
            if error_code in {self.ERROR_INVALID_FUNCTION, self.ERROR_NOT_READY}:
                if progress_callback is not None and total_size > 0:
                    progress_callback(
                        100,
                        100,
                        "Writing floppy complete; Windows did not confirm the final flush.",
                    )
                return
            raise FloppyImageError(_windows_last_error_message(f"Could not flush floppy device {self.path}"))
        if progress_callback is not None and total_size > 0:
            progress_callback(100, 100, "Writing floppy complete.")


def _open_block_device_for_read(device_path):
    if os.name == "nt":
        return _WindowsVolumeHandle(device_path, write=False)
    try:
        return os.open(device_path, os.O_RDONLY)
    except OSError as exc:
        detail = f"Could not open floppy device {device_path}: {exc}"
        lower = str(exc).lower()
        if "permission denied" in lower:
            detail += (
                "\n\nDirect floppy reads require read permission for the block device. "
                "Make sure the disk is not mounted and that your user has access to the device."
            )
        elif "no medium" in lower or "no media" in lower:
            detail += "\n\nInsert a floppy disk and try again."
        elif "busy" in lower:
            detail += "\n\nClose programs using the disk, unmount it if needed, and try again."
        raise FloppyImageError(detail) from exc


def _close_block_device(device):
    if hasattr(device, "close"):
        device.close()
    else:
        os.close(device)


def _read_windows_block_device_bytes(device_path, size_bytes, progress_callback=None, cancel_callback=None):
    if os.name == "nt":
        if not size_bytes:
            raise FloppyImageError(
                "Could not read the Windows floppy device because its disk size could not be detected. "
                "Insert a formatted 720K or 1.44M floppy, or use Greaseweazle with an explicit disk format."
            )
        chunks = []
        remaining = int(size_bytes)
        cursor = 0
        chunk_size = 64 * 1024
        last_progress = -1
        with _WindowsVolumeHandle(device_path, write=False) as volume:
            while remaining > 0:
                _raise_if_cancelled(cancel_callback)
                current_size = min(chunk_size, remaining)
                chunk = volume.read_at(cursor, current_size, "floppy image")
                if not chunk:
                    raise FloppyImageError(
                        "Could not read floppy device: the drive stopped returning data before the full disk was read. "
                        "Check that a disk is inserted and that the selected format matches the disk."
                    )
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
        _raise_if_cancelled(cancel_callback)
        return b"".join(chunks)
    raise FloppyImageError("Windows raw floppy byte reads are only available on Windows.")


def _read_block_device(device_path, output_path, size_bytes, progress_callback=None, cancel_callback=None):
    if os.name == "nt":
        data = _read_windows_block_device_bytes(
            device_path,
            size_bytes,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        with open(output_path, "wb") as output:
            output.write(data)
        return

    total_size = int(size_bytes or 0)
    copied = 0
    chunk_size = 64 * 1024
    try:
        with open(device_path, "rb", buffering=0) as source, open(output_path, "wb") as output:
            while True:
                _raise_if_cancelled(cancel_callback)
                if total_size:
                    remaining = total_size - copied
                    if remaining <= 0:
                        break
                    chunk = source.read(min(chunk_size, remaining))
                else:
                    chunk = source.read(chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                copied += len(chunk)
                if progress_callback is not None and total_size > 0:
                    progress = min(70, int((copied / total_size) * 70))
                    progress_callback(
                        progress,
                        100,
                        f"Reading floppy image: {display_bytes(copied)} of {display_bytes(total_size)}...",
                    )
        if total_size and copied < total_size:
            raise FloppyImageError(
                "Could not read floppy device: the drive stopped returning data before the full disk was read. "
                "Check that a disk is inserted and that the selected format matches the disk."
            )
    except OSError as exc:
        raise FloppyImageError(f"Could not read floppy device {device_path}: {exc}") from exc


def _capture_temp_output_path(output_path, *, suffix=None):
    output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(output_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    output_name = os.path.basename(output_path) or "floppy_image"
    temp_suffix = suffix if suffix is not None else os.path.splitext(output_name)[1]
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{output_name}.",
        suffix=temp_suffix or ".tmp",
        dir=output_dir,
    )
    os.close(fd)
    os.remove(temp_path)
    return temp_path


def _finish_capture_output(temp_path, output_path):
    output_path = os.path.abspath(output_path)
    os.replace(temp_path, output_path)
    return output_path


def capture_floppy_drive_image(
    drive_info,
    output_path,
    disk_format=None,
    progress_callback=None,
    cancel_callback=None,
):
    """Copy a physical floppy to a raw sector image without opening/scanning it."""
    if not isinstance(drive_info, FloppyDriveInfo):
        raise FloppyImageError("Invalid floppy drive selection.")

    read_size = 0
    if isinstance(disk_format, DiskFormat):
        read_size = disk_format.size_bytes
    else:
        read_size = int(drive_info.size_bytes or 0)
    if read_size <= 0:
        raise FloppyImageError(
            "Could not choose a floppy image size. Select the disk size before imaging the disk."
        )

    output_path = os.path.abspath(output_path)
    temp_path = _capture_temp_output_path(output_path)
    try:
        _notify_progress(
            progress_callback,
            0,
            100,
            f"Imaging floppy: 0 B of {display_bytes(read_size)}...",
        )
        _read_block_device(
            drive_info.path,
            temp_path,
            read_size,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 95, 100, "Saving floppy image...")
        final_path = _finish_capture_output(temp_path, output_path)
        temp_path = ""
        _notify_progress(progress_callback, 100, 100, "Floppy image saved.")
        return final_path
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def capture_greaseweazle_floppy_image(
    gw_source,
    output_path,
    progress_callback=None,
    cancel_callback=None,
):
    """Read a Greaseweazle floppy image without converting/opening it afterward."""
    if not isinstance(gw_source, GreaseweazleFloppySource):
        raise FloppyImageError("Invalid Greaseweazle source selection.")

    output_path = os.path.abspath(output_path)
    output_ext = image_extension(output_path)
    raw_capture = (
        gw_source.archival_quality
        or output_ext == "scp"
        or str(getattr(gw_source, "capture_output_ext", "") or "").lower().lstrip(".") == "scp"
    )
    temp_suffix = ".scp" if raw_capture else f".{output_ext or 'hfe'}"
    temp_path = _capture_temp_output_path(output_path, suffix=temp_suffix)
    try:
        if raw_capture:
            capture_kind = "SCP flux capture"
        elif output_ext == "hfe":
            capture_kind = "HFE image"
        elif output_ext in RAW_IMAGE_EXTENSIONS:
            capture_kind = "raw sector image"
        else:
            capture_kind = f"{(output_ext or 'HFE').upper()} image"
        _notify_progress(
            progress_callback,
            0,
            100,
            f"Reading {capture_kind} via Greaseweazle drive {gw_source.drive}...",
        )
        sector_map = _gw_read_floppy(
            gw_source,
            temp_path,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 95, 100, f"Saving {capture_kind}...")
        final_path = _finish_capture_output(temp_path, output_path)
        temp_path = ""
        _notify_progress(progress_callback, 100, 100, "Floppy image saved.")
        return final_path, sector_map
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def convert_greaseweazle_image_file(
    source_path,
    output_path,
    disk_format,
    progress_callback=None,
    cancel_callback=None,
    *,
    allow_sector_failures=True,
):
    if not isinstance(disk_format, DiskFormat):
        raise FloppyImageError("Invalid Greaseweazle conversion format.")
    if not os.path.isfile(source_path):
        raise FloppyImageError(f"The source image was not found: {source_path}")

    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temp_path = _capture_temp_output_path(output_path, suffix=os.path.splitext(output_path)[1] or ".img")
    try:
        _notify_progress(
            progress_callback,
            0,
            100,
            f"Converting {os.path.basename(source_path)} as {disk_format.label}...",
        )
        conversion_output = _gw_convert(
            source_path,
            temp_path,
            disk_format.key,
            cancel_callback=cancel_callback,
            allow_sector_failures=allow_sector_failures,
        )
        sector_map = _parse_gw_sector_map(conversion_output, disk_format)
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 95, 100, "Saving converted image...")
        final_path = _finish_capture_output(temp_path, output_path)
        temp_path = ""
        _notify_progress(progress_callback, 100, 100, "Converted image saved.")
        return final_path, sector_map
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _read_device_chunk_for_recovery(device, offset, size):
    if hasattr(device, "read_at"):
        return device.read_at(offset, size, "floppy recovery image")
    return os.pread(device, size, offset)


def _read_block_device_recovery_image(device_path, output_path, size_bytes, progress_callback=None, cancel_callback=None):
    total_size = int(size_bytes or 0)
    if total_size <= 0:
        total_size = _YAMAHA_TOTAL_SIZE

    image = bytearray(total_size)
    bad_ranges = []
    chunk_size = 64 * 1024
    sector_size = _YAMAHA_BYTES_PER_SECTOR
    last_progress = -1
    device = _open_block_device_for_read(device_path)
    try:
        offset = 0
        while offset < total_size:
            _raise_if_cancelled(cancel_callback)
            current_size = min(chunk_size, total_size - offset)
            try:
                chunk = _read_device_chunk_for_recovery(device, offset, current_size)
                if len(chunk) != current_size:
                    raise FloppyImageError("short read")
                image[offset:offset + current_size] = chunk
            except Exception:
                sector_end = offset + current_size
                sector_offset = offset
                while sector_offset < sector_end:
                    _raise_if_cancelled(cancel_callback)
                    sector_read_size = min(sector_size, sector_end - sector_offset)
                    try:
                        sector = _read_device_chunk_for_recovery(device, sector_offset, sector_read_size)
                        if len(sector) != sector_read_size:
                            raise FloppyImageError("short read")
                        image[sector_offset:sector_offset + sector_read_size] = sector
                    except Exception:
                        bad_ranges.append((sector_offset, sector_read_size))
                    sector_offset += sector_read_size

            offset += current_size
            if progress_callback is not None and total_size > 0:
                progress = min(70, int((offset / total_size) * 70))
                if progress > last_progress:
                    last_progress = progress
                    unreadable = ""
                    if bad_ranges:
                        unreadable = f" ({len(bad_ranges)} unreadable sector(s) filled with zeros)"
                    progress_callback(
                        progress,
                        100,
                        f"Copying full floppy image for recovery: {display_bytes(offset)} of {display_bytes(total_size)}{unreadable}...",
                    )
        _raise_if_cancelled(cancel_callback)
    finally:
        _close_block_device(device)

    with open(output_path, "wb") as output:
        output.write(image)

    if bad_ranges:
        return (
            f"Full recovery image copied with {len(bad_ranges)} unreadable sector(s) replaced by zeros; "
            "songs touching those sectors may be incomplete."
        )
    return "Full recovery image copied successfully."


def _write_block_device(input_path, device_path, progress_callback=None, cancel_callback=None):
    if os.name == "nt":
        permission_hint = (
            "Direct floppy writes on Windows require permission to lock and write the raw drive. "
            "Close Explorer windows using the drive and run the app as administrator if Windows denies access. "
            "You can also use Save As Image as a safer fallback."
        )
        try:
            with _WindowsVolumeHandle(device_path, write=True) as volume:
                volume.lock_for_write()
                try:
                    volume.write_file(
                        input_path,
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                finally:
                    volume.unlock_after_write()
            return
        except FloppyOperationCancelled:
            raise
        except FloppyImageError as exc:
            detail = str(exc)
            if "Access is denied" in detail or "denied" in detail.lower() or "lock" in detail.lower():
                detail = f"{detail}\n\n{permission_hint}"
            raise FloppyImageError(detail) from exc

    permission_hint = (
        "Direct floppy writes require write permission for the block device. "
        "On Linux, make sure the disk is not mounted and that your user has write "
        "access to the device, or run the app with appropriate elevated permissions. "
        "You can also use Save As Image as a safer fallback."
    )
    if os.name == "posix" and not os.access(device_path, os.W_OK):
        raise FloppyImageError(
            f"Could not write floppy device {device_path}: permission denied.\n\n{permission_hint}"
        )
    try:
        total_size = os.path.getsize(input_path)
        written = 0
        chunk_size = 8 * 1024
        if progress_callback is not None and total_size > 0:
            progress_callback(0, 100, f"Writing floppy: 0 B of {display_bytes(total_size)}...")
        with open(input_path, "rb") as source, open(device_path, "r+b", buffering=0) as target:
            while True:
                _raise_if_cancelled(cancel_callback)
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                target.write(chunk)
                written += len(chunk)
                if progress_callback is not None and total_size > 0:
                    progress = min(98, int((written / total_size) * 98))
                    progress_callback(
                        progress,
                        100,
                        f"Writing floppy: {display_bytes(written)} of {display_bytes(total_size)}...",
                    )
            if progress_callback is not None and total_size > 0:
                progress_callback(99, 100, "Finalizing floppy write...")
            target.flush()
            os.fsync(target.fileno())
        if progress_callback is not None and total_size > 0:
            progress_callback(100, 100, "Writing floppy complete.")
        _raise_if_cancelled(cancel_callback)
    except OSError as exc:
        detail = f"Could not write floppy device {device_path}: {exc}"
        if "Permission denied" in detail or "Text file busy" in detail or "Device or resource busy" in detail:
            detail = f"{detail}\n\n{permission_hint}"
        raise FloppyImageError(detail) from exc
    except FloppyOperationCancelled:
        raise
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


def _parse_7z_modified_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError, OSError):
        return None


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
        if folder == "-" and path and not _is_windows_volume_metadata_path(path):
            size = _parse_int(record.get("Size"), 0)
            packed_size = _parse_int(record.get("Packed Size"), allocated_size(size, cluster_size))
            entries.append(
                ImageEntry(
                    path=path,
                    size=size,
                    packed_size=packed_size,
                    attributes=record.get("Attributes", ""),
                    modified_time=_parse_7z_modified_timestamp(record.get("Modified")),
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


def _read_windows_filesystem_drive_listing(drive_path):
    root = _windows_filesystem_root(drive_path)
    if not root:
        raise FloppyImageError(f"Invalid Windows floppy drive path: {drive_path}")

    entries = []
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        raise FloppyImageError(
            f"Could not read floppy drive {drive_path}: {exc}. "
            "If this is a protected or damaged disk, use Read Floppy with recovery instead."
        ) from exc

    try:
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not _is_windows_volume_metadata_path(
                    os.path.relpath(os.path.join(current_root, dirname), root)
                )
            ]
            for filename in filenames:
                full_path = os.path.join(current_root, filename)
                relative_path = os.path.relpath(full_path, root)
                image_path = _normalize_image_path(relative_path)
                if _is_windows_volume_metadata_path(image_path):
                    continue
                try:
                    stat_result = os.stat(full_path)
                except OSError:
                    continue
                entries.append(
                    ImageEntry(
                        path=image_path,
                        size=stat_result.st_size,
                        packed_size=allocated_size(stat_result.st_size, 1024),
                        attributes="",
                        modified_time=stat_result.st_mtime,
                    )
                )
    except OSError as exc:
        raise FloppyImageError(f"Could not list files on floppy drive {drive_path}: {exc}") from exc

    entries.sort(key=lambda item: item.path.lower())
    return ImageListing(entries=entries, free_space=usage.free, cluster_size=1024)


def _windows_drive_file_path(root, image_path):
    parts = _split_image_path_components(image_path)
    if not parts or any(part in {".", ".."} for part in parts):
        raise FloppyImageError(f"Invalid floppy file path: {image_path}")
    return ntpath.join(root, *parts)


def _windows_mcopy_host_path(root, image_path):
    match = re.fullmatch(r"([A-Za-z]):\\", str(root or ""))
    if not match:
        raise FloppyImageError(f"Invalid Windows floppy drive root: {root}")
    parts = _split_image_path_components(image_path)
    if not parts or any(part in {".", ".."} for part in parts):
        raise FloppyImageError(f"Invalid floppy file path: {image_path}")
    return f"//?/{match.group(1).upper()}:/" + "/".join(parts)


def _windows_raw_write_denied(exc):
    lower = str(exc or "").lower()
    return (
        os.name == "nt"
        and (
            "access is denied" in lower
            or "permission denied" in lower
            or "could not lock" in lower
        )
    )


def _image_entry_key(entry):
    return _normalize_image_path(entry.path).upper()


def _must_refresh_floppy_sync_entry(entry):
    return is_eseq_directory_path(entry.path)


def _files_have_same_content(path_a, path_b):
    try:
        if os.path.getsize(path_a) != os.path.getsize(path_b):
            return False
        with open(path_a, "rb") as handle_a, open(path_b, "rb") as handle_b:
            while True:
                chunk_a = handle_a.read(64 * 1024)
                chunk_b = handle_b.read(64 * 1024)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except OSError:
        return False


def _is_block_device_path(path):
    if os.name != "posix":
        return False
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


def _read_fat12_block_device_listing(device_path):
    fd = os.open(device_path, os.O_RDONLY)
    try:
        boot_sector = _read_device_exact(fd, 0, _YAMAHA_BYTES_PER_SECTOR, "floppy boot sector")
        geometry = _geometry_from_boot_sector(boot_sector)
        if geometry is None:
            raise FloppyImageError(
                "Could not parse a FAT12 boot sector on this floppy. "
                "The disk may not be an IBM/Yamaha floppy, or it may need recovery."
            )
        fat = _read_device_exact(fd, geometry.fat_offset, geometry.fat_size, "floppy FAT")
        root_dir = _read_device_exact(fd, geometry.root_offset, geometry.root_size, "floppy root directory")
    finally:
        os.close(fd)

    if any(
        entry["attr"] & 0x10 and not _is_windows_volume_metadata_path(entry["name"])
        for entry in _iter_fat_directory_entries(root_dir)
    ):
        return _read_fat12_image_listing(device_path)

    data = boot_sector.ljust(geometry.data_offset, b"\x00")
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


def read_image_listing(img_path):
    if os.name == "nt" and _windows_filesystem_root(img_path):
        return _read_windows_filesystem_drive_listing(img_path)
    if _is_block_device_path(img_path):
        return _read_fat12_block_device_listing(img_path)
    try:
        return _read_fat12_image_listing(img_path)
    except FloppyImageError as fat_exc:
        if not shutil.which("7z"):
            raise fat_exc
        return _read_image_listing_with_7z(img_path)


def _u16le(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def _fat_datetime_to_timestamp(time_word, date_word):
    if not date_word:
        return None
    day = date_word & 0x1F
    month = (date_word >> 5) & 0x0F
    year = ((date_word >> 9) & 0x7F) + 1980
    second = (time_word & 0x1F) * 2
    minute = (time_word >> 5) & 0x3F
    hour = (time_word >> 11) & 0x1F
    try:
        return datetime.datetime(year, month, day, hour, minute, second).timestamp()
    except (ValueError, OSError):
        return None


def _protected_layout_hint_from_boot_sector(sector0):
    if len(sector0) < _YAMAHA_BYTES_PER_SECTOR:
        return None
    bytes_per_sector = _u16le(sector0, 11)
    if bytes_per_sector != _YAMAHA_BYTES_PER_SECTOR:
        return None

    total_sectors = _u16le(sector0, 19) or int.from_bytes(sector0[32:36], "little")
    if total_sectors <= 0:
        return None

    for layout in _PROTECTED_FAT12_LAYOUTS:
        if (
            int(layout["bytes_per_sector"]) == bytes_per_sector
            and int(layout["sectors_per_cluster"]) == sector0[13]
            and int(layout["reserved_sectors"]) == _u16le(sector0, 14)
            and int(layout["num_fats"]) == sector0[16]
            and int(layout["root_entries"]) == _u16le(sector0, 17)
            and int(layout["total_sectors"]) == total_sectors
            and int(layout["media_descriptor"]) == sector0[21]
            and int(layout["sectors_per_fat"]) == _u16le(sector0, 22)
            and int(layout["sectors_per_track"]) == _u16le(sector0, 24)
            and int(layout["num_heads"]) == _u16le(sector0, 26)
        ):
            return layout
    return None


def _validate_converted_image_matches_boot_hint(candidate_path, disk_format):
    try:
        with open(candidate_path, "rb") as handle:
            sector0 = handle.read(_YAMAHA_BYTES_PER_SECTOR)
    except OSError as exc:
        raise FloppyImageError(f"Could not inspect converted image: {exc}") from exc

    layout = _protected_layout_hint_from_boot_sector(sector0)
    if layout is None:
        return

    hinted_size = _layout_total_size(layout)
    actual_size = os.path.getsize(candidate_path)
    if hinted_size != actual_size or hinted_size != disk_format.size_bytes:
        suggested_format = DISK_FORMAT_BY_SIZE.get(hinted_size)
        raise ConvertedImageFormatMismatchError(
            f"Converted image appears to be {layout['label']}, not {disk_format.label}. "
            "Trying another disk geometry.",
            suggested_format=suggested_format,
            hinted_label=str(layout.get("label") or ""),
        )


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


def _fat12_geometry_from_electone_mdr_geometry(geometry):
    return Fat12Geometry(
        bytes_per_sector=geometry.bytes_per_sector,
        sectors_per_cluster=geometry.sectors_per_cluster,
        reserved_sectors=geometry.reserved_sectors,
        num_fats=geometry.num_fats,
        root_entries=geometry.root_entries,
        total_sectors=geometry.total_sectors,
        sectors_per_fat=geometry.sectors_per_fat,
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


def _read_device_exact(device, offset, size, label, cancel_callback=None):
    chunks = []
    remaining = int(size)
    cursor = int(offset)
    while remaining > 0:
        _raise_if_cancelled(cancel_callback)
        try:
            if hasattr(device, "read_at"):
                chunk = device.read_at(cursor, remaining, label)
            else:
                chunk = os.pread(device, remaining, cursor)
        except OSError as exc:
            raise FloppyImageError(f"Could not read {label}: {exc}") from exc
        if not chunk:
            raise FloppyImageError(
                f"Could not read {label}: the floppy stopped returning data before the requested sector was read. "
                "Check the disk and try again, or use Greaseweazle for a lower-level read."
            )
        chunks.append(chunk)
        cursor += len(chunk)
        remaining -= len(chunk)
    _raise_if_cancelled(cancel_callback)
    return b"".join(chunks)


def _try_read_device_exact(device, offset, size, cancel_callback=None):
    try:
        return _read_device_exact(device, offset, size, "floppy sector", cancel_callback=cancel_callback)
    except FloppyOperationCancelled:
        raise
    except FloppyImageError:
        return None


def _read_device_best_effort(device, offset, size, label, *, sector_size=_YAMAHA_BYTES_PER_SECTOR, cancel_callback=None):
    try:
        return _read_device_exact(device, offset, size, label, cancel_callback=cancel_callback), []
    except FloppyOperationCancelled:
        raise
    except FloppyImageError:
        pass

    chunks = []
    bad_ranges = []
    remaining = int(size)
    cursor = int(offset)
    sector_size = max(1, int(sector_size or _YAMAHA_BYTES_PER_SECTOR))
    while remaining > 0:
        _raise_if_cancelled(cancel_callback)
        current_size = min(sector_size, remaining)
        try:
            chunk = _read_device_exact(
                device,
                cursor,
                current_size,
                label,
                cancel_callback=cancel_callback,
            )
        except FloppyOperationCancelled:
            raise
        except FloppyImageError:
            chunk = b"\x00" * current_size
            bad_ranges.append((cursor, current_size))
        chunks.append(chunk)
        cursor += current_size
        remaining -= current_size
    _raise_if_cancelled(cancel_callback)
    return b"".join(chunks), bad_ranges


def _read_fat_area_best_effort(device, geometry, media_descriptor, cancel_callback=None):
    fat_copies = []
    bad_by_copy = []
    bad_ranges = []
    for fat_index in range(geometry.num_fats):
        copy_offset = geometry.fat_offset + fat_index * geometry.fat_size
        fat_copy, copy_bad_ranges = _read_device_best_effort(
            device,
            copy_offset,
            geometry.fat_size,
            f"floppy FAT {fat_index + 1}",
            sector_size=geometry.bytes_per_sector,
            cancel_callback=cancel_callback,
        )
        fat_copies.append(fat_copy)
        bad_ranges.extend(copy_bad_ranges)
        bad_by_copy.append(
            {
                max(0, int((bad_offset - copy_offset) // geometry.bytes_per_sector))
                for bad_offset, _bad_size in copy_bad_ranges
            }
        )

    valid_copies = [
        index
        for index, fat_copy in enumerate(fat_copies)
        if _fat_signature_at(fat_copy, 0, media_descriptor)
    ]
    if not valid_copies:
        raise FloppyImageError("Could not read a valid FAT from the floppy.")

    merged = bytearray(geometry.fat_size)
    sector_count = int(math.ceil(geometry.fat_size / geometry.bytes_per_sector))
    for sector_index in range(sector_count):
        sector_start = sector_index * geometry.bytes_per_sector
        sector_end = min(geometry.fat_size, sector_start + geometry.bytes_per_sector)
        chosen = None
        for copy_index in valid_copies:
            if sector_index not in bad_by_copy[copy_index]:
                chosen = fat_copies[copy_index][sector_start:sector_end]
                break
        if chosen is None:
            chosen = fat_copies[valid_copies[0]][sector_start:sector_end]
        merged[sector_start:sector_end] = chosen

    if not _fat_signature_at(merged, 0, media_descriptor):
        raise FloppyImageError("Could not reconstruct a valid FAT from the floppy.")
    return bytes(merged) * geometry.num_fats, bad_ranges


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
        name = _decode_dos_directory_name(entry[:11])
        if not name or _is_windows_volume_metadata_path(name):
            continue
        if attr & 0x10:
            raise FastFloppyReadError(
                "Fast floppy read does not support disks with subdirectories. "
                "Use image loading or Greaseweazle for this disk.",
                fallback_allowed=True,
            )

        yield {
            "name": name,
            "attr": attr,
            "cluster": _u16le(entry, 26),
            "size": int.from_bytes(entry[28:32], "little"),
            "modified_time": _fat_datetime_to_timestamp(_u16le(entry, 22), _u16le(entry, 24)),
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
            raise FloppyImageError("FAT12 cluster chain contains a bad cluster marker; the disk or image may be damaged.")
        if next_cluster < 2:
            break
        cluster = next_cluster

    if len(clusters) < needed_clusters:
        raise FloppyImageError("FAT12 cluster chain ended before the file data was complete; the disk or image may be damaged.")
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
            raise FloppyImageError("FAT12 cluster chain contains a bad cluster marker; the disk or image may be damaged.")
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
            raise FloppyImageError("A file points outside the floppy data area; the FAT directory appears corrupt.")
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
            "modified_time": _fat_datetime_to_timestamp(_u16le(entry, 22), _u16le(entry, 24)),
        }


def _read_directory_chain_from_image(data, geometry, fat, first_cluster):
    if first_cluster < 2:
        return b""
    clusters = _fat12_cluster_chain_from_start(fat, first_cluster)
    if not clusters:
        return b""
    return _read_cluster_chain_from_image(data, geometry, clusters, len(clusters) * geometry.cluster_size)


def _fat12_contiguous_file_bytes(data, geometry, first_cluster, size):
    if size <= 0:
        return b""
    offset = _cluster_offset(geometry, first_cluster)
    end = offset + size
    if offset < geometry.data_offset or end > len(data):
        raise FloppyImageError("A file points outside the floppy data area; the FAT directory appears corrupt.")
    return data[offset:end]


def _collect_fat12_listing_entries(data, geometry, fat, directory_bytes, parent_path="", *, allow_contiguous_fallback=False):
    entries = []
    for entry in _iter_fat_directory_entries(directory_bytes):
        attr = entry["attr"]
        image_path = entry["name"] if not parent_path else f"{parent_path}/{entry['name']}"
        image_path = _normalize_image_path(image_path)
        if _is_windows_volume_metadata_path(image_path):
            continue
        if attr & 0x08:
            continue
        if attr & 0x10:
            child_dir = _read_directory_chain_from_image(data, geometry, fat, entry["cluster"])
            entries.extend(
                _collect_fat12_listing_entries(
                    data,
                    geometry,
                    fat,
                    child_dir,
                    image_path,
                    allow_contiguous_fallback=allow_contiguous_fallback,
                )
            )
            continue

        try:
            cluster_chain = _fat12_cluster_chain(fat, entry["cluster"], entry["size"], geometry)
            packed_size = len(cluster_chain) * geometry.cluster_size
        except FloppyImageError:
            if not allow_contiguous_fallback:
                raise
            _fat12_contiguous_file_bytes(data, geometry, entry["cluster"], entry["size"])
            packed_size = allocated_size(entry["size"], geometry.cluster_size)
        entries.append(
            ImageEntry(
                path=image_path,
                size=entry["size"],
                packed_size=packed_size,
                attributes=f"{attr:02X}",
                modified_time=entry.get("modified_time"),
            )
        )
    return entries


def _read_fat12_image_context(img_path):
    with open(img_path, "rb") as handle:
        data = handle.read()

    geometry = _geometry_from_boot_sector(data[:_YAMAHA_BYTES_PER_SECTOR])
    if geometry is None:
        mdr_geometry = electone_mdr_to_midi.infer_mdr_geometry(data)
        if mdr_geometry is not None:
            geometry = _fat12_geometry_from_electone_mdr_geometry(mdr_geometry)
        else:
            raise FloppyImageError(
                "Could not parse a FAT12 boot sector in this image. "
                "The file may not be an IBM/Yamaha floppy image, or it may need to be read with Greaseweazle first."
            )
    if len(data) < geometry.total_size:
        raise FloppyImageError(
            "The floppy image ended before the FAT12 data area was complete. "
            "The image appears truncated or the selected disk format is wrong."
        )

    fat = data[geometry.fat_offset:geometry.fat_offset + geometry.fat_size]
    if len(fat) != geometry.fat_size:
        raise FloppyImageError("Could not read the FAT12 allocation table from this image; the image may be corrupt.")

    root_dir = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    if len(root_dir) != geometry.root_size:
        raise FloppyImageError("Could not read the FAT12 root directory from this image; the image may be corrupt.")

    return data, geometry, fat, root_dir


def _read_fat12_image_listing(img_path):
    data, geometry, fat, root_dir = _read_fat12_image_context(img_path)
    allow_contiguous_fallback = electone_mdr_to_midi.root_directory_has_mdr_entries(root_dir)
    entries = _collect_fat12_listing_entries(
        data,
        geometry,
        fat,
        root_dir,
        allow_contiguous_fallback=allow_contiguous_fallback,
    )
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
    try:
        clusters = _fat12_cluster_chain(fat, entry["cluster"], entry["size"], geometry)
        return _read_cluster_chain_from_image(data, geometry, clusters, entry["size"])
    except FloppyImageError:
        if not electone_mdr_to_midi.root_directory_has_mdr_entries(root_dir):
            raise
        return _fat12_contiguous_file_bytes(data, geometry, entry["cluster"], entry["size"])


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


def _read_floppy_device_fast_image(device_path, output_path, size_bytes, progress_callback=None, cancel_callback=None):
    try:
        device = _open_block_device_for_read(device_path)
    except FloppyImageError as exc:
        raise FastFloppyReadError(str(exc), fallback_allowed=False) from exc
    try:
        fallback_allowed = False
        try:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 0, 100, "Fast floppy read: checking boot sector and FAT...")
            sector0 = _try_read_device_exact(
                device,
                0,
                _YAMAHA_BYTES_PER_SECTOR,
                cancel_callback=cancel_callback,
            ) or b"\x00" * _YAMAHA_BYTES_PER_SECTOR
            geometry = _geometry_from_boot_sector(sector0)
            repair_result = YamahaRepairResult("Fast floppy read: valid FAT12 boot sector present.", False)
            boot = sector0
            fat_area = None
            root_dir = None

            if geometry is None:
                fallback_allowed = True
                matched_layout = None
                candidate_layouts = sorted(
                    _PROTECTED_FAT12_LAYOUTS,
                    key=lambda layout: (
                        0 if int(layout["total_sectors"]) * int(layout["bytes_per_sector"]) == int(size_bytes or 0) else 1,
                        int(layout["total_sectors"]) * int(layout["bytes_per_sector"]),
                    ),
                )
                for layout in candidate_layouts:
                    _raise_if_cancelled(cancel_callback)
                    candidate_geometry = _fat12_geometry_from_layout(layout)
                    if size_bytes and candidate_geometry.total_size > size_bytes:
                        continue
                    _notify_progress(
                        progress_callback,
                        5,
                        100,
                        f"Fast floppy read: checking {layout['label']} FAT/root directory...",
                    )
                    media_descriptor = int(layout["media_descriptor"])
                    try:
                        candidate_fat, candidate_fat_bad_ranges = _read_fat_area_best_effort(
                            device,
                            candidate_geometry,
                            media_descriptor,
                            cancel_callback=cancel_callback,
                        )
                        candidate_root, candidate_root_bad_ranges = _read_device_best_effort(
                            device,
                            candidate_geometry.root_offset,
                            candidate_geometry.root_size,
                            "floppy root directory",
                            sector_size=candidate_geometry.bytes_per_sector,
                            cancel_callback=cancel_callback,
                        )
                    except FloppyOperationCancelled:
                        raise
                    except FloppyImageError:
                        continue
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
                    fallback_allowed = False
                    break
                if geometry is None or matched_layout is None:
                    raise FastFloppyReadError(
                        "Fast floppy read only supports valid FAT12 disks or Yamaha protected FAT12 disks. "
                        "If this is a non-FAT disk or a difficult original, try reading it with Greaseweazle.",
                        fallback_allowed=True,
                    )
                _notify_progress(progress_callback, 10, 100, "Yamaha protected disk recognized; creating working copy...")
                serial = zlib.crc32(fat_area + root_dir) & 0xFFFFFFFF
                boot = _build_standard_fat12_boot_sector(matched_layout, serial, _find_volume_label(root_dir))
                repair_result = YamahaRepairResult(
                    "Fast floppy read applied Yamaha copy-protection repair: sector 0 appears blank/corrupt.",
                    True,
                )
                extra_bad_ranges = len(candidate_fat_bad_ranges) + len(candidate_root_bad_ranges)
                if extra_bad_ranges:
                    sector_word = "sector" if extra_bad_ranges == 1 else "sectors"
                    repair_result = YamahaRepairResult(
                        f"{repair_result.note} Reconstructed FAT/root data despite "
                        f"{extra_bad_ranges} unreadable {sector_word}.",
                        True,
                    )
            else:
                _notify_progress(progress_callback, 5, 100, "Fast floppy read: FAT12 boot sector recognized...")

            if size_bytes and geometry.total_size > size_bytes:
                raise FloppyImageError(
                    "The detected FAT12 geometry is larger than the selected floppy device. "
                    "Check that the inserted disk matches the selected drive/format."
                )

            if fat_area is None:
                media_descriptor = boot[21] if len(boot) > 21 else _YAMAHA_MEDIA_DESCRIPTOR
                fat_area, fat_bad_ranges = _read_fat_area_best_effort(
                    device,
                    geometry,
                    media_descriptor,
                    cancel_callback=cancel_callback,
                )
                if fat_bad_ranges:
                    sector_word = "sector" if len(fat_bad_ranges) == 1 else "sectors"
                    repair_result = YamahaRepairResult(
                        f"{repair_result.note} Reconstructed FAT data despite "
                        f"{len(fat_bad_ranges)} unreadable {sector_word}.",
                        True,
                    )
            if root_dir is None:
                root_dir, root_bad_ranges = _read_device_best_effort(
                    device,
                    geometry.root_offset,
                    geometry.root_size,
                    "floppy root directory",
                    sector_size=geometry.bytes_per_sector,
                    cancel_callback=cancel_callback,
                )
                if root_bad_ranges:
                    sector_word = "sector" if len(root_bad_ranges) == 1 else "sectors"
                    repair_result = YamahaRepairResult(
                        f"{repair_result.note} Read root directory despite "
                        f"{len(root_bad_ranges)} unreadable {sector_word}.",
                        True,
                    )
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
            _notify_progress(progress_callback, 20, 100, f"Planning fast read for {len(file_entries)} file(s)...")
            for entry in file_entries:
                _raise_if_cancelled(cancel_callback)
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
                f"Fast floppy read: reading {display_bytes(total_data_bytes)} of file data in {len(cluster_runs)} {pass_label}...",
            )
            read_data_bytes = 0
            last_progress = 25
            chunk_size = max(geometry.cluster_size, 16 * 1024)
            bad_file_ranges = []
            for start_cluster, end_cluster in cluster_runs:
                _raise_if_cancelled(cancel_callback)
                offset = geometry.data_offset + ((start_cluster - 2) * geometry.cluster_size)
                run_size = ((end_cluster - start_cluster) + 1) * geometry.cluster_size
                if offset < geometry.data_offset or offset + run_size > total_size:
                    raise FloppyImageError("A file points outside the floppy data area; the FAT directory appears corrupt.")
                run_cursor = 0
                while run_cursor < run_size:
                    _raise_if_cancelled(cancel_callback)
                    current_size = min(chunk_size, run_size - run_cursor)
                    chunk, bad_ranges = _read_device_best_effort(
                        device,
                        offset + run_cursor,
                        current_size,
                        f"clusters {start_cluster}-{end_cluster}",
                        sector_size=geometry.bytes_per_sector,
                        cancel_callback=cancel_callback,
                    )
                    bad_file_ranges.extend(bad_ranges)
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
                                f"Fast floppy read: {display_bytes(read_data_bytes)} of {display_bytes(total_data_bytes)}...",
                            )

            _notify_progress(progress_callback, 97, 100, "Preparing floppy contents...")
            _raise_if_cancelled(cancel_callback)
            if bad_file_ranges:
                sector_word = "sector" if len(bad_file_ranges) == 1 else "sectors"
                repair_result = YamahaRepairResult(
                    f"{repair_result.note} Fast floppy read kept going after "
                    f"{len(bad_file_ranges)} unreadable file-data {sector_word}; those bytes were filled with zeros.",
                    True,
                )

            with open(output_path, "wb") as handle:
                handle.write(image)
            _raise_if_cancelled(cancel_callback)
            return repair_result
        except FloppyOperationCancelled:
            raise
        except FastFloppyReadError:
            raise
        except FloppyImageError as exc:
            raise FastFloppyReadError(str(exc), fallback_allowed=fallback_allowed) from exc
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
        raise FloppyImageError(
            "FAT12 repair produced an unexpected image size. "
            "The source may not match a supported Yamaha/IBM floppy layout."
        )

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


def _scp_disk_type(source_path):
    try:
        with open(source_path, "rb") as handle:
            header = handle.read(5)
    except OSError:
        return None
    if len(header) < 5 or header[:3] != b"SCP":
        return None
    return header[4]


def _non_fat_gw_format_hint(source_path, source_ext):
    if str(source_ext or "").lower().lstrip(".") != "scp":
        return None
    return NON_FAT_GW_FORMAT_BY_SCP_TYPE.get(_scp_disk_type(source_path))


def _hfs_volume_name(img_path):
    try:
        with open(img_path, "rb") as handle:
            handle.seek(1024)
            mdb = handle.read(128)
    except OSError:
        return ""
    if len(mdb) < 64 or mdb[:2] != b"BD":
        return ""
    allocation_blocks = int.from_bytes(mdb[18:20], "big")
    allocation_block_size = int.from_bytes(mdb[20:24], "big")
    first_allocation_block = int.from_bytes(mdb[28:30], "big")
    if allocation_blocks <= 0:
        return ""
    if allocation_block_size < 512 or allocation_block_size % 512:
        return ""
    if first_allocation_block <= 0:
        return ""
    name_length = mdb[36]
    if name_length <= 0 or name_length > 27:
        return ""
    raw_name = mdb[37:37 + min(name_length, 27)]
    volume_name = raw_name.decode("mac_roman", errors="replace").strip()
    if not volume_name or any(ord(char) < 32 for char in volume_name):
        return ""
    return volume_name


def _should_probe_non_fat_gw_image(source_path, source_ext, disk_format_hint, sector_maps):
    if isinstance(disk_format_hint, DiskFormat):
        return False
    if _non_fat_gw_format_hint(source_path, source_ext) is None:
        return False

    meaningful_maps = [
        sector_map
        for sector_map in (sector_maps or [])
        if sector_map and sector_map.get("total") is not None
    ]
    if not meaningful_maps:
        return False
    for sector_map in meaningful_maps:
        found = sector_map.get("found")
        if found is None:
            found = sector_map.get("good")
        if int(found or 0) > 0:
            return False
    return True


def _detect_non_fat_gw_image(source_path, source_ext, temp_dir, progress_callback=None, cancel_callback=None):
    disk_format = _non_fat_gw_format_hint(source_path, source_ext)
    if disk_format is None:
        return None

    _raise_if_cancelled(cancel_callback)
    candidate = os.path.join(temp_dir, f"nonfat_{disk_format.key.replace('.', '_')}.img")
    try:
        _notify_progress(
            progress_callback,
            1,
            4,
            f"Checking whether this is a {disk_format.label} image...",
        )
        conversion_output = _gw_convert(
            source_path,
            candidate,
            disk_format.key,
            cancel_callback=cancel_callback,
            allow_sector_failures=True,
        )
    except FloppyOperationCancelled:
        raise
    except Exception:
        return None

    volume_name = _hfs_volume_name(candidate)
    if not volume_name:
        return None

    sector_map = _parse_gw_sector_map(conversion_output, disk_format)
    return {
        "disk_format": disk_format,
        "sector_map": sector_map,
        "volume_name": volume_name,
    }


def _conversion_candidate_formats(source_path, source_ext, disk_format_hint=None):
    if isinstance(disk_format_hint, DiskFormat):
        return [disk_format_hint]

    preferred = []
    if str(source_ext or "").lower().lstrip(".") == "hfe":
        try:
            size = os.path.getsize(source_path)
        except OSError:
            size = 0
        if 0 < size < 3 * 1024 * 1024:
            preferred.append(DISK_FORMAT_BY_KEY["ibm.720"])
        elif 0 < size < 6 * 1024 * 1024:
            preferred.append(DISK_FORMAT_BY_KEY["ibm.1440"])
        elif 0 < size < 10 * 1024 * 1024:
            preferred.append(DISK_FORMAT_BY_KEY["ibm.2880"])

    return preferred + [disk_format for disk_format in DISK_FORMATS if disk_format not in preferred]


def _looks_like_editable_fat_image(img_path):
    try:
        read_image_listing(img_path)
        return True
    except FloppyImageError:
        return False


def _is_probably_pianodir_bytes(data):
    return len(data) >= len(PIANODIR_HEADER) and data[:len(PIANODIR_HEADER)] == PIANODIR_HEADER


def _padded_pianodir_bytes(data):
    payload = bytes(data or b"")[:PIANODIR_TARGET_FILE_SIZE]
    if len(payload) < PIANODIR_TARGET_FILE_SIZE:
        payload += b"\x00" * (PIANODIR_TARGET_FILE_SIZE - len(payload))
    return payload


def _valid_recovery_filename(name, fallback):
    normalized = _normalize_image_path(name or "").upper()
    basename = os.path.basename(normalized)
    if not basename or basename in {".", ".."}:
        return fallback
    stem, ext = os.path.splitext(basename)
    stem = stem.lstrip("!")
    stem = re.sub(r"[^A-Z0-9_]", "_", stem)[:8].strip("._ ")
    ext = re.sub(r"[^A-Z0-9_]", "_", ext.lstrip("."))[:3].strip("_")
    if not stem:
        return fallback
    return f"{stem}.{ext}" if ext else stem


def _eseq_order_key_slice():
    start = PIANODIR_TRACK_SOURCE_START
    return slice(start, start + ESEQ_ORDER_KEY_SIZE)


def _update_recovered_eseq_order_key(data, image_path):
    if not _is_probably_eseq_bytes(data):
        return data
    payload = bytearray(data)
    payload[_eseq_order_key_slice()] = build_eseq_order_key_from_path(image_path)
    return bytes(payload)


def _update_recovered_pianodir_order_keys(data, order_key_map):
    if not order_key_map or not _is_probably_pianodir_bytes(data):
        return data
    payload = bytearray(_padded_pianodir_bytes(data))
    max_records = (PIANODIR_TARGET_FILE_SIZE - len(PIANODIR_HEADER)) // PIANODIR_TRACK_SIZE
    for slot in range(max_records):
        record_offset = len(PIANODIR_HEADER) + slot * PIANODIR_TRACK_SIZE
        order_key = bytes(payload[record_offset:record_offset + ESEQ_ORDER_KEY_SIZE])
        replacement = order_key_map.get(order_key) or order_key_map.get(order_key[:11])
        if replacement:
            payload[record_offset:record_offset + ESEQ_ORDER_KEY_SIZE] = replacement
    return bytes(payload)


def _unique_recovery_path(preferred_path, used_paths, fallback_prefix, extension, index):
    fallback = f"{fallback_prefix}{index:03d}.{extension}"
    candidate = _valid_recovery_filename(preferred_path, fallback)
    stem, ext = os.path.splitext(candidate)
    if not ext and extension:
        ext = f".{extension}"
    if not stem:
        stem = fallback_prefix
    counter = 1
    unique = f"{stem[:8]}{ext[:4]}".upper()
    while unique.upper() in used_paths or is_pianodir_path(unique):
        suffix = str(counter)
        unique_stem = f"{stem[:max(1, 8 - len(suffix))]}{suffix}"
        unique = f"{unique_stem}{ext[:4]}".upper()
        counter += 1
    used_paths.add(unique.upper())
    return unique


def _is_probably_eseq_bytes(data):
    return len(data) >= 0x77 and data[7:15] == b"COM-ESEQ"


def _eseq_declared_size(data, start):
    if start < 0 or start + 0x77 > len(data):
        return 0
    declared = int.from_bytes(data[start + 3:start + 7], "little")
    if 0x77 <= declared <= len(data) - start:
        return declared
    stream_length = int.from_bytes(data[start + 0x1F:start + 0x23], "little") if start + 0x23 <= len(data) else 0
    if stream_length > 0:
        stream_end = 0x77 + stream_length
        if 0x77 <= stream_end <= len(data) - start:
            return stream_end
    return 0


def _eseq_recovery_filename(data, fallback_index):
    fallback = f"REC{fallback_index:03d}.FIL"
    if len(data) >= 0x32:
        name = _decode_dos_directory_name(data[0x27:0x32])
        if name:
            stem, ext = os.path.splitext(name)
            if not ext:
                name = f"{stem}.FIL"
            return _valid_recovery_filename(name, fallback)
    return fallback


def _extract_midi_blob_for_recovery(data, start):
    if start < 0 or start + 14 > len(data) or data[start:start + 4] != b"MThd":
        return None
    header_size = int.from_bytes(data[start + 4:start + 8], "big")
    if header_size < 6 or start + 8 + header_size > len(data):
        return None

    header = data[start + 8:start + 8 + header_size]
    fmt = int.from_bytes(header[0:2], "big")
    declared_tracks = int.from_bytes(header[2:4], "big")
    division = int.from_bytes(header[4:6], "big")
    if fmt > 2 or declared_tracks <= 0 or declared_tracks > 128 or division == 0:
        return None

    cursor = start + 8 + header_size
    chunks = []
    track_count = 0
    while cursor + 8 <= len(data) and track_count < declared_tracks:
        chunk_type = data[cursor:cursor + 4]
        chunk_size = int.from_bytes(data[cursor + 4:cursor + 8], "big")
        chunk_end = cursor + 8 + chunk_size
        if chunk_size < 0 or chunk_end > len(data):
            break
        chunk = data[cursor:chunk_end]
        cursor = chunk_end
        if chunk_type == b"MTrk":
            chunks.append(chunk)
            track_count += 1
        elif track_count == 0 and chunk_type.isalpha():
            continue
        else:
            break

    if track_count <= 0:
        return None

    recovered_format = fmt if track_count > 1 else 0
    recovered_header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + int(recovered_format).to_bytes(2, "big")
        + int(track_count).to_bytes(2, "big")
        + int(division).to_bytes(2, "big")
    )
    return recovered_header + b"".join(chunks)


def _recover_files_from_fat_context(data, geometry):
    files = []
    if len(data) < geometry.root_offset + geometry.root_size:
        return files
    if len(data) < geometry.fat_offset + geometry.fat_size:
        return files

    fat = data[geometry.fat_offset:geometry.fat_offset + geometry.fat_size]
    root_dir = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    for entry in _iter_fat_directory_entries(root_dir):
        if _is_windows_volume_metadata_path(entry["name"]):
            continue
        if entry["attr"] & 0x10:
            continue
        name = _valid_recovery_filename(entry["name"], "")
        if not name:
            continue
        size = int(entry["size"] or 0)
        if size <= 0:
            continue
        try:
            clusters = _fat12_cluster_chain(fat, entry["cluster"], size, geometry)
            payload = _read_cluster_chain_from_image(data, geometry, clusters, size)
        except FloppyImageError:
            start_offset = _cluster_offset(geometry, entry["cluster"])
            if start_offset < geometry.data_offset or start_offset >= len(data):
                continue
            payload = data[start_offset:min(len(data), start_offset + size)]

        if is_pianodir_path(name) and _is_probably_pianodir_bytes(payload):
            files.append(
                RecoveredFile(
                    PIANODIR_FILENAME,
                    _padded_pianodir_bytes(payload),
                    "PIANODIR",
                    _cluster_offset(geometry, entry["cluster"]),
                    "fat",
                )
            )
        elif _is_probably_eseq_bytes(payload):
            files.append(RecoveredFile(name, payload, "E-SEQ", _cluster_offset(geometry, entry["cluster"]), "fat"))
        elif payload[:4] == b"MThd":
            midi_payload = _extract_midi_blob_for_recovery(payload, 0) or payload
            files.append(RecoveredFile(name, midi_payload, "MIDI", _cluster_offset(geometry, entry["cluster"]), "fat"))
    return files


def _geometry_for_disk_format_hint(disk_format):
    if not isinstance(disk_format, DiskFormat):
        return None
    for layout in _PROTECTED_FAT12_LAYOUTS:
        if _layout_total_size(layout) == disk_format.size_bytes:
            return _fat12_geometry_from_layout(layout)
    return None


def _recovery_geometries_for_data(data, disk_format_hint=None):
    geometries = []
    hinted_geometry = _geometry_for_disk_format_hint(disk_format_hint)
    if hinted_geometry is not None and hinted_geometry.total_size <= len(data):
        geometries.append(hinted_geometry)
    geometry = _geometry_from_boot_sector(data[:_YAMAHA_BYTES_PER_SECTOR])
    if geometry is not None and all(existing.total_size != geometry.total_size for existing in geometries):
        geometries.append(geometry)
    for layout in _PROTECTED_FAT12_LAYOUTS:
        candidate = _fat12_geometry_from_layout(layout)
        if candidate.total_size <= len(data) and all(existing.total_size != candidate.total_size for existing in geometries):
            geometries.append(candidate)
    if len(data) >= _YAMAHA_TOTAL_SIZE and all(existing.total_size != _YAMAHA_TOTAL_SIZE for existing in geometries):
        geometries.append(_yamaha_720_geometry())
    return geometries


def _carve_recovery_files_from_bytes(data):
    files = []
    pianodir_offset = data.find(PIANODIR_HEADER)
    if pianodir_offset >= 0:
        pianodir = data[pianodir_offset:pianodir_offset + PIANODIR_TARGET_FILE_SIZE]
        files.append(RecoveredFile(PIANODIR_FILENAME, _padded_pianodir_bytes(pianodir), "PIANODIR", pianodir_offset, "carve"))

    eseq_index = 1
    search_start = 0
    eseq_starts = set()
    while True:
        marker = data.find(b"COM-ESEQ", search_start)
        if marker < 0:
            break
        start = marker - 7
        search_start = marker + 1
        if start < 0 or start in eseq_starts:
            continue
        eseq_starts.add(start)
        size = _eseq_declared_size(data, start)
        if size <= 0:
            next_marker = data.find(b"COM-ESEQ", marker + 1)
            following = next_marker - 7 if next_marker >= 7 else len(data)
            size = min(following - start, 256 * 1024)
        if size < 0x77:
            continue
        payload = data[start:min(len(data), start + size)]
        if not _is_probably_eseq_bytes(payload):
            continue
        files.append(RecoveredFile(_eseq_recovery_filename(payload, eseq_index), payload, "E-SEQ", start, "carve"))
        eseq_index += 1

    midi_index = 1
    search_start = 0
    while True:
        start = data.find(b"MThd", search_start)
        if start < 0:
            break
        search_start = start + 1
        payload = _extract_midi_blob_for_recovery(data, start)
        if not payload:
            continue
        files.append(RecoveredFile(f"REC{midi_index:03d}.MID", payload, "MIDI", start, "carve"))
        midi_index += 1

    return files


def _recovered_file_identity_key(item, payload):
    if item.kind == "E-SEQ" and len(payload) >= PIANODIR_TRACK_SOURCE_END:
        return (item.kind, bytes(payload[PIANODIR_TRACK_SOURCE_START:PIANODIR_TRACK_SOURCE_END]))
    return None


def _dedupe_recovered_files(files):
    selected = []
    seen_payloads = set()
    seen_offsets = {}
    seen_identity_keys = {}
    used_paths = set()
    order_key_map = {}
    counters = {"MIDI": 1, "E-SEQ": 1, "PIANODIR": 1, "FILE": 1}

    def priority(item):
        kind_rank = {"PIANODIR": 0, "E-SEQ": 1, "MIDI": 2}.get(item.kind, 3)
        origin_rank = 1 if item.origin == "carve" else 0
        named_rank = 1 if os.path.basename(item.image_path).upper().startswith("REC") else 0
        size_rank = len(item.data or b"")
        return (kind_rank, origin_rank, named_rank, item.source_offset if item.source_offset >= 0 else 10**9, size_rank)

    for item in sorted(files, key=priority):
        payload = bytes(item.data or b"")
        if not payload:
            continue
        source_order_key = b""
        if item.kind == "E-SEQ" and len(payload) >= PIANODIR_TRACK_SOURCE_START + ESEQ_ORDER_KEY_SIZE:
            source_order_key = bytes(payload[_eseq_order_key_slice()])
        offset_key = None
        if item.kind in {"E-SEQ", "MIDI"} and item.source_offset >= 0:
            offset_key = (item.kind, item.source_offset)
            if offset_key in seen_offsets:
                continue
        identity_key = _recovered_file_identity_key(item, payload)
        if identity_key is not None and identity_key in seen_identity_keys:
            continue
        payload_key = (item.kind, len(payload), zlib.crc32(payload) & 0xFFFFFFFF)
        if payload_key in seen_payloads and item.kind != "PIANODIR":
            continue
        if item.kind == "PIANODIR":
            if PIANODIR_FILENAME.upper() in used_paths:
                continue
            image_path = PIANODIR_FILENAME
            used_paths.add(image_path.upper())
        elif item.kind == "MIDI":
            index = counters["MIDI"]
            image_path = _unique_recovery_path(item.image_path, used_paths, "REC", "MID", index)
            counters["MIDI"] += 1
        elif item.kind == "E-SEQ":
            index = counters["E-SEQ"]
            image_path = _unique_recovery_path(item.image_path, used_paths, "REC", "FIL", index)
            counters["E-SEQ"] += 1
            target_order_key = build_eseq_order_key_from_path(image_path)
            if source_order_key:
                order_key_map[source_order_key] = target_order_key
                order_key_map[source_order_key[:11]] = target_order_key
            payload = _update_recovered_eseq_order_key(payload, image_path)
        else:
            index = counters["FILE"]
            image_path = _unique_recovery_path(item.image_path, used_paths, "REC", "BIN", index)
            counters["FILE"] += 1
        seen_payloads.add(payload_key)
        if offset_key is not None:
            seen_offsets[offset_key] = image_path
        if identity_key is not None:
            seen_identity_keys[identity_key] = image_path
        selected.append(RecoveredFile(image_path, payload, item.kind, item.source_offset, item.origin))
    if not order_key_map:
        return selected
    return [
        RecoveredFile(
            item.image_path,
            _update_recovered_pianodir_order_keys(item.data, order_key_map) if item.kind == "PIANODIR" else item.data,
            item.kind,
            item.source_offset,
            item.origin,
        )
        for item in selected
    ]


def _preferred_recovery_formats_for_data(data, files, disk_format_hint=None):
    exact = DISK_FORMAT_BY_SIZE.get(len(data))
    total_payload = sum(len(item.data or b"") for item in files)
    formats = []
    if isinstance(disk_format_hint, DiskFormat):
        formats.append(disk_format_hint)
    if exact is not None:
        if exact not in formats:
            formats.append(exact)
    default = DISK_FORMAT_BY_SIZE.get(_YAMAHA_TOTAL_SIZE, DISK_FORMATS[0])
    if default not in formats:
        formats.append(default)
    for disk_format in sorted(DISK_FORMATS, key=lambda item: item.size_bytes):
        if disk_format in formats:
            continue
        if disk_format.size_bytes >= total_payload + 32 * 1024:
            formats.append(disk_format)
    for disk_format in DISK_FORMATS:
        if disk_format not in formats:
            formats.append(disk_format)
    return formats


def _write_recovered_files_to_image(
    files,
    temp_dir,
    source_data,
    disk_format_hint=None,
    progress_callback=None,
    cancel_callback=None,
):
    if not files:
        raise FloppyImageError(
            "Recovery did not find any PIANODIR.FIL, MIDI, or E-SEQ song data in the copied image."
        )

    last_error = None
    files_dir = os.path.join(temp_dir, "recovered_files")
    os.makedirs(files_dir, exist_ok=True)

    for disk_format in _preferred_recovery_formats_for_data(source_data, files, disk_format_hint=disk_format_hint):
        _raise_if_cancelled(cancel_callback)
        recovered_img = os.path.join(temp_dir, f"recovered_{disk_format.key.replace('.', '_')}.img")
        try:
            _notify_progress(progress_callback, 82, 100, f"Creating recovered {disk_format.label} image...")
            create_blank_floppy_image(
                recovered_img,
                disk_format,
                volume_label="RECOVER",
                cancel_callback=cancel_callback,
            )
            total = len(files)
            for index, item in enumerate(files, start=1):
                _raise_if_cancelled(cancel_callback)
                host_path = os.path.join(files_dir, f"{uuid.uuid4().hex}_{os.path.basename(item.image_path)}")
                with open(host_path, "wb") as handle:
                    handle.write(item.data)
                _notify_progress(
                    progress_callback,
                    82 + int((index / max(1, total)) * 15),
                    100,
                    f"Adding recovered file {index} of {total}: {item.image_path}...",
                )
                _copy_host_file_into_image(
                    recovered_img,
                    host_path,
                    item.image_path,
                    cancel_callback=cancel_callback,
                )
            _notify_progress(progress_callback, 98, 100, "Verifying recovered image...")
            read_image_listing(recovered_img)
            return recovered_img, disk_format
        except FloppyOperationCancelled:
            raise
        except Exception as exc:
            last_error = exc
            if os.path.exists(recovered_img):
                os.remove(recovered_img)

    detail = f" Last error: {last_error}" if last_error else ""
    raise FloppyImageError(
        "Recovery found data, but could not build a recovered floppy image. "
        "The recovered files may be too large for the supported disk formats or mtools could not write them."
        + detail
    )


def _recover_files_from_raw_image_bytes(data, disk_format_hint=None):
    recovered = []
    for geometry in _recovery_geometries_for_data(data, disk_format_hint=disk_format_hint):
        try:
            recovered.extend(_recover_files_from_fat_context(data, geometry))
        except FloppyOperationCancelled:
            raise
        except Exception:
            continue
    recovered.extend(_carve_recovery_files_from_bytes(data))
    return _dedupe_recovered_files(recovered)


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
        capture_path=None,
        capture_ext=None,
        gw_sector_reports=None,
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
        self.capture_path = capture_path
        self.capture_ext = capture_ext
        self.gw_sector_reports = tuple(gw_sector_reports or ())
        self.latest_gw_sector_reports = self.gw_sector_reports
        self.repair_note = repair_result.note
        self.repair_changed = repair_result.changed
        self.extracted_dir = os.path.join(temp_dir, "extracted")
        self.patched_dir = os.path.join(temp_dir, "patched")
        self._extracted_files = {}
        os.makedirs(self.extracted_dir, exist_ok=True)
        os.makedirs(self.patched_dir, exist_ok=True)

    @classmethod
    def load(cls, source_path, progress_callback=None, cancel_callback=None):
        disk_format_hint = None
        if isinstance(source_path, ImageLoadSource):
            disk_format_hint = source_path.disk_format
            source_path = source_path.path
        source_path = os.path.abspath(source_path)
        source_ext = image_extension(source_path)
        if source_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise FloppyImageError(_unsupported_image_type_message(source_ext))

        temp_dir = tempfile.mkdtemp(prefix="aps_floppy_image_")
        try:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 0, 4, "Preparing floppy image...")
            if source_ext in RAW_IMAGE_EXTENSIONS:
                return cls._load_raw(
                    source_path,
                    source_ext,
                    temp_dir,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
            return cls._load_converted(
                source_path,
                source_ext,
                temp_dir,
                disk_format_hint=disk_format_hint,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def load_floppy(cls, drive_info, progress_callback=None, cancel_callback=None):
        if not isinstance(drive_info, FloppyDriveInfo):
            raise FloppyImageError("Invalid floppy drive selection.")

        temp_dir = tempfile.mkdtemp(prefix="aps_floppy_drive_")
        try:
            source_copy = os.path.join(temp_dir, "source.img")
            working_img = os.path.join(temp_dir, "working.img")
            try:
                repair_result = _read_floppy_device_fast_image(
                    drive_info.path,
                    working_img,
                    drive_info.size_bytes,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
                disk_format = _disk_format_for_image(working_img)
                _notify_progress(progress_callback, 98, 100, "Scanning fast-read floppy contents...")
                read_image_listing(working_img)
            except FloppyOperationCancelled:
                raise
            except FastFloppyReadError as fast_exc:
                if not fast_exc.fallback_allowed:
                    raise FloppyImageError(
                        "Fast floppy read recognized this disk but could not finish without losing data.\n\n"
                        f"Details: {fast_exc}\n\n"
                        "Use Read Floppy with Start in recovery mode for a slower full-disk recovery pass."
                    ) from fast_exc
                _notify_progress(
                    progress_callback,
                    0,
                    100,
                    f"Fast floppy read unavailable: {fast_exc} Reading full floppy image from {drive_info.path}...",
                )
                if os.name == "nt":
                    raw_data = _read_windows_block_device_bytes(
                        drive_info.path,
                        drive_info.size_bytes,
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                    _raise_if_cancelled(cancel_callback)
                    _notify_progress(progress_callback, 75, 100, "Creating working copy...")
                    repair_result = prepare_yamaha_bytes(raw_data, working_img)
                else:
                    _read_block_device(
                        drive_info.path,
                        source_copy,
                        drive_info.size_bytes,
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                    _raise_if_cancelled(cancel_callback)
                    _notify_progress(progress_callback, 75, 100, "Creating working copy...")
                    repair_result = prepare_yamaha_image(source_copy, working_img)
                repair_result = YamahaRepairResult(
                    repair_result.note + f" Fast floppy file-level read was unavailable: {fast_exc}",
                    repair_result.changed,
                )
                disk_format = _disk_format_for_image(working_img)
                _notify_progress(progress_callback, 90, 100, "Scanning floppy contents...")
                read_image_listing(working_img)
            except FloppyImageError as scan_exc:
                raise FloppyImageError(
                    "Fast floppy read finished, but the resulting floppy image could not be scanned.\n\n"
                    f"Details: {scan_exc}\n\n"
                    "Use Read Floppy with Start in recovery mode for a slower full-disk recovery pass."
                ) from scan_exc
            _notify_progress(progress_callback, 100, 100, "Opening floppy contents...")
            _raise_if_cancelled(cancel_callback)
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
    def capture_greaseweazle_archival(cls, gw_source, progress_callback=None, cancel_callback=None):
        if not isinstance(gw_source, GreaseweazleFloppySource):
            raise FloppyImageError("Invalid Greaseweazle source selection.")
        if not gw_source.archival_quality:
            raise FloppyImageError("Greaseweazle SCP capture requires raw SCP mode.")

        temp_dir = tempfile.mkdtemp(prefix="aps_gw_capture_read_")
        try:
            source_capture = os.path.join(temp_dir, "source.scp")
            _notify_progress(
                progress_callback,
                0,
                2,
                f"Reading floppy via Greaseweazle drive {gw_source.drive}...",
            )
            sector_map = _gw_read_floppy(
                gw_source,
                source_capture,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 2, 2, "Greaseweazle SCP capture ready to save...")
            return GreaseweazleCapture(gw_source, source_capture, temp_dir, sector_map=sector_map)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def load_greaseweazle(cls, gw_source, progress_callback=None, cancel_callback=None):
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
            read_sector_map = {}
            try:
                read_sector_map = _gw_read_floppy(
                    gw_source,
                    source_capture,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
            except FloppyOperationCancelled:
                raise
            except Exception:
                if (
                    gw_source.archival_quality
                    and gw_source.capture_save_path
                    and os.path.isfile(source_capture)
                    and os.path.getsize(source_capture) > 0
                ):
                    saved_capture = os.path.abspath(gw_source.capture_save_path)
                    os.makedirs(os.path.dirname(saved_capture), exist_ok=True)
                    shutil.copy2(source_capture, saved_capture)
                raise
            progress_step += 1
            conversion_sector_map = {}
            if gw_source.archival_quality:
                saved_capture = os.path.abspath(gw_source.capture_save_path) if gw_source.capture_save_path else ""
                if saved_capture:
                    _raise_if_cancelled(cancel_callback)
                    _notify_progress(progress_callback, progress_step, total_steps, "Saving raw SCP capture...")
                    os.makedirs(os.path.dirname(saved_capture), exist_ok=True)
                    shutil.copy2(source_capture, saved_capture)
                    source_capture = saved_capture
                _notify_progress(progress_callback, progress_step, total_steps, "Converting raw SCP capture...")
                try:
                    conversion_output = _gw_convert(
                        source_capture,
                        source_copy,
                        gw_source.disk_format.key,
                        cancel_callback=cancel_callback,
                    )
                    conversion_sector_map = _parse_gw_sector_map(conversion_output, gw_source.disk_format)
                except GreaseweazleConversionError as exc:
                    raise GreaseweazleConversionError(
                        str(exc),
                        sector_map=exc.sector_map,
                        disk_format=gw_source.disk_format,
                        capture_path=source_capture,
                        reason=exc.reason,
                        suggested_format=exc.suggested_format,
                    ) from exc
                progress_step += 1
            else:
                conversion_output = ""
            try:
                _validate_converted_image_matches_boot_hint(source_copy, gw_source.disk_format)
            except ConvertedImageFormatMismatchError as exc:
                if gw_source.archival_quality:
                    raise GreaseweazleConversionError(
                        str(exc),
                        sector_map=_parse_gw_sector_map(conversion_output, gw_source.disk_format),
                        disk_format=gw_source.disk_format,
                        capture_path=source_capture,
                        reason="format_mismatch",
                        suggested_format=exc.suggested_format,
                    ) from exc
                raise
            except FloppyImageError as exc:
                if gw_source.archival_quality:
                    raise GreaseweazleConversionError(
                        str(exc),
                        sector_map=_parse_gw_sector_map(conversion_output, gw_source.disk_format),
                        disk_format=gw_source.disk_format,
                        capture_path=source_capture,
                    ) from exc
                raise
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, progress_step, total_steps, "Preparing editable floppy image...")
            repair_result = prepare_yamaha_image(source_copy, working_img)
            progress_step += 1
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, progress_step, total_steps, "Detecting floppy format...")
            disk_format = _disk_format_for_image(working_img)
            if disk_format.size_bytes != gw_source.disk_format.size_bytes:
                raise GreaseweazleConversionError(
                    "Greaseweazle read did not match the selected disk size. "
                    f"Selected {gw_source.disk_format.label}, but the captured image looks like {disk_format.label}. "
                    "Choose the matching disk format and try converting the saved capture again.",
                    sector_map=_parse_gw_sector_map(conversion_output, gw_source.disk_format),
                    disk_format=gw_source.disk_format,
                    capture_path=source_capture,
                    reason="format_mismatch",
                    suggested_format=disk_format,
                )
            progress_step += 1
            _notify_progress(progress_callback, progress_step, total_steps, "Scanning floppy contents...")
            read_image_listing(working_img)
            _raise_if_cancelled(cancel_callback)
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
                capture_path=source_capture,
                capture_ext="scp" if gw_source.archival_quality else "img",
                gw_sector_reports=_gw_sector_reports(
                    _gw_sector_report(
                        "read",
                        read_sector_map,
                        title="Greaseweazle Read Sector Map",
                        summary=f"Read {gw_source.display_name}.",
                        disk_format=gw_source.disk_format,
                    ),
                    _gw_sector_report(
                        "convert",
                        conversion_sector_map,
                        title="Greaseweazle Conversion Sector Map",
                        summary=f"Converted the Greaseweazle capture as {gw_source.disk_format.label}.",
                        disk_format=gw_source.disk_format,
                    )
                    if gw_source.archival_quality
                    else None,
                ),
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def load_greaseweazle_capture(
        cls,
        gw_source,
        capture_path,
        disk_format,
        progress_callback=None,
        cancel_callback=None,
    ):
        if not isinstance(gw_source, GreaseweazleFloppySource):
            raise FloppyImageError("Invalid Greaseweazle source selection.")
        if not isinstance(disk_format, DiskFormat):
            raise FloppyImageError("Invalid Greaseweazle conversion format.")

        capture_path = os.path.abspath(capture_path)
        if not os.path.isfile(capture_path):
            raise FloppyImageError(f"The saved Greaseweazle capture was not found: {capture_path}")

        temp_dir = tempfile.mkdtemp(prefix="aps_gw_capture_")
        retry_source = GreaseweazleFloppySource(
            device_path=gw_source.device_path,
            drive=gw_source.drive,
            disk_format=disk_format,
            archival_quality=True,
            revs=gw_source.revs,
            retries=gw_source.retries,
            capture_save_path=capture_path,
            capture_output_ext="scp",
        )
        try:
            source_copy = os.path.join(temp_dir, "source.img")
            working_img = os.path.join(temp_dir, "working.img")
            _notify_progress(
                progress_callback,
                1,
                4,
                f"Converting saved Greaseweazle SCP capture as {disk_format.label}...",
            )
            try:
                conversion_output = _gw_convert(
                    capture_path,
                    source_copy,
                    disk_format.key,
                    cancel_callback=cancel_callback,
                )
                conversion_sector_map = _parse_gw_sector_map(conversion_output, disk_format)
                _validate_converted_image_matches_boot_hint(source_copy, disk_format)
            except GreaseweazleConversionError as exc:
                raise GreaseweazleConversionError(
                    str(exc),
                    sector_map=exc.sector_map,
                    disk_format=disk_format,
                    capture_path=capture_path,
                    reason=exc.reason,
                    suggested_format=exc.suggested_format,
                ) from exc
            except ConvertedImageFormatMismatchError as exc:
                raise GreaseweazleConversionError(
                    str(exc),
                    sector_map=_parse_gw_sector_map(
                        conversion_output if "conversion_output" in locals() else "",
                        disk_format,
                    ),
                    disk_format=disk_format,
                    capture_path=capture_path,
                    reason="format_mismatch",
                    suggested_format=exc.suggested_format,
                ) from exc
            except FloppyImageError as exc:
                raise GreaseweazleConversionError(
                    str(exc),
                    sector_map=_parse_gw_sector_map(
                        conversion_output if "conversion_output" in locals() else "",
                        disk_format,
                    ),
                    disk_format=disk_format,
                    capture_path=capture_path,
                ) from exc

            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 2, 4, "Preparing editable floppy image...")
            repair_result = prepare_yamaha_image(source_copy, working_img)
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 3, 4, "Detecting floppy format...")
            detected_format = _disk_format_for_image(working_img)
            if detected_format.size_bytes != disk_format.size_bytes:
                raise GreaseweazleConversionError(
                    "Greaseweazle conversion did not match the selected disk size. "
                    f"Selected {disk_format.label}, but the captured image looks like {detected_format.label}. "
                    "Choose the matching disk format and try again.",
                    sector_map=_parse_gw_sector_map(
                        conversion_output if "conversion_output" in locals() else "",
                        disk_format,
                    ),
                    disk_format=disk_format,
                    capture_path=capture_path,
                    reason="format_mismatch",
                    suggested_format=detected_format,
                )
            _notify_progress(progress_callback, 4, 4, "Scanning floppy contents...")
            read_image_listing(working_img)
            _raise_if_cancelled(cancel_callback)
            return cls(
                source_copy,
                "img",
                temp_dir,
                working_img,
                disk_format,
                repair_result,
                source_kind="floppy_gw",
                source_name=retry_source.display_name,
                gw_source=retry_source,
                capture_path=capture_path,
                capture_ext="scp",
                gw_sector_reports=_gw_sector_reports(
                    _gw_sector_report(
                        "convert",
                        locals().get("conversion_sector_map", {}),
                        title="Greaseweazle Conversion Sector Map",
                        summary=f"Converted the saved Greaseweazle capture as {disk_format.label}.",
                        disk_format=disk_format,
                    ),
                ),
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def format_usb_floppy(
        cls,
        drive_info,
        disk_format,
        *,
        eseq_disk=False,
        volume_label="YAMAHA",
        progress_callback=None,
        cancel_callback=None,
    ):
        if not isinstance(drive_info, FloppyDriveInfo):
            raise FloppyImageError("Invalid floppy drive selection.")
        if not isinstance(disk_format, DiskFormat):
            raise FloppyImageError("Invalid disk format.")

        temp_dir = tempfile.mkdtemp(prefix="aps_format_usb_floppy_")
        try:
            working_img = os.path.join(temp_dir, "working.img")
            _notify_progress(progress_callback, 0, 100, f"Creating blank {disk_format.label} image...")
            create_blank_floppy_image(
                working_img,
                disk_format,
                volume_label=volume_label,
                cancel_callback=cancel_callback,
            )
            if eseq_disk:
                _raise_if_cancelled(cancel_callback)
                _notify_progress(progress_callback, 10, 100, "Adding empty PIANODIR.FIL...")
                _write_empty_pianodir_to_image(working_img, temp_dir)
            _notify_progress(progress_callback, 20, 100, f"Writing floppy {drive_info.path}...")

            def write_progress(step, total, message):
                if total and total > 0:
                    clamped_step = max(0, min(int(step), int(total)))
                    mapped_step = 20 + int((clamped_step / int(total)) * 77)
                    _notify_progress(progress_callback, mapped_step, 100, message)
                else:
                    _notify_progress(progress_callback, 20, 100, message)

            _write_block_device(
                working_img,
                drive_info.path,
                progress_callback=write_progress,
                cancel_callback=cancel_callback,
            )
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 98, 100, "Verifying formatted floppy...")
            read_image_listing(working_img)
            _notify_progress(progress_callback, 100, 100, "Opening formatted floppy...")
            _raise_if_cancelled(cancel_callback)
            return cls(
                drive_info.path,
                "img",
                temp_dir,
                working_img,
                disk_format,
                YamahaRepairResult("Formatted blank Yamaha Disklavier floppy.", False),
                source_kind="floppy_usb",
                source_name=f"{drive_info.path} - {disk_format.label}",
                drive_info=drive_info,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def format_greaseweazle_floppy(
        cls,
        gw_source,
        *,
        eseq_disk=False,
        volume_label="YAMAHA",
        progress_callback=None,
        cancel_callback=None,
    ):
        if not isinstance(gw_source, GreaseweazleFloppySource):
            raise FloppyImageError("Invalid Greaseweazle source selection.")

        temp_dir = tempfile.mkdtemp(prefix="aps_format_gw_floppy_")
        try:
            working_img = os.path.join(temp_dir, "working.img")
            _notify_progress(progress_callback, 0, 5, f"Creating blank {gw_source.disk_format.label} image...")
            create_blank_floppy_image(
                working_img,
                gw_source.disk_format,
                volume_label=volume_label,
                cancel_callback=cancel_callback,
            )
            if eseq_disk:
                _raise_if_cancelled(cancel_callback)
                _notify_progress(progress_callback, 1, 5, "Adding empty PIANODIR.FIL...")
                _write_empty_pianodir_to_image(working_img, temp_dir)
            _notify_progress(progress_callback, 2, 5, f"Writing Greaseweazle drive {gw_source.drive}...")
            write_sector_map = _gw_write_floppy(
                gw_source,
                working_img,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 4, 5, "Verifying formatted floppy...")
            read_image_listing(working_img)
            _notify_progress(progress_callback, 5, 5, "Opening formatted floppy...")
            _raise_if_cancelled(cancel_callback)
            return cls(
                working_img,
                "img",
                temp_dir,
                working_img,
                gw_source.disk_format,
                YamahaRepairResult("Formatted blank Yamaha Disklavier floppy.", False),
                source_kind="floppy_gw",
                source_name=gw_source.display_name,
                gw_source=gw_source,
                gw_sector_reports=_gw_sector_reports(
                    _gw_sector_report(
                        "write",
                        write_sector_map,
                        title="Greaseweazle Write Sector Map",
                        summary=f"Wrote {gw_source.disk_format.label} to {gw_source.display_name}.",
                        disk_format=gw_source.disk_format,
                    ),
                ),
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def create_blank_session(
        cls,
        disk_format,
        *,
        source_ext="img",
        eseq_disk=False,
        volume_label="YAMAHA",
        pianodir_metadata=None,
        progress_callback=None,
        cancel_callback=None,
    ):
        if not isinstance(disk_format, DiskFormat):
            raise FloppyImageError("Invalid disk format.")

        source_ext = (source_ext or "img").lower().lstrip(".")
        if source_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise FloppyImageError(_unsupported_image_type_message(source_ext, for_output=True))

        temp_dir = tempfile.mkdtemp(prefix="aps_new_image_")
        try:
            working_img = os.path.join(temp_dir, "working.img")
            source_name = f"Untitled {disk_format.label} {source_ext.upper()} image"
            _notify_progress(progress_callback, 0, 4, f"Creating blank {disk_format.label} image...")
            create_blank_floppy_image(
                working_img,
                disk_format,
                volume_label=volume_label,
                cancel_callback=cancel_callback,
            )
            if eseq_disk:
                _raise_if_cancelled(cancel_callback)
                _notify_progress(progress_callback, 1, 4, "Adding empty PIANODIR.FIL...")
                _write_empty_pianodir_to_image(working_img, temp_dir, metadata=pianodir_metadata)
                source_name = f"Untitled {disk_format.label} E-SEQ {source_ext.upper()} image"
            _notify_progress(progress_callback, 2, 4, "Verifying blank image...")
            read_image_listing(working_img)
            _notify_progress(progress_callback, 4, 4, "Opening new image...")
            _raise_if_cancelled(cancel_callback)
            return cls(
                os.path.join(temp_dir, f"untitled.{source_ext}"),
                source_ext,
                temp_dir,
                working_img,
                disk_format,
                YamahaRepairResult("New blank image created.", False),
                source_kind="new_image",
                source_name=source_name,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def recover(cls, load_kind, source, progress_callback=None, cancel_callback=None):
        if load_kind == "image":
            return cls._recover_image(source, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if load_kind == "floppy_usb":
            return cls._recover_usb_floppy(source, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if load_kind == "floppy_gw":
            return cls._recover_greaseweazle(source, progress_callback=progress_callback, cancel_callback=cancel_callback)
        raise FloppyImageError(f"Unsupported disk recovery kind: {load_kind}")

    @classmethod
    def _recover_image(cls, source, progress_callback=None, cancel_callback=None):
        disk_format_hint = None
        if isinstance(source, ImageRecoverySource):
            source_path = source.path
            disk_format_hint = source.disk_format
        else:
            source_path = source
        source_path = os.path.abspath(source_path)
        source_ext = image_extension(source_path)
        if source_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise FloppyImageError(_unsupported_image_type_message(source_ext))

        temp_dir = tempfile.mkdtemp(prefix="aps_recover_image_")
        try:
            _raise_if_cancelled(cancel_callback)
            hint_note = ""
            if isinstance(disk_format_hint, DiskFormat):
                hint_note = f" Recovery was run with the disk format hint: {disk_format_hint.label}."
            if source_ext in RAW_IMAGE_EXTENSIONS:
                source_copy = os.path.join(temp_dir, "source_recovery.img")
                _notify_progress(progress_callback, 5, 100, "Copying image for recovery...")
                shutil.copy2(source_path, source_copy)
                return cls._recover_from_raw_image(
                    source_copy,
                    temp_dir,
                    source_name=f"Recovered from {os.path.basename(source_path)}",
                    extra_note="The original image file was not modified." + hint_note,
                    disk_format_hint=disk_format_hint,
                    gw_sector_reports=_gw_sector_reports(
                        _gw_recovery_no_sector_report(
                            summary=(
                                f"Recovered {os.path.basename(source_path)} from a raw sector image. "
                                "No Greaseweazle read or conversion sector map was available to chart."
                            ),
                            disk_format=disk_format_hint,
                        )
                    ),
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )

            last_error = None
            candidate_formats = _conversion_candidate_formats(
                source_path,
                source_ext,
                disk_format_hint=disk_format_hint,
            )
            for disk_format in candidate_formats:
                _raise_if_cancelled(cancel_callback)
                converted = os.path.join(temp_dir, f"source_recovery_{disk_format.key.replace('.', '_')}.img")
                try:
                    _notify_progress(
                        progress_callback,
                        10,
                        100,
                        f"Converting {source_ext.upper()} image for {disk_format.label} recovery...",
                    )
                    conversion_output = _gw_convert(
                        source_path,
                        converted,
                        disk_format.key,
                        cancel_callback=cancel_callback,
                        allow_sector_failures=True,
                    )
                    conversion_sector_map = _parse_gw_sector_map(conversion_output, disk_format)
                    if (
                        not isinstance(disk_format_hint, DiskFormat)
                        and conversion_sector_map.get("found") == 0
                        and conversion_sector_map.get("total")
                    ):
                        raise FloppyImageError(
                            f"Greaseweazle found 0 sectors while trying {disk_format.label}; trying another format."
                        )
                    validation_note = ""
                    try:
                        _validate_converted_image_matches_boot_hint(converted, disk_format)
                    except ConvertedImageFormatMismatchError as exc:
                        if not isinstance(disk_format_hint, DiskFormat):
                            raise
                        detected_label = (
                            exc.suggested_format.label
                            if isinstance(exc.suggested_format, DiskFormat)
                            else exc.hinted_label
                            or "another format"
                        )
                        validation_note = (
                            f" The converted boot sector looked like {detected_label}, "
                            f"but recovery continued with the selected {disk_format.label} format."
                        )
                    except FloppyImageError as exc:
                        if not isinstance(disk_format_hint, DiskFormat):
                            raise
                        validation_note = (
                            f" Converted image geometry validation reported '{exc}', "
                            f"but recovery continued with the selected {disk_format.label} format."
                        )
                    return cls._recover_from_raw_image(
                        converted,
                        temp_dir,
                        source_name=f"Recovered from {os.path.basename(source_path)}",
                        extra_note=(
                            "The original image file was not modified."
                            + hint_note
                            + _gw_recovery_sector_note(conversion_sector_map, disk_format)
                            + validation_note
                        ),
                        disk_format_hint=disk_format_hint,
                        gw_sector_reports=_gw_sector_reports(
                            _gw_recovery_sector_report(
                                conversion_sector_map,
                                summary=(
                                    f"Recovered {os.path.basename(source_path)} by converting "
                                    f"the source image as {disk_format.label}."
                                ),
                                disk_format=disk_format,
                            )
                        ),
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                except FloppyOperationCancelled:
                    raise
                except Exception as exc:
                    last_error = exc

            detail = f" Last error: {last_error}" if last_error else ""
            raise FloppyImageError(
                "Recovery could not convert this image into raw floppy sectors. "
                "Try Autodetect, choose a different disk format, or use a raw IMG/BIN capture if one is available."
                + detail
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def _recover_usb_floppy(cls, drive_info, progress_callback=None, cancel_callback=None):
        disk_format_hint = None
        if isinstance(drive_info, FloppyRecoverySource):
            disk_format_hint = drive_info.disk_format
            drive_info = drive_info.drive_info
        if not isinstance(drive_info, FloppyDriveInfo):
            raise FloppyImageError("Invalid floppy drive selection.")

        temp_dir = tempfile.mkdtemp(prefix="aps_recover_floppy_")
        try:
            source_copy = os.path.join(temp_dir, "source_recovery.img")
            read_size = (
                disk_format_hint.size_bytes
                if isinstance(disk_format_hint, DiskFormat)
                else drive_info.size_bytes
            )
            _notify_progress(
                progress_callback,
                0,
                100,
                "Copying a full floppy image for recovery. This may take a long time...",
            )
            read_note = _read_block_device_recovery_image(
                drive_info.path,
                source_copy,
                read_size,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            format_note = ""
            if isinstance(disk_format_hint, DiskFormat):
                format_note = (
                    f" Recovery was run with the disk format hint: {disk_format_hint.label} "
                    f"({display_bytes(disk_format_hint.size_bytes)})."
                )
            return cls._recover_from_raw_image(
                source_copy,
                temp_dir,
                source_name=f"Recovered from {drive_info.display_name}",
                extra_note=f"{read_note}{format_note} The source floppy was not modified.",
                disk_format_hint=disk_format_hint,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def _recover_greaseweazle(cls, gw_source, progress_callback=None, cancel_callback=None):
        if not isinstance(gw_source, GreaseweazleFloppySource):
            raise FloppyImageError("Invalid Greaseweazle source selection.")

        temp_dir = tempfile.mkdtemp(prefix="aps_recover_gw_")
        try:
            attempts = [
                GreaseweazleFloppySource(
                    device_path=gw_source.device_path,
                    drive=gw_source.drive,
                    disk_format=gw_source.disk_format,
                    archival_quality=gw_source.archival_quality,
                    revs=gw_source.revs,
                    retries=max(gw_source.retries, 5),
                    capture_save_path=gw_source.capture_save_path,
                    capture_output_ext=gw_source.capture_output_ext,
                )
            ]
            if not gw_source.archival_quality:
                attempts.append(
                    GreaseweazleFloppySource(
                        device_path=gw_source.device_path,
                        drive=gw_source.drive,
                        disk_format=gw_source.disk_format,
                        archival_quality=True,
                        revs=max(gw_source.revs, 3),
                        retries=max(gw_source.retries, 5),
                        capture_output_ext="scp",
                    )
                )

            last_error = None
            for attempt_index, attempt in enumerate(attempts, start=1):
                _raise_if_cancelled(cancel_callback)
                capture_ext = "scp" if attempt.archival_quality else "img"
                capture = os.path.join(temp_dir, f"source_recovery_{attempt_index}.{capture_ext}")
                source_img = capture
                read_note = ""
                read_sector_map = {}
                conversion_sector_map = {}
                try:
                    _notify_progress(
                        progress_callback,
                        0,
                        100,
                        f"Reading floppy via Greaseweazle for recovery ({attempt.display_name})...",
                    )
                    read_sector_map = _gw_read_floppy(
                        attempt,
                        capture,
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                    if attempt.archival_quality and attempt.capture_save_path:
                        _raise_if_cancelled(cancel_callback)
                        _notify_progress(progress_callback, 68, 100, "Saving raw SCP capture...")
                        saved_capture = os.path.abspath(attempt.capture_save_path)
                        os.makedirs(os.path.dirname(saved_capture), exist_ok=True)
                        shutil.copy2(capture, saved_capture)
                        capture = saved_capture
                except FloppyOperationCancelled:
                    raise
                except Exception as exc:
                    read_sector_map = _parse_gw_sector_map(str(exc), attempt.disk_format)
                    last_error = exc
                    if not os.path.isfile(capture) or os.path.getsize(capture) <= 0:
                        continue
                    if attempt.archival_quality and attempt.capture_save_path:
                        _raise_if_cancelled(cancel_callback)
                        _notify_progress(progress_callback, 68, 100, "Saving partial raw SCP capture...")
                        saved_capture = os.path.abspath(attempt.capture_save_path)
                        os.makedirs(os.path.dirname(saved_capture), exist_ok=True)
                        shutil.copy2(capture, saved_capture)
                        capture = saved_capture
                    read_note = f"Greaseweazle reported a read error, but a partial {capture_ext.upper()} capture was available: {exc}"

                if attempt.archival_quality:
                    source_img = os.path.join(temp_dir, f"source_recovery_{attempt_index}.img")
                    try:
                        _notify_progress(progress_callback, 70, 100, "Converting raw SCP capture for recovery...")
                        conversion_output = _gw_convert(
                            capture,
                            source_img,
                            attempt.disk_format.key,
                            cancel_callback=cancel_callback,
                            allow_sector_failures=True,
                        )
                        conversion_sector_map = _parse_gw_sector_map(conversion_output, attempt.disk_format)
                        conversion_note = _gw_recovery_sector_note(conversion_sector_map, attempt.disk_format)
                        if conversion_note:
                            if read_note:
                                read_note += conversion_note
                            else:
                                read_note = "Greaseweazle recovery read completed." + conversion_note
                    except FloppyOperationCancelled:
                        raise
                    except Exception as exc:
                        last_error = exc
                        continue

                try:
                    note = read_note or "Greaseweazle recovery read completed."
                    note += " The source floppy was not modified."
                    return cls._recover_from_raw_image(
                        source_img,
                        temp_dir,
                        source_name=f"Recovered from {attempt.display_name}",
                        extra_note=note,
                        disk_format_hint=attempt.disk_format,
                        gw_sector_reports=_gw_sector_reports(
                            _gw_recovery_sector_report(
                                read_sector_map,
                                summary=f"Read {attempt.display_name} for recovery.",
                                disk_format=attempt.disk_format,
                            ),
                            _gw_recovery_sector_report(
                                conversion_sector_map,
                                summary=(
                                    f"Recovered {attempt.display_name} by converting "
                                    f"the Greaseweazle capture as {attempt.disk_format.label}."
                                ),
                                disk_format=attempt.disk_format,
                            ),
                        ),
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                except FloppyOperationCancelled:
                    raise
                except Exception as exc:
                    last_error = exc

            detail = f" Last error: {last_error}" if last_error else ""
            raise FloppyImageError(
                "Greaseweazle recovery could not produce recoverable sector data."
                + detail
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def _recover_from_raw_image(
        cls,
        source_img,
        temp_dir,
        *,
        source_name,
        extra_note="",
        disk_format_hint=None,
        gw_sector_reports=None,
        progress_callback=None,
        cancel_callback=None,
    ):
        source_img = os.path.abspath(source_img)
        prepared = os.path.join(temp_dir, "recovery_prepared.img")
        working_img = os.path.join(temp_dir, "working.img")
        try:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 72, 100, "Trying Yamaha/FAT repair before carving files...")
            prepare_yamaha_image(source_img, prepared)
            disk_format = _disk_format_for_image(prepared)
            listing = read_image_listing(prepared)
            if listing.entries:
                shutil.copy2(prepared, working_img)
                note = "Recovery opened a repaired editable image copy. Review before saving."
                if extra_note:
                    note += f" {str(extra_note).strip()}"
                return cls(
                    working_img,
                    "img",
                    temp_dir,
                    working_img,
                    disk_format,
                    YamahaRepairResult(note, True),
                    source_kind="recovered_image",
                    source_name=source_name,
                    gw_sector_reports=gw_sector_reports,
                )
        except FloppyOperationCancelled:
            raise
        except Exception:
            pass

        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 78, 100, "Scanning raw image for recoverable songs...")
        with open(source_img, "rb") as handle:
            source_data = handle.read()

        recovered_files = _recover_files_from_raw_image_bytes(source_data, disk_format_hint=disk_format_hint)
        if os.path.isfile(prepared):
            try:
                with open(prepared, "rb") as handle:
                    prepared_data = handle.read()
                if prepared_data != source_data:
                    recovered_files = _dedupe_recovered_files(
                        recovered_files + _recover_files_from_raw_image_bytes(
                            prepared_data,
                            disk_format_hint=disk_format_hint,
                        )
                    )
            except OSError:
                pass

        recovered_img, disk_format = _write_recovered_files_to_image(
            recovered_files,
            temp_dir,
            source_data,
            disk_format_hint=disk_format_hint,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        shutil.copy2(recovered_img, working_img)
        note = (
            f"Recovery created an editable image copy from {len(recovered_files)} recovered file(s). "
            "Some names, order, or damaged song data may be missing."
        )
        if extra_note:
            note += f" {str(extra_note).strip()}"
        return cls(
            working_img,
            "img",
            temp_dir,
            working_img,
            disk_format,
            YamahaRepairResult(note, True),
            source_kind="recovered_image",
            source_name=source_name,
            gw_sector_reports=gw_sector_reports,
        )

    @property
    def mode_name(self):
        return "Floppy Disk" if self.source_kind.startswith("floppy") else "Image Mode"

    @classmethod
    def _load_raw(cls, source_path, source_ext, temp_dir, progress_callback=None, cancel_callback=None):
        source_copy = os.path.join(temp_dir, "source.img")
        working_img = os.path.join(temp_dir, "working.img")
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 1, 4, "Copying raw floppy image...")
        shutil.copy2(source_path, source_copy)
        volume_name = _hfs_volume_name(source_copy)
        if volume_name:
            raise FloppyImageError(
                f"This appears to be a Macintosh HFS floppy image (volume '{volume_name}'), "
                "not an IBM/Yamaha FAT floppy image. APS MIDI Prep Tool cannot open Macintosh HFS volumes "
                "for Yamaha editing."
            )
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 2, 4, "Preparing editable floppy image...")
        repair_result = prepare_yamaha_image(source_copy, working_img)
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 3, 4, "Scanning floppy contents...")
        disk_format = _disk_format_for_image(working_img)
        read_image_listing(working_img)
        _raise_if_cancelled(cancel_callback)
        return cls(source_path, source_ext, temp_dir, working_img, disk_format, repair_result)

    @classmethod
    def _load_converted(
        cls,
        source_path,
        source_ext,
        temp_dir,
        disk_format_hint=None,
        progress_callback=None,
        cancel_callback=None,
    ):
        last_error = None
        conversion_failure_sector_maps = []
        candidate_formats = _conversion_candidate_formats(
            source_path,
            source_ext,
            disk_format_hint=disk_format_hint,
        )
        for disk_format in candidate_formats:
            _raise_if_cancelled(cancel_callback)
            candidate = os.path.join(temp_dir, f"candidate_{disk_format.key.replace('.', '_')}.img")
            prepared = os.path.join(temp_dir, f"prepared_{disk_format.key.replace('.', '_')}.img")
            try:
                _notify_progress(
                    progress_callback,
                    1,
                    4,
                    f"Converting image to editable {disk_format.label}...",
                )
                conversion_output = _gw_convert(source_path, candidate, disk_format.key, cancel_callback=cancel_callback)
                conversion_sector_map = _parse_gw_sector_map(conversion_output, disk_format)
                try:
                    _validate_converted_image_matches_boot_hint(candidate, disk_format)
                except ConvertedImageFormatMismatchError as exc:
                    if isinstance(disk_format_hint, DiskFormat):
                        raise GreaseweazleConversionError(
                            str(exc),
                            sector_map=_parse_gw_sector_map(conversion_output, disk_format),
                            disk_format=disk_format,
                            capture_path=source_path,
                            reason="format_mismatch",
                            suggested_format=exc.suggested_format,
                        ) from exc
                    raise
                except FloppyImageError as exc:
                    if isinstance(disk_format_hint, DiskFormat):
                        raise GreaseweazleConversionError(
                            str(exc),
                            sector_map=_parse_gw_sector_map(conversion_output, disk_format),
                            disk_format=disk_format,
                            capture_path=source_path,
                        ) from exc
                    raise
                _raise_if_cancelled(cancel_callback)
                _notify_progress(progress_callback, 2, 4, "Preparing editable floppy image...")
                repair_result = prepare_yamaha_image(candidate, prepared)
                _raise_if_cancelled(cancel_callback)
                _notify_progress(progress_callback, 3, 4, "Scanning floppy contents...")
                detected_format = _disk_format_for_image(prepared)
                if detected_format.size_bytes != disk_format.size_bytes:
                    raise FloppyImageError(
                        "Converted image did not match the requested disk size. "
                        f"Requested {disk_format.label}, but the converted image looks like {detected_format.label}."
                    )
                read_image_listing(prepared)
                working_img = os.path.join(temp_dir, "working.img")
                shutil.move(prepared, working_img)
                _raise_if_cancelled(cancel_callback)
                return cls(
                    source_path,
                    source_ext,
                    temp_dir,
                    working_img,
                    disk_format,
                    repair_result,
                    gw_sector_reports=_gw_sector_reports(
                        _gw_sector_report(
                            "convert",
                            conversion_sector_map,
                            title="Greaseweazle Conversion Sector Map",
                            summary=f"Converted {os.path.basename(source_path)} as {disk_format.label}.",
                            disk_format=disk_format,
                        )
                    ),
                )
            except FloppyOperationCancelled:
                raise
            except GreaseweazleConversionError as exc:
                last_error = exc
                if exc.sector_map:
                    conversion_failure_sector_maps.append(exc.sector_map)
                if isinstance(disk_format_hint, DiskFormat):
                    raise GreaseweazleConversionError(
                        str(exc),
                        sector_map=exc.sector_map,
                        disk_format=disk_format,
                        capture_path=source_path,
                        reason=exc.reason,
                        suggested_format=exc.suggested_format,
                    ) from exc
            except Exception as exc:
                last_error = exc
                if isinstance(disk_format_hint, DiskFormat):
                    raise

        if _should_probe_non_fat_gw_image(
            source_path,
            source_ext,
            disk_format_hint,
            conversion_failure_sector_maps,
        ):
            non_fat = _detect_non_fat_gw_image(
                source_path,
                source_ext,
                temp_dir,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            if non_fat is not None:
                disk_format = non_fat["disk_format"]
                volume_name = non_fat.get("volume_name") or "Untitled"
                raise GreaseweazleConversionError(
                    "Greaseweazle decoded this SCP as "
                    f"{disk_format.label} (volume '{volume_name}'), not an IBM/Yamaha FAT floppy. "
                    "APS MIDI Prep Tool cannot open Macintosh HFS volumes for Yamaha editing, "
                    "but it can save the decoded sector image without opening it.",
                    sector_map=non_fat.get("sector_map") or {},
                    disk_format=disk_format,
                    capture_path=source_path,
                    reason="non_fat_format",
                    details={"volume_name": volume_name},
                )

        detail = f" Last error: {last_error}" if last_error else ""
        raise FloppyImageError(
            "Could not convert this image into an editable FAT floppy image. "
            "Make sure the source is a supported floppy image and that Greaseweazle can convert it."
            + detail
        )

    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def list_entries(self):
        return read_image_listing(self.working_img_path)

    def _run_mtools(self, args, message, cancel_callback=None):
        _run_command(args, message, cancel_callback=cancel_callback)

    def _extract_from_image(self, source_img, image_path, dest_path, cancel_callback=None):
        if os.path.exists(dest_path):
            os.remove(dest_path)
        _raise_if_cancelled(cancel_callback)
        try:
            data = _read_fat12_file_bytes(source_img, image_path)
        except FloppyImageError as fat_exc:
            mcopy = shutil.which("mcopy")
            if not mcopy:
                raise fat_exc
            self._run_mtools(
                [mcopy, "-i", source_img, mtools_path(image_path), dest_path],
                f"Could not extract {image_path} from image",
                cancel_callback=cancel_callback,
            )
            return

        with open(dest_path, "wb") as handle:
            handle.write(data)
        _raise_if_cancelled(cancel_callback)

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

    def _write_generated_pianodir(
        self,
        target_img,
        pianodir_metadata=None,
        eseq_variant=None,
        eseq_directory_order=None,
        cancel_callback=None,
    ):
        eseq_variant = _normalized_eseq_variant(eseq_variant)
        directory_filename = _eseq_directory_filename_for_variant(eseq_variant)
        directory_order = {
            _normalize_image_path(path).upper(): bytes(order_key or b"")
            for path, order_key in dict(eseq_directory_order or {}).items()
        }
        listing = read_image_listing(target_img)
        track_entries = []

        for entry in listing.entries:
            _raise_if_cancelled(cancel_callback)
            if is_eseq_directory_path(entry.path):
                continue

            extracted_path = os.path.join(
                self.extracted_dir,
                f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(entry.path))}",
            )
            self._extract_from_image(
                target_img,
                entry.path,
                extracted_path,
                cancel_callback=cancel_callback,
            )
            if eseq_variant == ESEQ_VARIANT_CLAVINOVA:
                if not is_clavinova_mda_file(extracted_path):
                    continue
            elif not _host_file_is_eseq(extracted_path) or is_clavinova_mda_file(extracted_path):
                continue

            title = extract_eseq_title_from_file(extracted_path)
            if title.startswith("Error"):
                title = ""
            if not title and eseq_variant == ESEQ_VARIANT_CLAVINOVA:
                title = os.path.splitext(os.path.basename(entry.path))[0]
            track_entries.append(
                PianodirTrackEntry(
                    image_path=entry.path,
                    local_path=extracted_path,
                    title=title,
                )
            )

        def entry_sort_key(item):
            mapped_key = directory_order.get(_normalize_image_path(item.image_path).upper())
            if mapped_key:
                return mapped_key
            if os.path.isfile(item.local_path):
                return read_eseq_order_key_from_file(item.local_path)
            return build_eseq_order_key_from_path(item.image_path, sort_last=True)

        track_entries.sort(key=entry_sort_key)

        if eseq_variant == ESEQ_VARIANT_CLAVINOVA:
            directory_bytes = build_music_dir_bytes(track_entries)
        else:
            directory_bytes = build_pianodir_bytes(track_entries, metadata=pianodir_metadata)
        generated_path = os.path.join(self.patched_dir, f"{uuid.uuid4().hex}_{directory_filename}")
        with open(generated_path, "wb") as handle:
            handle.write(directory_bytes)

        mdel = _require_command("mdel")
        mcopy = _require_command("mcopy")
        for entry in listing.entries:
            _raise_if_cancelled(cancel_callback)
            if not is_eseq_directory_path(entry.path):
                continue
            self._run_mtools(
                [mdel, "-i", target_img, mtools_path(entry.path)],
                f"Could not replace existing {directory_filename} in image",
                cancel_callback=cancel_callback,
            )

        self._run_mtools(
            [mcopy, "-i", target_img, generated_path, mtools_path(directory_filename)],
            f"Could not write {directory_filename} into image",
            cancel_callback=cancel_callback,
        )

    def _delete_existing_pianodir(self, target_img, cancel_callback=None):
        listing = read_image_listing(target_img)
        mdel = _require_command("mdel")
        for entry in listing.entries:
            _raise_if_cancelled(cancel_callback)
            if not is_eseq_directory_path(entry.path):
                continue
            self._run_mtools(
                [mdel, "-i", target_img, mtools_path(entry.path)],
                f"Could not delete {entry.path} from image",
                cancel_callback=cancel_callback,
            )

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
        eseq_variant=None,
        eseq_directory_order=None,
        delete_pianodir=False,
        progress_callback=None,
        cancel_callback=None,
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
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 1, 4, "Applying pending changes to floppy image...")
            for image_path in sorted(deletes, key=lambda item: item.lower(), reverse=True):
                _raise_if_cancelled(cancel_callback)
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not delete {image_path} from image",
                    cancel_callback=cancel_callback,
                )

            if delete_pianodir:
                _raise_if_cancelled(cancel_callback)
                self._delete_existing_pianodir(target_img, cancel_callback=cancel_callback)

            for image_path, new_title in sorted(title_edits.items(), key=lambda item: item[0].lower()):
                _raise_if_cancelled(cancel_callback)
                if image_path in deletes or image_path in additions or image_path in replacements:
                    continue
                extracted_path = os.path.join(
                    self.extracted_dir,
                    f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(image_path))}",
                )
                self._extract_from_image(
                    target_img,
                    image_path,
                    extracted_path,
                    cancel_callback=cancel_callback,
                )
                patched_path = self._patched_metadata_path(
                    extracted_path,
                    image_path=image_path,
                    new_title=new_title,
                    order_key=order_key_edits.get(image_path),
                )
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not replace {image_path} in image",
                    cancel_callback=cancel_callback,
                )
                self._run_mtools(
                    [mcopy, "-i", target_img, patched_path, mtools_path(image_path)],
                    f"Could not write updated title for {image_path} into image",
                    cancel_callback=cancel_callback,
                )

            for image_path, host_path in sorted(replacements.items(), key=lambda item: item[0].lower()):
                _raise_if_cancelled(cancel_callback)
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
                    cancel_callback=cancel_callback,
                )
                self._run_mtools(
                    [mcopy, "-i", target_img, source_path, mtools_path(image_path)],
                    f"Could not write converted data for {image_path} into image",
                    cancel_callback=cancel_callback,
                )

            for source_path, target_path in sorted(renames.items(), key=lambda item: item[0].lower()):
                _raise_if_cancelled(cancel_callback)
                if source_path in deletes:
                    continue
                if _normalize_image_path(source_path).lower() == _normalize_image_path(target_path).lower():
                    continue
                self._run_mtools(
                    [mren, "-i", target_img, mtools_path(source_path), mtools_path(target_path)],
                    f"Could not rename {source_path} in image",
                    cancel_callback=cancel_callback,
                )

            for image_path, host_path in sorted(additions.items(), key=lambda item: item[0].lower()):
                _raise_if_cancelled(cancel_callback)
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
                    cancel_callback=cancel_callback,
                )

            for image_path, order_key in sorted(order_key_edits.items(), key=lambda item: item[0].lower()):
                _raise_if_cancelled(cancel_callback)
                if image_path in deletes or image_path in additions or image_path in replacements or image_path in title_edits:
                    continue
                extracted_path = os.path.join(
                    self.extracted_dir,
                    f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(image_path))}",
                )
                self._extract_from_image(
                    target_img,
                    image_path,
                    extracted_path,
                    cancel_callback=cancel_callback,
                )
                patched_path = self._patched_metadata_path(
                    extracted_path,
                    image_path=image_path,
                    order_key=order_key,
                )
                self._run_mtools(
                    [mdel, "-i", target_img, mtools_path(image_path)],
                    f"Could not replace {image_path} in image",
                    cancel_callback=cancel_callback,
                )
                self._run_mtools(
                    [mcopy, "-i", target_img, patched_path, mtools_path(image_path)],
                    f"Could not write updated order for {image_path} into image",
                    cancel_callback=cancel_callback,
                )

            if generate_pianodir:
                _raise_if_cancelled(cancel_callback)
                directory_filename = _eseq_directory_filename_for_variant(eseq_variant)
                _notify_progress(progress_callback, 2, 4, f"Generating {directory_filename}...")
                self._write_generated_pianodir(
                    target_img,
                    pianodir_metadata=pianodir_metadata,
                    eseq_variant=eseq_variant,
                    eseq_directory_order=eseq_directory_order,
                    cancel_callback=cancel_callback,
                )

            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 3, 4, "Verifying updated floppy image...")
            read_image_listing(target_img)
            _raise_if_cancelled(cancel_callback)
            return target_img
        except Exception:
            if os.path.exists(target_img):
                os.remove(target_img)
            raise

    def _write_image_direct(self, source_img, output_path, output_ext, cancel_callback=None):
        output_ext = output_ext.lower().lstrip(".")
        if output_ext in RAW_IMAGE_EXTENSIONS:
            _raise_if_cancelled(cancel_callback)
            shutil.copy2(source_img, output_path)
            _raise_if_cancelled(cancel_callback)
            return None
        if output_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise FloppyImageError(_unsupported_image_type_message(output_ext, for_output=True))
        output = _gw_convert(source_img, output_path, self.disk_format.key, cancel_callback=cancel_callback)
        return _gw_sector_report(
            "convert",
            _parse_gw_sector_map(output, self.disk_format),
            title="Greaseweazle Conversion Sector Map",
            summary=f"Converted the image to {output_ext.upper()} using {self.disk_format.label}.",
            disk_format=self.disk_format,
        )

    def write_image(self, source_img, output_path, output_ext, progress_callback=None, cancel_callback=None):
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
            report = self._write_image_direct(source_img, temp_output, output_ext, cancel_callback=cancel_callback)
            _raise_if_cancelled(cancel_callback)
            os.replace(temp_output, output_path)
            self.latest_gw_sector_reports = _gw_sector_reports(report)
        finally:
            if os.path.exists(temp_output):
                os.remove(temp_output)

    def _sync_modified_image_files_to_windows_drive(
        self,
        modified_img,
        drive_path,
        progress_callback=None,
        cancel_callback=None,
    ):
        root = _windows_filesystem_root(drive_path)
        if not root:
            raise FloppyImageError(f"Invalid Windows floppy drive path: {drive_path}")

        source_listing = read_image_listing(modified_img)
        target_listing = read_image_listing(drive_path)
        source_entries = list(source_listing.entries)
        target_entries = list(target_listing.entries)
        source_by_key = {_image_entry_key(entry): entry for entry in source_entries}
        target_by_key = {_image_entry_key(entry): entry for entry in target_entries}
        nested_entries = [
            entry.path
            for entry in source_entries + target_entries
            if entry.directory
        ]
        if nested_entries:
            raise FloppyImageError(
                "File-level Save To Floppy only supports root-directory floppy files. "
                "Use Write Current Image to Floppy for disks with folders."
            )

        permission_hint = (
            "Close File Explorer windows using the floppy, make sure the disk is not write-protected, "
            "and try again."
        )
        compare_keys = [
            key
            for key, source_entry in source_by_key.items()
            if (
                key in target_by_key
                and source_entry.size == target_by_key[key].size
                and not _must_refresh_floppy_sync_entry(source_entry)
            )
        ]
        total_steps = max(1, len(compare_keys) + len(target_entries) + len(source_entries) + 1)
        step = 0
        mcopy = _require_command("mcopy")
        preserved_keys = set()
        temp_extract_dir = tempfile.mkdtemp(prefix="aps_floppy_file_save_", dir=self.temp_dir)
        try:
            for key in sorted(compare_keys):
                _raise_if_cancelled(cancel_callback)
                source_entry = source_by_key[key]
                target_entry = target_by_key[key]
                step += 1
                _notify_progress(
                    progress_callback,
                    step,
                    total_steps,
                    f"Checking existing {source_entry.path} on floppy...",
                )
                source_extract_path = os.path.join(
                    temp_extract_dir,
                    f"{uuid.uuid4().hex}_{os.path.basename(source_entry.path)}",
                )
                self._extract_from_image(
                    modified_img,
                    source_entry.path,
                    source_extract_path,
                    cancel_callback=cancel_callback,
                )
                if _files_have_same_content(
                    source_extract_path,
                    _windows_drive_file_path(root, target_entry.path),
                ):
                    preserved_keys.add(key)

            for entry in sorted(target_entries, key=lambda item: item.path.lower()):
                _raise_if_cancelled(cancel_callback)
                step += 1
                key = _image_entry_key(entry)
                if key in preserved_keys:
                    _notify_progress(
                        progress_callback,
                        step,
                        total_steps,
                        f"Keeping unchanged {entry.path} on floppy...",
                    )
                    continue
                _notify_progress(
                    progress_callback,
                    step,
                    total_steps,
                    f"Removing old {entry.path} from floppy...",
                )
                target_path = _windows_drive_file_path(root, entry.path)
                try:
                    if os.path.isfile(target_path) or os.path.islink(target_path):
                        os.remove(target_path)
                except OSError as exc:
                    raise FloppyImageError(
                        f"Could not remove {entry.path} from the floppy: {exc}\n\n{permission_hint}"
                    ) from exc

            for entry in sorted(source_entries, key=lambda item: item.path.lower()):
                _raise_if_cancelled(cancel_callback)
                step += 1
                key = _image_entry_key(entry)
                if key in preserved_keys:
                    _notify_progress(
                        progress_callback,
                        step,
                        total_steps,
                        f"Skipping unchanged {entry.path}...",
                    )
                    continue
                _notify_progress(
                    progress_callback,
                    step,
                    total_steps,
                    f"Copying {entry.path} to floppy...",
                )
                self._run_mtools(
                    [
                        mcopy,
                        "-i",
                        modified_img,
                        mtools_path(entry.path),
                        _windows_mcopy_host_path(root, entry.path),
                    ],
                    f"Could not copy {entry.path} to the floppy",
                    cancel_callback=cancel_callback,
                )
        finally:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)

        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, total_steps, total_steps, "Checking floppy directory...")
        read_image_listing(drive_path)

    def _sync_modified_image_files_to_floppy_drive(
        self,
        modified_img,
        drive_path,
        progress_callback=None,
        cancel_callback=None,
    ):
        if os.name == "nt" and _windows_filesystem_root(drive_path):
            return self._sync_modified_image_files_to_windows_drive(
                modified_img,
                drive_path,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )

        mdel = _require_command("mdel")
        mcopy = _require_command("mcopy")
        source_listing = read_image_listing(modified_img)
        target_listing = read_image_listing(drive_path)

        source_entries = list(source_listing.entries)
        target_entries = list(target_listing.entries)
        source_by_key = {_image_entry_key(entry): entry for entry in source_entries}
        target_by_key = {_image_entry_key(entry): entry for entry in target_entries}
        nested_entries = [
            entry.path
            for entry in source_entries + target_entries
            if entry.directory
        ]
        if nested_entries:
            raise FloppyImageError(
                "File-level Save To Floppy only supports root-directory floppy files. "
                "Use Write Current Image to Floppy for disks with folders."
            )

        compare_keys = [
            key
            for key, source_entry in source_by_key.items()
            if (
                key in target_by_key
                and source_entry.size == target_by_key[key].size
                and not _must_refresh_floppy_sync_entry(source_entry)
            )
        ]
        total_steps = max(1, len(compare_keys) + len(target_entries) + len(source_entries) + 1)
        step = 0
        temp_extract_dir = tempfile.mkdtemp(prefix="aps_floppy_file_save_", dir=self.temp_dir)
        try:
            preserved_keys = set()
            source_extract_cache = {}
            for key in sorted(compare_keys):
                _raise_if_cancelled(cancel_callback)
                source_entry = source_by_key[key]
                target_entry = target_by_key[key]
                step += 1
                _notify_progress(
                    progress_callback,
                    step,
                    total_steps,
                    f"Checking existing {source_entry.path} on floppy...",
                )
                source_extract_path = os.path.join(
                    temp_extract_dir,
                    f"{uuid.uuid4().hex}_source_{os.path.basename(source_entry.path)}",
                )
                target_extract_path = os.path.join(
                    temp_extract_dir,
                    f"{uuid.uuid4().hex}_target_{os.path.basename(target_entry.path)}",
                )
                self._extract_from_image(
                    modified_img,
                    source_entry.path,
                    source_extract_path,
                    cancel_callback=cancel_callback,
                )
                source_extract_cache[key] = source_extract_path
                self._extract_from_image(
                    drive_path,
                    target_entry.path,
                    target_extract_path,
                    cancel_callback=cancel_callback,
                )
                if _files_have_same_content(source_extract_path, target_extract_path):
                    preserved_keys.add(key)

            for entry in sorted(target_entries, key=lambda item: item.path.lower()):
                _raise_if_cancelled(cancel_callback)
                step += 1
                key = _image_entry_key(entry)
                if key in preserved_keys:
                    _notify_progress(
                        progress_callback,
                        step,
                        total_steps,
                        f"Keeping unchanged {entry.path} on floppy...",
                    )
                    continue
                _notify_progress(
                    progress_callback,
                    step,
                    total_steps,
                    f"Removing old {entry.path} from floppy...",
                )
                self._run_mtools(
                    [mdel, "-i", drive_path, mtools_path(entry.path)],
                    f"Could not remove {entry.path} from the floppy",
                    cancel_callback=cancel_callback,
                )

            for entry in sorted(source_entries, key=lambda item: item.path.lower()):
                _raise_if_cancelled(cancel_callback)
                step += 1
                key = _image_entry_key(entry)
                if key in preserved_keys:
                    _notify_progress(
                        progress_callback,
                        step,
                        total_steps,
                        f"Skipping unchanged {entry.path}...",
                    )
                    continue
                _notify_progress(
                    progress_callback,
                    step,
                    total_steps,
                    f"Copying {entry.path} to floppy...",
                )
                extracted_path = source_extract_cache.get(key)
                if not extracted_path:
                    extracted_path = os.path.join(
                        temp_extract_dir,
                        f"{uuid.uuid4().hex}_{os.path.basename(entry.path)}",
                    )
                    self._extract_from_image(
                        modified_img,
                        entry.path,
                        extracted_path,
                        cancel_callback=cancel_callback,
                    )
                self._run_mtools(
                    [mcopy, "-i", drive_path, extracted_path, mtools_path(entry.path)],
                    f"Could not copy {entry.path} to the floppy",
                    cancel_callback=cancel_callback,
                )

            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, total_steps, total_steps, "Checking floppy directory...")
            read_image_listing(drive_path)
        finally:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)

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
        eseq_variant=None,
        eseq_directory_order=None,
        delete_pianodir=False,
        progress_callback=None,
        cancel_callback=None,
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
            eseq_variant=eseq_variant,
            eseq_directory_order=eseq_directory_order,
            delete_pianodir=delete_pianodir,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        try:
            self.write_image(
                modified_img,
                output_path,
                output_ext,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
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
        eseq_variant=None,
        eseq_directory_order=None,
        delete_pianodir=False,
        progress_callback=None,
        cancel_callback=None,
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
            eseq_variant=eseq_variant,
            eseq_directory_order=eseq_directory_order,
            delete_pianodir=delete_pianodir,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        try:
            reports = ()
            if self.source_kind == "floppy_usb":
                _notify_progress(progress_callback, 4, 5, f"Saving files to floppy {self.source_path}...")
                self._sync_modified_image_files_to_floppy_drive(
                    modified_img,
                    self.source_path,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
            elif self.source_kind == "floppy_gw":
                drive_name = self.gw_source.drive if self.gw_source is not None else "A"
                _notify_progress(progress_callback, 4, 5, f"Writing Greaseweazle drive {drive_name}...")
                write_sector_map = _gw_write_floppy(
                    self.gw_source,
                    modified_img,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
                reports = _gw_sector_reports(
                    _gw_sector_report(
                        "write",
                        write_sector_map,
                        title="Greaseweazle Write Sector Map",
                        summary=f"Wrote changes to {self.source_name}.",
                        disk_format=self.disk_format,
                    ),
                )
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
                report = self._write_image_direct(modified_img, temp_output, output_ext, cancel_callback=cancel_callback)
                reports = _gw_sector_reports(report)
                _raise_if_cancelled(cancel_callback)
                os.replace(temp_output, self.source_path)
            if not self.source_kind.startswith("floppy"):
                _raise_if_cancelled(cancel_callback)
            os.replace(modified_img, self.working_img_path)
            self._extracted_files.clear()
            self.repair_changed = False
            self.repair_note = "Floppy saved." if self.source_kind.startswith("floppy") else "Image saved."
            self.latest_gw_sector_reports = reports
        finally:
            temp_output = locals().get("temp_output")
            if temp_output and os.path.exists(temp_output):
                os.remove(temp_output)
            if os.path.exists(modified_img):
                os.remove(modified_img)

    def write_to_floppy_target(
        self,
        target_kind,
        target,
        renames=None,
        deletes=None,
        additions=None,
        replacements=None,
        title_edits=None,
        order_key_edits=None,
        pianodir_metadata=None,
        generate_pianodir=False,
        eseq_variant=None,
        eseq_directory_order=None,
        delete_pianodir=False,
        file_level=False,
        progress_callback=None,
        cancel_callback=None,
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
            eseq_variant=eseq_variant,
            eseq_directory_order=eseq_directory_order,
            delete_pianodir=delete_pianodir,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        try:
            reports = ()
            if target_kind == "floppy_usb":
                if not isinstance(target, FloppyDriveInfo):
                    raise FloppyImageError("Invalid floppy drive selection.")
                if file_level:
                    _notify_progress(progress_callback, 4, 5, f"Saving files to floppy {target.path}...")
                    self._sync_modified_image_files_to_floppy_drive(
                        modified_img,
                        target.path,
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                else:
                    _notify_progress(progress_callback, 4, 5, f"Writing floppy {target.path}...")
                    try:
                        _write_block_device(
                            modified_img,
                            target.path,
                            progress_callback=progress_callback,
                            cancel_callback=cancel_callback,
                        )
                    except FloppyImageError as exc:
                        if not _windows_raw_write_denied(exc):
                            raise
                        _notify_progress(
                            progress_callback,
                            4,
                            5,
                            "Windows denied direct floppy image writing; saving files through the mounted drive...",
                        )
                        try:
                            self._sync_modified_image_files_to_floppy_drive(
                                modified_img,
                                target.path,
                                progress_callback=progress_callback,
                                cancel_callback=cancel_callback,
                            )
                        except FloppyImageError as fallback_exc:
                            raise FloppyImageError(
                                f"Windows denied direct floppy image writing, and file-level saving also failed: {fallback_exc}"
                            ) from fallback_exc
            elif file_level:
                raise FloppyImageError(
                    "File-level Save To Floppy requires a floppy drive. "
                    "Use Write Current Image to Floppy for Greaseweazle writes."
                )
            elif target_kind == "floppy_gw":
                if not isinstance(target, GreaseweazleFloppySource):
                    raise FloppyImageError("Invalid Greaseweazle source selection.")
                _notify_progress(progress_callback, 4, 5, f"Writing Greaseweazle drive {target.drive}...")
                write_sector_map = _gw_write_floppy(
                    target,
                    modified_img,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
                reports = _gw_sector_reports(
                    _gw_sector_report(
                        "write",
                        write_sector_map,
                        title="Greaseweazle Write Sector Map",
                        summary=f"Wrote the current image to {target.display_name}.",
                        disk_format=target.disk_format,
                    ),
                )
            else:
                raise FloppyImageError("Invalid floppy write target.")
            _notify_progress(progress_callback, 5, 5, "Floppy write complete.")
            self.latest_gw_sector_reports = reports
        finally:
            if os.path.exists(modified_img):
                os.remove(modified_img)
