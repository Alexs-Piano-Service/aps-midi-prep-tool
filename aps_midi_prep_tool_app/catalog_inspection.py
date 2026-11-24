"""Read-only, lossless descriptions of Yamaha MIDI and E-SEQ catalogs."""

from dataclasses import dataclass
from pathlib import Path

from .eseq_pianodir import (
    PIANODIR_COUNT_OFFSET, PIANODIR_DISK_METADATA_OFFSET,
    PIANODIR_DISK_METADATA_SIZE, PIANODIR_HEADER,
    PIANODIR_SECONDARY_AGGREGATE_OFFSET, PIANODIR_TOTAL_DURATION_OFFSET,
    PIANODIR_TRACK_DURATION_OFFSET, PIANODIR_TRACK_SIZE,
    PIANODIR_TRACK_TYPE_OFFSET, PIANODIR_TRACK_WRITE_PROTECT_OFFSET,
    _pianodir_subset_template, decode_dos83_name,
    eseq_arrangement_type_label, parse_pianodir_metadata,
)
from .smart_pianosoft import (
    _canonical_catalog_bytes,
    parse_smart_pianosoft_disk_title,
    parse_smart_pianosoft_song_catalog,
)


CATALOG_NAMES = frozenset({"PDISK.MNG", "PSONG.MNG"})
# CATALOG_NAMES also controls MIDI catalog delivery and application. Keep the
# broader inspection set separate from those format-specific operations.
INSPECTION_CATALOG_NAMES = CATALOG_NAMES | {"PIANODIR.FIL"}
MAX_CATALOG_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class CatalogField:
    label: str
    offset: int
    size: int
    value: str
    translate_value: bool = False


@dataclass(frozen=True)
class PianodirSong:
    track_number: int
    filename: str
    title: str
    raw_record: bytes


@dataclass(frozen=True)
class CatalogInspection:
    kind: str
    title: str
    raw: bytes
    fields: tuple
    songs: tuple = ()
    normalized_line_endings: bool = False


def catalog_text(data):
    """Keep opaque byte values visible instead of treating them as metadata."""
    return "".join(
        chr(value) if 32 <= value < 127 or value >= 160
        else f"\\x{value:02X}"
        for value in bytes(data)
    ).strip()


def _field(label, data, start, end):
    return CatalogField(label, start, end - start,
                        catalog_text(data[start:end].rstrip(b" \x00\r\n")))


def _hex_field(label, data, start, end):
    return CatalogField(label, start, end - start, data[start:end].hex(" ").upper())


def _integer_field(label, data, start, size):
    return CatalogField(label, start, size, str(int.from_bytes(data[start:start + size], "little")))


def _inspect_pianodir(raw):
    records, _label = _pianodir_subset_template(raw)
    fields = [_hex_field("Header", raw, 0, len(PIANODIR_HEADER))]
    songs = tuple(PianodirSong(
        index + 1, decode_dos83_name(record[:11]),
        record[0x30:0x50].split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip(),
        record,
    ) for index, record in enumerate(records))
    metadata = parse_pianodir_metadata(raw)
    if len(raw) > len(PIANODIR_HEADER):
        label_offset = PIANODIR_DISK_METADATA_OFFSET
        label_size = PIANODIR_DISK_METADATA_SIZE
        fields.extend((
            _field("Disk label", raw, label_offset, label_offset + label_size),
            CatalogField("Catalog number", label_offset, label_size, metadata.catalog_number),
            CatalogField("Album title", label_offset, label_size, metadata.disk_title),
            _integer_field("Stored record count (songs + 1)", raw, PIANODIR_COUNT_OFFSET, 2),
            CatalogField("Declared records", PIANODIR_COUNT_OFFSET, 2, str(len(songs))),
            _integer_field("Total declared duration (ticks)", raw, PIANODIR_TOTAL_DURATION_OFFSET, 4),
            _hex_field("Secondary aggregate (raw)", raw,
                       PIANODIR_SECONDARY_AGGREGATE_OFFSET, PIANODIR_SECONDARY_AGGREGATE_OFFSET + 2),
        ))
        end = PIANODIR_COUNT_OFFSET + 2
        if end < len(raw):
            fields.append(CatalogField("Trailing bytes", end, len(raw) - end, str(len(raw) - end)))
    return CatalogInspection("PIANODIR.FIL", metadata.disk_title, raw, tuple(fields), songs)


