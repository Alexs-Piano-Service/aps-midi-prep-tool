import datetime
import errno
import hashlib
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
from .eseq_converter import _eseq_event_stream_start, is_eseq_file
from .dos83_renamer import is_dos83_filename
from .midi_metadata import (
    extract_eseq_title_from_file,
    has_eseq_title_metadata,
    is_midi_file,
    update_eseq_title_to_path,
    update_midi_title_to_path,
)
from .additional_formats import electone_mdr_to_midi, pianodisc_system3
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


class BlankDiskImageError(FloppyImageError):
    """Raised when a converted image has no usable filesystem or recoverable Yamaha data."""

    def __init__(self, message, *, disk_format=None, sector_map=None, source_path=""):
        super().__init__(message)
        self.disk_format = disk_format
        self.sector_map = sector_map or {}
        self.source_path = source_path or ""


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


class _FloppyReadStalled(FastFloppyReadError):
    """Stop a stalled read without starting another pass or filling it with zeros."""


class FloppyRecoveryError(FloppyImageError):
    """Raised when recovery fails with structured disk-level diagnostics."""

    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class _RecoveryReadDeadlineExceeded(FloppyImageError):
    """Raised when a bounded physical-floppy read reaches its deadline."""


def _raise_if_cancelled(cancel_callback=None):
    if cancel_callback is not None and cancel_callback():
        raise FloppyOperationCancelled("Operation cancelled.")


def _host_file_is_eseq(path):
    return os.path.isfile(path) and is_eseq_file(path) and has_eseq_title_metadata(path)


def _normalized_eseq_variant(eseq_variant):
    if eseq_variant == ESEQ_VARIANT_CLAVINOVA:
        return ESEQ_VARIANT_CLAVINOVA
    return ESEQ_VARIANT_DISKLAVIER


def _host_file_matches_eseq_variant(path, eseq_variant):
    if not _host_file_is_eseq(path):
        return False
    return is_clavinova_mda_file(path) == (
        _normalized_eseq_variant(eseq_variant) == ESEQ_VARIANT_CLAVINOVA
    )


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
DIRECT_FLOPPY_IMAGE_EXTENSIONS = RAW_IMAGE_EXTENSIONS | {"hfe"}
HFE_SIGNATURE = b"HXCPICFE"
HFE_TRACK_ENCODING_OFFSET = 0x0B
HFE_FLOPPY_INTERFACE_OFFSET = 0x10
HFE_ENCODING_ISOIBM_MFM = 0x00
HFE_INTERFACE_IBMPC_DD = 0x00
HFE_INTERFACE_IBMPC_HD = 0x01
HFE_INTERFACE_IBMPC_ED = 0x08
# HFE interface modes encode density separately from the track bitrate.
# https://hxc2001.com/floppy_drive_emulator/HFE-file-format.html
HFE_IBMPC_INTERFACE_BY_FORMAT = {
    "ibm.160": HFE_INTERFACE_IBMPC_DD,
    "ibm.180": HFE_INTERFACE_IBMPC_DD,
    "ibm.320": HFE_INTERFACE_IBMPC_DD,
    "ibm.360": HFE_INTERFACE_IBMPC_DD,
    "ibm.720": HFE_INTERFACE_IBMPC_DD,
    "ibm.800": HFE_INTERFACE_IBMPC_DD,
    "ibm.1200": HFE_INTERFACE_IBMPC_HD,
    "ibm.1440": HFE_INTERFACE_IBMPC_HD,
    "ibm.2880": HFE_INTERFACE_IBMPC_ED,
}

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

# POSIX recovery reads use synchronous pread calls. Windows recovery reads use
# overlapped I/O that can be cancelled at the deadline, although opening a
# device, submitting a read, or a driver that never completes cancellation can
# still overrun it. Keep the public limit described as a soft deadline.
USB_FLOPPY_RECOVERY_SECTOR_SIZE = _YAMAHA_BYTES_PER_SECTOR
USB_FLOPPY_RECOVERY_CHUNK_SIZE = 8 * 1024
USB_FLOPPY_RECOVERY_SOFT_DEADLINE_SECONDS = 5 * 60
USB_FLOPPY_RECOVERY_ALL_BAD_SAMPLE_SECTORS = 128
USB_FLOPPY_RECOVERY_MOSTLY_BAD_SAMPLE_SECTORS = 256
USB_FLOPPY_RECOVERY_MOSTLY_BAD_RATIO = 0.90
USB_FLOPPY_RECOVERY_CONSECUTIVE_BAD_SECTORS = 128
USB_FLOPPY_RECOVERY_BAD_MEDIA_MINIMUM_COVERAGE = 0.10
BLANK_FLOPPY_FILL_VALUES = frozenset({0x00, 0xE5, 0xF6, 0xFF})

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


def _diagnostic_bool(value):
    if value is None:
        return "unknown"
    return "yes" if bool(value) else "no"


def _diagnostic_count(value):
    if value is None:
        return "unknown"
    try:
        return str(max(0, int(value)))
    except (TypeError, ValueError):
        return "unknown"


def _diagnostic_percent(numerator, denominator):
    try:
        numerator = max(0, int(numerator or 0))
        denominator = max(0, int(denominator or 0))
    except (TypeError, ValueError):
        return "unknown"
    if denominator <= 0:
        return "unknown"
    return f"{(numerator / denominator) * 100:.1f}%"


def _format_diagnostic_sector_ranges(ranges, *, limit=12):
    labels = []
    for start, end in list(ranges or ())[:limit]:
        start = int(start)
        end = int(end)
        labels.append(str(start) if start == end else f"{start}-{end}")
    if len(ranges or ()) > limit:
        labels.append(f"+{len(ranges) - limit} more ranges")
    return ", ".join(labels) or "none"


def format_floppy_recovery_diagnostics(diagnostics):
    """Return a compact human-readable report for a JSON-safe diagnostic dict."""

    details = dict(diagnostics or {})
    selected_label = str(details.get("selected_format_label") or "Unknown")
    detected_label = str(details.get("detected_format_label") or "Unknown")
    drive_path = str(details.get("drive_path") or "Unknown")
    drive_model = str(details.get("drive_model") or "").strip()
    drive_transport = str(details.get("drive_transport") or "").strip().upper()
    drive_bits = [drive_path]
    if drive_transport:
        drive_bits.append(drive_transport)
    if drive_model:
        drive_bits.append(drive_model)

    expected = int(details.get("expected_sectors") or 0)
    attempted = int(details.get("attempted_sectors") or 0)
    readable = int(details.get("readable_sectors") or 0)
    unresolved = int(details.get("unresolved_sectors") or 0)
    duration = float(details.get("duration_seconds") or 0.0)
    deadline = float(details.get("soft_deadline_seconds") or 0.0)
    stop_reason = str(details.get("stop_reason") or "completed")
    lines = [
        "Floppy diagnostics",
        "------------------",
        f"Drive: {' | '.join(drive_bits)}",
        f"Drive-reported size: {display_bytes(int(details.get('drive_reported_bytes') or 0))}",
        f"Selected format: {selected_label} ({display_bytes(int(details.get('selected_bytes') or 0))})",
        f"Detected format: {detected_label}",
        f"Detection basis: {details.get('detection_basis') or 'none'}",
        f"Boot-sector geometry claim: {details.get('boot_claimed_format_label') or 'none'}",
        f"Geometry mismatch: {_diagnostic_bool(details.get('geometry_mismatch'))}",
        "Raw read:",
        f"  Expected sectors: {expected}",
        f"  Attempted sectors: {attempted}",
        f"  Good in chunk reads: {int(details.get('good_sectors') or 0)}",
        f"  Recovered after sector fallback: {int(details.get('recovered_after_fallback_sectors') or 0)}",
        f"  Readable sectors: {readable} ({_diagnostic_percent(readable, expected)})",
        f"  Bad sectors: {int(details.get('bad_sectors') or 0)}",
        f"  Attempted but unresolved sectors: {unresolved}",
        f"  Unattempted sectors: {int(details.get('unattempted_sectors') or 0)}",
        "  Bad sector ranges (zero-based): "
        f"{_format_diagnostic_sector_ranges(details.get('bad_sector_ranges'))}",
        "  Fallback-recovered ranges (zero-based): "
        f"{_format_diagnostic_sector_ranges(details.get('fallback_recovered_sector_ranges'))}",
        "  Attempted-but-unresolved ranges (zero-based): "
        f"{_format_diagnostic_sector_ranges(details.get('unresolved_sector_ranges'))}",
        "  Unattempted ranges (zero-based): "
        f"{_format_diagnostic_sector_ranges(details.get('unattempted_sector_ranges'))}",
        f"  Bytes recovered: {int(details.get('bytes_recovered') or 0):,}",
        f"  Partial-sector bytes retained: {int(details.get('partial_bytes_recovered') or 0):,} "
        f"across {int(details.get('partially_readable_sectors') or 0)} sector(s)",
        f"  Missing-sector fill: {details.get('fill_value') or '0x00'}",
        f"  Read calls: {int(details.get('read_calls') or 0)} "
        f"({int(details.get('fallback_read_calls') or 0)} sector fallback)",
        f"  Read passes used: {int(details.get('read_passes') or 0)}",
        "  Retry policy: one bulk attempt plus at most one per-sector fallback attempt",
        f"  Duration: {duration:.1f} seconds",
        f"  Stop reason: {stop_reason}",
        f"  Poor-media cutoff begins after: "
        f"{float(details.get('bad_media_minimum_coverage') or 0.0) * 100:.0f}% resolved coverage",
    ]
    if deadline > 0:
        if details.get("read_deadline_mode") == "windows_overlapped_cancel":
            lines.append(
                f"  Read deadline: {deadline:.0f} seconds "
                "(cancellation is requested for a pending Windows read at the deadline)"
            )
        else:
            lines.append(
                f"  Soft deadline: {deadline:.0f} seconds "
                "(checked between synchronous device reads)"
            )
    if details.get("windows_cancel_drain_incomplete"):
        lines.append(
            "  Windows cancellation warning: the driver did not confirm completion within the drain grace period"
        )

    read_errors = details.get("read_errors")
    if isinstance(read_errors, dict) and read_errors:
        lines.append("  Read errors:")
        for message, count in list(read_errors.items())[:8]:
            lines.append(f"    {int(count or 0)}x {message}")
        if len(read_errors) > 8:
            lines.append(f"    +{len(read_errors) - 8} more distinct error(s)")

    lines.extend(
        [
            "Filesystem:",
            f"  Boot sector readable: {_diagnostic_bool(details.get('boot_sector_readable'))}",
            f"  0x55AA signature: {_diagnostic_bool(details.get('boot_signature_present'))}",
            f"  Valid FAT copies: {int(details.get('fat_copies_valid') or 0)}/"
            f"{int(details.get('fat_copies_expected') or 0)}",
            f"  FAT area fully readable: {_diagnostic_bool(details.get('fat_area_readable'))}",
            f"  FAT copies consistent: {_diagnostic_bool(details.get('fat_copies_consistent'))}",
            f"  FAT allocated data clusters: "
            f"{_diagnostic_count(details.get('fat_allocated_data_clusters'))}",
            f"  FAT allocation empty: {_diagnostic_bool(details.get('fat_allocation_empty'))}",
            f"  Root directory fully readable: {_diagnostic_bool(details.get('root_directory_readable'))}",
            f"  Root directory structurally valid: "
            f"{_diagnostic_bool(details.get('root_directory_structurally_valid'))}",
            f"  Root directory plausible: {_diagnostic_bool(details.get('root_directory_plausible'))}",
            f"  Active root entries: {_diagnostic_count(details.get('root_directory_entries'))}",
            f"  PIANODIR.FIL directory entry: {_diagnostic_bool(details.get('pianodir_directory_entry_found'))}",
            "Raw scan:",
            f"  MIDI MThd signatures: {int(details.get('midi_header_signatures') or 0)}",
            f"  E-SEQ signatures: {int(details.get('eseq_signatures') or 0)}",
            f"  PIANODIR signatures: {int(details.get('pianodir_signatures') or 0)}",
            f"  Recognizable signatures: {int(details.get('recognizable_signatures') or 0)}",
            f"  Non-zero sectors: {int(details.get('nonzero_sectors') or 0)}",
            f"  Fill-only data: {_diagnostic_bool(details.get('blank_fill_only'))}",
            f"  Uniform fill byte: {details.get('blank_fill_value') or 'not detected'}",
            f"  Recovered files: {int(details.get('recovered_files') or 0)}",
            "Captured image:",
            f"  Bytes: {int(details.get('image_bytes') or 0):,}",
            f"  SHA256: {details.get('sha256') or 'not calculated'}",
        ]
    )

    geometry_scans = list(details.get("geometry_scans") or ())
    if geometry_scans:
        lines.append("Geometry scans:")
        for scan in geometry_scans:
            scan = dict(scan or {})
            label = str(scan.get("format_label") or scan.get("format_key") or "Unknown")
            scan_parts = [label]
            if scan.get("selected_hint"):
                scan_parts.append("selected")
            if scan.get("boot_hint"):
                scan_parts.append("boot-detected")
            if scan.get("attempted"):
                scan_parts.append("file recovery attempted")
            valid_fats = int(scan.get("fat_copies_valid") or 0)
            expected_fats = int(scan.get("fat_copies_expected") or 0)
            scan_parts.append(f"FAT {valid_fats}/{expected_fats}")
            if (
                scan.get("root_directory_structurally_valid")
                and scan.get("root_directory_entries") == 0
            ):
                scan_parts.append("root valid and empty")
            elif scan.get("root_directory_plausible"):
                scan_parts.append("root plausible")
            elif not scan.get("root_directory_readable"):
                scan_parts.append("root unreadable")
            else:
                scan_parts.append("root not recognized")
            recovered = int(scan.get("recovered_files") or 0)
            if recovered:
                scan_parts.append(f"{recovered} recovered file(s)")
            error = str(scan.get("error") or "").strip()
            if error:
                scan_parts.append(f"error: {error}")
            lines.append("  " + "; ".join(scan_parts))

    return "\n".join(lines)


def _new_usb_floppy_recovery_diagnostics(
    drive_info,
    disk_format,
    expected_bytes,
    *,
    sector_size,
    soft_deadline_seconds,
):
    expected_bytes = max(0, int(expected_bytes or 0))
    sector_size = max(1, int(sector_size or USB_FLOPPY_RECOVERY_SECTOR_SIZE))
    selected_format = disk_format if isinstance(disk_format, DiskFormat) else DISK_FORMAT_BY_SIZE.get(expected_bytes)
    details = {
        "schema_version": 1,
        "source_kind": "floppy_usb",
        "drive_path": str(getattr(drive_info, "path", "") or ""),
        "drive_model": str(getattr(drive_info, "model", "") or ""),
        "drive_transport": str(getattr(drive_info, "transport", "") or ""),
        "drive_reported_bytes": int(getattr(drive_info, "size_bytes", 0) or 0),
        "selected_format_key": str(getattr(selected_format, "key", "") or ""),
        "selected_format_label": str(getattr(selected_format, "label", "") or "Unknown"),
        "selected_bytes": expected_bytes,
        "detected_format_key": "",
        "detected_format_label": "",
        "detected_bytes": 0,
        "detection_basis": "",
        "geometry_mismatch": None,
        "boot_claimed_format_key": "",
        "boot_claimed_format_label": "",
        "boot_claimed_bytes": 0,
        "sector_size": sector_size,
        "expected_sectors": int(math.ceil(expected_bytes / sector_size)) if expected_bytes else 0,
        "attempted_sectors": 0,
        "good_sectors": 0,
        "recovered_after_fallback_sectors": 0,
        "readable_sectors": 0,
        "bad_sectors": 0,
        "unresolved_sectors": 0,
        "unattempted_sectors": int(math.ceil(expected_bytes / sector_size)) if expected_bytes else 0,
        "bad_sector_ranges": [],
        "fallback_recovered_sector_ranges": [],
        "unresolved_sector_ranges": [],
        "unattempted_sector_ranges": [],
        "sector_ranges_truncated": {},
        "bytes_recovered": 0,
        "partial_bytes_recovered": 0,
        "partially_readable_sectors": 0,
        "fill_value": "0x00",
        "read_calls": 0,
        "fallback_read_calls": 0,
        "read_passes": 0,
        "duration_seconds": 0.0,
        "soft_deadline_seconds": float(soft_deadline_seconds or 0.0),
        "read_deadline_mode": "",
        "windows_cancel_drain_incomplete": False,
        "bad_media_minimum_coverage": float(
            USB_FLOPPY_RECOVERY_BAD_MEDIA_MINIMUM_COVERAGE
        ),
        "stopped_early": False,
        "stop_reason": "",
        "read_errors": {},
        "boot_sector_readable": None,
        "boot_signature_present": None,
        "fat_copies_expected": 0,
        "fat_copies_valid": 0,
        "fat_area_readable": None,
        "fat_copies_consistent": None,
        "fat_allocated_data_clusters": None,
        "fat_allocation_empty": None,
        "root_directory_readable": None,
        "root_directory_structurally_valid": None,
        "root_directory_plausible": None,
        "root_directory_entries": None,
        "pianodir_directory_entry_found": None,
        "midi_header_signatures": 0,
        "eseq_signatures": 0,
        "pianodir_signatures": 0,
        "recognizable_signatures": 0,
        "nonzero_sectors": 0,
        "blank_fill_only": None,
        "blank_fill_value": "",
        "image_bytes": 0,
        "sha256": "",
        "geometry_scans": [],
        "recovered_files": 0,
        "recovered_midi_files": 0,
        "recovered_eseq_files": 0,
        "recovered_pianodir_files": 0,
        "human_report": "",
    }
    details["human_report"] = format_floppy_recovery_diagnostics(details)
    return details


def usb_floppy_format_capacity_error(drive_info, disk_format):
    if not isinstance(drive_info, FloppyDriveInfo) or not isinstance(
        disk_format,
        DiskFormat,
    ):
        return ""

    drive_size = int(drive_info.size_bytes or 0)
    format_size = int(disk_format.size_bytes or 0)
    if drive_size <= 0 or format_size <= drive_size:
        return ""

    return (
        f"The selected {disk_format.label} format requires {format_size:,} bytes, but "
        f"{drive_info.path} currently reports a capacity of only {drive_size:,} bytes. "
        "APS MIDI Prep Tool will not attempt an oversized raw write. Choose a format that "
        "fits the inserted disk and drive, or use compatible hardware such as an ED-capable "
        "drive and media for a 2.88 MB format."
    )


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
    if os.name == "nt":
        # A bundled one-file helper can have its own child process. Stopping
        # only its launcher leaves that child holding files in _MEI directories.
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=5,
                **windows_subprocess_kwargs(),
            )
            process.wait(timeout=2)
            return
        except Exception:
            pass
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


def _command_timeout_error(args, error_prefix, timeout):
    tool = os.path.basename(os.fspath(args[0]))
    return FloppyImageError(
        f"{error_prefix}. APS stopped waiting because the disk-image tool did not finish "
        f"within {timeout:g} seconds ({tool}). "
        "Try saving to a folder on this computer first. If you are reading an original "
        "floppy, try Read Floppy with Start in recovery mode."
    )


