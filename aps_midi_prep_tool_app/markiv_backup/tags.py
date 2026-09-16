# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Conservative, bounded readers for metadata found on Mark IV music media.

PDISK/PSONG layouts are based on the connected Mark IV's Ver1.01DMV0.54
files. MIDI names come only from the first track of an SMF 0 or 1 file;
instrument names in other tracks are deliberately ignored. Unknown formats
(including undocumented E-SEQ headers) retain their filesystem names.
"""

from __future__ import annotations

from pathlib import Path
import re
import struct
from typing import BinaryIO
import zipfile

from .paths import is_link

_TEXT_LIMIT = 1024 * 1024
_MIDI_LIMIT = 256 * 1024
_SEQUENCE_EXTENSIONS = {".mid", ".midi", ".fil", ".mda", ".esq", ".seq", ".eseq", ".e-seq"}
_AUDIO_EXTENSIONS = {".wav", ".mp3"}
_GENERIC_NAMES = {
    "piano", "acoustic grand piano", "grand piano", "melody", "conductor",
    "tempo", "tempo track", "untitled", "sequence", "drums", "bass",
}


def _text(data: bytes) -> str:
    """Decode common Yamaha/PC encodings and normalize display whitespace."""
    for encoding in ("utf-8", "cp932", "cp1252", "latin-1"):
        try:
            value = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return " ".join(value.replace("\x00", " ").split())


def _small_file(path: Path) -> bytes:
    if is_link(path):
        return b""
    with path.open("rb") as stream:
        data = stream.read(_TEXT_LIMIT + 1)
    return data if len(data) <= _TEXT_LIMIT else b""


def _vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if offset >= len(data):
            raise ValueError("Truncated MIDI quantity")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 127)
        if byte < 128:
            return value, offset
    raise ValueError("Oversized MIDI quantity")


def _clean_sequence_title(data: bytes) -> str | None:
    raw = ""
    # Demonstration MIDIs on this drive put the title in quotes, followed by
    # several spaces and advertising/instrumentation text. Keep the title.
    for encoding in ("utf-8", "cp932", "cp1252", "latin-1"):
        try:
            raw = data.decode(encoding).strip(" \t\r\n\x00")
            break
        except UnicodeDecodeError:
            continue
    match = re.match(r'^"(.*?)"(?:\s{2,}|\s+-\s+SmartKey|\s*/|\s*$)', raw or "", re.S)
    value = " ".join((match.group(1) if match else (raw or "")).split())
    if not value or len(value) > 512 or any(ord(ch) < 32 for ch in value):
        return None
    if value.casefold() in _GENERIC_NAMES or re.fullmatch(r"(?:track|channel|part)\s*\d+", value, re.I):
        return None
    return value


def _midi_stream_title(stream: BinaryIO) -> str | None:
    header = stream.read(14)
    if len(header) != 14 or header[:4] != b"MThd":
        return None
    header_size, kind, track_count, _division = struct.unpack(">IHHH", header[4:])
    if not 6 <= header_size <= 1024 or kind not in (0, 1) or not track_count:
        return None
    if len(stream.read(header_size - 6)) != header_size - 6:
        return None
    chunk = stream.read(8)
    if len(chunk) != 8 or chunk[:4] != b"MTrk":
        return None
    size = struct.unpack(">I", chunk[4:])[0]
    data = stream.read(min(size, _MIDI_LIMIT))
    offset = 0
    running_status = 0
    while offset < len(data):
        _delta, offset = _vlq(data, offset)
        if offset >= len(data):
            return None
        status = data[offset]
        if status >= 128:
            offset += 1
            if status < 240:
                running_status = status
        elif running_status:
            status = running_status
        else:
            return None
        if status == 255:
            if offset >= len(data):
                return None
            meta = data[offset]
            offset += 1
            length, offset = _vlq(data, offset)
            if offset + length > len(data):
                return None
            if meta == 3:
                # The first name alone is the sequence name in the first track.
                return _clean_sequence_title(data[offset:offset + length])
            if meta == 47:
                return None
            offset += length
        elif status in (240, 247):
            running_status = 0
            length, offset = _vlq(data, offset)
            offset += length
        elif 128 <= status < 240:
            length = 1 if status >> 4 in (12, 13) else 2
            if offset + length > len(data) or any(b >= 128 for b in data[offset:offset + length]):
                return None
            offset += length
        else:
            return None
    return None


def read_music_title(path: Path) -> str | None:
    """Read an SMF sequence title, or a single MIDI inside a PSPG ZIP.

    Audio payloads are never loaded. PSPG files are inspected without extracting
    anything; callers must preserve those packages intact so SMIL references
    continue to resolve. Invalid/unreadable metadata is simply unavailable.
    """
    path = Path(path)
    if is_link(path):
        return None
    try:
        if path.suffix.lower() == ".pspg":
            with zipfile.ZipFile(path) as package:
                members = package.infolist()
                if len(members) > 10000:
                    return None
                midis = [item for item in members if Path(item.filename).suffix.lower() in {".mid", ".midi"}]
                if len(midis) != 1 or midis[0].flag_bits & 1:
                    return None
                with package.open(midis[0]) as stream:
                    return _midi_stream_title(stream)
        if path.suffix.lower() not in _SEQUENCE_EXTENSIONS:
            return None
        with path.open("rb") as stream:
            return _midi_stream_title(stream)
    except (OSError, ValueError, EOFError, RuntimeError, struct.error, zipfile.BadZipFile, NotImplementedError):
        return None


def _management_tracks(data: bytes) -> dict[str, dict]:
    lines = data.splitlines()
    if len(lines) < 4 or lines[0][:11] != b"PSONG   MNG":
        return {}
    count_match = re.match(rb"FILE(\d{3})", lines[2])
    if not count_match:
        return {}
    result = {}
    for number in range(1, int(count_match.group(1)) + 1):
        index = 4 + (number - 1) * 8
        if index + 2 >= len(lines):
            break
        filename = lines[index]
        if len(filename) != 14 or filename[11:].strip() or not lines[index + 2].startswith(b"Ver"):
            continue
        stem, extension = filename[:8].strip(), filename[8:11].strip()
        if not stem or not extension or any(ch in stem + extension for ch in b"/\\\x00"):
            continue
        name = _text(stem) + "." + _text(extension)
        # Two 16-byte display lines form one title. Words can span the line
        # boundary, so retain those bytes before normalizing whitespace.
        # P.PLAYER after byte 32 is a device identifier, not an artist.
        title = _text(lines[index + 1][:32])
        result[name.casefold()] = {"track_number": number, "metadata_source": "PSONG.MNG"}
        if title:
            result[name.casefold()]["title"] = title
    return result


def read_folder_tags(folder: Path) -> tuple[dict, dict[str, dict]]:
    """Return album and filename-keyed track metadata from one music folder.

    Metadata keys are ``title``, ``track_number`` and ``metadata_source``.
    File keys preserve actual filename case. PSONG order/names take priority
    over MIDI titles, and same-stem audio inherits its sequence's metadata.
    Database metadata should take priority over the values returned here.
    """
    folder = Path(folder)
    album: dict = {}
    tracks: dict[str, dict] = {}
    try:
        files = sorted((p for p in folder.iterdir() if not is_link(p) and p.is_file()), key=lambda p: p.name)
    except OSError:
        return album, tracks
    names = {path.name.casefold(): path for path in files}
    disk = names.get("pdisk.mng")
    if disk:
        try:
            lines = _small_file(disk).splitlines()
            if len(lines) >= 4 and lines[0][:11] == b"PDISK   MNG":
                title = _text(lines[3])
                if title:
                    album.update(title=title, metadata_source="PDISK.MNG")
        except OSError:
            pass
    song = names.get("psong.mng")
    if song:
        try:
            for name, metadata in _management_tracks(_small_file(song)).items():
                if name in names:
                    tracks[names[name].name] = metadata
        except OSError:
            pass
    for path in files:
        if path.suffix.lower() not in _SEQUENCE_EXTENSIONS | {".pspg"}:
            continue
        metadata = tracks.setdefault(path.name, {})
        if not metadata.get("title"):
            title = read_music_title(path)
            if title:
                metadata.update(title=title, metadata_source="PSPG MIDI" if path.suffix.lower() == ".pspg" else "MIDI sequence name")
        if "track_number" not in metadata:
            number = re.match(r"^(?:track[-_ ]*)?(\d{1,4})(?=\D|$)", path.stem, re.I)
            if number and int(number.group(1)):
                metadata["track_number"] = int(number.group(1))
        if not metadata:
            del tracks[path.name]
    for path in files:
        if path.suffix.lower() not in _AUDIO_EXTENSIONS or path.name in tracks:
            continue
        for extension in (".mid", ".midi", ".fil", ".mda", ".esq", ".seq", ".eseq", ".e-seq"):
            sequence = names.get(path.stem.casefold() + extension)
            if sequence and sequence.name in tracks:
                tracks[path.name] = dict(tracks[sequence.name])
                break
    return album, tracks
