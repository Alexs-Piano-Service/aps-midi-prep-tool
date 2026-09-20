import os
import re

from .message_catalog import translate_text
from .helpers.atomic_file import atomic_write_bytes

from .eseq_converter import (
    is_clavinova_mda_eseq_bytes,
    is_eseq_file,
    parse_eseq_bytes,
)

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
_ESEQ_TITLE_START = 0x57
_ESEQ_TITLE_END = 0x76
_ESEQ_TITLE_LENGTH = _ESEQ_TITLE_END - _ESEQ_TITLE_START + 1
_MAX_MIDI_HEADER_BYTES = 64 * 1024
TITLE_DISPLAY_ENCODINGS = (
    ("latin1", "Latin-1 (default)"),
    ("cp1252", "Windows-1252"),
    ("shift_jis", "Japanese (Shift-JIS)"),
    ("gbk", "Simplified Chinese (GBK)"),
    ("big5", "Traditional Chinese (Big5)"),
)


class MidiTitleFormatError(ValueError):
    """The MIDI event stream or requested title could not be interpreted safely."""


def normalize_title_spacing(title):
    """Return a title with Disklavier splits and excess spacing cleaned up."""
    text = str(title or "").replace("\x00", " ")
    if len(text) > 16 and text[15].islower() and text[16].isupper():
        text = f"{text[:16]} {text[16:]}"
    return re.sub(r"\s+", " ", text).strip()


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


def probe_midi_file_type(midi_path):
    """Return the SMF type, None for other content, or raise on invalid headers/I/O.

    SMF permits additional header bytes. Bound the allocation and check the
    declared size against the actual file before reading an extended header.
    """
    with open(midi_path, "rb") as handle:
        prefix = handle.read(8)
        if prefix[:4] != b"MThd":
            return None
        if len(prefix) != 8:
            raise MidiTitleFormatError("Corrupt MIDI header length.")
        length = int.from_bytes(prefix[4:8], "big")
        if not 6 <= length <= _MAX_MIDI_HEADER_BYTES:
            raise MidiTitleFormatError("Invalid MIDI header length.")
        if 8 + length > os.fstat(handle.fileno()).st_size:
            raise MidiTitleFormatError("Corrupt MIDI header length.")
        return _extract_midi_format_type(prefix + handle.read(length))


def is_midi_file(midi_path):
    try:
        return probe_midi_file_type(midi_path) is not None
    except (OSError, ValueError):
        return False


def has_eseq_title_metadata(file_path):
    try:
        if (
            os.path.splitext(file_path or "")[1].lower() == ".evt"
            and not is_eseq_file(file_path)
        ):
            return False
        with open(file_path, "rb") as handle:
            header = handle.read(_ESEQ_TITLE_END + 1)
        if is_clavinova_mda_eseq_bytes(header, filename=os.path.basename(file_path)):
            return True
        return os.path.getsize(file_path) >= (_ESEQ_TITLE_END + 1)
    except OSError:
        return False


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

def _find_first_track_name_event(track_data, *, validate_remainder=False):
    pos = 0
    track_end = len(track_data)
    running_status = None
    first_title_event = None

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

            if meta_type == 0x03 and first_title_event is None:
                first_title_event = {
                    "length_start": length_start,
                    "payload_start": payload_start,
                    "payload_end": payload_end,
                }
                if not validate_remainder:
                    return first_title_event

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

    return first_title_event

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

def _decode_title_bytes(title_bytes, encoding="latin1"):
    return title_bytes.decode(encoding)


def title_for_display(raw_title, encoding="latin1"):
    """Interpret the lossless Latin-1 carrier without changing stored title bytes.

    Display decoding is explicit, never guessed. Replacement characters in a
    preview are deliberately not propagated back to the stored title.
    """
    if encoding not in dict(TITLE_DISPLAY_ENCODINGS):
        raise ValueError(f"Unsupported title display encoding: {encoding}")
    try:
        title_bytes = raw_title.encode("latin1")
    except UnicodeEncodeError:
        # Filename fallbacks and catalog titles may already be Unicode text.
        return raw_title
    return title_bytes.decode(encoding, errors="replace")


def _extract_eseq_title_from_bytes(data):
    if is_clavinova_mda_eseq_bytes(data):
        return ""
    if len(data) < (_ESEQ_TITLE_END + 1):
        raise ValueError("File is too small to contain E-SEQ title metadata.")

    title_bytes = data[_ESEQ_TITLE_START:_ESEQ_TITLE_END + 1]
    return _decode_title_bytes(title_bytes).split("\x00", 1)[0].rstrip(" ")


def _set_eseq_title_in_bytes(data, new_title):
    if is_clavinova_mda_eseq_bytes(data):
        return bytes(data)
    if len(data) < (_ESEQ_TITLE_END + 1):
        raise ValueError("File is too small to contain E-SEQ title metadata.")

    title_bytes = _encode_title_bytes(new_title)
    if len(title_bytes) > _ESEQ_TITLE_LENGTH:
        raise ValueError(f"E-SEQ title must be {_ESEQ_TITLE_LENGTH} characters or fewer.")

    padded = title_bytes.ljust(_ESEQ_TITLE_LENGTH, b" ")
    patched = (
        data[:_ESEQ_TITLE_START]
        + padded
        + data[_ESEQ_TITLE_END + 1:]
    )
    # A title edit must preserve source timing/header conventions, including
    # the zero before/after fields written by MID2ESEQ. No events changed.
    return patched


def _describe_char_for_error(ch):
    code = ord(ch)
    if ch == " ":
        display = "SPACE"
    elif ch.isprintable() and ch not in {"'"}:
        display = ch
    else:
        display = f"\\u{code:04X}"
    return f"'{display}' (U+{code:04X})"