def _run_command(args, error_prefix, *, cancel_callback=None, timeout=120.0):
    _raise_if_cancelled(cancel_callback)
    process = subprocess.Popen(
        args,
        text=True,
        stdin=subprocess.DEVNULL,
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
    deadline = time.monotonic() + timeout
    try:
        while communicator.is_alive():
            _raise_if_cancelled(cancel_callback)
            if time.monotonic() >= deadline:
                raise _command_timeout_error(args, error_prefix, timeout)
            communicator.join(timeout=0.1)
    except BaseException:
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
        stdin=subprocess.DEVNULL,
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
        os.path.join("aps_midi_prep_tool_app", "bin"),
        *(os.path.join(root, tool) for root in ("bin", os.path.join("aps_midi_prep_tool_app", "bin"))
          for tool in ("mtools", "7zip", "greaseweazle", "fluidsynth", "lame")),
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
            "APS could not find one of the tools needed to prepare this disk image "
            f"(mtools: {command}). "
            "Please download a complete APS build and try again. "
            "If you run APS from Python source, install mtools."
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
        timeout=5.0,
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
        separator = " - " if str(prefix or "").endswith(":") else ": "
        return f"{prefix}{separator}{ctypes.FormatError(error_code).strip()}"
    return f"{prefix}."


def _windows_write_failure_message(
    device_path,
    *,
    image_size,
    written_before,
    requested_size,
    written_size,
    windows_error="",
):
    image_size = max(0, int(image_size or 0))
    written_before = max(0, int(written_before or 0))
    requested_size = max(0, int(requested_size or 0))
    written_size = max(0, int(written_size or 0))
    written_total = min(image_size, written_before + written_size)
    message = (
        f"Could not fully write floppy device {device_path}. Windows wrote "
        f"{written_total:,} of {image_size:,} image bytes; the unsuccessful write "
        f"requested {requested_size:,} bytes and wrote {written_size:,} bytes."
    )
    if windows_error:
        message += f"\n\nWindows error: {windows_error}"
    message += (
        "\n\nThe selected image may be larger than the floppy or drive capacity. "
        "Check that the selected disk format matches both the inserted media and the drive."
    )
    return message


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
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            text=True,
            capture_output=True,
            check=False,
            timeout=5.0,
            **windows_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        # Registry discovery can still work when WMI or PowerShell is stuck.
        return []
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
        timeout=5.0,
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
    path = shutil.which(command_name) or _find_bundled_command(command_name)
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
        disk_format = GW_FORMAT_BY_KEY.get(_disk_format_key(disk_format))
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


def verify_image_payloads(delivered_path, prepared_raw_path, disk_format, *, cancel_callback=None):
    """Reopen delivered bytes, decoding HFE if needed, and compare all files.

    Conversion has already produced the prepared payloads, so byte equality at
    this boundary also protects their titles, musical events and catalog order.
    This does not claim compatibility with an instrument or physical playback.
    """
    with tempfile.TemporaryDirectory(prefix="aps_verify_image_") as temp_dir:
        _raise_if_cancelled(cancel_callback)
        delivered_raw = os.fspath(delivered_path)
        if image_extension(delivered_raw) not in RAW_IMAGE_EXTENSIONS:
            delivered_raw = os.path.join(temp_dir, "readback.img")
            _gw_convert(
                delivered_path, delivered_raw, disk_format.key,
                cancel_callback=cancel_callback,
            )
        expected = {
            entry.path.upper(): entry.path
            for entry in _read_fat12_image_listing(prepared_raw_path).entries if not entry.directory
        }
        actual = {
            entry.path.upper(): entry.path
            for entry in _read_fat12_image_listing(delivered_raw).entries if not entry.directory
        }
        if set(expected) != set(actual):
            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            raise FloppyImageError(
                f"Delivered image verification failed: missing files {missing}; unexpected files {extra}."
            )
        digests = {}
        for key, expected_path in expected.items():
            _raise_if_cancelled(cancel_callback)
            prepared = _read_fat12_file_bytes(prepared_raw_path, expected_path)
            delivered = _read_fat12_file_bytes(delivered_raw, actual[key])
            if prepared != delivered:
                raise FloppyImageError(f"Delivered image verification failed: contents differ for {expected_path}.")
            digests[expected_path] = hashlib.sha256(delivered).hexdigest()
        return {"confidence": "contents_verified", "files_verified": len(digests), "sha256_by_file": digests,
                "hardware_tested": False}


def _verify_physical_floppy_contents(prepared_path, target_kind, target, disk_format,
                                    *, progress_callback=None, cancel_callback=None):
    _notify_progress(progress_callback, 4, 5, "Reading back floppy contents for verification...")
    with tempfile.TemporaryDirectory(prefix="aps_floppy_readback_") as verify_dir:
        readback_path = os.path.join(verify_dir, "readback.img")
        try:
            if target_kind == "floppy_usb":
                _read_block_device(
                    target.path, readback_path, target.size_bytes or disk_format.size_bytes,
                    progress_callback=progress_callback, cancel_callback=cancel_callback,
                )
            else:
                _gw_read_floppy(target, readback_path, progress_callback=progress_callback,
                                cancel_callback=cancel_callback)
            return verify_image_payloads(readback_path, prepared_path, disk_format,
                                         cancel_callback=cancel_callback)
        except FloppyOperationCancelled:
            raise
        except Exception as exc:
            raise FloppyImageError(f"Files were written, but readback verification failed: {exc}") from exc


def _finish_temp_output(temp_path, output_path):
    try:
        os.replace(temp_path, output_path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(temp_path, output_path)
        os.remove(temp_path)
    return output_path


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


def _create_empty_pianodir_file(temp_dir, metadata=None):
    pianodir_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{PIANODIR_FILENAME}")
    with open(pianodir_path, "wb") as handle:
        handle.write(build_pianodir_bytes([], metadata=metadata))
    return pianodir_path


def _delete_eseq_directory_entries_from_image(target_img, cancel_callback=None):
    listing = read_image_listing(target_img)
    mdel = _require_command("mdel")
    for entry in listing.entries:
        _raise_if_cancelled(cancel_callback)
        if not is_eseq_directory_path(entry.path):
            continue
        _run_command(
            [mdel, "-i", target_img, mtools_path(entry.path)],
            f"Could not replace existing {entry.path} in image",
            cancel_callback=cancel_callback,
        )


def _write_empty_pianodir_to_image(target_img, temp_dir, metadata=None, cancel_callback=None):
    pianodir_path = _create_empty_pianodir_file(temp_dir, metadata=metadata)
    _delete_eseq_directory_entries_from_image(target_img, cancel_callback=cancel_callback)
    _copy_host_file_into_image(target_img, pianodir_path, PIANODIR_FILENAME, cancel_callback=cancel_callback)


def _delete_image_entries(target_img, entries, *, cancel_callback=None):
    entries = list(entries or ())
    if not entries:
        return
    mdel = _require_command("mdel")
    for entry in sorted(entries, key=lambda item: item.path.lower()):
        _raise_if_cancelled(cancel_callback)
        _run_command(
            [mdel, "-i", target_img, mtools_path(entry.path)],
            f"Could not remove {entry.path} from image",
            cancel_callback=cancel_callback,
        )


def _prepare_existing_formatted_image(
    target_img,
    entries,
    temp_dir,
    *,
    eseq_disk=False,
    metadata=None,
    cancel_callback=None,
):
    _delete_image_entries(target_img, entries, cancel_callback=cancel_callback)
    if eseq_disk:
        pianodir_path = _create_empty_pianodir_file(temp_dir, metadata=metadata)
        _copy_host_file_into_image(
            target_img,
            pianodir_path,
            PIANODIR_FILENAME,
            cancel_callback=cancel_callback,
        )


def _prepare_existing_formatted_usb_floppy(
    drive_path,
    entries,
    temp_dir,
    *,
    eseq_disk=False,
    metadata=None,
    progress_callback=None,
    cancel_callback=None,
):
    entries = sorted(list(entries or ()), key=lambda item: item.path.lower())
    pianodir_path = _create_empty_pianodir_file(temp_dir, metadata=metadata) if eseq_disk else None
    total_steps = max(1, len(entries) + (1 if eseq_disk else 0) + 1)
    step = 0
    root = _windows_filesystem_root(drive_path) if os.name == "nt" else None
    permission_hint = (
        "Close File Explorer windows using the floppy, make sure the disk is not write-protected, "
        "and try again."
    )

    if root:
        for entry in entries:
            _raise_if_cancelled(cancel_callback)
            step += 1
            _notify_progress(progress_callback, step, total_steps, f"Clearing {entry.path} from floppy...")
            target_path = _windows_drive_file_path(root, entry.path)
            try:
                if os.path.isfile(target_path) or os.path.islink(target_path):
                    os.remove(target_path)
            except OSError as exc:
                raise FloppyImageError(
                    f"Could not remove {entry.path} from the floppy: {exc}\n\n{permission_hint}"
                ) from exc
        if pianodir_path:
            _raise_if_cancelled(cancel_callback)
            step += 1
            _notify_progress(progress_callback, step, total_steps, "Adding empty PIANODIR.FIL...")
            try:
                shutil.copy2(pianodir_path, _windows_drive_file_path(root, PIANODIR_FILENAME))
            except OSError as exc:
                raise FloppyImageError(
                    f"Could not add PIANODIR.FIL to the floppy: {exc}\n\n{permission_hint}"
                ) from exc
    else:
        mdel = _require_command("mdel") if entries else None
        for entry in entries:
            _raise_if_cancelled(cancel_callback)
            step += 1
            _notify_progress(progress_callback, step, total_steps, f"Clearing {entry.path} from floppy...")
            _run_command(
                [mdel, "-i", drive_path, mtools_path(entry.path)],
                f"Could not remove {entry.path} from the floppy",
                cancel_callback=cancel_callback,
            )
        if pianodir_path:
            _raise_if_cancelled(cancel_callback)
            step += 1
            _notify_progress(progress_callback, step, total_steps, "Adding empty PIANODIR.FIL...")
            _run_mcopy_host_to_image(
                _run_command,
                drive_path,
                pianodir_path,
                PIANODIR_FILENAME,
                "Could not add PIANODIR.FIL to the floppy",
                cancel_callback=cancel_callback,
            )

    _raise_if_cancelled(cancel_callback)
    _notify_progress(progress_callback, total_steps, total_steps, "Checking floppy directory...")
    read_image_listing(drive_path)


def _clean_ascii_temp_filename(filename, fallback="FILE"):
    cleaned = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_"
        for ch in str(filename or "")
    ).strip("._")
    return cleaned or fallback


def _mtools_host_source_path(host_path, image_path):
    host_path = os.fsdecode(host_path)
    if host_path.isascii():
        return host_path, ""
    temp_dir = tempfile.mkdtemp(prefix="aps_mtools_host_")
    alias_name = _clean_ascii_temp_filename(
        os.path.basename(_normalize_image_path(image_path)) or os.path.basename(host_path),
        fallback="FILE",
    )
    alias_path = os.path.join(temp_dir, alias_name)
    shutil.copy2(host_path, alias_path)
    return alias_path, temp_dir


def _mtools_host_destination_path(dest_path, image_path):
    dest_path = os.fsdecode(dest_path)
    if dest_path.isascii():
        return dest_path, ""
    temp_dir = tempfile.mkdtemp(prefix="aps_mtools_dest_")
    alias_name = _clean_ascii_temp_filename(
        os.path.basename(_normalize_image_path(image_path)) or os.path.basename(dest_path),
        fallback="FILE",
    )
    return os.path.join(temp_dir, alias_name), temp_dir


def _run_mcopy_host_to_image(command_runner, target_img, host_path, image_path, error_message, cancel_callback=None):
    mcopy = _require_command("mcopy")
    mcopy_host_path, cleanup_dir = _mtools_host_source_path(host_path, image_path)
    try:
        command_runner(
            [mcopy, "-i", target_img, mcopy_host_path, mtools_path(image_path)],
            error_message,
            cancel_callback=cancel_callback,
        )
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


def _copy_host_file_into_image(target_img, host_path, image_path, cancel_callback=None):
    if not os.path.isfile(host_path):
        raise FloppyImageError(f"File to add no longer exists: {host_path}")
    if is_eseq_file(host_path) and not is_dos83_filename(os.path.basename(image_path)):
        raise FloppyImageError(
            f"E-SEQ file '{os.path.basename(image_path)}' must use a DOS 8.3 filename."
        )
    _run_mcopy_host_to_image(
        _run_command,
        target_img,
        host_path,
        image_path,
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
                temp_dir,
                f".aps_image_{uuid.uuid4().hex}.{output_ext.lower().lstrip('.')}",
            )
            try:
                report = _write_image_direct(raw_img, temp_output, output_ext, disk_format)
                _finish_temp_output(temp_output, final_path)
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


def _gw_expected_track_total(disk_format):
    layout = DISK_FORMAT_TRACK_LAYOUTS.get(_disk_format_key(disk_format))
    if not layout:
        return 0
    return int(layout.get("cylinders") or 0) * int(layout.get("heads") or 0)


_GW_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_GW_YAMAHA_COPY_PROTECTION_STATUS = "p"


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


def _sector_rows_from_track_counts(track_counts, disk_format=None):
    if not track_counts:
        return []
    protected_layout = _protected_layout_for_disk_format(disk_format)
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
        missing_count = max(0, total - found)
        yamaha_protected_first_sector = bool(
            protected_layout
            and cylinder == 0
            and head == 0
            and missing_count == 1
        )
        for sector in range(total):
            grid.setdefault((head, sector), [" "] * (max_cylinder + 1))
            if yamaha_protected_first_sector:
                grid[(head, sector)][cylinder] = "x" if sector == 0 else "."
            else:
                grid[(head, sector)][cylinder] = "." if sector < found else "x"

    rows = []
    for head in heads:
        for sector in range(max_sector):
            statuses = "".join(grid.get((head, sector), [" "] * (max_cylinder + 1)))
            if statuses.strip():
                rows.append({"head": head, "sector": sector, "statuses": statuses})
    return rows


def _annotate_yamaha_copy_protection_sector(rows, disk_format=None):
    if not rows or not _protected_layout_for_disk_format(disk_format):
        return

    first_head_sectors = sorted(
        int(row.get("sector", 0))
        for row in rows
        if int(row.get("head", 0)) == 0
    )
    if not first_head_sectors:
        return
    if 0 in first_head_sectors:
        first_sector = 0
    elif 1 in first_head_sectors:
        first_sector = 1
    else:
        return

    for row in rows:
        if int(row.get("head", 0)) != 0 or int(row.get("sector", 0)) != first_sector:
            continue
        statuses = list(str(row.get("statuses", "")))
        if not statuses:
            return
        status = statuses[0]
        if status == "." or not str(status).strip():
            return
        statuses[0] = _GW_YAMAHA_COPY_PROTECTION_STATUS
        row["statuses"] = "".join(statuses)
        return


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
        rows = _sector_rows_from_track_counts(track_counts, disk_format)
        found = sum(item["found"] for item in track_counts)
        total = sum(item["total"] for item in track_counts)

    if not rows and found is None and total is None:
        return {}

    _annotate_yamaha_copy_protection_sector(rows, disk_format)
    good = 0
    bad = 0
    protected = 0
    for row in rows:
        for char in row["statuses"]:
            if char == ".":
                good += 1
            elif char == _GW_YAMAHA_COPY_PROTECTION_STATUS:
                protected += 1
            elif str(char).strip():
                bad += 1
    if found is not None and total is not None and found < total:
        bad = max(bad, max(0, total - found - protected))

    return {
        "rows": rows,
        "found": found,
        "total": total,
        "good": good,
        "bad": bad,
        "expected_yamaha_protection": protected,
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
    _normalize_nalbantov_hfe_header(output_path, disk_format)
    return output


def _normalize_nalbantov_hfe_header(output_path, disk_format):
    if image_extension(output_path) != "hfe":
        return False
    interface_mode = HFE_IBMPC_INTERFACE_BY_FORMAT.get(_disk_format_key(disk_format))
    if interface_mode is None:
        return False
    try:
        with open(output_path, "r+b") as handle:
            header = bytearray(handle.read(32))
            if len(header) <= HFE_FLOPPY_INTERFACE_OFFSET or header[:8] != HFE_SIGNATURE:
                return False
            changed = False
            if header[HFE_TRACK_ENCODING_OFFSET] != HFE_ENCODING_ISOIBM_MFM:
                header[HFE_TRACK_ENCODING_OFFSET] = HFE_ENCODING_ISOIBM_MFM
                changed = True
            if header[HFE_FLOPPY_INTERFACE_OFFSET] != interface_mode:
                header[HFE_FLOPPY_INTERFACE_OFFSET] = interface_mode
                changed = True
            if not changed:
                return False
            handle.seek(0)
            handle.write(header)
            return True
    except OSError as exc:
        raise FloppyImageError(f"Could not update HFE compatibility header: {exc}") from exc


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


def _notify_gw_progress(progress_callback, state, step, total, message):
    progress_key = (int(step or 0), int(total or 0), str(message or ""))
    if state.get("last_progress") == progress_key:
        return
    state["last_progress"] = progress_key
    _notify_progress(progress_callback, progress_key[0], progress_key[1], progress_key[2])


def _gw_first_track_protection_status(clean_line, disk_format):
    if not _protected_layout_for_disk_format(disk_format):
        return ""
    expected_sectors = _gw_expected_sectors_per_track(disk_format)
    track_count = _track_status_line_to_counts(clean_line, expected_sectors)
    if not track_count:
        return ""
    if track_count["cylinder"] != 0 or track_count["head"] != 0:
        return ""
    if max(0, track_count["total"] - track_count["found"]) != 1:
        return ""
    return "Yamaha copy protection?"


def _gw_first_track_retrying(clean_line, disk_format, status_text):
    if not _protected_layout_for_disk_format(disk_format):
        return False
    expected_sectors = _gw_expected_sectors_per_track(disk_format)
    track_count = _track_status_line_to_counts(clean_line, expected_sectors)
    if (
        track_count
        and track_count["cylinder"] == 0
        and track_count["head"] == 0
        and track_count["found"] < track_count["total"]
    ):
        return True
    return bool(
        re.search(
            r"\b(?:retry|fail|failed|error|bad|missing|lost|no\s+flux)\b",
            status_text or "",
            flags=re.IGNORECASE,
        )
    )


def _gw_first_track_protection_message(action, completed_tracks, total_tracks, confirmed=False):
    status = "Yamaha copy protection?" if confirmed else "checking possible Yamaha copy protection"
    if total_tracks > 0:
        return f"{action} T0.0 ({completed_tracks}/{total_tracks})... {status}"
    return f"{action} T0.0... {status}"


def _gw_intermediate_progress_message(action, completed_tracks, total_tracks, disk_format):
    if (
        action == "Reading"
        and completed_tracks == 0
        and _protected_layout_for_disk_format(disk_format)
    ):
        return "Reading first track; checking possible Yamaha copy protection..."
    return f"{action} floppy via Greaseweazle ({completed_tracks}/{total_tracks} tracks)..."


def _handle_gw_track_progress_line(progress_callback, state, line, *, action="Reading"):
    clean_line = _clean_gw_output_line(line)
    if not clean_line:
        return
    if clean_line.startswith("*** "):
        return

    disk_format = state.get("disk_format")

    header_match = re.match(r"^(?:Reading|Writing)\s+(?P<trackspec>.+?)(?:\s+revs=\d+)?$", clean_line)
    if header_match:
        parsed_total_tracks = _extract_gw_track_total(header_match.group("trackspec"))
        total_tracks = parsed_total_tracks or int(state.get("total_tracks") or 0)
        state["total_tracks"] = total_tracks
        if parsed_total_tracks > 0:
            state["seen_tracks"] = set()
        if total_tracks > 0:
            _notify_gw_progress(
                progress_callback,
                state,
                0,
                total_tracks,
                f"{action} floppy via Greaseweazle (0/{total_tracks} tracks)...",
            )
        else:
            _notify_gw_progress(progress_callback, state, 0, 1, clean_line)
        return

    if clean_line.startswith("Format "):
        return

    track_match = re.match(r"^T(?P<cyl>\d+)\.(?P<head>\d+)(?:\s+<-.*)?\s*:\s*(?P<status>.*)$", clean_line)
    if track_match:
        track_key = (int(track_match.group("cyl")), int(track_match.group("head")))
        first_track_candidate = bool(
            action == "Reading"
            and track_key == (0, 0)
            and _protected_layout_for_disk_format(disk_format)
        )
        status_text = track_match.group("status")
        protection_status = _gw_first_track_protection_status(clean_line, disk_format)
        first_track_protection = bool(
            first_track_candidate
            and (
                protection_status
                or state.get("first_track_protection_active")
                or _gw_first_track_retrying(clean_line, disk_format, status_text)
            )
        )
        if protection_status:
            state["first_track_protection_confirmed"] = True
        if first_track_protection:
            state["first_track_protection_active"] = True
        else:
            state["first_track_protection_active"] = False
        seen_tracks = state.setdefault("seen_tracks", set())
        seen_tracks.add(track_key)
        completed_tracks = len(seen_tracks)
        total_tracks = state.get("total_tracks", 0)
        track_label = f"T{track_match.group('cyl')}.{track_match.group('head')}"
        status = protection_status
        if first_track_protection:
            status = "Yamaha copy protection?"
        elif not status:
            status = _gw_short_status(status_text)
        if total_tracks > 0:
            if first_track_protection:
                message = _gw_first_track_protection_message(
                    action,
                    completed_tracks,
                    total_tracks,
                    confirmed=bool(state.get("first_track_protection_confirmed")),
                )
            else:
                message = f"{action} {track_label} ({completed_tracks}/{total_tracks})..."
                if status:
                    message = f"{message} {status}"
            # Greaseweazle can print several retry/detail lines after a track
            # result. Keep the result visible while handling those lines instead
            # of briefly replacing it with a shorter generic message, which
            # makes an auto-sizing progress dialog visibly jump.
            state["steady_track_message"] = message
            _notify_gw_progress(progress_callback, state, completed_tracks, total_tracks, message)
        else:
            _notify_gw_progress(progress_callback, state, 0, 1, clean_line)
        return

    total_tracks = state.get("total_tracks", 0)
    completed_tracks = len(state.get("seen_tracks", set()))
    if total_tracks > 0:
        if state.get("first_track_protection_active"):
            message = _gw_first_track_protection_message(
                action,
                completed_tracks,
                total_tracks,
                confirmed=bool(state.get("first_track_protection_confirmed")),
            )
        else:
            message = state.get("steady_track_message") or _gw_intermediate_progress_message(
                action,
                completed_tracks,
                total_tracks,
                disk_format,
            )
        _notify_gw_progress(progress_callback, state, completed_tracks, total_tracks, message)
    else:
        _notify_gw_progress(progress_callback, state, 0, 1, clean_line)


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

    progress_state = {
        "total_tracks": _gw_expected_track_total(source.disk_format),
        "seen_tracks": set(),
        "command_failed": "",
        "disk_format": source.disk_format,
    }

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
    _normalize_nalbantov_hfe_header(output_path, source.disk_format)
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

    progress_state = {
        "total_tracks": _gw_expected_track_total(source.disk_format),
        "seen_tracks": set(),
        "disk_format": source.disk_format,
    }

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
    FILE_FLAG_OVERLAPPED = 0x40000000
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
            self._creation_flags(),
            None,
        )
        if self.handle == self._ctypes.c_void_p(-1).value:
            raise FloppyImageError(_windows_last_error_message(f"Could not open floppy device {self.path}"))

    def _creation_flags(self):
        return self.FILE_ATTRIBUTE_NORMAL

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
                    error_code = self._ctypes.get_last_error() if not ok else 0
                    windows_error = (
                        self._ctypes.FormatError(error_code).strip()
                        if error_code
                        else ""
                    )
                    raise FloppyImageError(
                        _windows_write_failure_message(
                            self.path,
                            image_size=total_size,
                            written_before=written_total,
                            requested_size=len(chunk),
                            written_size=bytes_written.value,
                            windows_error=windows_error,
                        )
                    )
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


class _WindowsRecoveryVolumeHandle(_WindowsVolumeHandle):
    """Windows raw-volume handle whose recovery reads can be cancelled."""

    ERROR_HANDLE_EOF = 38
    ERROR_OPERATION_ABORTED = 995
    ERROR_IO_INCOMPLETE = 996
    ERROR_IO_PENDING = 997
    ERROR_NOT_FOUND = 1168
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF
    RECOVERY_WAIT_SLICE_MS = 100
    CANCEL_DRAIN_GRACE_SECONDS = 2.0
    _overlapped_type = None
    _retained_pending_reads = []
    _retained_pending_reads_lock = threading.Lock()

    def _creation_flags(self):
        return self.FILE_ATTRIBUTE_NORMAL | self.FILE_FLAG_OVERLAPPED

    def _configure_api(self):
        super()._configure_api()
        ctypes = self._ctypes
        wintypes = self._wintypes

        if type(self)._overlapped_type is None:
            class _OverlappedOffset(ctypes.Structure):
                _fields_ = [
                    ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD),
                ]

            class _OverlappedPosition(ctypes.Union):
                _anonymous_ = ("parts",)
                _fields_ = [
                    ("parts", _OverlappedOffset),
                    ("Pointer", wintypes.LPVOID),
                ]

            class _Overlapped(ctypes.Structure):
                _anonymous_ = ("position",)
                _fields_ = [
                    ("Internal", ctypes.c_size_t),
                    ("InternalHigh", ctypes.c_size_t),
                    ("position", _OverlappedPosition),
                    ("hEvent", wintypes.HANDLE),
                ]

            type(self)._overlapped_type = _Overlapped

        overlapped_pointer = ctypes.POINTER(type(self)._overlapped_type)
        self._kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            overlapped_pointer,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        self._kernel32.GetOverlappedResult.restype = wintypes.BOOL
        self._kernel32.CancelIoEx.argtypes = [
            wintypes.HANDLE,
            overlapped_pointer,
        ]
        self._kernel32.CancelIoEx.restype = wintypes.BOOL

    def _error_message(self, prefix, error_code):
        detail = self._ctypes.FormatError(int(error_code or 0)).strip()
        return f"{prefix}: {detail}" if detail else f"{prefix}."

    def _wait_slice_ms(self, deadline_at):
        if deadline_at is None:
            return self.RECOVERY_WAIT_SLICE_MS
        remaining = float(deadline_at) - time.monotonic()
        if remaining <= 0:
            return 0
        return max(
            1,
            min(
                self.RECOVERY_WAIT_SLICE_MS,
                int(math.ceil(remaining * 1000.0)),
            ),
        )

    def _overlapped_result(self, overlapped):
        bytes_read = self._wintypes.DWORD()
        if self._kernel32.GetOverlappedResult(
            self.handle,
            self._ctypes.byref(overlapped),
            self._ctypes.byref(bytes_read),
            False,
        ):
            return True, True, int(bytes_read.value), 0
        error_code = int(self._ctypes.get_last_error() or 0)
        if error_code == self.ERROR_IO_INCOMPLETE:
            return False, False, 0, error_code
        return True, False, 0, error_code

    def _retain_pending_read(self, buffer, overlapped, event):
        # A pathological driver may ignore cancellation. Keep every object that
        # Windows could still touch alive instead of risking a use-after-free.
        retained = (buffer, overlapped, event)
        retained_type = type(self)
        with retained_type._retained_pending_reads_lock:
            retained_type._retained_pending_reads.append(retained)
        self.incomplete_cancel_drain = True

        kernel32 = self._kernel32

        def reap_when_signalled():
            while True:
                wait_result = int(
                    kernel32.WaitForSingleObject(
                        event,
                        self.RECOVERY_WAIT_SLICE_MS,
                    )
                )
                if wait_result == self.WAIT_TIMEOUT:
                    continue
                if wait_result != self.WAIT_OBJECT_0:
                    # Keep the objects rooted if Windows cannot confirm that it
                    # has stopped touching them.
                    return
                with retained_type._retained_pending_reads_lock:
                    retained_type._retained_pending_reads[:] = [
                        item
                        for item in retained_type._retained_pending_reads
                        if item is not retained
                    ]
                kernel32.CloseHandle(event)
                return

        try:
            threading.Thread(
                target=reap_when_signalled,
                name="aps-floppy-read-reaper",
                daemon=True,
            ).start()
        except Exception:
            # The tuple and event intentionally remain retained. Losing a small
            # amount of memory is safer than releasing objects a driver may
            # still be writing into.
            pass

    def _drain_cancelled_read(self, overlapped):
        # CancelIoEx requests cancellation; the OVERLAPPED, event, and buffer
        # must remain alive until Windows signals final completion.
        result = self._overlapped_result(overlapped)
        if result[0]:
            return result
        if not self._kernel32.CancelIoEx(
            self.handle,
            self._ctypes.byref(overlapped),
        ):
            error_code = self._ctypes.get_last_error()
            if error_code != self.ERROR_NOT_FOUND:
                # Still wait: the operation owns the Python buffers until its
                # completion is observed, even when cancellation reports an error.
                pass
        drain_deadline = time.monotonic() + self.CANCEL_DRAIN_GRACE_SECONDS
        while True:
            result = self._overlapped_result(overlapped)
            if result[0]:
                return result
            remaining = drain_deadline - time.monotonic()
            if remaining <= 0:
                return result
            wait_result = int(
                self._kernel32.WaitForSingleObject(
                    overlapped.hEvent,
                    max(
                        1,
                        min(
                            self.RECOVERY_WAIT_SLICE_MS,
                            int(math.ceil(remaining * 1000.0)),
                        ),
                    ),
                )
            )
            if wait_result in {self.WAIT_OBJECT_0, self.WAIT_TIMEOUT}:
                continue
            return self._overlapped_result(overlapped)

    def read_at_recovery(
        self,
        offset,
        size,
        label,
        *,
        cancel_callback=None,
        deadline_at=None,
        submitted_callback=None,
    ):
        _raise_if_cancelled(cancel_callback)
        if deadline_at is not None and time.monotonic() >= float(deadline_at):
            raise _RecoveryReadDeadlineExceeded(
                "The physical-floppy recovery read reached its overall time limit."
            )

        offset = int(offset)
        size = int(size)
        if offset < 0 or size < 0:
            raise FloppyImageError(f"Invalid recovery read range for {label}.")
        if size == 0:
            return b""

        event = self._kernel32.CreateEventW(None, True, False, None)
        if not event:
            error_code = self._ctypes.get_last_error()
            raise FloppyImageError(
                self._error_message("Could not create a floppy read event", error_code)
            )

        buffer = self._ctypes.create_string_buffer(size)
        overlapped = type(self)._overlapped_type()
        overlapped.Offset = offset & 0xFFFFFFFF
        overlapped.OffsetHigh = (offset >> 32) & 0xFFFFFFFF
        overlapped.hEvent = event
        operation_started = False
        completion_observed = False
        drain_attempted = False
        retain_event = False
        try:
            if submitted_callback is not None:
                submitted_callback()
            ok = self._kernel32.ReadFile(
                self.handle,
                buffer,
                size,
                None,
                self._ctypes.byref(overlapped),
            )
            if ok:
                operation_started = True
            else:
                error_code = self._ctypes.get_last_error()
                if error_code == self.ERROR_HANDLE_EOF:
                    return b""
                if error_code != self.ERROR_IO_PENDING:
                    raise FloppyImageError(
                        self._error_message(f"Could not read {label}", error_code)
                    )
                operation_started = True
                while True:
                    _raise_if_cancelled(cancel_callback)
                    wait_slice_ms = self._wait_slice_ms(deadline_at)
                    wait_result = int(
                        self._kernel32.WaitForSingleObject(
                            event,
                            wait_slice_ms,
                        )
                    )
                    if wait_result == self.WAIT_OBJECT_0:
                        break
                    if wait_result == self.WAIT_TIMEOUT:
                        if (
                            deadline_at is not None
                            and time.monotonic() >= float(deadline_at)
                        ):
                            # CancelIoEx races with normal completion. Preserve
                            # valid bytes if the operation won that race at the
                            # deadline instead of reporting the range unresolved.
                            drain_attempted = True
                            (
                                terminal,
                                succeeded,
                                completed_bytes,
                                completion_error,
                            ) = self._drain_cancelled_read(overlapped)
                            completion_observed = terminal
                            if succeeded:
                                return buffer.raw[:completed_bytes]
                            if (
                                terminal
                                and completion_error == self.ERROR_HANDLE_EOF
                            ):
                                return b""
                            raise _RecoveryReadDeadlineExceeded(
                                "The physical-floppy recovery read reached its overall time limit."
                            )
                        continue
                    if wait_result == self.WAIT_FAILED:
                        error_code = self._ctypes.get_last_error()
                        raise FloppyImageError(
                            self._error_message(
                                f"Could not wait while reading {label}",
                                error_code,
                            )
                        )
                    raise FloppyImageError(
                        f"Could not wait while reading {label}: unexpected wait result {wait_result}."
                    )

            (
                completion_observed,
                succeeded,
                completed_bytes,
                error_code,
            ) = self._overlapped_result(overlapped)
            if not succeeded:
                if completion_observed and error_code == self.ERROR_HANDLE_EOF:
                    return b""
                raise FloppyImageError(
                    self._error_message(f"Could not finish reading {label}", error_code)
                )
            return buffer.raw[:completed_bytes]
        finally:
            try:
                if operation_started and not completion_observed:
                    if not drain_attempted:
                        completion_observed = self._drain_cancelled_read(overlapped)[0]
                    if not completion_observed:
                        retain_event = True
                        self._retain_pending_read(buffer, overlapped, event)
            finally:
                if not retain_event:
                    self._kernel32.CloseHandle(event)

    def read_at(self, offset, size, label):
        return self.read_at_recovery(offset, size, label)


