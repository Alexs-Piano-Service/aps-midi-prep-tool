"""Read-only, lossless descriptions of Yamaha PDISK/PSONG catalogs."""

from dataclasses import dataclass
from pathlib import Path

from .smart_pianosoft import (
    _canonical_catalog_bytes,
    parse_smart_pianosoft_disk_title,
    parse_smart_pianosoft_song_catalog,
)


CATALOG_NAMES = frozenset({"PDISK.MNG", "PSONG.MNG"})
MAX_CATALOG_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class CatalogField:
    label: str
    offset: int
    size: int
    value: str


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


def inspect_catalog(data):
    """Describe known fields; retain all original bytes for raw inspection."""
    raw = bytes(data)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("The catalog is too large to inspect.")
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
    raise ValueError("This is not a PDISK.MNG or PSONG.MNG catalog.")


def read_catalog(path):
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_CATALOG_BYTES + 1)
    return inspect_catalog(data)


def song_fields(song):
    """Known record text and opaque remainder, at absolute catalog offsets."""
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


def catalog_hex(data):
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        lines.append(f"{offset:08X}  {chunk.hex(' ').upper():47}  {ascii_text}")
    return "\n".join(lines)