def validate_legacy_title_input(title, language_code="en"):
    """Validate edited titles against a conservative legacy-safe character set."""
    return validate_title_input(title, language_code, legacy=True)


def validate_title_input(title, language_code="en", *, legacy=True):
    """Validate an edit for legacy hardware or the supported Latin-1 MIDI writer."""
    invalid = []
    seen = set()
    for ch in title:
        code = ord(ch)
        if _LEGACY_TITLE_MIN_CODEPOINT <= code <= _LEGACY_TITLE_MAX_CODEPOINT:
            continue
        if not legacy and code <= 0xFF and ch.isprintable():
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
    message = (
        "Use printable ASCII only (space through ~). Unsupported characters: {characters}"
        if legacy else
        "Use printable Latin-1 characters only. Unsupported characters: {characters}"
    )
    return translate_text(message, language_code).format(characters=preview)

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

def read_first_title_from_midi(midi_path):
    """Read the first MIDI track name, allowing format and I/O errors to propagate."""
    with open(midi_path, "rb") as f:
        midi_bytes = f.read()

    _, chunks = _parse_midi_chunks(midi_bytes)
    for chunk in chunks:
        if chunk["id"] != b"MTrk":
            continue
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        title_event = _find_first_track_name_event(track_data)
        if title_event is None:
            continue

        title_bytes = track_data[title_event["payload_start"]:title_event["payload_end"]]
        return _decode_title_bytes(title_bytes)
    return ""


def extract_first_title_from_midi(midi_path):
    try:
        result = read_first_title_from_midi(midi_path)
        print(f"extract: {os.path.basename(midi_path)} => '{result}'")
        return result
    except Exception as e:
        print(f"Error extracting title from {midi_path}: {e}")
        return f"Error reading MIDI title from {os.path.basename(midi_path)}: {e}"


def read_eseq_title_from_file(file_path):
    """Read an E-SEQ title, allowing format and I/O errors to propagate."""
    with open(file_path, "rb") as f:
        return _extract_eseq_title_from_bytes(f.read())


def extract_eseq_title_from_file(file_path):
    try:
        result = read_eseq_title_from_file(file_path)
        print(f"extract eseq: {os.path.basename(file_path)} => '{result}'")
        return result
    except Exception as e:
        print(f"Error extracting E-SEQ title from {file_path}: {e}")
        return f"Error reading E-SEQ title from {os.path.basename(file_path)}: {e}"


def update_midi_title(midi_path, new_title):
    try:
        write_midi_title_to_path(midi_path, new_title, midi_path)
        return None
    except Exception as e:
        return f"Could not update MIDI title in {os.path.basename(midi_path)}: {e}"


def update_eseq_title(file_path, new_title):
    try:
        write_eseq_title_to_path(file_path, new_title, file_path)
        return None
    except Exception as e:
        return f"Could not update E-SEQ title in {os.path.basename(file_path)}: {e}"

def update_midi_title_to_destination(source_path, new_title, dest_dir):
    try:
        dest_path = os.path.join(dest_dir, os.path.basename(source_path))
        write_midi_title_to_path(source_path, new_title, dest_path)
        return None
    except Exception as e:
        return f"Could not write updated title for {os.path.basename(source_path)}: {e}"


def _validate_midi_title_bytes(midi_bytes, new_title):
    declared_track_count, chunks = _parse_midi_chunks(midi_bytes)
    tracks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]
    if declared_track_count != len(tracks):
        raise ValueError("The MIDI track count does not match its header.")
    first_title = None
    for chunk in tracks:
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        event = _find_first_track_name_event(track_data, validate_remainder=True)
        if event is not None and first_title is None:
            first_title = _decode_title_bytes(track_data[event["payload_start"]:event["payload_end"]])
    if first_title != new_title:
        raise ValueError("The saved MIDI title does not match the requested title.")


def write_midi_title_to_path(source_path, new_title, dest_path):
    """Atomically write a checked title edit; keep format and I/O errors distinct."""
    with open(source_path, "rb") as f:
        midi_bytes = f.read()

    try:
        patched = _set_first_title_in_midi_bytes(midi_bytes, new_title)
        atomic_write_bytes(
            dest_path, patched,
            validate=lambda data: _validate_midi_title_bytes(data, new_title),
        )
    except ValueError as exc:
        raise MidiTitleFormatError(str(exc)) from exc


def update_midi_title_to_path(source_path, new_title, dest_path):
    try:
        write_midi_title_to_path(source_path, new_title, dest_path)
        return None
    except Exception as e:
        return f"Could not write updated title for {os.path.basename(source_path)}: {e}"


def write_eseq_title_to_path(source_path, new_title, dest_path):
    """Atomically write a checked E-SEQ title, allowing errors to propagate."""
    with open(source_path, "rb") as f:
        data = f.read()

    patched = _set_eseq_title_in_bytes(data, new_title)

    def validate(written):
        parse_eseq_bytes(written)
        # Clavinova MDA has no embedded title and intentionally stays unchanged.
        if not is_clavinova_mda_eseq_bytes(written):
            expected = _encode_title_bytes(new_title).ljust(_ESEQ_TITLE_LENGTH, b" ")
            if written[_ESEQ_TITLE_START:_ESEQ_TITLE_END + 1] != expected:
                raise ValueError("The saved E-SEQ title does not match the requested title.")

    atomic_write_bytes(dest_path, patched, validate=validate)


def update_eseq_title_to_path(source_path, new_title, dest_path):
    try:
        write_eseq_title_to_path(source_path, new_title, dest_path)
        return None
    except Exception as e:
        return f"Could not write updated E-SEQ title for {os.path.basename(source_path)}: {e}"
