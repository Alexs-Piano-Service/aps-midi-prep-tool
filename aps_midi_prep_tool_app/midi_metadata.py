import os

_SYSTEM_MESSAGE_DATA_LENGTHS = {
    0xF1: 1,
    0xF2: 2,
    0xF3: 1,
    0xF6: 0,
    0xF8: 0,
    0xFA: 0,
    0xFB: 0,
    0xFC: 0,
    0xFE: 0,
}

_LEGACY_TITLE_MIN_CODEPOINT = 0x20
_LEGACY_TITLE_MAX_CODEPOINT = 0x7E


def _extract_midi_format_type(midi_bytes):
    if len(midi_bytes) < 14:
        raise ValueError("File is too small to be a valid MIDI file.")
    if midi_bytes[:4] != b"MThd":
        raise ValueError("Missing MThd header chunk.")

    header_len = int.from_bytes(midi_bytes[4:8], "big")
    if header_len < 6:
        raise ValueError("Invalid MIDI header length.")

    header_end = 8 + header_len
    if header_end > len(midi_bytes):
        raise ValueError("Corrupt MIDI header length.")

    return int.from_bytes(midi_bytes[8:10], "big")


def extract_midi_type_label_from_midi(midi_path):
    try:
        with open(midi_path, "rb") as f:
            midi_bytes = f.read()
        format_type = _extract_midi_format_type(midi_bytes)
        return f"Type {format_type}"
    except Exception as e:
        print(f"Error detecting MIDI type for {midi_path}: {e}")
        return "Error"


def _parse_vlq(data, offset, end):
    value = 0
    pos = offset
    for _ in range(4):
        if pos >= end:
            raise ValueError("Unexpected end of data while reading variable-length value.")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if (byte & 0x80) == 0:
            return value, pos
    raise ValueError("Invalid variable-length value (too many bytes).")

def _encode_vlq(value):
    if value < 0 or value > 0x0FFFFFFF:
        raise ValueError("Variable-length value out of range.")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    out.reverse()
    return bytes(out)

def _parse_midi_chunks(midi_bytes):
    if len(midi_bytes) < 14:
        raise ValueError("File is too small to be a valid MIDI file.")
    if midi_bytes[:4] != b"MThd":
        raise ValueError("Missing MThd header chunk.")

    header_len = int.from_bytes(midi_bytes[4:8], "big")
    if header_len < 6:
        raise ValueError("Invalid MIDI header length.")

    header_end = 8 + header_len
    if header_end > len(midi_bytes):
        raise ValueError("Corrupt MIDI header length.")

    declared_track_count = int.from_bytes(midi_bytes[10:12], "big")
    chunks = []
    pos = header_end
    midi_len = len(midi_bytes)

    while pos + 8 <= midi_len:
        chunk_id = midi_bytes[pos:pos + 4]
        chunk_len = int.from_bytes(midi_bytes[pos + 4:pos + 8], "big")
        data_start = pos + 8
        data_end = data_start + chunk_len
        if data_end > midi_len:
            raise ValueError("Corrupt MIDI chunk length.")
        chunks.append({
            "id": chunk_id,
            "start": pos,
            "length_start": pos + 4,
            "data_start": data_start,
            "data_end": data_end,
        })
        pos = data_end

    return declared_track_count, chunks

def _find_first_track_name_event(track_data):
    pos = 0
    track_end = len(track_data)
    running_status = None

    while pos < track_end:
        _, pos = _parse_vlq(track_data, pos, track_end)
        if pos >= track_end:
            raise ValueError("Unexpected end of track data.")

        status_byte = track_data[pos]
        status_from_stream = status_byte >= 0x80

        if status_from_stream:
            status = status_byte
            pos += 1
        else:
            if running_status is None:
                raise ValueError("Invalid running status in track data.")
            status = running_status

        if status == 0xFF:
            if not status_from_stream:
                raise ValueError("Meta events cannot use running status.")
            if pos >= track_end:
                raise ValueError("Unexpected end of meta event.")

            meta_type = track_data[pos]
            pos += 1

            length_start = pos
            meta_len, pos = _parse_vlq(track_data, pos, track_end)
            payload_start = pos
            payload_end = payload_start + meta_len

            if payload_end > track_end:
                raise ValueError("Meta event exceeds track bounds.")

            if meta_type == 0x03:
                return {
                    "length_start": length_start,
                    "payload_start": payload_start,
                    "payload_end": payload_end,
                }

            pos = payload_end
            continue

        if status in (0xF0, 0xF7):
            if not status_from_stream:
                raise ValueError("SysEx events cannot use running status.")
            sysex_len, pos = _parse_vlq(track_data, pos, track_end)
            if pos + sysex_len > track_end:
                raise ValueError("SysEx event exceeds track bounds.")
            pos += sysex_len
            running_status = None
            continue

        if 0x80 <= status <= 0xEF:
            msg_type = status & 0xF0
            data_len = 1 if msg_type in (0xC0, 0xD0) else 2
            if pos + data_len > track_end:
                raise ValueError("Channel event exceeds track bounds.")
            pos += data_len
            running_status = status
            continue

        if not status_from_stream:
            raise ValueError("System messages cannot use running status.")

        data_len = _SYSTEM_MESSAGE_DATA_LENGTHS.get(status)
        if data_len is None:
            raise ValueError(f"Unsupported system status byte: 0x{status:02X}")
        if pos + data_len > track_end:
            raise ValueError("System message exceeds track bounds.")
        pos += data_len
        running_status = None

    return None