def inspect_catalog(data):
    """Describe known fields; retain all original bytes for raw inspection."""
    raw = bytes(data)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("The catalog is too large to inspect.")
    # Binary PIANODIR bytes must never pass through MNG newline normalization.
    if raw.startswith(PIANODIR_HEADER) or raw[7:15] == b"PIANODIR":
        return _inspect_pianodir(raw)
    canonical = _canonical_catalog_bytes(raw)
    if canonical[:14].upper().startswith(b"PDISK   MNG"):
        title = parse_smart_pianosoft_disk_title(canonical)
        fields = [
            _field("Header", canonical, 0, 14),
            _field("Program field", canonical, 0x10, 0x1e),
            _field("Version field", canonical, 0x20, 0x2e),
            CatalogField("Album title", 0x30, 64, title),
        ]
        if len(canonical) > 0x72:
            fields.append(_field("Uninterpreted data", canonical, 0x72, len(canonical)))
        return CatalogInspection("PDISK.MNG", title, raw, tuple(fields),
                                 normalized_line_endings=raw != canonical)
    if canonical[:14].upper().startswith(b"PSONG   MNG"):
        songs = parse_smart_pianosoft_song_catalog(canonical)
        count = int(canonical[0x24:0x27])
        fields = [
            _field("Header", canonical, 0, 14),
            _field("Capacity field", canonical, 0x10, 0x1e),
            CatalogField("Declared records", 0x20, 14, str(count)),
        ]
        header_extra = canonical[0x30:0x80]
        if header_extra.strip(b" \x00\r\n"):
            fields.append(_field("Uninterpreted data", canonical, 0x30, 0x80))
        end = 0x80 + count * 0xb0
        if end < len(canonical):
            fields.append(CatalogField("Trailing bytes", end, len(canonical) - end,
                                       str(len(canonical) - end)))
        return CatalogInspection("PSONG.MNG", "", raw, tuple(fields), songs,
                                 normalized_line_endings=raw != canonical)
    raise ValueError("This is not a PDISK.MNG, PSONG.MNG, or PIANODIR.FIL catalog.")


def read_catalog(path):
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_CATALOG_BYTES + 1)
    return inspect_catalog(data)


def song_fields(song):
    """Known record text and opaque remainder, at absolute catalog offsets."""
    if isinstance(song, PianodirSong):
        return _pianodir_song_fields(song)
    record = song.raw_record
    fields = (
        CatalogField("Filename", 0, 11, song.filename),
        CatalogField("Title", 0x10, 32, song.title),
        _field("Program field", record, 0x30, 0x3e),
        _field("Version field", record, 0x40, 0x4e),
        _field("Playback descriptor", record, 0x50, 0x5e),
        _field("Uninterpreted data", record, 0x60, len(record)),
    )
    base = 0x80 + (song.track_number - 1) * 0xb0
    return tuple(CatalogField(f.label, base + f.offset, f.size, f.value) for f in fields)


def song_playback_descriptor(song):
    if isinstance(song, PianodirSong):
        return eseq_arrangement_type_label(song.raw_record[PIANODIR_TRACK_TYPE_OFFSET])
    return catalog_text(song.raw_record[0x50:0x5e])


def _pianodir_song_fields(song):
    record = song.raw_record
    fields = (
        CatalogField("Filename", 0, 11, song.filename),
        CatalogField("Title", 0x30, 32, song.title),
        _integer_field("Declared duration (ticks)", record, PIANODIR_TRACK_DURATION_OFFSET, 4),
        # Historical timing fields are album-specific bookkeeping, not
        # established leading/trailing silence durations.
        _hex_field("Timing bytes (album-specific)", record, 0x14, 0x1a),
        CatalogField("Write protection", PIANODIR_TRACK_WRITE_PROTECT_OFFSET, 1,
                     "Set" if record[PIANODIR_TRACK_WRITE_PROTECT_OFFSET] & 0x80 else "Not set", True),
        CatalogField("Arrangement/type code", PIANODIR_TRACK_TYPE_OFFSET, 1,
                     song_playback_descriptor(song), True),
        _hex_field("Uninterpreted data", record, 0x0b, 0x10),
        _hex_field("Uninterpreted data", record, 0x1a, 0x28),
        _hex_field("Uninterpreted data", record, 0x2a, 0x30),
    )
    base = len(PIANODIR_HEADER) + (song.track_number - 1) * PIANODIR_TRACK_SIZE
    return tuple(CatalogField(f.label, base + f.offset, f.size, f.value, f.translate_value) for f in fields)


def catalog_hex(data):
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        lines.append(f"{offset:08X}  {chunk.hex(' ').upper():47}  {ascii_text}")
    return "\n".join(lines)
