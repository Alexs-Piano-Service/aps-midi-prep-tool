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


def extract_evt_files_from_image(image_path: Path, output_dir: Path) -> List[Path]:
    data = Path(image_path).read_bytes()
    geometry = infer_mdr_geometry(data)
    if geometry is None:
        raise ValueError("This image does not look like an Electone MDR disk")
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []
    for entry in parse_root_directory(data, geometry):
        if not EVT_FILE_RE.match(entry.name):
            continue
        target = output_dir / entry.name
        target.write_bytes(extract_file_from_image(data, geometry, entry))
        extracted.append(target)
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


def _safe_text(raw: str, fallback: str) -> str:
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def safe_filename(name: str, fallback: str = "electone") -> str:
    text = _safe_text(name, fallback)
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip(" ._")
    return text or fallback


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
    running_status: Optional[int] = None
    warning_limit = 24

    def warn(message: str) -> None:
        if len(warnings) < warning_limit:
            warnings.append(message)

    while pos < len(data):
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
            payload = data[pos:pos + data_len]
            if len(payload) < data_len or any(item >= 0x80 for item in payload):
                warn(f"Incomplete running-status MIDI event at offset 0x{pos:X}")
                pos += 1
                continue
            events.append((tick, bytes([running_status]) + payload))
            pos += data_len
            continue

        if 0x80 <= byte <= 0xEF:
            data_len = _channel_data_length(byte)
            payload = data[pos + 1:pos + 1 + data_len]
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
            end = pos + 1
            while end < len(data) and data[end] < 0x80:
                end += 1
            if end < len(data) and data[end] == 0xF7:
                payload = data[pos + 1:end + 1]
                pos = end + 1
            else:
                payload = data[pos + 1:end] + b"\xF7"
                warn(f"Closed unterminated SysEx event at offset 0x{pos:X} with a synthetic F7")
                pos = end
            events.append((tick, b"\xF0" + _vlq(len(payload)) + payload))
            continue

        if byte == 0xF1:
            if pos + 1 < len(data) and data[pos + 1] < 0x80:
                pos += 2
            else:
                pos += 1
            continue

        if byte == 0xF2:
            break

        if byte == 0xF3:
            if pos + 1 >= len(data):
                warn(f"Missing F3 delta-time byte at offset 0x{pos:X}")
                break
            tick += data[pos + 1]
            pos += 2
            continue

        if byte == 0xF4:
            if pos + 2 >= len(data):
                warn(f"Missing F4 delta-time bytes at offset 0x{pos:X}")
                break
            tick += data[pos + 1] + 128 * data[pos + 2]
            pos += 3
            continue

        if byte == 0xFE:
            if pos + 1 >= len(data):
                warn(f"Missing FE pseudo-event byte at offset 0x{pos:X}")
                break
            code = data[pos + 1]
            if code == 0x72:
                if pos + 3 >= len(data):
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

    if len(warnings) >= warning_limit:
        warnings.append("Additional parser warnings were suppressed.")
    return events, tick, warnings


def _track_name_event(name: str) -> bytes:
    encoded = _safe_text(name, "Electone").encode("latin1", errors="replace")[:80]
    return b"\xFF\x03" + _vlq(len(encoded)) + encoded


def _write_smf_type0(output_path: Path, events: Sequence[Tuple[int, bytes]], sequence_name: str) -> None:
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
    track.extend(_vlq(0))
    track.extend(b"\xFF\x2F\x00")

    output = bytearray()
    output.extend(b"MThd")
    output.extend(struct.pack(">IHHH", 6, 0, 1, MIDI_DIVISION))
    output.extend(b"MTrk")
    output.extend(struct.pack(">I", len(track)))
    output.extend(track)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(output))


def convert_one(input_path: Path, output_dir: Path, output_stem: Optional[str] = None) -> EvtConversionReport:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    data = input_path.read_bytes()
    if not looks_like_evt_bytes(data):
        raise ValueError("The file does not look like an Electone MDR EVT performance file")

    sequence_name = safe_filename(output_stem or input_path.stem, "electone")
    events, duration_ms, warnings = _parse_evt_events(data)
    output_path = output_dir / f"{sequence_name}.mid"
    counter = 2
    while output_path.exists():
        output_path = output_dir / f"{sequence_name}_{counter}.mid"
        counter += 1
    _write_smf_type0(output_path, events, sequence_name)
    return EvtConversionReport(
        input=str(input_path),
        output=str(output_path),
        sequence_name=sequence_name,
        events_written=len(events),
        duration_ms=duration_ms,
        warnings=warnings,
    )


def convert_many(evt_paths: Iterable[Path], output_dir: Path) -> List[EvtConversionReport]:
    reports: List[EvtConversionReport] = []
    for evt_path in evt_paths:
        reports.append(convert_one(Path(evt_path), output_dir))
    return reports


def convert_image_evt_files(image_path: Path, output_dir: Path) -> List[EvtConversionReport]:
    extracted_dir = Path(output_dir) / "_evt"
    evt_paths = extract_evt_files_from_image(Path(image_path), extracted_dir)
    midi_dir = Path(output_dir) / "midi"
    return convert_many(evt_paths, midi_dir)
