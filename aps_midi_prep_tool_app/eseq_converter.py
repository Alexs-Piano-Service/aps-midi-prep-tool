import os
import struct
from dataclasses import dataclass

from .midi_type0_converter import _parse_midi_chunks, _parse_track_events


ESEQ_SIGNATURE = b"COM-ESEQ"
ESEQ_HEADER_SIZE = 0x77
ESEQ_TITLE_START = 0x57
ESEQ_TITLE_END = 0x76
ESEQ_TITLE_LENGTH = ESEQ_TITLE_END - ESEQ_TITLE_START + 1
ESEQ_MIDI_DIVISION = 384
DEFAULT_MIDI_MPQN = 500000
DEFAULT_TIME_SIGNATURE = (4, 2)
DEFAULT_KEY_SIGNATURE = (0, 0)
_ESEQ_PADDING_BYTE = 0xF6
_ESEQ_TEMPLATE = bytes(
    [
        0xFE,
        0x00,
        0x00,
        0x83,
        0x00,
        0x00,
        0x00,
        0x43,
        0x4F,
        0x4D,
        0x2D,
        0x45,
        0x53,
        0x45,
        0x51,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x80,
        0x00,
        0x40,
        0x00,
        0x50,
        0x00,
        0x00,
        0x00,
        0x0C,
        0x00,
        0x00,
        0x01,
        0x58,
        0x00,
        0x00,
        0x50,
        0x49,
        0x41,
        0x4E,
        0x4F,
        0x30,
        0x30,
        0x30,
        0x46,
        0x49,
        0x4C,
        0x00,
        0x58,
        0x04,
        0x04,
        0x00,
        0x00,
        0x06,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x82,
        0x00,
        0x77,
        0x00,
        0x00,
        0x10,
        0x7F,
        0x00,
        0x00,
        0x41,
        0x01,
        0x00,
        0x00,
        0x00,
        0x00,
        0x01,
        0x00,
        0x41,
        0x05,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
    ]
)


class EseqConversionError(ValueError):
    """Raised when E-SEQ or MIDI conversion fails."""


@dataclass(frozen=True)
class ParsedEseqFile:
    events: list[tuple[int, int, bytes]]
    tempo_events: list[tuple[int, int]]
    time_signature_events: list[tuple[int, int, int]]
    base_bpm: int
    title: str


def is_eseq_file(file_path):
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(15)
        return len(header) >= 15 and header[7:15] == ESEQ_SIGNATURE
    except OSError:
        return False


def _decode_15(lo, hi):
    return ((hi & 0x7F) << 7) | (lo & 0x7F)


def _encode_15(value):
    if value < 0 or value > 0x7FFF:
        raise EseqConversionError("15-bit E-SEQ value out of range.")
    return bytes([value & 0x7F, (value >> 7) & 0x7F])


def _read_used_length(data):
    if len(data) >= 7:
        used_length = int.from_bytes(data[3:7], "little")
        if used_length >= ESEQ_HEADER_SIZE:
            return min(len(data), used_length)
    return len(data)


def _decode_title_bytes(raw_title):
    return raw_title.decode("latin1", errors="replace").split("\x00", 1)[0].rstrip(" ")


def _encode_title_bytes(title):
    return (title or "").encode("latin1", errors="replace")[:ESEQ_TITLE_LENGTH].ljust(ESEQ_TITLE_LENGTH, b" ")


def _sanitize_ascii_filename_key(filename):
    stem, ext = os.path.splitext(os.path.basename(filename or ""))
    raw = (stem + ext.lstrip(".")).upper()
    cleaned = "".join(ch if 0x20 <= ord(ch) <= 0x7E else "_" for ch in raw)
    if not cleaned:
        cleaned = "PIANO000FIL"
    return cleaned[:11].ljust(11).encode("ascii", errors="replace")


def _clamp_base_bpm(bpm):
    bpm = int(round(float(bpm or 0)))
    return max(29, min(284, bpm))


def _mpqn_to_bpm(mpqn):
    if mpqn <= 0:
        return 120.0
    return 60_000_000.0 / float(mpqn)


def _encode_vlq(value):
    if value < 0 or value > 0x0FFFFFFF:
        raise EseqConversionError("Variable-length value out of range.")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    out.reverse()
    return bytes(out)


def _build_midi_track(track_events):
    track = bytearray()
    previous_tick = 0
    for abs_tick, raw in track_events:
        delta = abs_tick - previous_tick
        if delta < 0:
            raise EseqConversionError("MIDI events are out of order.")
        track.extend(_encode_vlq(delta))
        track.extend(raw)
        previous_tick = abs_tick
    track.extend(b"\x00\xFF\x2F\x00")
    return bytes(track)


