"""Shared Standard MIDI File header and chunk-boundary validation."""

from dataclasses import dataclass
from io import BytesIO


MAX_MIDI_HEADER_BYTES = 64 * 1024


@dataclass(frozen=True)
class SmfHeader:
    header_end: int
    format_type: int
    track_count: int
    division: int


def _read_smf_header(handle, file_size, *, prefix=None, max_header_bytes=None):
    if prefix is None:
        prefix = handle.read(8)
    if prefix[:4] != b"MThd":
        raise ValueError("Missing MThd header chunk.")
    if len(prefix) != 8:
        raise ValueError("Corrupt MIDI header length.")
    header_length = int.from_bytes(prefix[4:8], "big")
    if header_length < 6 or (
        max_header_bytes is not None and header_length > max_header_bytes
    ):
        raise ValueError("Invalid MIDI header length.")
    header_end = 8 + header_length
    if header_end > file_size:
        raise ValueError("Corrupt MIDI header length.")
    fields = handle.read(6)
    if len(fields) != 6:
        raise ValueError("Corrupt MIDI header length.")

    format_type = int.from_bytes(fields[:2], "big")
    track_count = int.from_bytes(fields[2:4], "big")
    division = int.from_bytes(fields[4:6], "big")
    if format_type not in (0, 1, 2):
        raise ValueError(f"MIDI format {format_type} is not a Standard MIDI File type.")
    if (format_type == 0 and track_count != 1) or track_count == 0:
        raise ValueError("The MIDI header contains an invalid track count.")
    if division & 0x8000:
        frame_code = 0x100 - (division >> 8)
        if frame_code not in (24, 25, 29, 30) or (division & 0xFF) == 0:
            raise ValueError("The MIDI header contains an invalid SMPTE time division.")
    elif division == 0:
        raise ValueError("The MIDI header contains an invalid time division of zero.")
    return SmfHeader(header_end, format_type, track_count, division)


def parse_smf_header(midi_bytes):
    """Validate the complete MThd chunk and return its standard fields."""
    return _read_smf_header(BytesIO(midi_bytes), len(midi_bytes))


def read_smf_layout(
    handle, file_size, *, prefix=None, include_trailing_chunks=False,
    max_header_bytes=None,
):
    """Validate declared tracks without reading or allocating track payloads.

    Unknown chunks may occur between tracks. By default bytes after the last
    declared track are opaque, as required by the channel-merging utility's
    preservation behavior. Converters that historically consume all complete
    chunks can request that behavior with include_trailing_chunks.
    """
    header = _read_smf_header(
        handle, file_size, prefix=prefix, max_header_bytes=max_header_bytes,
    )
    chunks = []
    found_tracks = 0
    offset = header.header_end
    while found_tracks < header.track_count or (
        include_trailing_chunks and offset + 8 <= file_size
    ):
        if offset + 8 > file_size:
            raise ValueError("A declared MIDI track is missing or malformed.")
        handle.seek(offset)
        chunk_header = handle.read(8)
        if len(chunk_header) != 8:
            raise ValueError("A declared MIDI track is missing or malformed.")
        data_start = offset + 8
        data_end = data_start + int.from_bytes(chunk_header[4:8], "big")
        if data_end > file_size:
            raise ValueError("Corrupt MIDI chunk length.")
        chunk_id = chunk_header[:4]
        chunks.append({
            "id": chunk_id,
            "start": offset,
            "data_start": data_start,
            "data_end": data_end,
        })
        if chunk_id == b"MTrk":
            found_tracks += 1
        offset = data_end
    return header, chunks, offset


def parse_smf_layout(midi_bytes, *, include_trailing_chunks=False):
    return read_smf_layout(
        BytesIO(midi_bytes), len(midi_bytes),
        include_trailing_chunks=include_trailing_chunks,
    )
