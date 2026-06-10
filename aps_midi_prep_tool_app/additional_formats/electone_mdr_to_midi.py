#!/usr/bin/env python3
"""
Best-effort Yamaha Electone MDR disk and EVT-to-MIDI support.

Electone MDR disks usually contain MDR_00.EVT/MDR_00.B00 file pairs. EVT
streams are MIDI-like performance event files with a 256-byte header, millisecond
delta opcodes, raw SysEx bytes, and ordinary MIDI channel messages.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


BYTES_PER_SECTOR = 512
EVT_HEADER_SIZE = 256
MIDI_DIVISION = 1000
MIDI_TEMPO_US_PER_QUARTER = 1_000_000
BAD_SECTOR_MARKER = b"-=[BAD SECTOR]=-"
BAD_SECTOR_TEXT = b"BAD SECTOR"

MDR_FILE_RE = re.compile(r"^MDR_[0-9A-Z]{2}\.(?:EVT|B00|R00|BAK)$", re.IGNORECASE)
EVT_FILE_RE = re.compile(r"^MDR_[0-9A-Z]{2}\.EVT$", re.IGNORECASE)


@dataclass(frozen=True)
class MdrGeometry:
    label: str
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entries: int
    total_sectors: int
    sectors_per_fat: int

    @property
    def root_dir_sectors(self) -> int:
        return int(math.ceil((self.root_entries * 32) / self.bytes_per_sector))

    @property
    def fat_offset(self) -> int:
        return self.reserved_sectors * self.bytes_per_sector

    @property
    def fat_size(self) -> int:
        return self.sectors_per_fat * self.bytes_per_sector

    @property
    def root_offset(self) -> int:
        return (self.reserved_sectors + self.num_fats * self.sectors_per_fat) * self.bytes_per_sector

    @property
    def root_size(self) -> int:
        return self.root_dir_sectors * self.bytes_per_sector

    @property
    def data_offset(self) -> int:
        return self.root_offset + self.root_size

    @property
    def cluster_size(self) -> int:
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def total_size(self) -> int:
        return self.total_sectors * self.bytes_per_sector


@dataclass(frozen=True)
class MdrFileEntry:
    name: str
    attr: int
    start_cluster: int
    size: int

    @property
    def display_name(self) -> str:
        return self.name


@dataclass
class EvtConversionReport:
    input: str
    output: str
    sequence_name: str
    events_written: int
    duration_ms: int
    warnings: List[str]
    instrument_summary: str = ""


MDR_720K_GEOMETRY = MdrGeometry(
    label="Electone MDR 720K",
    bytes_per_sector=512,
    sectors_per_cluster=2,
    reserved_sectors=1,
    num_fats=2,
    root_entries=112,
    total_sectors=1440,
    sectors_per_fat=3,
)

MDR_1440K_GEOMETRY = MdrGeometry(
    label="Electone MDR 1.44M",
    bytes_per_sector=512,
    sectors_per_cluster=1,
    reserved_sectors=1,
    num_fats=2,
    root_entries=224,
    total_sectors=2880,
    sectors_per_fat=9,
)

MDR_GEOMETRIES = (MDR_720K_GEOMETRY, MDR_1440K_GEOMETRY)


def _u16le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _decode_dos_name(raw_name: bytes) -> str:
    stem = raw_name[:8].decode("ascii", errors="replace").rstrip()
    ext = raw_name[8:11].decode("ascii", errors="replace").rstrip()
    stem = stem.strip()
    ext = ext.strip()
    if not stem:
        return ""
    return f"{stem}.{ext}" if ext else stem


def _boot_geometry(data: bytes) -> Optional[MdrGeometry]:
    if len(data) < BYTES_PER_SECTOR:
        return None
    sector0 = data[:BYTES_PER_SECTOR]
    bytes_per_sector = _u16le(sector0, 11)
    sectors_per_cluster = sector0[13]
    reserved_sectors = _u16le(sector0, 14)
    num_fats = sector0[16]
    root_entries = _u16le(sector0, 17)
    total_sectors = _u16le(sector0, 19) or int.from_bytes(sector0[32:36], "little")
    sectors_per_fat = _u16le(sector0, 22)
    for geometry in MDR_GEOMETRIES:
        if (
            bytes_per_sector == geometry.bytes_per_sector
            and sectors_per_cluster == geometry.sectors_per_cluster
            and reserved_sectors == geometry.reserved_sectors
            and num_fats == geometry.num_fats
            and root_entries == geometry.root_entries
            and total_sectors == geometry.total_sectors
            and sectors_per_fat == geometry.sectors_per_fat
        ):
            return geometry
    return None


def _candidate_geometries(data: bytes) -> List[MdrGeometry]:
    boot_geometry = _boot_geometry(data)
    candidates: List[MdrGeometry] = []
    if boot_geometry is not None:
        candidates.append(boot_geometry)
    exact = [geometry for geometry in MDR_GEOMETRIES if geometry.total_size == len(data)]
    for geometry in exact + list(MDR_GEOMETRIES):
        if geometry not in candidates:
            candidates.append(geometry)
    return candidates


def parse_root_directory(data: bytes, geometry: MdrGeometry) -> List[MdrFileEntry]:
    root = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    if len(root) != geometry.root_size:
        return []
    entries: List[MdrFileEntry] = []
    for pos in range(0, len(root), 32):
        entry = root[pos:pos + 32]
        if len(entry) < 32:
            break
        first = entry[0]
        attr = entry[11]
        if first == 0x00:
            break
        if first == 0xE5 or attr == 0x0F:
            continue
        if attr & 0x08 or attr & 0x10:
            continue
        name = _decode_dos_name(entry[:11])
        if not name:
            continue
        entries.append(
            MdrFileEntry(
                name=name,
                attr=attr,
                start_cluster=_u16le(entry, 26),
                size=int.from_bytes(entry[28:32], "little"),
            )
        )
    return entries


def root_directory_has_mdr_entries(root_dir: bytes) -> bool:
    for pos in range(0, len(root_dir), 32):
        entry = root_dir[pos:pos + 32]
        if len(entry) < 32:
            break
        first = entry[0]
        attr = entry[11]
        if first == 0x00:
            break
        if first == 0xE5 or attr == 0x0F or attr & 0x18:
            continue
        name = _decode_dos_name(entry[:11])
        if MDR_FILE_RE.match(name or ""):
            return True
    return False


def infer_mdr_geometry(data: bytes) -> Optional[MdrGeometry]:
    for geometry in _candidate_geometries(data):
        if len(data) < geometry.data_offset:
            continue
        entries = parse_root_directory(data, geometry)
        if any(MDR_FILE_RE.match(entry.name) for entry in entries):
            return geometry
    return None


def looks_like_mdr_image_bytes(data: bytes) -> bool:
    return infer_mdr_geometry(data) is not None


def _fat12_next_cluster(fat: bytes, cluster: int) -> int:
    index = cluster + (cluster // 2)
    if index + 1 >= len(fat):
        return 0xFFF
    if cluster & 1:
        return ((fat[index] >> 4) | (fat[index + 1] << 4)) & 0xFFF
    return (fat[index] | ((fat[index + 1] & 0x0F) << 8)) & 0xFFF


def _cluster_offset(geometry: MdrGeometry, cluster: int) -> int:
    return geometry.data_offset + ((cluster - 2) * geometry.cluster_size)


def _fat12_cluster_chain(fat: bytes, first_cluster: int, size: int, geometry: MdrGeometry) -> List[int]:
    if size <= 0 or first_cluster < 2:
        return []
    needed_clusters = int(math.ceil(size / geometry.cluster_size))
    clusters: List[int] = []
    seen = set()
    cluster = first_cluster
    while 2 <= cluster < 0xFF0 and cluster not in seen:
        clusters.append(cluster)
        seen.add(cluster)
        if len(clusters) >= needed_clusters:
            break
        next_cluster = _fat12_next_cluster(fat, cluster)
        if next_cluster >= 0xFF8:
            break
        if next_cluster == 0xFF7 or next_cluster < 2:
            break
        cluster = next_cluster
    if len(clusters) < needed_clusters:
        return []
    return clusters[:needed_clusters]


def _read_clusters(data: bytes, geometry: MdrGeometry, clusters: Sequence[int], size: int) -> bytes:
    output = bytearray()
    for cluster in clusters:
        offset = _cluster_offset(geometry, cluster)
        end = offset + geometry.cluster_size
        if offset < geometry.data_offset or end > len(data):
            return b""
        output.extend(data[offset:end])
        if len(output) >= size:
            break
    return bytes(output[:size])


def extract_file_from_image(data: bytes, geometry: MdrGeometry, entry: MdrFileEntry) -> bytes:
    if entry.size <= 0:
        return b""
    fat = data[geometry.fat_offset:geometry.fat_offset + geometry.fat_size]
    clusters = _fat12_cluster_chain(fat, entry.start_cluster, entry.size, geometry)
    if clusters:
        payload = _read_clusters(data, geometry, clusters, entry.size)
        if len(payload) == entry.size:
            return payload

    offset = _cluster_offset(geometry, entry.start_cluster)
    end = offset + entry.size
    if offset < geometry.data_offset or end > len(data):
        raise ValueError(f"{entry.name} points outside the MDR image data area")
    return data[offset:end]


def extract_evt_files_from_image(
    image_path: Path,
    output_dir: Path,
    *,
    include_registration_files: bool = False,
) -> List[Path]:
    data = Path(image_path).read_bytes()
    geometry = infer_mdr_geometry(data)
    if geometry is None:
        raise ValueError("This image does not look like an Electone MDR disk")
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []
    entries = parse_root_directory(data, geometry)
    entries_by_name = {entry.name.upper(): entry for entry in entries}
    for entry in entries:
        if not EVT_FILE_RE.match(entry.name):
            continue
        target = output_dir / entry.name
        target.write_bytes(extract_file_from_image(data, geometry, entry))
        extracted.append(target)
        if not include_registration_files:
            continue
        stem = Path(entry.name).with_suffix("").name
        for suffix in (".R00", ".B00"):
            registration_entry = entries_by_name.get(f"{stem}{suffix}".upper())
            if registration_entry is None:
                continue
            registration_target = output_dir / registration_entry.name
            registration_target.write_bytes(extract_file_from_image(data, geometry, registration_entry))
            break
    return extracted


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    buffer = value & 0x7F
    value >>= 7
    while value:
        buffer <<= 8
        buffer |= (value & 0x7F) | 0x80
        value >>= 7
    output = bytearray()
    while True:
        output.append(buffer & 0xFF)
        if buffer & 0x80:
            buffer >>= 8
        else:
            break
    return bytes(output)


def _read_vlq(data: bytes, offset: int) -> Tuple[int, int]:
    value = 0
    pos = offset
    for _ in range(4):
        if pos >= len(data):
            raise ValueError("Unexpected end of variable-length value")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, pos
    raise ValueError("Variable-length value is too long")


def _safe_text(raw: str, fallback: str) -> str:
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def safe_filename(name: str, fallback: str = "electone") -> str:
    text = _safe_text(name, fallback)
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip(" ._")
    return text or fallback


def _midi_text_event(text: str) -> bytes:
    encoded = _safe_text(text, "Electone registration approximation").encode(
        "latin1",
        errors="replace",
    )[:240]
    return b"\xFF\x01" + _vlq(len(encoded)) + encoded


def _decode_smf_sysex_payload(raw: bytes) -> Optional[bytes]:
    if not raw or raw[0] != 0xF0:
        return None
    try:
        length, pos = _read_vlq(raw, 1)
    except ValueError:
        return None
    payload = raw[pos:pos + length]
    if len(payload) != length:
        return None
    return payload


def _current_registration_blob_from_payload(payload: bytes) -> Optional[bytes]:
    if not payload or not payload.endswith(b"\xF7"):
        return None
    if payload.startswith(b"\x43\x70\x75\x42") or payload.startswith(b"\x43\x70\x78\x42"):
        blob = payload[4:-1]
        return blob if len(blob) >= 32 else None
    return None


def _evt_current_registration_blobs(events: Sequence[Tuple[int, bytes]]) -> List[Tuple[int, bytes]]:
    blobs: List[Tuple[int, bytes]] = []
    seen = set()
    for tick, raw in events:
        payload = _decode_smf_sysex_payload(raw)
        blob = _current_registration_blob_from_payload(payload or b"")
        if blob is None:
            continue
        key = (tick, blob)
        if key in seen:
            continue
        seen.add(key)
        blobs.append((tick, blob))
    return blobs


def _candidate_registration_paths(evt_path: Path) -> List[Path]:
    stem = evt_path.with_suffix("")
    candidates = []
    for suffix in (".R00", ".B00", ".r00", ".b00"):
        candidates.append(stem.with_suffix(suffix))
    return candidates


def _find_paired_registration_path(evt_path: Path) -> Optional[Path]:
    for candidate in _candidate_registration_paths(evt_path):
        if candidate.is_file():
            return candidate
    return None


def _find_blob_offset(container: bytes, blob: bytes) -> Optional[int]:
    if not container or not blob:
        return None
    for trim in range(0, min(8, len(blob) - 24) + 1):
        candidate = blob[:len(blob) - trim] if trim else blob
        offset = container.find(candidate)
        if offset >= 0:
            return offset
    return None


def bad_sector_marker_offset(data: bytes, start: int = 0) -> Optional[int]:
    if not data:
        return None
    start = max(0, int(start or 0))
    marker_offset = data.find(BAD_SECTOR_MARKER, start)
    if marker_offset >= 0:
        return marker_offset
    text_offset = data.find(BAD_SECTOR_TEXT, start)
    if text_offset >= 3 and data[text_offset - 3:text_offset] == b"-=[":
        return text_offset - 3
    return None


_ELECTONE_UPPER_LOWER_GM = {
    0x00: ("Strings", 48),
    0x01: ("Brass", 61),
    0x02: ("Clarinet", 71),
    0x03: ("Saxophone", 66),
    0x04: ("Choir", 52),
    0x05: ("Harmonica", 22),
    0x06: ("Drawbar Organ", 16),
    0x07: ("Acoustic Piano", 0),
    0x08: ("Guitar", 24),
    0x09: ("Vibraphone", 11),
    0x0A: ("Synth Pad", 88),
    0x0B: ("Tutti/Strings", 48),
    0x0C: ("Piano", 0),
    0x0D: ("Organ", 16),
}

_ELECTONE_PEDAL_GM = {
    0x00: ("Acoustic Bass", 32),
    0x01: ("Tuba", 58),
    0x02: ("Electric Bass", 33),
    0x03: ("Organ Bass", 16),
    0x04: ("Synth Bass", 38),
}


def _selector_value(blob: bytes, offsets: Sequence[int]) -> Optional[int]:
    for offset in offsets:
        if 0 <= offset < len(blob):
            value = blob[offset]
            if 0 <= value <= 0x0D:
                return value
    return None


def _program_for_selector(value: Optional[int], role: str) -> Tuple[str, int]:
    if role == "pedal":
        if value in _ELECTONE_PEDAL_GM:
            return _ELECTONE_PEDAL_GM[value]
        return "Acoustic Bass", 32
    if value in _ELECTONE_UPPER_LOWER_GM:
        return _ELECTONE_UPPER_LOWER_GM[value]
    if role == "lead":
        return "Trumpet", 56
    return "Drawbar Organ", 16


def _infer_registration_programs(blob: bytes, note_channels: set[int]) -> dict[int, Tuple[str, int]]:
    programs: dict[int, Tuple[str, int]] = {}
    if 0 in note_channels:
        programs[0] = _program_for_selector(_selector_value(blob, (0,)), "upper")
    if 1 in note_channels:
        programs[1] = _program_for_selector(_selector_value(blob, (20, 27, 42)), "lower")
    if 2 in note_channels:
        programs[2] = _program_for_selector(_selector_value(blob, (42, 49, 56)), "pedal")
    if 3 in note_channels:
        programs[3] = _program_for_selector(_selector_value(blob, (56, 64, 74)), "lead")
    return programs


def _note_channels(events: Sequence[Tuple[int, bytes]]) -> set[int]:
    channels: set[int] = set()
    for _tick, raw in events:
        if len(raw) >= 3 and 0x90 <= raw[0] <= 0x9F and raw[2] > 0:
            channels.add(raw[0] & 0x0F)
    return channels


def _registration_change_ticks(events: Sequence[Tuple[int, bytes]]) -> List[int]:
    ticks = {0}
    for tick, raw in events:
        if len(raw) >= 2 and 0xC0 <= raw[0] <= 0xCF:
            ticks.add(max(0, int(tick)))
    return sorted(ticks)


def _approximate_registration_events(
    events: Sequence[Tuple[int, bytes]],
    registration_data: Optional[bytes],
) -> Tuple[List[Tuple[int, bytes]], str, List[str]]:
    warnings: List[str] = []
    note_channels = _note_channels(events)
    if not note_channels:
        return [], "", warnings

    registration_blobs = _evt_current_registration_blobs(events)
    if not registration_blobs:
        warnings.append("No Electone current-registration SysEx was found for GM instrument approximation.")
        return [], "", warnings

    first_blob = registration_blobs[0][1]
    registration_offset = _find_blob_offset(registration_data or b"", first_blob)

    programs = _infer_registration_programs(first_blob, note_channels)
    if not programs:
        return [], "", warnings

    inferred_events: List[Tuple[int, bytes]] = []
    summary_parts = []
    for channel, (name, program) in sorted(programs.items()):
        summary_parts.append(f"ch{channel + 1} {name}")

    source = "EVT registration"
    if registration_offset is not None:
        source = f"R00/B00 match @0x{registration_offset:X}"
    summary = f"Approximate Electone GM instruments from {source}: " + ", ".join(summary_parts)
    inferred_events.append((0, _midi_text_event(summary)))

    for tick in _registration_change_ticks(events):
        for channel, (_name, program) in sorted(programs.items()):
            inferred_events.append((tick, bytes([0xC0 | channel, program & 0x7F])))
    return inferred_events, summary, warnings


def looks_like_evt_bytes(data: bytes) -> bool:
    if len(data) <= EVT_HEADER_SIZE:
        return False
    if b"COM-ESEQ" in data[:EVT_HEADER_SIZE]:
        return True
    first = data[EVT_HEADER_SIZE]
    return first in {0xF0, 0xF1, 0xF3, 0xF4, 0xFE} or 0x80 <= first <= 0xEF


def is_evt_path(path: str | Path) -> bool:
    path = Path(path)
    if path.suffix.lower() != ".evt" or not path.is_file():
        return False
    try:
        return looks_like_evt_bytes(path.read_bytes())
    except OSError:
        return False


def _channel_data_length(status: int) -> int:
    high = status & 0xF0
    if high in (0xC0, 0xD0):
        return 1
    if 0x80 <= high <= 0xE0:
        return 2
    return 0


def _parse_evt_events(data: bytes) -> Tuple[List[Tuple[int, bytes]], int, List[str]]:
    events: List[Tuple[int, bytes]] = []
    warnings: List[str] = []
    tick = 0
    pos = min(EVT_HEADER_SIZE, len(data))
    bad_sector_offset = bad_sector_marker_offset(data, pos)
    parse_end = bad_sector_offset if bad_sector_offset is not None else len(data)
    running_status: Optional[int] = None
    stopped_at_bad_sector = False
    warning_limit = 24

    def warn(message: str) -> None:
        if len(warnings) < warning_limit:
            warnings.append(message)

    def stop_if_bad_sector_truncates(required_end: int) -> bool:
        nonlocal pos, stopped_at_bad_sector
        if bad_sector_offset is None or required_end <= parse_end:
            return False
        pos = parse_end
        stopped_at_bad_sector = True
        return True

    while pos < parse_end:
        byte = data[pos]

        if byte == 0x00:
            pos += 1
            continue

        if byte < 0x80:
            if running_status is None:
                warn(f"Skipped data byte 0x{byte:02X} at offset 0x{pos:X} with no running status")
                pos += 1
                continue
            data_len = _channel_data_length(running_status)
            if stop_if_bad_sector_truncates(pos + data_len):
                break
            payload = data[pos:min(pos + data_len, parse_end)]
            if len(payload) < data_len or any(item >= 0x80 for item in payload):
                warn(f"Incomplete running-status MIDI event at offset 0x{pos:X}")
                pos += 1
                continue
            events.append((tick, bytes([running_status]) + payload))
            pos += data_len
            continue

        if 0x80 <= byte <= 0xEF:
            data_len = _channel_data_length(byte)
            if stop_if_bad_sector_truncates(pos + 1 + data_len):
                break
            payload = data[pos + 1:min(pos + 1 + data_len, parse_end)]
            if len(payload) < data_len:
                warn(f"Incomplete MIDI channel event at offset 0x{pos:X}")
                break
            if any(item >= 0x80 for item in payload):
                warn(f"Skipped malformed MIDI channel event at offset 0x{pos:X}")
                pos += 1
                continue
            events.append((tick, bytes([byte]) + payload))
            running_status = byte
            pos += 1 + data_len
            continue

        running_status = None

        if byte == 0xF0:
            sysex_end = pos + 1
            while sysex_end < parse_end and data[sysex_end] < 0x80:
                sysex_end += 1
            if sysex_end < parse_end and data[sysex_end] == 0xF7:
                payload = data[pos + 1:sysex_end + 1]
                pos = sysex_end + 1
            elif sysex_end >= parse_end and bad_sector_offset is not None:
                stopped_at_bad_sector = True
                pos = parse_end
                break
            else:
                payload = data[pos + 1:sysex_end] + b"\xF7"
                warn(f"Closed unterminated SysEx event at offset 0x{pos:X} with a synthetic F7")
                pos = sysex_end
            events.append((tick, b"\xF0" + _vlq(len(payload)) + payload))
            continue

        if byte == 0xF1:
            if pos + 1 < parse_end and data[pos + 1] < 0x80:
                pos += 2
            else:
                pos += 1
            continue

        if byte == 0xF2:
            break

        if byte == 0xF3:
            if stop_if_bad_sector_truncates(pos + 2):
                break
            if pos + 1 >= parse_end:
                warn(f"Missing F3 delta-time byte at offset 0x{pos:X}")
                break
            tick += data[pos + 1]
            pos += 2
            continue

        if byte == 0xF4:
            if stop_if_bad_sector_truncates(pos + 3):
                break
            if pos + 2 >= parse_end:
                warn(f"Missing F4 delta-time bytes at offset 0x{pos:X}")
                break
            tick += data[pos + 1] + 128 * data[pos + 2]
            pos += 3
            continue

        if byte == 0xFE:
            if stop_if_bad_sector_truncates(pos + 2):
                break
            if pos + 1 >= parse_end:
                warn(f"Missing FE pseudo-event byte at offset 0x{pos:X}")
                break
            code = data[pos + 1]
            if code == 0x72:
                if stop_if_bad_sector_truncates(pos + 4):
                    break
                if pos + 3 >= parse_end:
                    warn(f"Truncated FE 72 pseudo-event at offset 0x{pos:X}")
                    break
                pos += 4
            else:
                if code not in {0x78, 0x7A, 0x7B, 0x7C, 0xF8}:
                    warn(f"Skipped unknown FE pseudo-event 0x{code:02X} at offset 0x{pos:X}")
                pos += 2
            continue

        if byte in {0xF7, 0xF8, 0xFA, 0xFB, 0xFC, 0xFF}:
            pos += 1
            continue

        warn(f"Skipped unknown EVT opcode 0x{byte:02X} at offset 0x{pos:X}")
        pos += 1

    if bad_sector_offset is not None and (stopped_at_bad_sector or pos >= parse_end):
        warnings.append(
            f"Unreadable sector marker found at offset 0x{bad_sector_offset:X}; "
            "conversion used events before that point."
        )
    if len(warnings) >= warning_limit:
        warnings.append("Additional parser warnings were suppressed.")
    return events, tick, warnings


def _track_name_event(name: str) -> bytes:
    encoded = _safe_text(name, "Electone").encode("latin1", errors="replace")[:80]
    return b"\xFF\x03" + _vlq(len(encoded)) + encoded


def _write_smf_type0(
    output_path: Path,
    events: Sequence[Tuple[int, bytes]],
    sequence_name: str,
    end_tick: int = 0,
) -> None:
    ordered_events = sorted(enumerate(events), key=lambda item: (item[1][0], item[0]))
    track = bytearray()
    last_tick = 0
    initial_events = [
        b"\xFF\x51\x03" + MIDI_TEMPO_US_PER_QUARTER.to_bytes(3, "big"),
        _track_name_event(sequence_name),
    ]
    for event in initial_events:
        track.extend(_vlq(0))
        track.extend(event)
    for _index, (tick, event) in ordered_events:
        tick = max(0, int(tick))
        track.extend(_vlq(tick - last_tick))
        track.extend(event)
        last_tick = tick
    track.extend(_vlq(max(0, int(end_tick or 0) - last_tick)))
    track.extend(b"\xFF\x2F\x00")

    output = bytearray()
    output.extend(b"MThd")
    output.extend(struct.pack(">IHHH", 6, 0, 1, MIDI_DIVISION))
    output.extend(b"MTrk")
    output.extend(struct.pack(">I", len(track)))
    output.extend(track)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(output))


def convert_one(
    input_path: Path,
    output_dir: Path,
    output_stem: Optional[str] = None,
    *,
    registration_path: Optional[Path] = None,
    approximate_instruments: bool = False,
) -> EvtConversionReport:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    data = input_path.read_bytes()
    if not looks_like_evt_bytes(data):
        bad_offset = bad_sector_marker_offset(data)
        if bad_offset is not None and bad_offset < EVT_HEADER_SIZE:
            raise ValueError(
                f"Unreadable sector data starts at offset 0x{bad_offset:X}; "
                "the EVT header could not be recovered"
            )
        raise ValueError("The file does not look like an Electone MDR EVT performance file")

    sequence_name = safe_filename(output_stem or input_path.stem, "electone")
    events, duration_ms, warnings = _parse_evt_events(data)
    instrument_summary = ""
    if approximate_instruments:
        registration_data = None
        resolved_registration_path = (
            Path(registration_path)
            if registration_path
            else _find_paired_registration_path(input_path)
        )
        has_note_events = bool(_note_channels(events))
        if resolved_registration_path is not None:
            try:
                registration_data = resolved_registration_path.read_bytes()
            except OSError as exc:
                if has_note_events:
                    warnings.append(
                        f"Could not read paired registration file {resolved_registration_path.name}: {exc}"
                    )
        elif has_note_events:
            warnings.append("No paired R00/B00 registration file was found for GM instrument approximation.")
        inferred_events, instrument_summary, instrument_warnings = _approximate_registration_events(
            events,
            registration_data,
        )
        events = list(events) + inferred_events
        warnings.extend(instrument_warnings)
    output_path = output_dir / f"{sequence_name}.mid"
    counter = 2
    while output_path.exists():
        output_path = output_dir / f"{sequence_name}_{counter}.mid"
        counter += 1
    _write_smf_type0(output_path, events, sequence_name, end_tick=duration_ms)
    return EvtConversionReport(
        input=str(input_path),
        output=str(output_path),
        sequence_name=sequence_name,
        events_written=len(events),
        duration_ms=duration_ms,
        warnings=warnings,
        instrument_summary=instrument_summary,
    )


def convert_many(
    evt_paths: Iterable[Path],
    output_dir: Path,
    *,
    approximate_instruments: bool = False,
) -> List[EvtConversionReport]:
    reports: List[EvtConversionReport] = []
    for evt_path in evt_paths:
        reports.append(
            convert_one(
                Path(evt_path),
                output_dir,
                approximate_instruments=approximate_instruments,
            )
        )
    return reports


def convert_image_evt_files(
    image_path: Path,
    output_dir: Path,
    *,
    approximate_instruments: bool = False,
) -> List[EvtConversionReport]:
    extracted_dir = Path(output_dir) / "_evt"
    evt_paths = extract_evt_files_from_image(
        Path(image_path),
        extracted_dir,
        include_registration_files=approximate_instruments,
    )
    midi_dir = Path(output_dir) / "midi"
    return convert_many(evt_paths, midi_dir, approximate_instruments=approximate_instruments)
