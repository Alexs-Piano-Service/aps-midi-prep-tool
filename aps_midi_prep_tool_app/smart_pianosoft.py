"""Read and adapt Yamaha Smart PianoSoft song and album MNG catalogs."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .midi_metadata import normalize_legacy_title_spacing, normalize_title_spacing


SMART_PIANOSOFT_SONG_CATALOG_NAME = "PSONG.MNG"
SMART_PIANOSOFT_DISK_CATALOG_NAME = "PDISK.MNG"
_CATALOG_HEADER_SIZE = 0x80
_CATALOG_RECORD_SIZE = 0xB0


@dataclass(frozen=True)
class SmartPianoSoftSong:
    """A catalog entry with its record in the canonical CRLF-based layout."""

    track_number: int
    filename: str
    title: str
    raw_record: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True)
class SmartPianoSoftMetadata:
    """Folder metadata whose catalog payloads use the CRLF-based layout."""

    disk_title: str = ""
    songs: tuple[SmartPianoSoftSong, ...] = ()
    song_catalog: bytes = field(default=b"", repr=False)
    disk_catalog: bytes = field(default=b"", repr=False)


def _canonical_catalog_bytes(data):
    """Restore fixed offsets when a text transfer removed catalog CR bytes.

    MNG catalogs use CRLF delimiters inside fixed-size headers and records.
    Some extracted collections contain LF-only copies. Identify those by the
    first header line before restoring missing CR bytes; normal catalogs may
    contain opaque fields and must otherwise remain byte-for-byte intact.
    """
    data = bytes(data)
    if (
        data[:14].upper() in {b"PSONG   MNG   ", b"PDISK   MNG   "}
        and data[14:15] == b"\n"
    ):
        return re.sub(rb"(?<!\r)\n", b"\r\n", data)
    return data


def parse_smart_pianosoft_disk_title(data):
    """Read the 64-byte album field shared by 114- and 128-byte PDISK files."""
    data = _canonical_catalog_bytes(data)
    if len(data) < 0x72:
        raise ValueError("PDISK.MNG is truncated.")
    if not data[:14].upper().startswith(b"PDISK   MNG"):
        raise ValueError("PDISK.MNG header not found.")
    if data[0x70:0x72] != b"\r\n":
        raise ValueError("PDISK.MNG title terminator not found.")
    title = data[0x30:0x70].split(b"\x00", 1)[0].decode("cp1252", errors="replace")
    return normalize_title_spacing(title)


def smart_pianosoft_metadata_from_directory(directory):
    """Read optional folder-local catalogs, tolerating absent or invalid files."""
    try:
        with os.scandir(directory) as entries:
            catalogs = sorted(
                (
                    entry for entry in entries
                    if entry.name.upper() in {
                        SMART_PIANOSOFT_SONG_CATALOG_NAME,
                        SMART_PIANOSOFT_DISK_CATALOG_NAME,
                    } and entry.is_file()
                ),
                key=lambda entry: (entry.name != entry.name.upper(), entry.name),
            )
    except OSError:
        return SmartPianoSoftMetadata()

    values = {}
    payloads = {}
    for entry in catalogs:
        catalog_name = entry.name.upper()
        if catalog_name in values:
            continue
        try:
            with open(entry.path, "rb") as handle:
                if catalog_name == SMART_PIANOSOFT_DISK_CATALOG_NAME:
                    payload = _canonical_catalog_bytes(handle.read())
                    values[catalog_name] = parse_smart_pianosoft_disk_title(payload)
                else:
                    payload = _canonical_catalog_bytes(
                        handle.read(_CATALOG_HEADER_SIZE + 999 * _CATALOG_RECORD_SIZE)
                    )
                    values[catalog_name] = parse_smart_pianosoft_song_catalog(payload)
                payloads[catalog_name] = payload
        except (OSError, ValueError):
            values[catalog_name] = (
                "" if catalog_name == SMART_PIANOSOFT_DISK_CATALOG_NAME else ()
            )
    return SmartPianoSoftMetadata(
        disk_title=values.get(SMART_PIANOSOFT_DISK_CATALOG_NAME, ""),
        songs=values.get(SMART_PIANOSOFT_SONG_CATALOG_NAME, ()),
        song_catalog=payloads.get(SMART_PIANOSOFT_SONG_CATALOG_NAME, b""),
        disk_catalog=payloads.get(SMART_PIANOSOFT_DISK_CATALOG_NAME, b""),
    )


def parse_smart_pianosoft_song_catalog(data):
    """Parse PSONG.MNG and return its songs in catalog order."""
    data = _canonical_catalog_bytes(data)
    if len(data) < _CATALOG_HEADER_SIZE:
        raise ValueError("PSONG.MNG is truncated.")
    if not data[:14].upper().startswith(b"PSONG   MNG"):
        raise ValueError("PSONG.MNG header not found.")

    count_match = re.match(rb"FILE(\d{3})", data[0x20:0x30].upper())
    if count_match is None:
        raise ValueError("PSONG.MNG song count not found.")
    record_count = int(count_match.group(1))
    required_size = _CATALOG_HEADER_SIZE + record_count * _CATALOG_RECORD_SIZE
    if required_size > len(data):
        raise ValueError("PSONG.MNG song records are truncated.")

    songs = []
    for record_index in range(record_count):
        start = _CATALOG_HEADER_SIZE + record_index * _CATALOG_RECORD_SIZE
        record = data[start:start + _CATALOG_RECORD_SIZE]
        stem = record[:8].decode("cp1252", errors="replace").strip(" \x00")
        extension = record[8:11].decode("cp1252", errors="replace").strip(" \x00")
        if not stem:
            continue
        filename = f"{stem}.{extension}" if extension else stem
        raw_title = record[0x10:0x30].decode("cp1252", errors="replace")
        songs.append(
            SmartPianoSoftSong(
                track_number=record_index + 1,
                filename=filename,
                title=normalize_legacy_title_spacing(raw_title),
                raw_record=record,
            )
        )
    return tuple(songs)


def build_smart_pianosoft_song_record(filename, title, *, source_record=b"", midi_format=0):
    """Adapt a source record to a prepared MIDI file, retaining opaque fields."""
    midi_format = int(midi_format)
    if midi_format not in {0, 1, 2}:
        raise ValueError("PSONG.MNG requires a valid Standard MIDI format type.")
    stem, extension = os.path.splitext(filename)
    extension = extension.lstrip(".")
    if not 1 <= len(stem) <= 8 or not 1 <= len(extension) <= 3:
        raise ValueError("PSONG.MNG requires DOS 8.3 filenames.")
    if source_record:
        if len(source_record) != _CATALOG_RECORD_SIZE:
            raise ValueError("PSONG.MNG song record has an invalid size.")
        record = bytearray(source_record)
    else:
        # A song absent from the source catalog still needs an entry when
        # automatic filling combines cataloged and uncataloged folders.
        record = bytearray((b" " * 14 + b"\r\n") * 11)
        record[0x30:0x40] = b"P.PLAYER      \r\n"
        record[0x40:0x50] = b"Ver1.01       \r\n"
        record[0x50:0x60] = b"A,I,P,M,SMF0,0\r\n"
        record[0x60:0x70] = b"L               "
    record[:11] = stem.encode("ascii").ljust(8, b" ") + extension.encode("ascii").ljust(3, b" ")
    record[0x10:0x30] = str(title).encode("cp1252", errors="replace")[:32].ljust(32, b" ")
    # Converting E-SEQ or preparing MIDI can change the SMF type. Preserve the
    # other source flags instead of replacing the entire playback descriptor.
    record[0x50:0x60] = re.sub(
        rb"SMF[012]", f"SMF{midi_format}".encode("ascii"), record[0x50:0x60],
    )
    return bytes(record)


def build_smart_pianosoft_song_catalog(template, records):
    """Build an image-local catalog in playback order without stale records."""
    template = _canonical_catalog_bytes(template)
    parse_smart_pianosoft_song_catalog(template)
    records = tuple(records)
    if len(records) > 999 or any(len(record) != _CATALOG_RECORD_SIZE for record in records):
        raise ValueError("PSONG.MNG has invalid song records.")
    header = bytearray(template[:_CATALOG_HEADER_SIZE])
    header[0x10:0x20] = f"MAX{len(records):03d}        \r\n".encode("ascii")
    header[0x20:0x30] = f"FILE{len(records):03d}       \r\n".encode("ascii")
    return bytes(header) + b"".join(records)


def update_smart_pianosoft_disk_title(data, title):
    """Update the album field in a catalog with canonical CRLF delimiters."""
    data = _canonical_catalog_bytes(data)
    parse_smart_pianosoft_disk_title(data)
    patched = bytearray(data)
    patched[0x30:0x70] = str(title).encode("cp1252", errors="replace")[:64].ljust(64, b" ")
    return bytes(patched)


def smart_pianosoft_catalog_by_filename(data):
    """Return a case-insensitive filename lookup for a PSONG.MNG payload."""
    return {
        song.filename.casefold(): song
        for song in parse_smart_pianosoft_song_catalog(data)
    }


def update_smart_pianosoft_song_catalog(data, title_updates):
    """Return PSONG.MNG bytes with titles replaced by source filename."""
    data = _canonical_catalog_bytes(data)
    songs = parse_smart_pianosoft_song_catalog(data)
    updates = {
        os.path.basename(str(filename or "")).casefold(): str(title or "")
        for filename, title in dict(title_updates or {}).items()
    }
    known_filenames = {song.filename.casefold() for song in songs}
    unknown = sorted(set(updates) - known_filenames)
    if unknown:
        raise ValueError(f"PSONG.MNG has no song record for {unknown[0]}.")

    patched = bytearray(data)
    for song in songs:
        new_title = updates.get(song.filename.casefold())
        if new_title is None:
            continue
        try:
            encoded_title = new_title.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError("Smart PianoSoft titles must use Windows-1252 characters.") from exc
        if len(encoded_title) > 32:
            raise ValueError("Smart PianoSoft titles must be 32 bytes or fewer.")
        record_start = _CATALOG_HEADER_SIZE + (song.track_number - 1) * _CATALOG_RECORD_SIZE
        patched[record_start + 0x10:record_start + 0x30] = encoded_title.ljust(32, b" ")
    return bytes(patched)


def update_smart_pianosoft_catalog_to_path(source_path, destination_path, title_updates):
    """Write a PSONG.MNG copy with the requested catalog title updates."""
    with open(source_path, "rb") as handle:
        payload = update_smart_pianosoft_song_catalog(handle.read(), title_updates)
    with open(destination_path, "wb") as handle:
        handle.write(payload)


def smart_pianosoft_catalog_from_session(session, entries):
    """Read PSONG.MNG from an image/floppy session, or return an empty lookup."""
    for entry in entries:
        if (
            os.path.basename(str(entry.path or "")).casefold()
            != SMART_PIANOSOFT_SONG_CATALOG_NAME.casefold()
        ):
            continue
        try:
            catalog_path = session.extract_file(entry.path)
            with open(catalog_path, "rb") as handle:
                return smart_pianosoft_catalog_by_filename(handle.read())
        except Exception:
            return {}
    return {}


def smart_pianosoft_disk_title_from_session(session, entries):
    """Read the album title from an image/floppy session's optional PDISK.MNG."""
    for entry in entries:
        if os.path.basename(str(entry.path or "")).upper() != SMART_PIANOSOFT_DISK_CATALOG_NAME:
            continue
        try:
            with open(session.extract_file(entry.path), "rb") as handle:
                return parse_smart_pianosoft_disk_title(handle.read(0x80))
        except Exception:
            return ""
    return ""