def _open_block_device_for_read(device_path):
    if os.name == "nt":
        return _WindowsRecoveryVolumeHandle(device_path, write=False)
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


def _open_block_device_for_recovery_read(device_path):
    if os.name == "nt":
        return _WindowsRecoveryVolumeHandle(device_path, write=False)
    return _open_block_device_for_read(device_path)


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
        with _WindowsRecoveryVolumeHandle(device_path, write=False) as volume:
            while remaining > 0:
                _raise_if_cancelled(cancel_callback)
                current_size = min(chunk_size, remaining)
                chunk = _read_device_exact(
                    volume, cursor, current_size, "floppy image", cancel_callback=cancel_callback,
                )
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
        prefix=f".aps_capture_{uuid.uuid4().hex}.",
        suffix=temp_suffix or ".tmp",
    )
    os.close(fd)
    os.remove(temp_path)
    return temp_path


def _finish_capture_output(temp_path, output_path):
    output_path = os.path.abspath(output_path)
    return _finish_temp_output(temp_path, output_path)


def capture_floppy_drive_image(
    drive_info,
    output_path,
    disk_format=None,
    progress_callback=None,
    cancel_callback=None,
):
    """Copy a physical floppy to an image without opening/scanning it."""
    if not isinstance(drive_info, FloppyDriveInfo):
        raise FloppyImageError("Invalid floppy drive selection.")

    output_ext = image_extension(output_path) or "img"
    if output_ext not in DIRECT_FLOPPY_IMAGE_EXTENSIONS:
        raise FloppyImageError(_unsupported_image_type_message(output_ext, for_output=True))

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
    raw_output = output_ext in RAW_IMAGE_EXTENSIONS
    raw_temp_path = _capture_temp_output_path(
        output_path,
        suffix=None if raw_output else ".img",
    )
    converted_temp_path = ""
    try:
        _notify_progress(
            progress_callback,
            0,
            100,
            f"Imaging floppy: 0 B of {display_bytes(read_size)}...",
        )
        _read_block_device(
            drive_info.path,
            raw_temp_path,
            read_size,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        _raise_if_cancelled(cancel_callback)

        if raw_output:
            _notify_progress(progress_callback, 95, 100, "Saving floppy image...")
            final_path = _finish_capture_output(raw_temp_path, output_path)
            raw_temp_path = ""
        else:
            if not isinstance(disk_format, DiskFormat):
                raise FloppyImageError(
                    "Could not choose a disk format for image conversion. "
                    "Select the disk size before imaging the disk."
                )
            converted_temp_path = _capture_temp_output_path(
                output_path,
                suffix=f".{output_ext}",
            )
            _notify_progress(
                progress_callback,
                88,
                100,
                f"Converting floppy image to {output_ext.upper()}...",
            )
            _write_image_direct(
                raw_temp_path,
                converted_temp_path,
                output_ext,
                disk_format,
            )
            _raise_if_cancelled(cancel_callback)
            _notify_progress(
                progress_callback,
                95,
                100,
                "Saving converted floppy image...",
            )
            final_path = _finish_capture_output(converted_temp_path, output_path)
            converted_temp_path = ""

        _notify_progress(progress_callback, 100, 100, "Floppy image saved.")
        return final_path
    finally:
        for temp_path in (converted_temp_path, raw_temp_path):
            if not temp_path:
                continue
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


def _read_device_chunk_for_recovery(
    device,
    offset,
    size,
    *,
    cancel_callback=None,
    deadline_at=None,
    submitted_callback=None,
):
    bounded_reader = getattr(device, "read_at_recovery", None)
    if callable(bounded_reader):
        return bounded_reader(
            offset,
            size,
            "floppy recovery image",
            cancel_callback=cancel_callback,
            deadline_at=deadline_at,
            submitted_callback=submitted_callback,
        )
    if submitted_callback is not None:
        submitted_callback()
    if hasattr(device, "read_at"):
        return device.read_at(offset, size, "floppy recovery image")
    return os.pread(device, size, offset)


def _record_recovery_read_error(diagnostics, exc):
    errors = diagnostics.setdefault("read_errors", {})
    message = " ".join(str(exc or "read failed").split()) or "read failed"
    if len(message) > 200:
        message = message[:197].rstrip() + "..."
    if message not in errors and len(errors) >= 16:
        message = "additional distinct read errors"
    errors[message] = int(errors.get(message) or 0) + 1


def _update_usb_recovery_counts(
    diagnostics,
    sector_states,
    sector_size,
    total_size=None,
    partial_sector_bytes=None,
):
    good = sum(1 for state in sector_states if state == 1)
    recovered = sum(1 for state in sector_states if state == 2)
    bad = sum(1 for state in sector_states if state == 3)
    unresolved = sum(1 for state in sector_states if state == 4)
    expected = len(sector_states)
    attempted = good + recovered + bad + unresolved
    total_size = (
        expected * int(sector_size)
        if total_size is None
        else max(0, int(total_size))
    )
    complete_bytes = sum(
        max(0, min(int(sector_size), total_size - index * int(sector_size)))
        for index, state in enumerate(sector_states)
        if state in {1, 2}
    )
    if not isinstance(partial_sector_bytes, list):
        partial_sector_bytes = []
    partial_bytes = sum(
        max(
            0,
            min(
                int(value or 0),
                int(sector_size),
                total_size - index * int(sector_size),
            ),
        )
        for index, value in enumerate(partial_sector_bytes[:expected])
        if sector_states[index] not in {1, 2}
    )
    partially_readable = sum(
        1
        for index, value in enumerate(partial_sector_bytes[:expected])
        if int(value or 0) > 0 and sector_states[index] not in {1, 2}
    )
    diagnostics.update(
        {
            "expected_sectors": expected,
            "attempted_sectors": attempted,
            "good_sectors": good,
            "recovered_after_fallback_sectors": recovered,
            "readable_sectors": good + recovered,
            "bad_sectors": bad,
            "unresolved_sectors": unresolved,
            "unattempted_sectors": max(0, expected - attempted),
            "bytes_recovered": complete_bytes + partial_bytes,
            "partial_bytes_recovered": partial_bytes,
            "partially_readable_sectors": partially_readable,
        }
    )


def _sector_state_ranges(sector_states, target_state, *, max_ranges=256):
    ranges = []
    total_ranges = 0
    start = None
    previous = None
    for sector_index, state in enumerate(sector_states):
        if state == target_state:
            if start is None:
                start = previous = sector_index
            elif sector_index == previous + 1:
                previous = sector_index
            else:
                total_ranges += 1
                if len(ranges) < max_ranges:
                    ranges.append([start, previous])
                start = previous = sector_index
        elif start is not None:
            total_ranges += 1
            if len(ranges) < max_ranges:
                ranges.append([start, previous])
            start = previous = None
    if start is not None:
        total_ranges += 1
        if len(ranges) < max_ranges:
            ranges.append([start, previous])
    return ranges, max(0, total_ranges - len(ranges))


def _update_usb_recovery_sector_ranges(diagnostics, sector_states):
    truncated = {}
    for key, state in (
        ("unattempted_sector_ranges", 0),
        ("fallback_recovered_sector_ranges", 2),
        ("bad_sector_ranges", 3),
        ("unresolved_sector_ranges", 4),
    ):
        ranges, omitted = _sector_state_ranges(sector_states, state)
        diagnostics[key] = ranges
        if omitted:
            truncated[key] = omitted
    diagnostics["sector_ranges_truncated"] = truncated


def _set_recovery_boot_geometry(diagnostics, sector0):
    sector0 = bytes(sector0 or b"")
    geometry = _geometry_from_boot_sector(sector0)
    if geometry is None:
        return None
    claimed_bytes = int(geometry.total_size)
    claimed_format = DISK_FORMAT_BY_SIZE.get(claimed_bytes)
    diagnostics.update(
        {
            "boot_claimed_format_key": str(getattr(claimed_format, "key", "") or ""),
            "boot_claimed_format_label": str(
                getattr(claimed_format, "label", "")
                or f"FAT12 ({geometry.total_sectors} sectors)"
            ),
            "boot_claimed_bytes": claimed_bytes,
        }
    )
    if not _recovery_boot_geometry_is_known_layout(sector0, geometry):
        return geometry

    diagnostics.update(
        {
            "detected_format_key": diagnostics["boot_claimed_format_key"],
            "detected_format_label": diagnostics["boot_claimed_format_label"],
            "detected_bytes": claimed_bytes,
            "detection_basis": "validated_boot_sector",
        }
    )
    selected_bytes = int(diagnostics.get("selected_bytes") or 0)
    diagnostics["geometry_mismatch"] = bool(
        selected_bytes > 0 and claimed_bytes > 0 and selected_bytes != claimed_bytes
    )
    return geometry


def _recovery_boot_geometry_is_known_layout(sector0, geometry):
    if geometry is None:
        return False
    layout = _protected_layout_hint_from_boot_sector(bytes(sector0 or b""))
    return layout is not None and _layout_total_size(layout) == geometry.total_size


def _usb_recovery_read_note(diagnostics):
    details = dict(diagnostics or {})
    readable = int(details.get("readable_sectors") or 0)
    expected = int(details.get("expected_sectors") or 0)
    bad = int(details.get("bad_sectors") or 0)
    unresolved = int(details.get("unresolved_sectors") or 0)
    unattempted = int(details.get("unattempted_sectors") or 0)
    note = (
        f"Physical recovery read {readable} of {expected} selected sector(s) "
        f"({_diagnostic_percent(readable, expected)}), including "
        f"{int(details.get('recovered_after_fallback_sectors') or 0)} recovered after sector fallback."
    )
    if bad:
        note += f" {bad} sector(s) remained unreadable and were filled with zeros."
    if unresolved:
        note += (
            f" {unresolved} attempted sector(s) could not be resolved before recovery stopped; "
            "any missing bytes remain zero-filled."
        )
    if unattempted:
        note += f" {unattempted} sector(s) were not attempted because recovery stopped early."
    stop_reason = str(details.get("stop_reason") or "")
    if stop_reason == "soft_deadline":
        if details.get("read_deadline_mode") == "windows_overlapped_cancel":
            note += (
                " Recovery reached its five-minute read deadline and requested cancellation "
                "of the pending Windows device read."
            )
        else:
            note += (
                " The five-minute recovery deadline is soft because each operating-system "
                "read is synchronous."
            )
    return note


def _read_block_device_recovery_image(
    device_path,
    output_path,
    size_bytes,
    progress_callback=None,
    cancel_callback=None,
    *,
    diagnostics=None,
    soft_deadline_seconds=None,
    chunk_size=None,
    sector_size=None,
    all_bad_sample_sectors=None,
    mostly_bad_sample_sectors=None,
    mostly_bad_ratio=None,
    consecutive_bad_sectors=None,
    bad_media_minimum_coverage=None,
):
    """Best-effort raw USB read with bounded fallback and structured diagnostics.

    POSIX pread calls are synchronous, so one call can overrun the time limit.
    Windows recovery reads use cancellable overlapped I/O, but device open,
    read submission, or an unresponsive driver can still overrun it. The
    deadline and cancellation state are also checked between every call.
    """

    requested_size = int(size_bytes or 0)
    if requested_size <= 0:
        requested_size = _YAMAHA_TOTAL_SIZE
    sector_size = max(
        1,
        int(
            USB_FLOPPY_RECOVERY_SECTOR_SIZE
            if sector_size is None
            else sector_size
        ),
    )
    chunk_size = max(
        sector_size,
        int(USB_FLOPPY_RECOVERY_CHUNK_SIZE if chunk_size is None else chunk_size),
    )
    chunk_size = max(sector_size, (chunk_size // sector_size) * sector_size)
    soft_deadline_seconds = max(
        0.0,
        float(
            USB_FLOPPY_RECOVERY_SOFT_DEADLINE_SECONDS
            if soft_deadline_seconds is None
            else soft_deadline_seconds
        ),
    )
    all_bad_sample_sectors = max(
        1,
        int(
            USB_FLOPPY_RECOVERY_ALL_BAD_SAMPLE_SECTORS
            if all_bad_sample_sectors is None
            else all_bad_sample_sectors
        ),
    )
    mostly_bad_sample_sectors = max(
        1,
        int(
            USB_FLOPPY_RECOVERY_MOSTLY_BAD_SAMPLE_SECTORS
            if mostly_bad_sample_sectors is None
            else mostly_bad_sample_sectors
        ),
    )
    mostly_bad_ratio = min(
        1.0,
        max(
            0.0,
            float(
                USB_FLOPPY_RECOVERY_MOSTLY_BAD_RATIO
                if mostly_bad_ratio is None
                else mostly_bad_ratio
            ),
        ),
    )
    consecutive_bad_sectors = max(
        1,
        int(
            USB_FLOPPY_RECOVERY_CONSECUTIVE_BAD_SECTORS
            if consecutive_bad_sectors is None
            else consecutive_bad_sectors
        ),
    )
    bad_media_minimum_coverage = min(
        1.0,
        max(
            0.0,
            float(
                USB_FLOPPY_RECOVERY_BAD_MEDIA_MINIMUM_COVERAGE
                if bad_media_minimum_coverage is None
                else bad_media_minimum_coverage
            ),
        ),
    )

    if diagnostics is None:
        diagnostics = _new_usb_floppy_recovery_diagnostics(
            None,
            DISK_FORMAT_BY_SIZE.get(requested_size),
            requested_size,
            sector_size=sector_size,
            soft_deadline_seconds=soft_deadline_seconds,
        )
        diagnostics["drive_path"] = str(device_path or "")
    else:
        diagnostics = dict(diagnostics)
        diagnostics["selected_bytes"] = requested_size
        diagnostics["sector_size"] = sector_size
        diagnostics["soft_deadline_seconds"] = soft_deadline_seconds
    diagnostics["bad_media_minimum_coverage"] = bad_media_minimum_coverage

    expected_sectors = int(math.ceil(requested_size / sector_size))
    sector_states = [0] * expected_sectors
    partial_sector_bytes = [0] * expected_sectors
    diagnostics["_sector_states"] = sector_states
    image = bytearray(requested_size)
    read_limit = requested_size
    detected_smaller_geometry = False
    started_at = time.monotonic()
    deadline_at = (
        started_at + soft_deadline_seconds
        if soft_deadline_seconds > 0
        else None
    )
    last_progress_state = None
    consecutive_bad = 0

    def deadline_reached():
        return bool(
            soft_deadline_seconds > 0
            and time.monotonic() - started_at >= soft_deadline_seconds
        )

    def stop(reason):
        diagnostics["stopped_early"] = True
        diagnostics["stop_reason"] = str(reason)

    def update_counts():
        _update_usb_recovery_counts(
            diagnostics,
            sector_states,
            sector_size,
            requested_size,
            partial_sector_bytes,
        )

    def mark_attempted_range(start_offset, end_offset, *, fallback=False):
        diagnostics["read_calls"] = int(diagnostics.get("read_calls") or 0) + 1
        if fallback:
            diagnostics["fallback_read_calls"] = int(
                diagnostics.get("fallback_read_calls") or 0
            ) + 1
            diagnostics["read_passes"] = max(
                2,
                int(diagnostics.get("read_passes") or 0),
            )
        else:
            diagnostics["read_passes"] = max(
                1,
                int(diagnostics.get("read_passes") or 0),
            )
        first_sector = max(0, int(start_offset) // sector_size)
        sector_end = min(
            expected_sectors,
            int(math.ceil(int(end_offset) / sector_size)),
        )
        for sector_index in range(first_sector, sector_end):
            if sector_states[sector_index] == 0:
                sector_states[sector_index] = 4

    def inspect_boot_sector():
        nonlocal read_limit, detected_smaller_geometry
        sector0 = bytes(image[:sector_size])
        if len(sector0) < sector_size:
            return
        diagnostics["boot_sector_readable"] = True
        diagnostics["boot_signature_present"] = (
            sector0[-2:] == _YAMAHA_BOOT_SIGNATURE
        )
        boot_geometry = _set_recovery_boot_geometry(diagnostics, sector0)
        if (
            boot_geometry is not None
            and _recovery_boot_geometry_is_known_layout(sector0, boot_geometry)
            and 0 < boot_geometry.total_size < read_limit
            and boot_geometry.total_size in DISK_FORMAT_BY_SIZE
        ):
            read_limit = int(boot_geometry.total_size)
            first_sector_beyond_geometry = int(
                math.ceil(read_limit / sector_size)
            )
            for sector_index in range(
                first_sector_beyond_geometry,
                expected_sectors,
            ):
                sector_states[sector_index] = 0
                partial_sector_bytes[sector_index] = 0
            detected_smaller_geometry = True
            diagnostics["stopped_early"] = True
            diagnostics["stop_reason"] = "detected_smaller_geometry"

    def detect_smaller_geometry_at_eof(eof_offset, attempted_start, attempted_end):
        nonlocal read_limit, detected_smaller_geometry
        eof_offset = int(eof_offset)
        detected_format = DISK_FORMAT_BY_SIZE.get(eof_offset)
        prefix_sectors = eof_offset // sector_size
        if (
            detected_format is None
            or eof_offset <= 0
            or eof_offset >= read_limit
            or eof_offset % sector_size
            or prefix_sectors <= 0
        ):
            return False
        prefix_states = sector_states[:prefix_sectors]
        resolved = sum(1 for state in prefix_states if state in {1, 2, 3})
        readable = sum(1 for state in prefix_states if state in {1, 2})
        if (
            resolved != prefix_sectors
            or readable / prefix_sectors < 0.90
        ):
            return False

        for sector_index in range(
            max(0, attempted_start // sector_size),
            min(expected_sectors, int(math.ceil(attempted_end / sector_size))),
        ):
            if sector_states[sector_index] == 4 and not partial_sector_bytes[sector_index]:
                sector_states[sector_index] = 0
        diagnostics.update(
            {
                "detected_format_key": str(detected_format.key),
                "detected_format_label": str(detected_format.label),
                "detected_bytes": eof_offset,
                "detection_basis": "device_eof_after_readable_prefix",
                "geometry_mismatch": bool(
                    requested_size > 0 and requested_size != eof_offset
                ),
                "stopped_early": True,
                "stop_reason": "detected_smaller_geometry",
            }
        )
        read_limit = eof_offset
        detected_smaller_geometry = True
        return True

    def notify_read_progress():
        nonlocal last_progress_state
        if progress_callback is None or expected_sectors <= 0:
            return
        attempted = int(diagnostics.get("attempted_sectors") or 0)
        readable = int(diagnostics.get("readable_sectors") or 0)
        bad = int(diagnostics.get("bad_sectors") or 0)
        unresolved = int(diagnostics.get("unresolved_sectors") or 0)
        progress_state = (attempted, readable, bad, unresolved)
        if progress_state == last_progress_state:
            return
        last_progress_state = progress_state
        progress_callback(
            min(70, int((attempted / expected_sectors) * 70)),
            100,
            "Copying floppy for recovery: "
            f"{attempted} of {expected_sectors} sector(s) attempted; "
            f"{readable} readable, {bad} bad, {unresolved} unresolved...",
        )

    def check_bad_media_cutoff():
        readable = int(diagnostics.get("readable_sectors") or 0)
        bad = int(diagnostics.get("bad_sectors") or 0)
        resolved = readable + bad
        if expected_sectors > 0 and resolved / expected_sectors < bad_media_minimum_coverage:
            return False
        if resolved >= all_bad_sample_sectors and readable <= 0:
            stop("all_sectors_bad")
            return True
        if (
            resolved >= mostly_bad_sample_sectors
            and resolved > 0
            and (bad / resolved) >= mostly_bad_ratio
        ):
            stop("mostly_bad_media")
            return True
        if consecutive_bad >= consecutive_bad_sectors:
            stop("consecutive_bad_sectors")
            return True
        return False

    def attach_cancelled_diagnostics(exc):
        stop("cancelled")
        diagnostics["duration_seconds"] = round(time.monotonic() - started_at, 3)
        update_counts()
        _update_usb_recovery_sector_ranges(diagnostics, sector_states)
        diagnostics.pop("_sector_states", None)
        diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)
        exc.diagnostics = dict(diagnostics)

    try:
        _raise_if_cancelled(cancel_callback)
        device = _open_block_device_for_recovery_read(device_path)
        diagnostics["read_deadline_mode"] = (
            "windows_overlapped_cancel"
            if callable(getattr(device, "read_at_recovery", None))
            else "synchronous_soft"
        )
    except FloppyOperationCancelled as exc:
        attach_cancelled_diagnostics(exc)
        raise
    except (OSError, FloppyImageError) as exc:
        diagnostics["stop_reason"] = "device_open_failed"
        diagnostics["stopped_early"] = True
        diagnostics["duration_seconds"] = round(time.monotonic() - started_at, 3)
        _record_recovery_read_error(diagnostics, exc)
        update_counts()
        _update_usb_recovery_sector_ranges(diagnostics, sector_states)
        diagnostics.pop("_sector_states", None)
        diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)
        raise FloppyRecoveryError(
            f"Could not open floppy device {device_path} for recovery: {exc}\n\n"
            f"{diagnostics['human_report']}",
            diagnostics=diagnostics,
        ) from exc

    cancelled_error = None
    try:
        offset = 0
        while offset < read_limit:
            _raise_if_cancelled(cancel_callback)
            if deadline_reached():
                stop("soft_deadline")
                break

            current_size = min(chunk_size, read_limit - offset)
            first_target_sector = offset // sector_size
            fallback_start = offset
            try:
                chunk = _read_device_chunk_for_recovery(
                    device,
                    offset,
                    current_size,
                    cancel_callback=cancel_callback,
                    deadline_at=deadline_at,
                    submitted_callback=lambda start=offset, end=offset + current_size: mark_attempted_range(
                        start,
                        end,
                    ),
                )
                chunk = bytes(chunk or b"")
                if not chunk and detect_smaller_geometry_at_eof(
                    offset,
                    offset,
                    offset + current_size,
                ):
                    break
                if len(chunk) != current_size:
                    if chunk:
                        image[offset:offset + len(chunk)] = chunk
                        complete_length = (len(chunk) // sector_size) * sector_size
                        complete_sector_end = min(
                            expected_sectors,
                            (offset + complete_length) // sector_size,
                        )
                        for sector_index in range(
                            first_target_sector,
                            complete_sector_end,
                        ):
                            sector_states[sector_index] = 1
                            partial_sector_bytes[sector_index] = 0
                        if complete_length > 0:
                            consecutive_bad = 0
                        partial_length = len(chunk) - complete_length
                        if partial_length:
                            partial_index = (offset + complete_length) // sector_size
                            if 0 <= partial_index < expected_sectors:
                                partial_sector_bytes[partial_index] = max(
                                    partial_sector_bytes[partial_index],
                                    partial_length,
                                )
                                sector_states[partial_index] = 4
                        fallback_start = offset + complete_length
                        if offset == 0 and complete_length >= sector_size:
                            inspect_boot_sector()
                        if (
                            len(chunk) % sector_size == 0
                            and detect_smaller_geometry_at_eof(
                                offset + len(chunk),
                                offset + len(chunk),
                                offset + current_size,
                            )
                        ):
                            break
                    raise FloppyImageError(
                        f"short read at byte {offset}: expected {current_size}, received {len(chunk)}"
                    )
                image[offset:offset + current_size] = chunk
                sector_count = int(math.ceil(current_size / sector_size))
                for sector_index in range(
                    first_target_sector,
                    min(expected_sectors, first_target_sector + sector_count),
                ):
                    sector_states[sector_index] = 1
                    partial_sector_bytes[sector_index] = 0
                consecutive_bad = 0

                if offset == 0 and len(chunk) >= sector_size:
                    inspect_boot_sector()
            except _RecoveryReadDeadlineExceeded:
                stop("soft_deadline")
                break
            except FloppyOperationCancelled:
                raise
            except (OSError, FloppyImageError) as chunk_exc:
                _record_recovery_read_error(diagnostics, chunk_exc)
                sector_end = min(offset + current_size, read_limit)
                sector_offset = fallback_start
                while sector_offset < sector_end:
                    _raise_if_cancelled(cancel_callback)
                    if deadline_reached():
                        stop("soft_deadline")
                        break
                    sector_read_size = min(sector_size, sector_end - sector_offset)
                    sector_index = sector_offset // sector_size
                    try:
                        sector = _read_device_chunk_for_recovery(
                            device,
                            sector_offset,
                            sector_read_size,
                            cancel_callback=cancel_callback,
                            deadline_at=deadline_at,
                            submitted_callback=lambda start=sector_offset, end=(
                                sector_offset + sector_read_size
                            ): mark_attempted_range(start, end, fallback=True),
                        )
                        sector = bytes(sector or b"")
                        if len(sector) != sector_read_size:
                            if sector:
                                image[
                                    sector_offset:sector_offset + len(sector)
                                ] = sector
                                if 0 <= sector_index < expected_sectors:
                                    partial_sector_bytes[sector_index] = max(
                                        partial_sector_bytes[sector_index],
                                        len(sector),
                                    )
                                    sector_states[sector_index] = 4
                            raise FloppyImageError(
                                f"short sector read at byte {sector_offset}: "
                                f"expected {sector_read_size}, received {len(sector)}"
                            )
                        image[
                            sector_offset:sector_offset + sector_read_size
                        ] = sector
                        if 0 <= sector_index < expected_sectors:
                            sector_states[sector_index] = 2
                            partial_sector_bytes[sector_index] = 0
                        consecutive_bad = 0
                        if sector_index == 0:
                            inspect_boot_sector()
                            sector_end = min(sector_end, read_limit)
                    except _RecoveryReadDeadlineExceeded:
                        stop("soft_deadline")
                        break
                    except FloppyOperationCancelled:
                        raise
                    except (OSError, FloppyImageError) as sector_exc:
                        _record_recovery_read_error(diagnostics, sector_exc)
                        if 0 <= sector_index < expected_sectors:
                            sector_states[sector_index] = (
                                4 if partial_sector_bytes[sector_index] else 3
                            )
                        consecutive_bad += 1

                    update_counts()
                    notify_read_progress()
                    if check_bad_media_cutoff():
                        break
                    sector_offset += sector_read_size

                if diagnostics.get("stopped_early") and diagnostics.get("stop_reason") != "detected_smaller_geometry":
                    break

            update_counts()
            notify_read_progress()
            offset += current_size

        _raise_if_cancelled(cancel_callback)
    except FloppyOperationCancelled as exc:
        cancelled_error = exc
    finally:
        _close_block_device(device)
        diagnostics["windows_cancel_drain_incomplete"] = bool(
            getattr(device, "incomplete_cancel_drain", False)
        )
        diagnostics["duration_seconds"] = round(time.monotonic() - started_at, 3)

    update_counts()
    _update_usb_recovery_sector_ranges(diagnostics, sector_states)
    # This complete map remains useful even when the human-readable range list
    # is truncated. Never mistake zero-filled unread sectors for recovered data.
    diagnostics["sector_states"] = list(sector_states)
    diagnostics["sector_state_labels"] = {
        "0": "unattempted", "1": "readable", "2": "recovered_after_fallback",
        "3": "unreadable", "4": "unresolved",
    }
    diagnostics["partial_sector_bytes"] = list(partial_sector_bytes)
    if cancelled_error is not None:
        stop("cancelled")
    if not diagnostics.get("stop_reason"):
        diagnostics["stop_reason"] = "completed"
    if detected_smaller_geometry:
        output_image = bytes(image[:read_limit])
    else:
        output_image = bytes(image)
    diagnostics["image_bytes"] = len(output_image)
    diagnostics["sha256"] = hashlib.sha256(output_image).hexdigest()
    diagnostics["nonzero_sectors"] = sum(
        1
        for offset in range(0, len(output_image), sector_size)
        if any(output_image[offset:offset + sector_size])
    )
    if diagnostics.get("boot_sector_readable") is None and sector_states:
        diagnostics["boot_sector_readable"] = sector_states[0] in {1, 2}
    diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)

    try:
        with open(output_path, "wb") as output:
            output.write(output_image)
    except OSError as exc:
        _record_recovery_read_error(diagnostics, exc)
        diagnostics["stop_reason"] = "output_write_failed"
        diagnostics.pop("_sector_states", None)
        diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)
        raise FloppyRecoveryError(
            f"Could not save the temporary floppy recovery image: {exc}\n\n"
            f"{diagnostics['human_report']}",
            diagnostics=diagnostics,
        ) from exc

    if cancelled_error is not None:
        attach_cancelled_diagnostics(cancelled_error)
        raise cancelled_error
    return diagnostics


def _recovery_affected_files(image_path, diagnostics):
    """Only attribute damage when readable FAT and directories support a chain."""
    states = diagnostics.get("sector_states", [])
    sector_size = int(diagnostics.get("sector_size") or 512)

    def unread_in_range(offset, size):
        return [
            index for index in range(offset // sector_size, math.ceil((offset + size) / sector_size))
            if index >= len(states) or states[index] not in (1, 2)
        ]

    try:
        data, geometry, fat, root_dir = _read_fat12_image_context(image_path)
        if unread_in_range(0, sector_size) or unread_in_range(geometry.fat_offset, geometry.fat_size):
            return [], "Song damage cannot be mapped because the boot sector or FAT was not fully read."
        if unread_in_range(geometry.root_offset, geometry.root_size):
            return [], "Song damage cannot be mapped because the root directory was not fully read."
        affected = []
        seen_directories = set()

        def visit(directory, prefix=""):
            for entry in _iter_fat_directory_entries(directory):
                if entry["attr"] & 0x08:
                    continue
                path = prefix + entry["name"]
                if entry["attr"] & 0x10:
                    cluster = entry["cluster"]
                    if cluster in seen_directories:
                        continue
                    seen_directories.add(cluster)
                    clusters = _fat12_cluster_chain_from_start(fat, cluster)
                    if any(unread_in_range(_cluster_offset(geometry, item), geometry.cluster_size) for item in clusters):
                        affected.append({"path": path, "status": "directory unreadable; contained songs unknown"})
                    else:
                        visit(_read_directory_chain_from_image(data, geometry, fat, cluster), path + "/")
                    continue
                try:
                    clusters = _fat12_cluster_chain(fat, entry["cluster"], entry["size"], geometry)
                except FloppyImageError:
                    affected.append({"path": path, "status": "invalid allocation chain"})
                    continue
                unread = []
                remaining = entry["size"]
                for cluster in clusters:
                    size = min(remaining, geometry.cluster_size)
                    unread.extend(unread_in_range(_cluster_offset(geometry, cluster), size))
                    remaining -= size
                if unread:
                    affected.append({"path": path, "status": "contains unread sectors", "unread_sectors": unread})

        visit(root_dir)
        return affected, "Mapped against readable FAT and directory entries; unlisted/deleted songs may remain unknown."
    except (OSError, FloppyImageError, ValueError) as exc:
        return [], f"Song damage could not be mapped: {exc}"


def retain_floppy_recovery_capture(source_path, diagnostics):
    """Retain useful acquisition data independently of an editable session."""
    details = dict(diagnostics or {})
    if not os.path.isfile(source_path):
        return details
    capture_path = details.get("partial_capture_path", "")
    if not os.path.isfile(capture_path):
        retained_dir = tempfile.mkdtemp(prefix="aps_partial_floppy_")
        capture_path = os.path.join(retained_dir, "partial.img")
        shutil.copy2(source_path, capture_path)
    details["partial_capture_path"] = capture_path
    details["partial_capture_diagnostics_path"] = capture_path + ".json"
    details["affected_files"], details["affected_files_note"] = _recovery_affected_files(capture_path, details)
    details.pop("_sector_states", None)
    with open(capture_path + ".json", "w", encoding="utf-8") as handle:
        json.dump(details, handle, ensure_ascii=False, indent=2)
    return details


def save_floppy_recovery_capture(diagnostics, output_path):
    """Export a retained acquisition and coverage report without rereading media."""
    details = dict(diagnostics or {})
    source = details.get("partial_capture_path", "")
    if not source or not os.path.isfile(source):
        raise FloppyImageError("No retained partial capture is available.")
    output_path = os.path.abspath(os.fspath(output_path))
    if not output_path.lower().endswith(".img"):
        output_path += ".img"
    report_path = output_path + ".json"
    image_temp = _capture_temp_output_path(output_path, suffix=".img")
    report_temp = _capture_temp_output_path(report_path, suffix=".json")
    try:
        shutil.copy2(source, image_temp)
        details["partial_capture_path"] = output_path
        details["partial_capture_diagnostics_path"] = report_path
        with open(report_temp, "w", encoding="utf-8") as handle:
            json.dump(details, handle, ensure_ascii=False, indent=2)
        os.replace(image_temp, output_path)
        os.replace(report_temp, report_path)
    finally:
        for path in (image_temp, report_temp):
            if os.path.exists(path):
                os.remove(path)
    return output_path, report_path


_WINDOWS_RAW_WRITE_HELPER_ARG = "--aps-raw-floppy-write-helper"


def _write_block_device(input_path, device_path, progress_callback=None, cancel_callback=None):
    if os.name == "nt":
        permission_hint = (
            "Direct floppy writes on Windows require permission to lock and write the raw drive. "
            "Close Explorer windows using the drive and run the app as administrator if Windows denies access. "
            "You can also use Save As Image as a safer fallback."
        )
        try:
            _write_block_device_windows_direct(
                input_path,
                device_path,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            return
        except FloppyOperationCancelled:
            raise
        except FloppyImageError as exc:
            detail = str(exc)
            if _windows_raw_write_denied(exc):
                try:
                    _write_block_device_windows_elevated(
                        input_path,
                        device_path,
                        progress_callback=progress_callback,
                        cancel_callback=cancel_callback,
                    )
                    return
                except FloppyOperationCancelled:
                    raise
                except FloppyImageError as elevated_exc:
                    detail = (
                        f"{detail}\n\n"
                        f"Administrator retry failed: {elevated_exc}"
                    )
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


def _write_block_device_windows_direct(input_path, device_path, progress_callback=None, cancel_callback=None):
    if os.name != "nt":
        raise FloppyImageError("Windows raw floppy writes are only available on Windows.")
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


def _windows_raw_write_helper_command(input_path, device_path, result_path):
    helper_args = [
        _WINDOWS_RAW_WRITE_HELPER_ARG,
        os.path.abspath(input_path),
        str(device_path),
        os.path.abspath(result_path),
    ]
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline(helper_args)

    script_path = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if not script_path:
        raise FloppyImageError("Could not find the app entry point for administrator retry.")
    return sys.executable, subprocess.list2cmdline([script_path, *helper_args])


def _run_windows_process_as_admin(executable, parameters, cancel_callback=None):
    ctypes, wintypes, _kernel32 = _windows_ctypes()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    class _ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF
    INFINITE_SLICE_MS = 100

    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(_ShellExecuteInfo)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.hwnd = None
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = parameters
    info.lpDirectory = None
    info.nShow = SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise FloppyImageError(_windows_last_error_message("Could not request administrator approval for floppy writing"))

    try:
        while True:
            result = kernel32.WaitForSingleObject(info.hProcess, INFINITE_SLICE_MS)
            if result == WAIT_OBJECT_0:
                break
            if result == WAIT_TIMEOUT:
                continue
            if result == WAIT_FAILED:
                raise FloppyImageError(_windows_last_error_message("Could not wait for administrator floppy write helper"))
            _raise_if_cancelled(cancel_callback)
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise FloppyImageError(_windows_last_error_message("Could not read administrator floppy write result"))
        return int(exit_code.value)
    finally:
        if info.hProcess:
            kernel32.CloseHandle(info.hProcess)


def _write_block_device_windows_elevated(input_path, device_path, progress_callback=None, cancel_callback=None):
    if os.name != "nt":
        raise FloppyImageError("Administrator retry is only available on Windows.")
    _raise_if_cancelled(cancel_callback)
    fd, result_path = tempfile.mkstemp(prefix="aps_raw_floppy_write_", suffix=".json")
    os.close(fd)
    try:
        executable, parameters = _windows_raw_write_helper_command(input_path, device_path, result_path)
        _notify_progress(
            progress_callback,
            0,
            100,
            "Requesting administrator approval for direct floppy write...",
        )
        exit_code = _run_windows_process_as_admin(
            executable,
            parameters,
            cancel_callback=cancel_callback,
        )
        result = {}
        try:
            with open(result_path, "r", encoding="utf-8") as handle:
                result = json.load(handle)
        except (OSError, json.JSONDecodeError):
            result = {}
        if exit_code == 0 and result.get("ok"):
            _notify_progress(progress_callback, 100, 100, "Administrator floppy write complete.")
            return
        helper_error = str(result.get("error") or "").strip()
        if helper_error:
            raise FloppyImageError(helper_error)
        raise FloppyImageError(f"The administrator floppy write helper exited with code {exit_code}.")
    finally:
        try:
            os.remove(result_path)
        except OSError:
            pass


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
            "If this is a protected or damaged disk, use Disk > Read Floppy... with recovery instead."
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


def _helper_argv_uses_windows_raw_write(argv=None):
    argv = list(sys.argv if argv is None else argv)
    return len(argv) >= 2 and argv[1] == _WINDOWS_RAW_WRITE_HELPER_ARG


def run_windows_raw_write_helper_from_argv(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if not _helper_argv_uses_windows_raw_write(argv):
        return None
    result = {"ok": False}
    result_path = argv[4] if len(argv) >= 5 else ""
    try:
        if os.name != "nt":
            raise FloppyImageError("The elevated floppy write helper is only available on Windows.")
        if len(argv) < 5:
            raise FloppyImageError("The elevated floppy write helper received incomplete arguments.")
        input_path = argv[2]
        device_path = argv[3]
        _write_block_device_windows_direct(input_path, device_path)
        result = {"ok": True}
        return 0
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
        return 1
    finally:
        if result_path:
            try:
                with open(result_path, "w", encoding="utf-8") as handle:
                    json.dump(result, handle)
            except OSError:
                pass


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


def _root_dir_is_structurally_valid(data, offset, root_dir_sectors):
    end = offset + int(root_dir_sectors) * _YAMAHA_BYTES_PER_SECTOR
    if offset < 0 or end > len(data):
        return False

    for pos in range(offset, end, 32):
        entry = data[pos:pos + 32]
        if len(entry) < 32:
            return False
        first = entry[0]
        attr = entry[11]
        if first == 0x00:
            return True
        if first == 0xE5 or attr == 0x0F:
            continue
        if attr & 0xC0 or not _entry_name_looks_plausible(entry[:11]):
            return False
    return True


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
        bytes_per_sector = int(layout["bytes_per_sector"])
        primary_boot = data[:bytes_per_sector]
        relocated_boot = data[fat2_offset:fat2_offset + bytes_per_sector]
        primary_boot_is_valid = _geometry_from_boot_sector(primary_boot) is not None
        fat_tails_match = (
            data[fat1_offset + bytes_per_sector:fat1_offset + fat_size]
            == data[fat2_offset + bytes_per_sector:fat2_offset + fat_size]
        )

        if (
            size == total_size
            and not primary_boot_is_valid
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

        relocated_layout = _protected_layout_hint_from_boot_sector(relocated_boot)
        relocated_boot_is_signed = relocated_boot[510:512] == _YAMAHA_BOOT_SIGNATURE
        primary_boot_is_blank = (
            primary_boot
            and primary_boot[0] in {0x00, 0xF6}
            and primary_boot == bytes([primary_boot[0]]) * len(primary_boot)
        )
        relocated_bpb_is_unsigned_stub = (
            not relocated_boot_is_signed
            and primary_boot_is_blank
            and fat_tails_match
            and not any(relocated_boot[28:])
        )
        if (
            size == total_size
            and not primary_boot_is_valid
            and _fat_signature_at(data, fat1_offset, media_descriptor)
            and relocated_layout == layout
            and (relocated_boot_is_signed or relocated_bpb_is_unsigned_stub)
            and fat_tails_match
            and _root_dir_looks_plausible(data, root_offset, root_dir_sectors)
        ):
            detection = {
                "mode": "replace_sector0",
                "layout": layout,
                "fat1_offset": fat1_offset,
                "fat_size": fat_size,
                "root_offset": root_offset,
                "root_dir_sectors": root_dir_sectors,
                "repair_fat_mirrors": True,
            }
            if relocated_boot_is_signed:
                detection["boot_sector"] = relocated_boot
                detection["notes"] = (
                    f"sector 0 appears blank/corrupt; a valid {layout['label']} boot sector was stored "
                    "over the second FAT while the first FAT and root directory remain intact"
                )
            else:
                detection["notes"] = (
                    f"sector 0 appears blank/corrupt; an unsigned {layout['label']} BPB stub was stored "
                    "over the second FAT while the first FAT, matching FAT tails, and root directory remain intact"
                )
            return detection

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
    primary_boot = data[:_YAMAHA_BYTES_PER_SECTOR]

    if size == _YAMAHA_TOTAL_SIZE and _geometry_from_boot_sector(primary_boot) is not None:
        return {
            "mode": "already_valid",
            "fat1_offset": fat1_offset,
            "root_offset": root_offset,
            "notes": "valid 720 KB FAT boot sector already present",
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
    data_clusters = _fat12_data_cluster_count(geometry)
    fat_entry_capacity = (geometry.fat_size * 2) // 3
    if (
        data_clusters <= 0
        or data_clusters >= 4085
        or data_clusters > max(0, fat_entry_capacity - 2)
    ):
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
            if callable(getattr(device, "read_at_recovery", None)):
                try:
                    chunk = device.read_at_recovery(
                        cursor, remaining, label, cancel_callback=cancel_callback,
                        deadline_at=time.monotonic() + 30.0,
                    )
                except _RecoveryReadDeadlineExceeded as exc:
                    raise _FloppyReadStalled(
                        "The floppy drive did not finish reading this part of the disk within 30 seconds. "
                        "APS stopped the read. Try Read Floppy with Start in recovery mode. "
                        f"Read location: {label}, byte {cursor}."
                    ) from exc
            elif hasattr(device, "read_at"):
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
    except (FloppyOperationCancelled, _FloppyReadStalled):
        raise
    except FloppyImageError:
        return None


def _read_device_best_effort(device, offset, size, label, *, sector_size=_YAMAHA_BYTES_PER_SECTOR, cancel_callback=None):
    try:
        return _read_device_exact(device, offset, size, label, cancel_callback=cancel_callback), []
    except (FloppyOperationCancelled, _FloppyReadStalled):
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
        except (FloppyOperationCancelled, _FloppyReadStalled):
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


def _fat_lfn_checksum(short_name_bytes):
    checksum = 0
    for value in bytes(short_name_bytes or b"")[:11]:
        checksum = (((checksum & 1) << 7) | (checksum >> 1))
        checksum = (checksum + value) & 0xFF
    return checksum


def _decode_fat_lfn_entries(lfn_entries, short_name_bytes):
    if not lfn_entries:
        return ""
    expected_checksum = _fat_lfn_checksum(short_name_bytes)
    chunks = {}
    expected_count = 0
    for entry in lfn_entries:
        ordinal = entry[0]
        sequence = ordinal & 0x1F
        if sequence <= 0 or entry[11] != 0x0F or entry[13] != expected_checksum:
            return ""
        if ordinal & 0x40:
            expected_count = sequence
        chunks[sequence] = entry[1:11] + entry[14:26] + entry[28:32]
    if expected_count <= 0 or set(chunks) != set(range(1, expected_count + 1)):
        return ""

    encoded_name = b"".join(chunks[index] for index in range(1, expected_count + 1))
    clean_name = bytearray()
    for offset in range(0, len(encoded_name), 2):
        code_unit = encoded_name[offset:offset + 2]
        if len(code_unit) < 2 or code_unit == b"\x00\x00":
            break
        if code_unit == b"\xff\xff":
            continue
        clean_name.extend(code_unit)
    try:
        return bytes(clean_name).decode("utf-16-le")
    except UnicodeDecodeError:
        return ""


def _iter_fat_directory_entries(directory_bytes):
    lfn_entries = []
    for pos in range(0, len(directory_bytes), 32):
        entry = directory_bytes[pos:pos + 32]
        if len(entry) < 32:
            break
        first = entry[0]
        attr = entry[11]
        if first == 0x00:
            break
        if first == 0xE5:
            lfn_entries = []
            continue
        if attr == 0x0F:
            lfn_entries.append(entry)
            continue
        short_name = _decode_dos_directory_name(entry[:11])
        name = _decode_fat_lfn_entries(lfn_entries, entry[:11]) or short_name
        lfn_entries = []
        if not name or name in {".", ".."}:
            continue
        yield {
            "name": name,
            "short_name": short_name,
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
        if (
            entry["name"].upper() != target_name
            and entry.get("short_name", "").upper() != target_name
        ):
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


def _reconstructed_eseq_file_size(payload):
    """Recover a file boundary using only bytes from its allocated FAT chain."""
    stream_start = _eseq_event_stream_start(payload)
    declared_sizes = (
        int.from_bytes(payload[3:7], "little"),
        stream_start + int.from_bytes(payload[0x1F:0x23], "little"),
    )
    candidates = [size for size in declared_sizes if stream_start < size <= len(payload)]

    # Some Yamaha headers undercount the file by eight bytes or saturate at
    # 0xFFFF. An F2 at an opcode boundary can establish the complete stream,
    # including a valid trailer beyond either declared length. Do not search
    # blindly for F2: it can also occur inside a SysEx payload.
    pos = stream_start
    while pos < len(payload):
        status = payload[pos]
        pos += 1
        if status == 0xF2:
            candidates.append(pos)
            break
        if status < 0x80 or status == 0xF6:
            continue
        if status == 0xF0:
            while pos < len(payload):
                byte = payload[pos]
                pos += 1
                if byte in (0xF3, 0xF4):
                    pos += 1 if byte == 0xF3 else 2
                elif byte == 0xF7:
                    break
            else:
                break
            if pos > len(payload):
                break
            continue
        if status in (0xF1, 0xF3, 0xFF) or status >> 4 in (0xC, 0xD):
            operands = 1
        elif status in (0xF4, 0xF9, 0xFB) or 0x80 <= status < 0xF0:
            operands = 2
        else:
            break
        if pos + operands > len(payload):
            break
        pos += operands

    return max(candidates) if candidates else len(payload)


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
            payload = _read_cluster_chain_from_image(data, geometry, chain, allocated)
            matched_cluster = cluster
            matched_size = _reconstructed_eseq_file_size(payload)
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
                    except (FloppyOperationCancelled, _FloppyReadStalled):
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
                _notify_progress(
                    progress_callback,
                    10,
                    100,
                    "Possible Yamaha-protected disk recognized; creating working copy...",
                )
                serial = zlib.crc32(fat_area + root_dir) & 0xFFFFFFFF
                boot = _build_standard_fat12_boot_sector(matched_layout, serial, _find_volume_label(root_dir))
                repair_result = YamahaRepairResult(
                    "Fast floppy read rebuilt a FAT12 boot sector for possible Yamaha copy protection: sector 0 appears blank/corrupt.",
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
    stored_boot = detection.get("boot_sector")
    serial = zlib.crc32(data[int(detection["fat1_offset"]):]) & 0xFFFFFFFF
    expected_size = _layout_total_size(layout) if layout else _YAMAHA_TOTAL_SIZE
    if stored_boot is not None:
        boot = bytes(stored_boot)
    elif layout:
        boot = _build_standard_fat12_boot_sector(layout, serial, _find_volume_label(root_dir))
    else:
        boot = _build_standard_yamaha_boot_sector(serial, _find_volume_label(root_dir))

    if detection["mode"] == "prepend_sector0":
        repaired = boot + data
    else:
        repaired = boot + data[bytes_per_sector:]

    if len(repaired) != expected_size:
        raise FloppyImageError(
            "FAT12 repair produced an unexpected image size. "
            "The source may not match a supported Yamaha/IBM floppy layout."
        )

    if detection.get("repair_fat_mirrors"):
        repaired_data = bytearray(repaired)
        fat1_offset = int(detection["fat1_offset"])
        fat_size = int(detection.get("fat_size") or _layout_fat_size(layout))
        fat1 = bytes(repaired_data[fat1_offset:fat1_offset + fat_size])
        for fat_index in range(1, int(layout["num_fats"])):
            mirror_offset = fat1_offset + fat_index * fat_size
            repaired_data[mirror_offset:mirror_offset + fat_size] = fat1
        repaired = bytes(repaired_data)

    if detection.get("root_dir") is not None:
        root_offset = int(detection["root_offset"])
        repaired_data = bytearray(repaired)
        repaired_data[root_offset:root_offset + len(root_dir)] = root_dir
        repaired = bytes(repaired_data)

    write_output(repaired)

    return YamahaRepairResult(
        "Yamaha-compatible boot-sector repair applied: " + detection["notes"] + ".",
        True,
    )


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

    source_ext = str(source_ext or "").lower().lstrip(".")
    preferred = []

    def add_preferred(disk_format):
        if disk_format not in preferred:
            preferred.append(disk_format)

    if source_ext == "hfe":
        try:
            size = os.path.getsize(source_path)
        except OSError:
            size = 0
        if 0 < size < 3 * 1024 * 1024:
            add_preferred(DISK_FORMAT_BY_KEY["ibm.720"])
        elif 0 < size < 6 * 1024 * 1024:
            add_preferred(DISK_FORMAT_BY_KEY["ibm.1440"])
        elif 0 < size < 10 * 1024 * 1024:
            add_preferred(DISK_FORMAT_BY_KEY["ibm.2880"])

    if source_ext in {"scp", "hfe"}:
        add_preferred(DISK_FORMAT_BY_KEY["ibm.720"])

    return preferred + [disk_format for disk_format in DISK_FORMATS if disk_format not in preferred]


def _looks_like_editable_fat_image(img_path):
    try:
        read_image_listing(img_path)
        return True
    except FloppyImageError:
        return False


def _sector_map_has_blank_disk_evidence(sector_map):
    sector_map = sector_map or {}
    found = sector_map.get("found")
    total = sector_map.get("total")
    try:
        found = int(found)
        total = int(total)
    except (TypeError, ValueError):
        return False
    if total <= 0:
        return False
    # Zero decoded sectors is evidence of an unreadable or wrong-geometry
    # capture, not evidence that the underlying disk is blank. Only a complete
    # sector read gives us enough information to inspect the payload itself.
    return found == total


def _uniform_blank_fill_value(data):
    data = bytes(data or b"")
    if not data or data[0] not in BLANK_FLOPPY_FILL_VALUES:
        return None
    fill_value = data[0]
    return fill_value if data.count(fill_value) == len(data) else None


def _converted_image_appears_blank_or_unformatted(img_path, disk_format, sector_map):
    if not _sector_map_has_blank_disk_evidence(sector_map):
        return False
    try:
        with open(img_path, "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    if not data:
        return False
    if isinstance(disk_format, DiskFormat) and len(data) != disk_format.size_bytes:
        return False
    if _looks_like_valid_yamaha_boot_sector(data[:_YAMAHA_BYTES_PER_SECTOR]):
        return False
    if _protected_layout_hint_from_boot_sector(data[:_YAMAHA_BYTES_PER_SECTOR]) is not None:
        return False
    try:
        recovered = _recover_files_from_raw_image_bytes(
            data,
            disk_format_hint=disk_format if isinstance(disk_format, DiskFormat) else None,
        )
    except Exception:
        return False
    if recovered:
        return False

    # Fully readable, non-zero data that this version does not recognize may be
    # an unsupported filesystem or instrument format. Calling it blank hides a
    # materially different (and actionable) result from the user.
    return _uniform_blank_fill_value(data) is not None


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
    if (
        geometry is not None
        and geometry.total_size <= len(data)
        and all(existing.total_size != geometry.total_size for existing in geometries)
    ):
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


def _recovery_range_fully_readable(diagnostics, offset, size):
    states = (diagnostics or {}).get("_sector_states")
    if not isinstance(states, list):
        return None
    sector_size = max(
        1,
        int((diagnostics or {}).get("sector_size") or USB_FLOPPY_RECOVERY_SECTOR_SIZE),
    )
    start_sector = max(0, int(offset) // sector_size)
    end_sector = int(math.ceil((int(offset) + max(0, int(size))) / sector_size))
    if end_sector > len(states):
        return False
    return all(states[index] in {1, 2} for index in range(start_sector, end_sector))


def _recovery_geometry_label(geometry):
    disk_format = DISK_FORMAT_BY_SIZE.get(int(geometry.total_size))
    if disk_format is not None:
        return disk_format.key, disk_format.label
    return "", f"FAT12 ({geometry.total_sectors} sectors)"


def _recovery_file_kind_counts(files):
    files = list(files or ())
    return {
        "recovered_files": len(files),
        "recovered_midi_files": sum(1 for item in files if getattr(item, "kind", "") == "MIDI"),
        "recovered_eseq_files": sum(1 for item in files if getattr(item, "kind", "") == "E-SEQ"),
        "recovered_pianodir_files": sum(
            1 for item in files if getattr(item, "kind", "") == "PIANODIR"
        ),
    }


def _listing_recovery_kind_counts(listing):
    entries = list(getattr(listing, "entries", ()) or ())
    midi_count = 0
    eseq_count = 0
    pianodir_count = 0
    for entry in entries:
        path = str(getattr(entry, "path", "") or "")
        if is_pianodir_path(path):
            pianodir_count += 1
            continue
        extension = os.path.splitext(path)[1].lower()
        if extension in {".mid", ".midi"}:
            midi_count += 1
        elif extension in {".fil", ".mda"}:
            eseq_count += 1
    return {
        "recovered_files": len(entries),
        "recovered_midi_files": midi_count,
        "recovered_eseq_files": eseq_count,
        "recovered_pianodir_files": pianodir_count,
    }


def _populate_recovery_scan_diagnostics(data, diagnostics, disk_format_hint=None):
    if not isinstance(diagnostics, dict):
        return
    data = bytes(data or b"")
    sector_size = max(
        1,
        int(diagnostics.get("sector_size") or USB_FLOPPY_RECOVERY_SECTOR_SIZE),
    )
    diagnostics["image_bytes"] = len(data)
    diagnostics["sha256"] = hashlib.sha256(data).hexdigest() if data else ""
    diagnostics["nonzero_sectors"] = sum(
        1
        for offset in range(0, len(data), sector_size)
        if any(data[offset:offset + sector_size])
    )
    blank_fill_value = _uniform_blank_fill_value(data)
    diagnostics["blank_fill_only"] = blank_fill_value is not None
    diagnostics["blank_fill_value"] = (
        f"0x{blank_fill_value:02X}" if blank_fill_value is not None else ""
    )
    diagnostics["midi_header_signatures"] = data.count(b"MThd")
    diagnostics["eseq_signatures"] = data.count(b"COM-ESEQ")
    diagnostics["pianodir_signatures"] = data.count(PIANODIR_HEADER)
    diagnostics["recognizable_signatures"] = (
        int(diagnostics["midi_header_signatures"])
        + int(diagnostics["eseq_signatures"])
        + int(diagnostics["pianodir_signatures"])
    )

    boot_readable = _recovery_range_fully_readable(
        diagnostics,
        0,
        min(sector_size, len(data)),
    )
    if boot_readable is None:
        boot_readable = len(data) >= sector_size
    diagnostics["boot_sector_readable"] = bool(boot_readable)
    diagnostics["boot_signature_present"] = (
        bool(
            len(data) >= sector_size
            and data[sector_size - 2:sector_size] == _YAMAHA_BOOT_SIGNATURE
        )
        if boot_readable
        else None
    )
    boot_geometry = (
        _geometry_from_boot_sector(data[:sector_size])
        if diagnostics["boot_sector_readable"]
        else None
    )
    boot_layout = (
        _protected_layout_hint_from_boot_sector(data[:sector_size])
        if diagnostics["boot_sector_readable"]
        else None
    )
    trusted_boot_geometry = (
        boot_geometry
        if boot_geometry is not None
        and boot_layout is not None
        and _layout_total_size(boot_layout) == boot_geometry.total_size
        else None
    )

    geometries = list(_recovery_geometries_for_data(data, disk_format_hint=disk_format_hint))
    hinted_geometry = _geometry_for_disk_format_hint(disk_format_hint)
    if (
        hinted_geometry is not None
        and all(existing.total_size != hinted_geometry.total_size for existing in geometries)
    ):
        geometries.insert(0, hinted_geometry)

    scans = []
    for geometry in geometries:
        format_key, format_label = _recovery_geometry_label(geometry)
        available = len(data) >= geometry.total_size
        media_descriptor = (
            data[21]
            if trusted_boot_geometry is not None
            and trusted_boot_geometry.total_size == geometry.total_size
            and len(data) > 21
            else next(
                (
                    int(layout["media_descriptor"])
                    for layout in _PROTECTED_FAT12_LAYOUTS
                    if _layout_total_size(layout) == geometry.total_size
                ),
                _YAMAHA_MEDIA_DESCRIPTOR,
            )
        )
        fat_readable = []
        fat_valid = []
        fat_payloads = []
        for fat_index in range(geometry.num_fats):
            fat_offset = geometry.fat_offset + fat_index * geometry.fat_size
            copy_available = fat_offset + geometry.fat_size <= len(data)
            copy_readable = _recovery_range_fully_readable(
                diagnostics,
                fat_offset,
                geometry.fat_size,
            )
            if copy_readable is None:
                copy_readable = copy_available
            fat_readable.append(bool(copy_available and copy_readable))
            fat_payloads.append(
                data[fat_offset:fat_offset + geometry.fat_size]
                if copy_available and copy_readable
                else None
            )
            fat_valid.append(
                bool(
                    copy_available
                    and copy_readable
                    and _fat_signature_at(data, fat_offset, media_descriptor)
                )
            )
        fat_copies_consistent = None
        if fat_payloads and all(payload is not None for payload in fat_payloads):
            fat_copies_consistent = all(
                payload == fat_payloads[0]
                for payload in fat_payloads[1:]
            )
        allocated_data_clusters = None
        fat_allocation_empty = None
        if fat_payloads and fat_payloads[0] is not None and fat_valid[0]:
            fat_entry_capacity = max(0, (len(fat_payloads[0]) * 2) // 3 - 2)
            captured_cluster_capacity = max(
                0,
                (len(data) - geometry.data_offset) // max(1, geometry.cluster_size),
            )
            allocation_scan_clusters = min(
                _fat12_data_cluster_count(geometry),
                fat_entry_capacity,
                captured_cluster_capacity,
                4084,
            )
            allocated_data_clusters = sum(
                1
                for cluster in range(2, allocation_scan_clusters + 2)
                if _fat12_next_cluster(fat_payloads[0], cluster) != 0
            )
            fat_allocation_empty = (
                allocated_data_clusters == 0
                if allocation_scan_clusters == _fat12_data_cluster_count(geometry)
                else None
            )

        root_available = geometry.root_offset + geometry.root_size <= len(data)
        root_readable = _recovery_range_fully_readable(
            diagnostics,
            geometry.root_offset,
            geometry.root_size,
        )
        if root_readable is None:
            root_readable = root_available
        root_readable = bool(root_available and root_readable)
        root_dir = (
            data[geometry.root_offset:geometry.root_offset + geometry.root_size]
            if root_available
            else b""
        )
        root_plausible = (
            bool(
                _root_dir_looks_plausible(
                    root_dir,
                    0,
                    geometry.root_dir_sectors,
                )
            )
            if root_readable
            else None
        )
        root_structurally_valid = (
            bool(
                _root_dir_is_structurally_valid(
                    root_dir,
                    0,
                    geometry.root_dir_sectors,
                )
            )
            if root_readable
            else None
        )
        active_root_entries = None
        pianodir_entry = None
        if root_readable:
            try:
                active_root_entries = [
                    entry
                    for entry in _iter_fat_directory_entries(root_dir)
                    if not (int(entry.get("attr") or 0) & 0x08)
                    and not _is_windows_volume_metadata_path(entry.get("name", ""))
                ]
                pianodir_entry = any(
                    is_pianodir_path(entry.get("name", ""))
                    for entry in active_root_entries
                )
            except Exception:
                active_root_entries = None
                pianodir_entry = None

        scans.append(
            {
                "format_key": format_key,
                "format_label": format_label,
                "total_bytes": int(geometry.total_size),
                "total_sectors": int(geometry.total_sectors),
                "selected_hint": bool(
                    isinstance(disk_format_hint, DiskFormat)
                    and disk_format_hint.size_bytes == geometry.total_size
                ),
                "boot_hint": bool(
                    trusted_boot_geometry is not None
                    and trusted_boot_geometry.total_size == geometry.total_size
                ),
                "image_has_full_geometry": bool(available),
                "fat_copies_expected": int(geometry.num_fats),
                "fat_copies_readable": sum(1 for value in fat_readable if value),
                "fat_copies_valid": sum(1 for value in fat_valid if value),
                "fat_area_readable": bool(fat_readable and all(fat_readable)),
                "fat_copies_consistent": fat_copies_consistent,
                "fat_allocated_data_clusters": allocated_data_clusters,
                "fat_allocation_empty": fat_allocation_empty,
                "root_directory_readable": root_readable,
                "root_directory_structurally_valid": root_structurally_valid,
                "root_directory_plausible": root_plausible,
                "root_directory_entries": (
                    len(active_root_entries)
                    if active_root_entries is not None
                    else None
                ),
                "pianodir_directory_entry_found": pianodir_entry,
                "attempted": False,
                "recovered_files": 0,
                "error": "",
            }
        )

    selected_bytes = int(diagnostics.get("selected_bytes") or 0)
    detected_scan = None
    if trusted_boot_geometry is not None:
        detected_scan = next(
            (
                scan
                for scan in scans
                if int(scan.get("total_bytes") or 0) == trusted_boot_geometry.total_size
            ),
            None,
        )
    if detected_scan is None:
        credible_scans = [
            scan
            for scan in scans
            if int(scan.get("fat_copies_expected") or 0) > 0
            and int(scan.get("fat_copies_valid") or 0)
            == int(scan.get("fat_copies_expected") or 0)
            and scan.get("fat_area_readable")
            and scan.get("fat_copies_consistent")
            and scan.get("root_directory_structurally_valid")
        ]
        if credible_scans:
            detected_scan = max(
                credible_scans,
                key=lambda scan: (
                    int(scan.get("fat_copies_valid") or 0),
                    int(scan.get("fat_copies_readable") or 0),
                    bool(scan.get("selected_hint")),
                ),
            )

    if detected_scan is not None:
        detected_bytes = int(detected_scan.get("total_bytes") or 0)
        diagnostics.update(
            {
                "detected_format_key": str(detected_scan.get("format_key") or ""),
                "detected_format_label": str(detected_scan.get("format_label") or ""),
                "detected_bytes": detected_bytes,
                "detection_basis": "validated_boot_sector"
                if detected_scan.get("boot_hint")
                else "fat_and_root",
                "geometry_mismatch": bool(
                    selected_bytes > 0
                    and detected_bytes > 0
                    and selected_bytes != detected_bytes
                ),
            }
        )
    elif diagnostics.get("geometry_mismatch") is None:
        diagnostics["geometry_mismatch"] = None

    filesystem_scan = detected_scan
    if filesystem_scan is None and selected_bytes:
        filesystem_scan = next(
            (
                scan
                for scan in scans
                if int(scan.get("total_bytes") or 0) == selected_bytes
            ),
            None,
        )
    if filesystem_scan is not None:
        for key in (
            "fat_copies_expected",
            "fat_copies_valid",
            "fat_area_readable",
            "fat_copies_consistent",
            "fat_allocated_data_clusters",
            "fat_allocation_empty",
            "root_directory_readable",
            "root_directory_structurally_valid",
            "root_directory_plausible",
            "root_directory_entries",
            "pianodir_directory_entry_found",
        ):
            diagnostics[key] = filesystem_scan.get(key)

    diagnostics["geometry_scans"] = scans
    diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)


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


def _recover_files_from_raw_image_bytes(data, disk_format_hint=None, diagnostics=None):
    if isinstance(diagnostics, dict):
        _populate_recovery_scan_diagnostics(
            data,
            diagnostics,
            disk_format_hint=disk_format_hint,
        )
    recovered = []
    scans_by_size = {
        int(scan.get("total_bytes") or 0): scan
        for scan in (diagnostics or {}).get("geometry_scans", ())
        if isinstance(scan, dict)
    }
    for geometry in _recovery_geometries_for_data(data, disk_format_hint=disk_format_hint):
        scan = scans_by_size.get(int(geometry.total_size))
        if scan is not None:
            scan["attempted"] = True
        try:
            geometry_files = _recover_files_from_fat_context(data, geometry)
            recovered.extend(geometry_files)
            if scan is not None:
                scan["recovered_files"] = len(geometry_files)
        except FloppyOperationCancelled:
            raise
        except Exception as exc:
            if scan is not None:
                scan["error"] = " ".join(str(exc).split())[:240]
            continue
    recovered.extend(_carve_recovery_files_from_bytes(data))
    recovered = _dedupe_recovered_files(recovered)
    if isinstance(diagnostics, dict):
        diagnostics.update(_recovery_file_kind_counts(recovered))
        diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)
    return recovered


def _usb_recovery_no_data_message(diagnostics):
    details = dict(diagnostics or {})
    expected = max(0, int(details.get("expected_sectors") or 0))
    readable = max(0, int(details.get("readable_sectors") or 0))
    bad = max(0, int(details.get("bad_sectors") or 0))
    unresolved = max(0, int(details.get("unresolved_sectors") or 0))
    unattempted = max(0, int(details.get("unattempted_sectors") or 0))
    readable_ratio = (readable / expected) if expected > 0 else 0.0
    readable_percent = _diagnostic_percent(readable, expected)
    selected_label = str(details.get("selected_format_label") or "the selected format")
    detected_label = str(details.get("detected_format_label") or "another disk geometry")

    # A credible geometry disagreement is more actionable than the downstream
    # absence of carved files, so it deliberately takes precedence here.
    if details.get("geometry_mismatch"):
        if details.get("detection_basis") == "device_eof_after_readable_prefix":
            detection_evidence = (
                "the device reached a clean end-of-media boundary after a mostly readable prefix "
                f"matching {detected_label}"
            )
        else:
            detection_evidence = f"validated FAT/boot data identifies the disk as {detected_label}"
        return (
            f"Recovery was requested as {selected_label}, but {detection_evidence}. "
            "The geometry mismatch can make valid sectors appear "
            "unreadable. Retry recovery with the detected disk format. "
            f"This pass read {readable} of {expected} selected sectors ({readable_percent}) and "
            "found no recoverable PIANODIR.FIL, MIDI, or E-SEQ files."
        )

    if expected <= 0 or readable_ratio < 0.90:
        stop_reason = str(details.get("stop_reason") or "")
        stop_note = ""
        if stop_reason == "soft_deadline":
            if details.get("read_deadline_mode") == "windows_overlapped_cancel":
                stop_note = (
                    " Recovery reached its five-minute read deadline and requested cancellation "
                    "of the pending Windows device read."
                )
            else:
                stop_note = (
                    " Recovery stopped at its five-minute soft deadline; a single synchronous "
                    "operating-system read can finish after that limit."
                )
        elif stop_reason in {
            "all_sectors_bad",
            "mostly_bad_media",
            "consecutive_bad_sectors",
        }:
            stop_note = " Recovery stopped early because the sampled media was all or mostly unreadable."
        return (
            f"Recovery could read only {readable} of {expected} sectors ({readable_percent}); "
            f"{bad} were unreadable, {unresolved} attempted sectors remained unresolved, and "
            f"{unattempted} were not attempted. "
            "No identifiable PIANODIR.FIL, MIDI, or E-SEQ data was found in the readable sectors."
            + stop_note
        )

    fully_readable = bad == 0 and unattempted == 0 and readable >= expected > 0
    if fully_readable:
        if int(details.get("nonzero_sectors") or 0) <= 0:
            return (
                f"Recovery read all {expected} sectors, but the captured disk contains no non-zero "
                "sector data and no recognizable FAT, PIANODIR.FIL, MIDI, or E-SEQ structures. "
                "The disk may be blank or unformatted."
            )
        if details.get("blank_fill_only"):
            fill_value = str(details.get("blank_fill_value") or "a common blank-media value")
            return (
                f"Recovery read all {expected} sectors, but every byte has the repeated blank-media "
                f"fill value {fill_value}, with no recognizable FAT, PIANODIR.FIL, MIDI, or E-SEQ "
                "structures. The disk may be blank or unformatted."
            )
        fat_copies_expected = int(details.get("fat_copies_expected") or 0)
        fat_copies_valid = int(details.get("fat_copies_valid") or 0)
        credible_fat_metadata = bool(
            details.get("detection_basis") == "validated_boot_sector"
            or (
                fat_copies_expected > 0
                and fat_copies_valid == fat_copies_expected
                and details.get("fat_copies_consistent") is True
            )
            or (
                fat_copies_valid > 0
                and details.get("root_directory_plausible") is True
                and int(details.get("root_directory_entries") or 0) > 0
            )
        )
        damaged_fat_metadata = bool(
            credible_fat_metadata
            and (
                details.get("fat_area_readable") is False
                or (
                    fat_copies_expected > 0
                    and fat_copies_valid < fat_copies_expected
                )
                or details.get("fat_copies_consistent") is False
                or details.get("root_directory_readable") is False
                or details.get("root_directory_structurally_valid") is False
            )
        )
        if damaged_fat_metadata:
            return (
                f"Recovery read all {expected} sectors and recognized FAT12 filesystem metadata, "
                "but the FAT copies and/or root directory are incomplete or inconsistent. "
                "This points to filesystem damage rather than a blank disk; no recoverable "
                "PIANODIR.FIL, MIDI, or E-SEQ files were found."
            )
        empty_root_filesystem_is_credible = (
            fat_copies_expected > 0
            and fat_copies_valid == fat_copies_expected
            and details.get("fat_area_readable")
            and details.get("fat_copies_consistent")
            and details.get("root_directory_readable")
            and details.get("root_directory_structurally_valid")
            and details.get("root_directory_entries") == 0
        )
        if empty_root_filesystem_is_credible and details.get("fat_allocation_empty"):
            return (
                f"Recovery successfully read all {expected} sectors and found a readable FAT filesystem, "
                "but its root directory contains no files. The disk appears to be formatted but blank."
            )
        if (
            empty_root_filesystem_is_credible
            and details.get("fat_allocation_empty") is False
            and int(details.get("fat_allocated_data_clusters") or 0) > 0
        ):
            return (
                f"Recovery successfully read all {expected} sectors and found FAT filesystem metadata, "
                "but the root directory is empty while the FAT still marks "
                f"{int(details.get('fat_allocated_data_clusters') or 0)} data cluster(s) as allocated. "
                "This suggests orphaned files or filesystem damage; it is not evidence that the disk is blank."
            )
        if credible_fat_metadata:
            return (
                f"Recovery successfully read all {expected} sectors and found FAT12 filesystem metadata, "
                "but no recoverable PIANODIR.FIL, MIDI, or E-SEQ files. The disk may contain "
                "unsupported file types or an unsupported disk layout; it is not evidence that the disk is blank."
            )
        return (
            f"Recovery successfully read all {expected} sectors and non-zero data is present, "
            "but no recognizable FAT filesystem, PIANODIR.FIL, MIDI headers, or E-SEQ structures "
            "were found. The disk may use an unsupported format; this is not evidence that it is blank."
        )

    return (
        f"Recovery read {readable} of {expected} sectors ({readable_percent}), but found no "
        "recoverable PIANODIR.FIL, MIDI, or E-SEQ files. Review the disk diagnostics for unreadable "
        "areas, filesystem damage, or an unsupported format."
    )


def _finalize_recovery_diagnostics(diagnostics):
    if not isinstance(diagnostics, dict):
        return {}
    diagnostics.pop("_sector_states", None)
    diagnostics["human_report"] = format_floppy_recovery_diagnostics(diagnostics)
    return diagnostics


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
        virtual_files=None,
        conversion_warnings=None,
        read_only_format="",
        recovery_diagnostics=None,
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
        self.virtual_files = {
            _normalize_image_path(path): bytes(data)
            for path, data in dict(virtual_files or {}).items()
        }
        self.conversion_warnings = tuple(conversion_warnings or ())
        self.read_only_format = str(read_only_format or "")
        self.recovery_diagnostics = dict(recovery_diagnostics or {})
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
            except _FloppyReadStalled:
                raise
            except FastFloppyReadError as fast_exc:
                if not fast_exc.fallback_allowed:
                    raise FloppyImageError(
                        "Fast floppy read recognized this disk but could not finish without losing data.\n\n"
                        f"Details: {fast_exc}\n\n"
                        "Use Disk > Read Floppy... with Start in recovery mode for a slower full-disk recovery pass."
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
                    "Use Disk > Read Floppy... with Start in recovery mode for a slower full-disk recovery pass."
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
    def _try_prepare_existing_usb_floppy(
        cls,
        drive_info,
        disk_format,
        *,
        eseq_disk=False,
        progress_callback=None,
        cancel_callback=None,
    ):
        _notify_progress(progress_callback, 0, 100, "Checking existing floppy format...")
        session = None
        mutation_started = False

        def read_progress(step, total, message):
            if total and total > 0:
                clamped_step = max(0, min(int(step), int(total)))
                mapped_step = int((clamped_step / int(total)) * 35)
                _notify_progress(progress_callback, mapped_step, 100, message)
            else:
                _notify_progress(progress_callback, 0, 100, message)

        def write_progress(step, total, message):
            if total and total > 0:
                clamped_step = max(0, min(int(step), int(total)))
                mapped_step = 50 + int((clamped_step / int(total)) * 45)
                _notify_progress(progress_callback, mapped_step, 100, message)
            else:
                _notify_progress(progress_callback, 50, 100, message)

        temp_dir = tempfile.mkdtemp(prefix="aps_format_usb_probe_")
        try:
            working_img = os.path.join(temp_dir, "working.img")
            try:
                repair_result = _read_floppy_device_fast_image(
                    drive_info.path,
                    working_img,
                    drive_info.size_bytes,
                    progress_callback=read_progress,
                    cancel_callback=cancel_callback,
                )
                detected_format = _disk_format_for_image(working_img)
                read_image_listing(working_img)
            except FloppyOperationCancelled:
                raise
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None

            session = cls(
                drive_info.path,
                "img",
                temp_dir,
                working_img,
                detected_format,
                repair_result,
                source_kind="floppy_usb",
                source_name=drive_info.display_name,
                drive_info=drive_info,
            )
            temp_dir = None
            if session.disk_format.size_bytes != disk_format.size_bytes or session.repair_changed:
                session.cleanup()
                return None

            listing = session.list_entries()
            entries = list(listing.entries)
            if any(entry.directory for entry in entries):
                session.cleanup()
                return None

            mode_message = "Preparing existing E-SEQ floppy..." if eseq_disk else "Clearing existing floppy..."
            _notify_progress(progress_callback, 40, 100, mode_message)
            _prepare_existing_formatted_image(
                session.working_img_path,
                entries,
                session.temp_dir,
                eseq_disk=eseq_disk,
                cancel_callback=cancel_callback,
            )
            mutation_started = True
            _prepare_existing_formatted_usb_floppy(
                drive_info.path,
                entries,
                session.temp_dir,
                eseq_disk=eseq_disk,
                progress_callback=write_progress,
                cancel_callback=cancel_callback,
            )
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 100, 100, "Opening prepared floppy...")
            session.format_applied_lightly = True
            session.format_cleared_file_count = len(entries)
            session.repair_changed = False
            if eseq_disk:
                session.repair_note = (
                    "Existing IBM FAT format reused; floppy contents were cleared and an empty PIANODIR.FIL was added."
                )
            else:
                session.repair_note = "Existing IBM FAT format reused; floppy contents were cleared."
            return session
        except FloppyOperationCancelled:
            if session is not None:
                session.cleanup()
            raise
        except Exception:
            if session is not None:
                session.cleanup()
            if mutation_started:
                raise
            return None
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

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
            report_sector_map = (
                conversion_sector_map
                if gw_source.archival_quality and conversion_sector_map
                else read_sector_map
            )
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
                        report_sector_map,
                        title="Greaseweazle Read Sector Map",
                        disk_format=gw_source.disk_format,
                    )
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
                gw_sector_reports=(),
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

        capacity_error = usb_floppy_format_capacity_error(drive_info, disk_format)
        if capacity_error:
            raise FloppyImageError(capacity_error)

        existing_session = cls._try_prepare_existing_usb_floppy(
            drive_info,
            disk_format,
            eseq_disk=eseq_disk,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        if existing_session is not None:
            return existing_session

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
                _write_empty_pianodir_to_image(working_img, temp_dir, cancel_callback=cancel_callback)
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
                _write_empty_pianodir_to_image(working_img, temp_dir, cancel_callback=cancel_callback)
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
                _write_empty_pianodir_to_image(
                    working_img,
                    temp_dir,
                    metadata=pianodir_metadata,
                    cancel_callback=cancel_callback,
                )
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
            if int(read_size or 0) <= 0:
                read_size = _YAMAHA_TOTAL_SIZE
            diagnostics = _new_usb_floppy_recovery_diagnostics(
                drive_info,
                disk_format_hint,
                read_size,
                sector_size=USB_FLOPPY_RECOVERY_SECTOR_SIZE,
                soft_deadline_seconds=USB_FLOPPY_RECOVERY_SOFT_DEADLINE_SECONDS,
            )
            _notify_progress(
                progress_callback,
                0,
                100,
                "Copying a floppy image for recovery (five-minute soft limit)...",
            )
            diagnostics = _read_block_device_recovery_image(
                drive_info.path,
                source_copy,
                read_size,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                diagnostics=diagnostics,
            )
            diagnostics = retain_floppy_recovery_capture(source_copy, diagnostics)
            read_note = _usb_recovery_read_note(diagnostics)
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
                recovery_diagnostics=diagnostics,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        except Exception as exc:
            details = dict(getattr(exc, "diagnostics", None) or locals().get("diagnostics") or {})
            if isinstance(exc, FloppyOperationCancelled):
                details["recovery_cancelled"] = True
            _finalize_recovery_diagnostics(details)
            retained_in_original_dir = False
            try:
                details = retain_floppy_recovery_capture(source_copy, details)
            except OSError as retain_error:
                # Keep the original acquisition if copying the retained bundle
                # fails, so a disk-space error cannot erase recovered bytes.
                retained_in_original_dir = os.path.isfile(source_copy)
                if retained_in_original_dir:
                    details["partial_capture_path"] = source_copy
                details["capture_retention_error"] = str(retain_error)
            exc.diagnostics = details
            if not retained_in_original_dir:
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
        recovery_diagnostics=None,
        progress_callback=None,
        cancel_callback=None,
    ):
        source_img = os.path.abspath(source_img)
        prepared = os.path.join(temp_dir, "recovery_prepared.img")
        working_img = os.path.join(temp_dir, "working.img")
        diagnostics = (
            recovery_diagnostics
            if isinstance(recovery_diagnostics, dict)
            else None
        )

        def attach_recovery_cancellation(exc):
            if diagnostics is None:
                return
            diagnostics["recovery_cancelled"] = True
            _finalize_recovery_diagnostics(diagnostics)
            exc.diagnostics = dict(diagnostics)

        source_data = None
        if diagnostics is not None:
            try:
                with open(source_img, "rb") as handle:
                    source_data = handle.read()
                _populate_recovery_scan_diagnostics(
                    source_data,
                    diagnostics,
                    disk_format_hint=disk_format_hint,
                )
            except OSError as exc:
                diagnostics["filesystem_repair_error"] = str(exc)
                _finalize_recovery_diagnostics(diagnostics)
                raise FloppyRecoveryError(
                    f"Recovery could not inspect the copied floppy image: {exc}\n\n"
                    f"{diagnostics['human_report']}",
                    diagnostics=diagnostics,
                ) from exc
        try:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 72, 100, "Trying Yamaha/FAT repair before carving files...")
            prepare_yamaha_image(source_img, prepared)
            disk_format = _disk_format_for_image(prepared)
            listing = read_image_listing(prepared)
            if listing.entries:
                if diagnostics is not None:
                    diagnostics.update(_listing_recovery_kind_counts(listing))
                    diagnostics["filesystem_repair_succeeded"] = True
                    _finalize_recovery_diagnostics(diagnostics)
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
                    recovery_diagnostics=diagnostics,
                )
        except FloppyOperationCancelled as exc:
            attach_recovery_cancellation(exc)
            raise
        except Exception as exc:
            if diagnostics is not None:
                diagnostics["filesystem_repair_succeeded"] = False
                diagnostics["filesystem_repair_error"] = " ".join(str(exc).split())[:500]

        try:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(
                progress_callback,
                78,
                100,
                "Scanning raw image for recoverable songs...",
            )
        except FloppyOperationCancelled as exc:
            attach_recovery_cancellation(exc)
            raise
        if source_data is None:
            with open(source_img, "rb") as handle:
                source_data = handle.read()

        recovered_files = _recover_files_from_raw_image_bytes(
            source_data,
            disk_format_hint=disk_format_hint,
            diagnostics=diagnostics,
        )
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

        if diagnostics is not None:
            diagnostics.update(_recovery_file_kind_counts(recovered_files))
            if not recovered_files:
                _finalize_recovery_diagnostics(diagnostics)
                primary_message = _usb_recovery_no_data_message(diagnostics)
                raise FloppyRecoveryError(
                    f"{primary_message}\n\n{diagnostics['human_report']}",
                    diagnostics=diagnostics,
                )

        try:
            recovered_img, disk_format = _write_recovered_files_to_image(
                recovered_files,
                temp_dir,
                source_data,
                disk_format_hint=disk_format_hint,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            shutil.copy2(recovered_img, working_img)
        except FloppyOperationCancelled as exc:
            attach_recovery_cancellation(exc)
            raise
        except Exception as exc:
            if diagnostics is None:
                raise
            diagnostics["recovery_output_error"] = " ".join(str(exc).split())[:500]
            _finalize_recovery_diagnostics(diagnostics)
            raise FloppyRecoveryError(
                f"Recovery found identifiable data but could not create the editable recovery image: {exc}\n\n"
                f"{diagnostics['human_report']}",
                diagnostics=diagnostics,
            ) from exc
        note = (
            f"Recovery created an editable image copy from {len(recovered_files)} recovered file(s). "
            "Some names, order, or damaged song data may be missing."
        )
        if extra_note:
            note += f" {str(extra_note).strip()}"
        if diagnostics is not None:
            _finalize_recovery_diagnostics(diagnostics)
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
            recovery_diagnostics=diagnostics,
        )

    @property
    def mode_name(self):
        return "Floppy Disk" if self.source_kind.startswith("floppy") else "Image Mode"

    @classmethod
    def _from_pianodisc_system3_image(
        cls,
        source_path,
        source_ext,
        temp_dir,
        working_img_path,
        *,
        source_kind="image",
        source_name=None,
        gw_sector_reports=None,
    ):
        try:
            with open(working_img_path, "rb") as handle:
                conversion = pianodisc_system3.convert_pianodisc_system3_image(handle.read())
        except (OSError, pianodisc_system3.PianoDiscSystem3Error) as exc:
            raise FloppyImageError(f"Could not read PianoDisc System 3 image: {exc}") from exc

        virtual_files = {item.filename: item.data for item in conversion.files}
        return cls(
            source_path,
            source_ext,
            temp_dir,
            working_img_path,
            DiskFormat(
                "pianodisc.system3",
                "PianoDisc System 3 (read-only)",
                os.path.getsize(working_img_path),
            ),
            YamahaRepairResult(
                "PianoDisc System 3 songs were decoded to Standard MIDI files. "
                "The source image is read-only and was not modified.",
                False,
            ),
            source_kind=source_kind,
            source_name=source_name or os.path.basename(source_path),
            gw_sector_reports=gw_sector_reports,
            virtual_files=virtual_files,
            conversion_warnings=conversion.errors,
            read_only_format="pianodisc_system3",
        )

    @classmethod
    def _load_raw(cls, source_path, source_ext, temp_dir, progress_callback=None, cancel_callback=None):
        source_copy = os.path.join(temp_dir, "source.img")
        working_img = os.path.join(temp_dir, "working.img")
        _raise_if_cancelled(cancel_callback)
        _notify_progress(progress_callback, 1, 4, "Copying raw floppy image...")
        shutil.copy2(source_path, source_copy)
        try:
            with open(source_copy, "rb") as handle:
                is_pianodisc = pianodisc_system3.looks_like_pianodisc_system3_bytes(handle.read())
        except OSError:
            is_pianodisc = False
        if is_pianodisc:
            _notify_progress(progress_callback, 3, 4, "Decoding PianoDisc System 3 songs...")
            return cls._from_pianodisc_system3_image(
                source_path,
                source_ext,
                temp_dir,
                source_copy,
            )
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
            is_first_candidate = disk_format == candidate_formats[0]
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
                conversion_output = _gw_convert(
                    source_path,
                    candidate,
                    disk_format.key,
                    cancel_callback=cancel_callback,
                    allow_sector_failures=True,
                )
                conversion_sector_map = _parse_gw_sector_map(conversion_output, disk_format)
                try:
                    with open(candidate, "rb") as handle:
                        is_pianodisc = pianodisc_system3.looks_like_pianodisc_system3_bytes(
                            handle.read()
                        )
                except OSError:
                    is_pianodisc = False
                if is_pianodisc:
                    _notify_progress(
                        progress_callback,
                        3,
                        4,
                        "Decoding PianoDisc System 3 songs...",
                    )
                    return cls._from_pianodisc_system3_image(
                        source_path,
                        source_ext,
                        temp_dir,
                        candidate,
                        gw_sector_reports=_gw_sector_reports(
                            _gw_sector_report(
                                "convert",
                                conversion_sector_map,
                                title="Greaseweazle Conversion Sector Map",
                                summary=(
                                    f"Converted {os.path.basename(source_path)} as "
                                    "a PianoDisc System 3 disk."
                                ),
                                disk_format=disk_format,
                            )
                        ),
                    )
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
                try:
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
                except FloppyImageError as exc:
                    can_treat_blank_success_as_final = (
                        isinstance(disk_format_hint, DiskFormat)
                        or (str(source_ext or "").lower().lstrip(".") == "hfe" and is_first_candidate)
                    )
                    if (
                        can_treat_blank_success_as_final
                        and _converted_image_appears_blank_or_unformatted(candidate, disk_format, conversion_sector_map)
                    ):
                        filename = os.path.basename(source_path) or "This image"
                        raise BlankDiskImageError(
                            f"{filename} appears to be a blank or unformatted {disk_format.label} disk image. "
                            "No Yamaha/FAT directory or recoverable MIDI/E-SEQ data was found.",
                            disk_format=disk_format,
                            sector_map=conversion_sector_map,
                            source_path=source_path,
                        ) from exc
                    raise
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
            except BlankDiskImageError:
                raise
            except GreaseweazleConversionError as exc:
                can_treat_blank_failure_as_final = (
                    isinstance(disk_format_hint, DiskFormat)
                    or (str(source_ext or "").lower().lstrip(".") == "hfe" and is_first_candidate)
                )
                if (
                    can_treat_blank_failure_as_final
                    and _converted_image_appears_blank_or_unformatted(candidate, disk_format, exc.sector_map)
                ):
                    filename = os.path.basename(source_path) or "This image"
                    raise BlankDiskImageError(
                        f"{filename} appears to be a blank or unformatted {disk_format.label} disk image. "
                        "No Yamaha/FAT directory or recoverable MIDI/E-SEQ data was found.",
                        disk_format=disk_format,
                        sector_map=exc.sector_map,
                        source_path=source_path,
                    ) from exc
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
        if self.virtual_files:
            entries = [
                ImageEntry(
                    path=path,
                    size=len(data),
                    packed_size=len(data),
                )
                for path, data in self.virtual_files.items()
            ]
            return ImageListing(
                entries=entries,
                free_space=0,
                cluster_size=pianodisc_system3.SECTOR_SIZE,
            )
        return read_image_listing(self.working_img_path)

    def _run_mtools(self, args, message, cancel_callback=None):
        _run_command(args, message, cancel_callback=cancel_callback)

    def _extract_from_image(self, source_img, image_path, dest_path, cancel_callback=None):
        if os.path.exists(dest_path):
            os.remove(dest_path)
        _raise_if_cancelled(cancel_callback)
        try:
            data = _read_fat12_file_bytes(source_img, image_path)
        except FloppyImageError:
            mcopy = _require_command("mcopy")
            mcopy_dest_path, cleanup_dir = _mtools_host_destination_path(dest_path, image_path)
            try:
                self._run_mtools(
                    [mcopy, "-i", source_img, mtools_path(image_path), mcopy_dest_path],
                    f"Could not extract {image_path} from image",
                    cancel_callback=cancel_callback,
                )
                if mcopy_dest_path != dest_path:
                    shutil.copy2(mcopy_dest_path, dest_path)
            finally:
                if cleanup_dir:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)
            return

        with open(dest_path, "wb") as handle:
            handle.write(data)
        _raise_if_cancelled(cancel_callback)

    def extract_file(self, image_path):
        normalized = _normalize_image_path(image_path)
        cached = self._extracted_files.get(normalized)
        if cached and os.path.isfile(cached):
            return cached

        if self.virtual_files:
            data = self.virtual_files.get(normalized)
            if data is None:
                raise FloppyImageError(f"The decoded file was not found: {image_path}")
            filename = os.path.basename(normalized) or "decoded-file"
            dest_path = os.path.join(self.extracted_dir, f"{uuid.uuid4().hex}_{filename}")
            with open(dest_path, "wb") as handle:
                handle.write(data)
            self._extracted_files[normalized] = dest_path
            return dest_path

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
            if not _host_file_matches_eseq_variant(extracted_path, eseq_variant):
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
        for entry in listing.entries:
            _raise_if_cancelled(cancel_callback)
            # Updating the active catalog must not remove other payloads or
            # the opposite variant's catalog from an in-place save.
            if os.path.basename(entry.path).upper() != directory_filename:
                continue
            self._run_mtools(
                [mdel, "-i", target_img, mtools_path(entry.path)],
                f"Could not delete {entry.path} from image",
                cancel_callback=cancel_callback,
            )

        _run_mcopy_host_to_image(
            self._run_mtools,
            target_img,
            generated_path,
            directory_filename,
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
        *,
        clean_eseq_delivery=False,
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

        try:
            _raise_if_cancelled(cancel_callback)
            _notify_progress(progress_callback, 1, 4, "Applying pending changes to floppy image...")
            if generate_pianodir and clean_eseq_delivery:
                # Classify the final payload, including pending conversions, before
                # copying files or applying metadata edits. Only explicit delivery
                # exports prune unrelated payloads; commits preserve them.
                existing_paths = {entry.path for entry in read_image_listing(target_img).entries}
                excluded_paths = set()
                for image_path in sorted(existing_paths | additions.keys()):
                    _raise_if_cancelled(cancel_callback)
                    if image_path in deletes and image_path not in additions:
                        continue
                    if is_eseq_directory_path(image_path):
                        excluded_paths.add(image_path)
                        continue
                    source_path = additions.get(image_path) or replacements.get(image_path)
                    if source_path is None:
                        source_path = os.path.join(
                            self.extracted_dir,
                            f"{uuid.uuid4().hex}_{os.path.basename(_normalize_image_path(image_path))}",
                        )
                        self._extract_from_image(
                            target_img, image_path, source_path, cancel_callback=cancel_callback,
                        )
                    elif not os.path.isfile(source_path):
                        if image_path in additions:
                            raise FloppyImageError(f"File to add no longer exists: {source_path}")
                        raise FloppyImageError(f"Replacement file no longer exists: {source_path}")
                    if not _host_file_matches_eseq_variant(source_path, eseq_variant):
                        excluded_paths.add(image_path)

                deletes.update(existing_paths & excluded_paths)
                additions = {path: value for path, value in additions.items() if path not in excluded_paths}
                replacements = {path: value for path, value in replacements.items() if path not in excluded_paths}
                renames = {path: value for path, value in renames.items() if path not in excluded_paths}
                title_edits = {path: value for path, value in title_edits.items() if path not in excluded_paths}
                order_key_edits = {path: value for path, value in order_key_edits.items() if path not in excluded_paths}

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
                _run_mcopy_host_to_image(
                    self._run_mtools,
                    target_img,
                    patched_path,
                    image_path,
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
                _run_mcopy_host_to_image(
                    self._run_mtools,
                    target_img,
                    source_path,
                    image_path,
                    f"Could not write converted data for {image_path} into image",
                    cancel_callback=cancel_callback,
                )

            for source_path, target_path in sorted(renames.items(), key=lambda item: item[0].lower()):
                _raise_if_cancelled(cancel_callback)
                if source_path in deletes:
                    continue
                normalized_source = _normalize_image_path(source_path)
                normalized_target = _normalize_image_path(target_path)
                if normalized_source == normalized_target:
                    continue
                if normalized_source.lower() == normalized_target.lower():
                    directory = os.path.dirname(normalized_source).replace("\\", "/")
                    temp_name = f"APSR{uuid.uuid4().hex[:4].upper()}.TMP"
                    temp_path = f"{directory}/{temp_name}" if directory else temp_name
                    self._run_mtools(
                        [mren, "-i", target_img, mtools_path(source_path), mtools_path(temp_path)],
                        f"Could not stage case-only rename for {source_path} in image",
                        cancel_callback=cancel_callback,
                    )
                    self._run_mtools(
                        [mren, "-i", target_img, mtools_path(temp_path), mtools_path(target_path)],
                        f"Could not rename {source_path} in image",
                        cancel_callback=cancel_callback,
                    )
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
                _run_mcopy_host_to_image(
                    self._run_mtools,
                    target_img,
                    source_path,
                    image_path,
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
                _run_mcopy_host_to_image(
                    self._run_mtools,
                    target_img,
                    patched_path,
                    image_path,
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
            self.temp_dir,
            f".aps_image_{uuid.uuid4().hex}.{output_ext.lower().lstrip('.')}",
        )
        try:
            if output_ext.lower().lstrip(".") in RAW_IMAGE_EXTENSIONS:
                _notify_progress(progress_callback, 4, 5, "Writing raw floppy image...")
            else:
                _notify_progress(progress_callback, 4, 5, f"Converting floppy image to {output_ext.upper()}...")
            report = self._write_image_direct(source_img, temp_output, output_ext, cancel_callback=cancel_callback)
            _raise_if_cancelled(cancel_callback)
            _finish_temp_output(temp_output, output_path)
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
                "Use Disk > Write Current Image to Floppy... for disks with folders."
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
                "Use Disk > Write Current Image to Floppy... for disks with folders."
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
                _run_mcopy_host_to_image(
                    self._run_mtools,
                    drive_path,
                    extracted_path,
                    entry.path,
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
            clean_eseq_delivery=True,
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

    def export_to_images(
        self,
        output_path,
        output_ext,
        disk_format,
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
        if not isinstance(disk_format, DiskFormat):
            raise FloppyImageError("Invalid disk format for image export.")

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
            clean_eseq_delivery=True,
        )
        extract_dir = tempfile.mkdtemp(prefix="aps_repack_image_", dir=self.temp_dir)
        try:
            if disk_format.size_bytes == self.disk_format.size_bytes:
                self.write_image(
                    modified_img,
                    output_path,
                    output_ext,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
                return [output_path]

            listing = read_image_listing(modified_img)
            file_specs = []
            for index, entry in enumerate(listing.entries, start=1):
                _raise_if_cancelled(cancel_callback)
                if entry.directory:
                    raise FloppyImageError(
                        "Changing disk size for images with folders is not supported. "
                        "Export to the same disk size, or save the files to a folder first."
                    )
                extracted_path = os.path.join(
                    extract_dir,
                    f"{index:04d}_{_clean_ascii_temp_filename(entry.name)}",
                )
                self._extract_from_image(
                    modified_img,
                    entry.path,
                    extracted_path,
                    cancel_callback=cancel_callback,
                )
                file_specs.append(
                    {
                        "host_path": extracted_path,
                        "image_path": entry.path,
                        "display_name": entry.path,
                    }
                )

            reports = []
            output_paths = create_floppy_images_from_files(
                file_specs,
                output_path,
                output_ext,
                disk_format,
                progress_callback=progress_callback,
                sector_report_callback=reports.append,
            )
            self.latest_gw_sector_reports = _gw_sector_reports(*reports)
            return output_paths
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
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
        verify_after_write=False,
    ):
        self.last_write_verification = {"confidence": "not_written", "hardware_tested": False}
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
                temp_output = os.path.join(
                    self.temp_dir,
                    f".aps_image_{uuid.uuid4().hex}.{output_ext}",
                )
                if output_ext.lower().lstrip(".") in RAW_IMAGE_EXTENSIONS:
                    _notify_progress(progress_callback, 4, 5, "Saving raw floppy image...")
                else:
                    _notify_progress(progress_callback, 4, 5, f"Converting image back to {output_ext.upper()}...")
                report = self._write_image_direct(modified_img, temp_output, output_ext, cancel_callback=cancel_callback)
                reports = _gw_sector_reports(report)
                _raise_if_cancelled(cancel_callback)
                _finish_temp_output(temp_output, self.source_path)
            if self.source_kind.startswith("floppy"):
                self.last_write_verification = {"confidence": "written", "hardware_tested": False}
                if verify_after_write:
                    target = self.gw_source if self.source_kind == "floppy_gw" else (
                        self.drive_info or FloppyDriveInfo(self.source_path, self.disk_format.size_bytes)
                    )
                    self.last_write_verification = _verify_physical_floppy_contents(
                        modified_img, self.source_kind, target, self.disk_format,
                        progress_callback=progress_callback, cancel_callback=cancel_callback,
                    )
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
        verify_after_write=False,
        progress_callback=None,
        cancel_callback=None,
    ):
        self.last_write_verification = {"confidence": "not_written", "hardware_tested": False}
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
            clean_eseq_delivery=True,
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
                                "Windows would not allow a full image write to the floppy drive. "
                                f"The app tried copying files to the mounted drive instead, but that also failed: {fallback_exc}"
                            ) from fallback_exc
            elif file_level:
                raise FloppyImageError(
                    "File-level Save To Floppy requires a floppy drive. "
                    "Use Disk > Write Current Image to Floppy... for Greaseweazle writes."
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
            self.last_write_verification = {"confidence": "written", "hardware_tested": False}
            if verify_after_write:
                self.last_write_verification = _verify_physical_floppy_contents(
                    modified_img, target_kind, target, self.disk_format,
                    progress_callback=progress_callback, cancel_callback=cancel_callback,
                )
            _notify_progress(progress_callback, 5, 5, "Floppy write complete.")
            self.latest_gw_sector_reports = reports
        finally:
            if os.path.exists(modified_img):
                os.remove(modified_img)