def _write_midi_track_name(raw_title):
    title_bytes = (raw_title or "Untitled").encode("latin1", errors="replace")
    return b"\xFF\x03" + _encode_vlq(len(title_bytes)) + title_bytes


def _write_midi_tempo(mpqn):
    mpqn = max(1, min(int(mpqn), 0xFFFFFF))
    return b"\xFF\x51\x03" + mpqn.to_bytes(3, "big")


def _write_midi_time_signature(numerator, denominator_power):
    denominator_power = max(0, min(int(denominator_power), 7))
    return b"\xFF\x58\x04" + bytes([max(1, min(int(numerator), 255)), denominator_power, 24, 8])


def _write_midi_key_signature(sharps_flats, mode):
    sharps_flats = max(-7, min(int(sharps_flats), 7))
    mode = 1 if int(mode) else 0
    return b"\xFF\x59\x02" + struct.pack("bb", sharps_flats, mode)


def _encode_midi_sysex_event(raw):
    if not raw or raw[0] not in (0xF0, 0xF7):
        raise EseqConversionError("Invalid SysEx event payload.")
    return bytes([raw[0]]) + _encode_vlq(len(raw) - 1) + raw[1:]


def parse_eseq_bytes(eseq_bytes):
    if len(eseq_bytes) < ESEQ_HEADER_SIZE:
        raise EseqConversionError("File is too small to be a valid E-SEQ file.")
    if eseq_bytes[7:15] != ESEQ_SIGNATURE:
        raise EseqConversionError("Missing COM-ESEQ signature.")

    used_length = _read_used_length(eseq_bytes)
    data = eseq_bytes[:used_length]
    title = _decode_title_bytes(data[ESEQ_TITLE_START:ESEQ_TITLE_END + 1])
    base_bpm = _clamp_base_bpm(data[0x33] + 29)

    abs_tick = 0
    pos = ESEQ_HEADER_SIZE
    events = []
    tempo_events = [(0, 60_000_000 // base_bpm)]
    time_signature_events = []
    last_time_signature = None

    while pos < len(data):
        status = data[pos]
        pos += 1

        if status < 0x80:
            continue

        if status == 0xF1:
            if pos < len(data):
                pos += 1
            continue

        if status == 0xF2:
            break

        if status == 0xF3:
            if pos >= len(data):
                break
            abs_tick += data[pos]
            pos += 1
            continue

        if status == 0xF4:
            if pos + 1 >= len(data):
                break
            abs_tick += _decode_15(data[pos], data[pos + 1])
            pos += 2
            continue

        if status == 0xFB:
            if pos + 1 >= len(data):
                break
            factor = max(1, _decode_15(data[pos], data[pos + 1]))
            pos += 2
            effective_bpm = base_bpm * factor / 1000.0
            tempo_events.append((abs_tick, max(1, int(60_000_000 // effective_bpm))))
            continue

        if status == 0xF9:
            if pos + 1 >= len(data):
                break
            numerator = data[pos]
            denominator_power = data[pos + 1]
            pos += 2
            marker = (abs_tick, numerator, denominator_power)
            if last_time_signature != marker[1:]:
                time_signature_events.append(marker)
                last_time_signature = marker[1:]
            continue

        if status == 0xFF:
            if pos >= len(data):
                break
            channel = data[pos]
            pos += 1
            events.append((abs_tick, 1, b"\xFF\x20\x01" + bytes([channel & 0x0F])))
            continue

        if status in (0xF0, 0xF7):
            start = pos - 1
            while pos < len(data):
                if data[pos] == 0xF7:
                    pos += 1
                    break
                pos += 1
            payload = data[start:pos]
            if status == 0xF0 and (not payload or payload[-1] != 0xF7):
                raise EseqConversionError("Encountered an unterminated E-SEQ SysEx event.")
            events.append((abs_tick, 3, payload))
            continue

        hi = status & 0xF0
        if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            if pos + 1 >= len(data):
                break
            raw = bytes([status]) + data[pos:pos + 2]
            pos += 2
            events.append((abs_tick, 2, raw))
            continue

        if hi in (0xC0, 0xD0):
            if pos >= len(data):
                break
            raw = bytes([status, data[pos]])
            pos += 1
            events.append((abs_tick, 2, raw))
            continue

        raise EseqConversionError(f"Unsupported E-SEQ opcode 0x{status:02X}.")

    return ParsedEseqFile(
        events=events,
        tempo_events=tempo_events,
        time_signature_events=time_signature_events,
        base_bpm=base_bpm,
        title=title,
    )


def convert_eseq_bytes_to_midi_bytes(eseq_bytes, *, title_override=None):
    parsed = parse_eseq_bytes(eseq_bytes)
    track_events = []
    title = (title_override or parsed.title or "Yamaha File").strip() or "Yamaha File"

    initial_mpqn = parsed.tempo_events[0][1] if parsed.tempo_events else DEFAULT_MIDI_MPQN
    track_events.append((0, _write_midi_tempo(initial_mpqn)))
    if parsed.time_signature_events:
        _, numerator, denominator_power = parsed.time_signature_events[0]
    else:
        numerator, denominator_power = DEFAULT_TIME_SIGNATURE
    track_events.append((0, _write_midi_time_signature(numerator, denominator_power)))
    track_events.append((0, _write_midi_key_signature(*DEFAULT_KEY_SIGNATURE)))
    track_events.append((0, _write_midi_track_name(title)))
    if not any(abs_tick == 0 and raw and (raw[0] & 0xF0) == 0xC0 for abs_tick, _, raw in parsed.events):
        track_events.append((0, b"\xC0\x00"))

    seen_tempos = set()
    for tick, mpqn in parsed.tempo_events:
        if (tick, mpqn) in seen_tempos or tick == 0:
            continue
        seen_tempos.add((tick, mpqn))
        track_events.append((tick, _write_midi_tempo(mpqn)))

    seen_signatures = set()
    for tick, numerator, denominator_power in parsed.time_signature_events:
        marker = (tick, numerator, denominator_power)
        if marker in seen_signatures or tick == 0:
            continue
        seen_signatures.add(marker)
        track_events.append((tick, _write_midi_time_signature(numerator, denominator_power)))

    for abs_tick, order, raw in parsed.events:
        if raw and raw[0] in (0xF0, 0xF7):
            raw = _encode_midi_sysex_event(raw)
        track_events.append((abs_tick, raw))

    track_events.sort(key=lambda item: (item[0], _event_sort_key(item[1])))
    track_bytes = _build_midi_track(track_events)
    header = struct.pack(">4sIHHH", b"MThd", 6, 0, 1, ESEQ_MIDI_DIVISION)
    return header + struct.pack(">4sI", b"MTrk", len(track_bytes)) + track_bytes


def _event_sort_key(raw):
    if raw[:2] == b"\xFF\x51":
        return 0
    if raw[:2] == b"\xFF\x58":
        return 1
    if raw[:2] == b"\xFF\x59":
        return 2
    if raw[:2] == b"\xFF\x03":
        return 3
    if raw[:2] == b"\xFF\x20":
        return 4
    if raw and raw[0] in (0xF0, 0xF7):
        return 5
    return 6


def _parse_midi_header(midi_bytes):
    if len(midi_bytes) < 14 or midi_bytes[:4] != b"MThd":
        raise EseqConversionError("Missing MThd header chunk.")
    header_length = int.from_bytes(midi_bytes[4:8], "big")
    if header_length < 6 or 8 + header_length > len(midi_bytes):
        raise EseqConversionError("Invalid MIDI header length.")
    format_type = int.from_bytes(midi_bytes[8:10], "big")
    division = int.from_bytes(midi_bytes[12:14], "big")
    if division & 0x8000:
        raise EseqConversionError("SMPTE MIDI timebases are not supported for E-SEQ conversion.")
    return format_type, division


def _collect_merged_midi_events(midi_bytes):
    format_type, division = _parse_midi_header(midi_bytes)
    if format_type == 2:
        raise EseqConversionError("MIDI format 2 files are not supported for E-SEQ conversion.")

    _, _, _, chunks = _parse_midi_chunks(midi_bytes)
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]
    if not track_chunks:
        raise EseqConversionError("No MIDI track chunks were found.")

    merged = []
    for track_index, chunk in enumerate(track_chunks):
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, _ = _parse_track_events(track_data)
        for abs_tick, order, raw in events:
            merged.append((abs_tick, track_index, order, raw))

    merged.sort(key=lambda item: (item[0], item[1], item[2]))
    return division, merged


def _rescale_tick(tick, source_division, target_division):
    if source_division <= 0:
        raise EseqConversionError("MIDI division must be positive.")
    if source_division == target_division:
        return int(tick)
    return int(round((int(tick) * target_division) / float(source_division)))


def _extract_midi_titles(merged_events):
    titles = []
    for _, _, _, raw in merged_events:
        if raw[:2] != b"\xFF\x03":
            continue
        meta_len, pos = _read_vlq_from_bytes(raw, 2)
        title_bytes = raw[pos:pos + meta_len]
        title = title_bytes.decode("latin1", errors="replace").strip()
        if title:
            titles.append(title)
    return titles


def _read_vlq_from_bytes(data, offset):
    value = 0
    pos = offset
    for _ in range(4):
        if pos >= len(data):
            raise EseqConversionError("Unexpected end of MIDI meta event.")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if (byte & 0x80) == 0:
            return value, pos
    raise EseqConversionError("Invalid variable-length quantity.")


def _choose_eseq_title(merged_events, title_override, filename_hint):
    if title_override is not None:
        title = title_override.strip()
        if title:
            return title

    for candidate in _extract_midi_titles(merged_events):
        if candidate:
            return candidate

    stem = os.path.splitext(os.path.basename(filename_hint or "UNTITLED"))[0].strip()
    return stem or "Untitled"


def _decode_midi_sysex_event(raw):
    if not raw or raw[0] not in (0xF0, 0xF7):
        raise EseqConversionError("Invalid MIDI SysEx event.")
    payload_length, payload_start = _read_vlq_from_bytes(raw, 1)
    payload_end = payload_start + payload_length
    if payload_end > len(raw):
        raise EseqConversionError("Malformed MIDI SysEx event.")
    return bytes([raw[0]]) + raw[payload_start:payload_end]


def _encode_eseq_delta(delta):
    if delta < 0:
        raise EseqConversionError("Negative E-SEQ deltas are not supported.")
    out = bytearray()
    remaining = int(delta)
    while remaining > 0:
        if remaining <= 0xFF:
            out.extend(b"\xF3" + bytes([remaining]))
            remaining = 0
            continue
        chunk = min(remaining, 0x7FFF)
        out.extend(b"\xF4" + _encode_15(chunk))
        remaining -= chunk
    return bytes(out)


def _build_time_signature_markers(time_signature_events, last_tick):
    if not time_signature_events:
        time_signature_events = [(0, *DEFAULT_TIME_SIGNATURE)]

    sorted_events = sorted(time_signature_events, key=lambda item: (item[0], item[1], item[2]))
    deduped = []
    previous = None
    for tick, numerator, denominator_power in sorted_events:
        marker = (int(tick), max(1, int(numerator)), max(0, min(int(denominator_power), 7)))
        if previous == marker:
            continue
        deduped.append(marker)
        previous = marker

    markers = set()
    for index, (tick, numerator, denominator_power) in enumerate(deduped):
        next_tick = deduped[index + 1][0] if index + 1 < len(deduped) else last_tick
        denominator = 1 << denominator_power
        measure_ticks = max(1, int(round(ESEQ_MIDI_DIVISION * numerator * 4.0 / denominator)))
        cursor = tick
        stop_tick = max(next_tick, tick)
        if index == len(deduped) - 1 and last_tick > stop_tick:
            stop_tick = last_tick
        while cursor <= stop_tick:
            markers.add((cursor, numerator, denominator_power))
            cursor += measure_ticks
    return sorted(markers, key=lambda item: (item[0], item[1], item[2]))


def _finalize_eseq_header(stream_bytes, base_bpm, title, filename_hint, time_signature_events):
    used_length = ESEQ_HEADER_SIZE + len(stream_bytes)
    if used_length > 0xFFFFFFFF:
        raise EseqConversionError("E-SEQ output is too large.")

    numerator, denominator_power = (
        time_signature_events[0][1],
        time_signature_events[0][2],
    ) if time_signature_events else DEFAULT_TIME_SIGNATURE
    denominator = 1 << denominator_power
    marker_count = max(1, len(time_signature_events) - 1) if time_signature_events else 1

    header = bytearray(_ESEQ_TEMPLATE)
    header[3:7] = used_length.to_bytes(4, "little")
    header[31:35] = len(stream_bytes).to_bytes(4, "little")
    header[0x24] = max(0, min(base_bpm - 29, 255))
    header[0x33] = max(0, min(base_bpm - 29, 255))
    header[0x27:0x32] = _sanitize_ascii_filename_key(filename_hint)
    header[ESEQ_TITLE_START:ESEQ_TITLE_END + 1] = _encode_title_bytes(title)
    header[0x34] = max(1, min(numerator, 255))
    header[0x35] = max(1, min(denominator, 255))
    header[0x38:0x3A] = (marker_count * 6).to_bytes(2, "little")
    header[0x41:0x43] = ((used_length - 1) & 0xFFFF).to_bytes(2, "big")
    return bytes(header)


def _pad_eseq_output(data):
    remainder = len(data) % 2048
    if remainder == 0:
        return data
    return data + bytes([_ESEQ_PADDING_BYTE]) * (2048 - remainder)


def convert_midi_bytes_to_eseq_bytes(midi_bytes, *, title_override=None, filename_hint=""):
    division, merged_events = _collect_merged_midi_events(midi_bytes)
    normalized_events = []
    tempo_events = []
    time_signature_events = []

    for abs_tick, _, _, raw in merged_events:
        scaled_tick = _rescale_tick(abs_tick, division, ESEQ_MIDI_DIVISION)

        if raw[:2] == b"\xFF\x51":
            if len(raw) >= 6:
                tempo_events.append((scaled_tick, int.from_bytes(raw[3:6], "big")))
            continue

        if raw[:2] == b"\xFF\x58":
            if len(raw) >= 6:
                time_signature_events.append((scaled_tick, raw[3], raw[4]))
            continue

        if raw[:2] == b"\xFF\x20":
            if len(raw) >= 5 and raw[2] == 0x01:
                normalized_events.append((scaled_tick, 1, b"\xFF" + bytes([raw[4] & 0x0F])))
            continue

        if raw and raw[0] == 0xFF:
            continue

        if raw and raw[0] in (0xF0, 0xF7):
            normalized_events.append((scaled_tick, 3, _decode_midi_sysex_event(raw)))
            continue

        normalized_events.append((scaled_tick, 2, raw))

    tempo_events.sort(key=lambda item: item[0])
    time_signature_events.sort(key=lambda item: item[0])
    normalized_events.sort(key=lambda item: (item[0], item[1]))

    initial_mpqn = tempo_events[0][1] if tempo_events else DEFAULT_MIDI_MPQN
    base_bpm = _clamp_base_bpm(_mpqn_to_bpm(initial_mpqn))
    title = _choose_eseq_title(merged_events, title_override, filename_hint)

    last_tick = 0
    if normalized_events:
        last_tick = max(last_tick, normalized_events[-1][0])
    if tempo_events:
        last_tick = max(last_tick, tempo_events[-1][0])
    if time_signature_events:
        last_tick = max(last_tick, time_signature_events[-1][0])

    marker_events = _build_time_signature_markers(time_signature_events, last_tick)
    combined_events = [(0, 0, b"\xF1\x00")]
    combined_events.extend((tick, 1, b"\xF9" + bytes([numerator, denominator_power])) for tick, numerator, denominator_power in marker_events)

    for tick, mpqn in tempo_events:
        factor = max(1, min(int(round((_mpqn_to_bpm(mpqn) * 1000.0) / base_bpm)), 0x7FFF))
        if tick == 0 and factor == 1000:
            continue
        combined_events.append((tick, 2, b"\xFB" + _encode_15(factor)))

    combined_events.extend((tick, order + 10, raw) for tick, order, raw in normalized_events)
    combined_events.sort(key=lambda item: (item[0], item[1]))

    stream = bytearray()
    previous_tick = 0
    first_event = True
    for abs_tick, _, raw in combined_events:
        if first_event:
            first_event = False
        else:
            stream.extend(_encode_eseq_delta(abs_tick - previous_tick))
        stream.extend(raw)
        previous_tick = abs_tick
    stream.extend(b"\xF2")

    header = _finalize_eseq_header(bytes(stream), base_bpm, title, filename_hint, marker_events)
    return _pad_eseq_output(header + bytes(stream))


def _write_destination_bytes(dest_path, payload):
    temp_path = f"{dest_path}.aps_eseq_{os.getpid()}.tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(payload)
        os.replace(temp_path, dest_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def convert_eseq_file_to_midi_path(source_path, dest_path, *, title_override=None):
    with open(source_path, "rb") as handle:
        eseq_bytes = handle.read()
    payload = convert_eseq_bytes_to_midi_bytes(eseq_bytes, title_override=title_override)
    _write_destination_bytes(dest_path, payload)


def convert_midi_file_to_eseq_path(source_path, dest_path, *, title_override=None, filename_hint=""):
    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()
    payload = convert_midi_bytes_to_eseq_bytes(
        midi_bytes,
        title_override=title_override,
        filename_hint=filename_hint or os.path.basename(dest_path),
    )
    _write_destination_bytes(dest_path, payload)