def _replace_chunk_data(midi_bytes, chunk, new_track_data):
    new_len = len(new_track_data)
    if new_len > 0xFFFFFFFF:
        raise ValueError("Track chunk is too large.")
    return (
        midi_bytes[:chunk["length_start"]]
        + new_len.to_bytes(4, "big")
        + new_track_data
        + midi_bytes[chunk["data_end"]:]
    )

def _encode_title_bytes(title):
    try:
        return title.encode("latin1")
    except UnicodeEncodeError as exc:
        raise ValueError("Title contains characters that are not representable in Latin-1.") from exc

def _decode_title_bytes(title_bytes):
    return title_bytes.decode("latin1")


def _describe_char_for_error(ch):
    code = ord(ch)
    if ch == " ":
        display = "SPACE"
    elif ch.isprintable() and ch not in {"'"}:
        display = ch
    else:
        display = f"\\u{code:04X}"
    return f"'{display}' (U+{code:04X})"


def validate_legacy_title_input(title):
    """Validate edited titles against a conservative legacy-safe character set."""
    invalid = []
    seen = set()
    for ch in title:
        code = ord(ch)
        if _LEGACY_TITLE_MIN_CODEPOINT <= code <= _LEGACY_TITLE_MAX_CODEPOINT:
            continue
        if ch in seen:
            continue
        seen.add(ch)
        invalid.append(ch)

    if not invalid:
        return None

    preview = ", ".join(_describe_char_for_error(ch) for ch in invalid[:5])
    if len(invalid) > 5:
        preview += ", ..."
    return (
        "Unsupported character(s) for legacy MIDI compatibility. "
        "Use printable ASCII only (space through ~). "
        f"Found: {preview}"
    )

def _set_first_title_in_midi_bytes(midi_bytes, new_title):
    declared_track_count, chunks = _parse_midi_chunks(midi_bytes)
    title_bytes = _encode_title_bytes(new_title)
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]

    for chunk in track_chunks:
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        title_event = _find_first_track_name_event(track_data)
        if title_event is None:
            continue

        new_track_data = (
            track_data[:title_event["length_start"]]
            + _encode_vlq(len(title_bytes))
            + title_bytes
            + track_data[title_event["payload_end"]:]
        )
        return _replace_chunk_data(midi_bytes, chunk, new_track_data)

    if track_chunks:
        first_track = track_chunks[0]
        original_track_data = midi_bytes[first_track["data_start"]:first_track["data_end"]]
        title_event = b"\x00\xFF\x03" + _encode_vlq(len(title_bytes)) + title_bytes
        new_track_data = title_event + original_track_data
        return _replace_chunk_data(midi_bytes, first_track, new_track_data)

    if declared_track_count != 0:
        raise ValueError("No track chunks were found in this MIDI file.")
    if declared_track_count >= 0xFFFF:
        raise ValueError("Cannot add a track to this MIDI file.")

    title_track_data = (
        b"\x00\xFF\x03"
        + _encode_vlq(len(title_bytes))
        + title_bytes
        + b"\x00\xFF\x2F\x00"
    )
    title_track_chunk = b"MTrk" + len(title_track_data).to_bytes(4, "big") + title_track_data

    patched = bytearray(midi_bytes)
    patched[10:12] = (declared_track_count + 1).to_bytes(2, "big")
    patched.extend(title_track_chunk)
    return bytes(patched)

def extract_first_title_from_midi(midi_path):
    try:
        with open(midi_path, "rb") as f:
            midi_bytes = f.read()

        _, chunks = _parse_midi_chunks(midi_bytes)
        result = ""
        for chunk in chunks:
            if chunk["id"] != b"MTrk":
                continue
            track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
            title_event = _find_first_track_name_event(track_data)
            if title_event is None:
                continue

            title_bytes = track_data[title_event["payload_start"]:title_event["payload_end"]]
            result = _decode_title_bytes(title_bytes)
            break

        print(f"extract: {os.path.basename(midi_path)} => '{result}'")
        return result
    except Exception as e:
        print(f"Error extracting title from {midi_path}: {e}")
        return f"Error: {str(e)}"

def update_midi_title(midi_path, new_title):
    try:
        with open(midi_path, "rb") as f:
            midi_bytes = f.read()

        patched = _set_first_title_in_midi_bytes(midi_bytes, new_title)

        with open(midi_path, "wb") as f:
            f.write(patched)

        return None
    except Exception as e:
        return f"Error updating MIDI title: {str(e)}"

def update_midi_title_to_destination(source_path, new_title, dest_dir):
    try:
        with open(source_path, "rb") as f:
            midi_bytes = f.read()

        patched = _set_first_title_in_midi_bytes(midi_bytes, new_title)

        dest_path = os.path.join(dest_dir, os.path.basename(source_path))

        with open(dest_path, "wb") as f:
            f.write(patched)

        return None
    except Exception as e:
        return f"Error updating {os.path.basename(source_path)}: {str(e)}"
