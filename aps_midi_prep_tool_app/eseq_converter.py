import os
import struct
from dataclasses import dataclass

from .midi_type0_converter import _parse_midi_chunks, _parse_track_events


ESEQ_SIGNATURE = b"COM-ESEQ"
Q11_SIGNATURE = b"Q11V1.00"
ESEQ_HEADER_SIZE = 0x77
Q11_EVENT_STREAM_START = 0x200
ESEQ_TITLE_START = 0x57
ESEQ_TITLE_END = 0x76
ESEQ_TITLE_LENGTH = ESEQ_TITLE_END - ESEQ_TITLE_START + 1
ESEQ_DURATION_TICKS_OFFSET = 0x37
ESEQ_DELAY_BEFORE_TICKS_OFFSET = 0x3B
ESEQ_DELAY_AFTER_TICKS_OFFSET = 0x3F
ESEQ_MIDI_DIVISION = 384
ESEQ_DELAY15_MAX = 0x3FFF
ESEQ_DIRECTORY_U16_MAX = 0xFFFF
DEFAULT_MIDI_MPQN = 500000
DEFAULT_TIME_SIGNATURE = (4, 2)
DEFAULT_KEY_SIGNATURE = (0, 0)
CHANNEL_VOLUME_CONTROLLER = 7
CC7_POLICY_PRESERVE = "preserve"
CC7_POLICY_WARN_ONLY = "warn_only"
CC7_POLICY_PLAYBACK_FIX_100 = "playback_fix_100"
CC7_POLICY_PLAYBACK_FIX_127 = "playback_fix_127"
CC7_POLICY_DROP_EARLY_ZERO = "drop_early_cc7_zero"
DEFAULT_CC7_POLICY = CC7_POLICY_PLAYBACK_FIX_100
_ESEQ_PADDING_BYTE = 0xF6
_ESEQ_TIMING_META_PREFIX = "APS-ESEQ-TIMING"
_LEGACY_ESEQ_TEMPLATE = bytes(
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


def _build_eseq_template():
    header = bytearray(ESEQ_HEADER_SIZE)
    header[0] = 0xFE
    header[7:15] = ESEQ_SIGNATURE
    header[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    header[0x23] = 0x01
    header[0x34:0x36] = bytes([4, 4])
    header[0x43:0x47] = bytes([0x00, 0x77, 0x00, 0x00])
    header[0x47:0x51] = bytes.fromhex("10 7F 00 00 41 01 00 00 80 00")
    return bytes(header)


_ESEQ_TEMPLATE = _build_eseq_template()


class EseqConversionError(ValueError):
    """Raised when E-SEQ or MIDI conversion fails."""


@dataclass(frozen=True)
class ParsedEseqFile:
    events: list[tuple[int, int, bytes]]
    tempo_events: list[tuple[int, int]]
    time_signature_events: list[tuple[int, int, int]]
    base_bpm: int
    title: str
    end_tick: int


@dataclass(frozen=True)
class EseqTimingFields:
    duration_ticks: int
    delay_before_ticks: int
    delay_after_ticks: int


def is_eseq_file(file_path):
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(15)
        return len(header) >= 15 and header[7:15] == ESEQ_SIGNATURE
    except OSError:
        return False


def _is_q11_eseq(data):
    return len(data) >= ESEQ_TITLE_END + 1 and data[7:15] == ESEQ_SIGNATURE and data[0x0F:0x17] == Q11_SIGNATURE


def _eseq_base_bpm(data):
    if _is_q11_eseq(data):
        return _clamp_base_bpm(data[0x24] + 29)
    return _clamp_base_bpm(data[0x33] + 29)


def _eseq_event_stream_start(data):
    if _is_q11_eseq(data):
        return min(len(data), Q11_EVENT_STREAM_START)
    return ESEQ_HEADER_SIZE


def _decode_15(lo, hi):
    return ((hi & 0x7F) << 7) | (lo & 0x7F)


def _encode_15(value):
    if value < 0 or value > ESEQ_DELAY15_MAX:
        raise EseqConversionError("14-bit E-SEQ value out of range.")
    return bytes([value & 0x7F, (value >> 7) & 0x7F])


def _declared_stream_end(data, stream_start):
    if len(data) >= 0x23:
        stream_length = int.from_bytes(data[0x1F:0x23], "little")
        stream_end = stream_start + stream_length
        if stream_length > 0 and stream_start <= stream_end <= len(data):
            return stream_end
    if len(data) >= 7:
        used_length = int.from_bytes(data[3:7], "little")
        if stream_start < used_length <= len(data):
            return used_length
    return None


def _decode_title_bytes(raw_title):
    return raw_title.decode("latin1", errors="replace").split("\x00", 1)[0].rstrip(" ")


def _encode_title_bytes(title):
    return (title or "").encode("latin1", errors="replace")[:ESEQ_TITLE_LENGTH].ljust(ESEQ_TITLE_LENGTH, b" ")


def _sanitize_ascii_filename_key(filename):
    stem, ext = os.path.splitext(os.path.basename(filename or ""))
    if not stem:
        stem = "PIANO000"
        ext = ext or ".FIL"

    def clean(text):
        return "".join(ch if 0x20 <= ord(ch) <= 0x7E else "_" for ch in text.upper())

    stem_bytes = clean(stem).encode("ascii", errors="replace")[:8].ljust(8)
    ext_bytes = clean(ext.lstrip(".")).encode("ascii", errors="replace")[:3].ljust(3)
    return stem_bytes + ext_bytes


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


def _build_midi_track(track_events, end_tick=None):
    track = bytearray()
    previous_tick = 0
    for abs_tick, raw in track_events:
        delta = abs_tick - previous_tick
        if delta < 0:
            raise EseqConversionError("MIDI events are out of order.")
        track.extend(_encode_vlq(delta))
        track.extend(raw)
        previous_tick = abs_tick
    if end_tick is None:
        end_delta = 0
    else:
        end_delta = int(end_tick) - previous_tick
        if end_delta < 0:
            raise EseqConversionError("MIDI end tick is before the last event.")
    track.extend(_encode_vlq(end_delta))
    track.extend(b"\xFF\x2F\x00")
    return bytes(track)


def _write_midi_track_name(raw_title):
    title_bytes = (raw_title or "Untitled").encode("latin1", errors="replace")
    return b"\xFF\x03" + _encode_vlq(len(title_bytes)) + title_bytes


def _write_midi_text(text):
    text_bytes = (text or "").encode("ascii", errors="replace")
    return b"\xFF\x01" + _encode_vlq(len(text_bytes)) + text_bytes


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


def _is_zero_channel_volume_event(raw):
    return (
        len(raw) >= 3
        and (raw[0] & 0xF0) == 0xB0
        and raw[1] == CHANNEL_VOLUME_CONTROLLER
        and raw[2] == 0
    )


def _is_channel_volume_event(raw):
    return (
        len(raw) >= 3
        and (raw[0] & 0xF0) == 0xB0
        and raw[1] == CHANNEL_VOLUME_CONTROLLER
    )


def _is_note_on_event(raw):
    return len(raw) >= 3 and (raw[0] & 0xF0) == 0x90 and raw[2] > 0


def _midi_channel(raw):
    if raw and 0x80 <= raw[0] <= 0xEF:
        return raw[0] & 0x0F
    return None


def _zero_cc7_indexes_needing_playback_fix(events, tick_limit=None):
    candidates = set()
    indexed_events = list(enumerate(events))
    for index, (abs_tick, _, raw) in indexed_events:
        if tick_limit is not None and abs_tick > tick_limit:
            continue
        if not _is_zero_channel_volume_event(raw):
            continue
        channel = _midi_channel(raw)
        if channel is None:
            continue

        note_later = False
        restored_before_note = False
        for _, (later_tick, _, later_raw) in indexed_events[index + 1:]:
            if later_tick < abs_tick or _midi_channel(later_raw) != channel:
                continue
            if _is_channel_volume_event(later_raw) and later_raw[2] > 0:
                restored_before_note = True
                break
            if _is_note_on_event(later_raw):
                note_later = True
                break
        if note_later and not restored_before_note:
            candidates.add(index)
    return candidates


def _apply_cc7_policy(raw, should_adjust, cc7_policy):
    if not should_adjust:
        return raw
    if cc7_policy in (CC7_POLICY_PRESERVE, CC7_POLICY_WARN_ONLY):
        return raw
    if cc7_policy == CC7_POLICY_DROP_EARLY_ZERO:
        return None
    if cc7_policy == CC7_POLICY_PLAYBACK_FIX_100:
        return raw[:2] + bytes([100])
    if cc7_policy == CC7_POLICY_PLAYBACK_FIX_127:
        return raw[:2] + bytes([127])
    raise EseqConversionError(f"Unsupported CC7 policy '{cc7_policy}'.")


def _event_class_flags(events):
    has_notes = False
    has_controllers = False
    for _, _, raw in events:
        if not raw:
            continue
        status = raw[0] & 0xF0
        if status in (0x80, 0x90):
            has_notes = True
        elif status == 0xB0:
            has_controllers = True
    return has_notes, has_controllers


def parse_eseq_bytes(eseq_bytes):
    if len(eseq_bytes) < ESEQ_HEADER_SIZE:
        raise EseqConversionError("File is too small to be a valid E-SEQ file.")
    if eseq_bytes[7:15] != ESEQ_SIGNATURE:
        raise EseqConversionError("Missing COM-ESEQ signature.")

    data = eseq_bytes
    title = _decode_title_bytes(data[ESEQ_TITLE_START:ESEQ_TITLE_END + 1])
    base_bpm = _eseq_base_bpm(data)

    abs_tick = 0
    pos = _eseq_event_stream_start(data)
    declared_stream_end = _declared_stream_end(data, pos)
    events = []
    tempo_events = [(0, 60_000_000 // base_bpm)]
    time_signature_events = []
    last_time_signature = None

    while pos < len(data):
        if declared_stream_end is not None and pos >= declared_stream_end:
            if all(byte in (0x00, _ESEQ_PADDING_BYTE) for byte in data[pos:]):
                break
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
        end_tick=abs_tick,
    )


def derive_eseq_timing_fields(eseq_bytes):
    parsed = parse_eseq_bytes(eseq_bytes)
    event_ticks = [tick for tick, _, _ in parsed.events]
    note_ticks = [tick for tick, _, raw in parsed.events if _is_note_on_event(raw)]
    if event_ticks:
        delay_before_ticks = min(note_ticks) if note_ticks else min(event_ticks)
        delay_after_ticks = max(0, parsed.end_tick - max(event_ticks))
    else:
        delay_before_ticks = 0
        delay_after_ticks = 0
    return EseqTimingFields(
        duration_ticks=max(0, int(parsed.end_tick)),
        delay_before_ticks=max(0, int(delay_before_ticks)),
        delay_after_ticks=max(0, int(delay_after_ticks)),
    )


def _clamp_directory_u16(value):
    return max(0, min(int(value or 0), ESEQ_DIRECTORY_U16_MAX))


def refresh_eseq_timing_fields_in_bytes(eseq_bytes):
    if _is_q11_eseq(eseq_bytes):
        return bytes(eseq_bytes)
    timing = derive_eseq_timing_fields(eseq_bytes)
    data = bytearray(eseq_bytes)
    if len(data) < ESEQ_DELAY_AFTER_TICKS_OFFSET + 2:
        raise EseqConversionError("File is too small to contain E-SEQ timing metadata.")
    data[ESEQ_DURATION_TICKS_OFFSET:ESEQ_DURATION_TICKS_OFFSET + 4] = timing.duration_ticks.to_bytes(4, "little")
    data[ESEQ_DELAY_BEFORE_TICKS_OFFSET:ESEQ_DELAY_BEFORE_TICKS_OFFSET + 2] = _clamp_directory_u16(
        timing.delay_before_ticks,
    ).to_bytes(2, "little")
    data[ESEQ_DELAY_AFTER_TICKS_OFFSET:ESEQ_DELAY_AFTER_TICKS_OFFSET + 2] = _clamp_directory_u16(
        timing.delay_after_ticks,
    ).to_bytes(2, "little")
    return bytes(data)


def convert_eseq_bytes_to_midi_bytes(eseq_bytes, *, title_override=None, cc7_policy=DEFAULT_CC7_POLICY):
    parsed = parse_eseq_bytes(eseq_bytes)
    timing = derive_eseq_timing_fields(eseq_bytes)
    track_events = []
    event_sequence = 0
    title_candidate = title_override if title_override is not None else parsed.title
    title = title_candidate if title_candidate and title_candidate.strip() else "Yamaha File"

    def add_track_event(abs_tick, raw):
        nonlocal event_sequence
        track_events.append((abs_tick, event_sequence, raw))
        event_sequence += 1

    initial_mpqn = parsed.tempo_events[0][1] if parsed.tempo_events else DEFAULT_MIDI_MPQN
    add_track_event(0, _write_midi_tempo(initial_mpqn))
    if parsed.time_signature_events:
        _, numerator, denominator_power = parsed.time_signature_events[0]
    else:
        numerator, denominator_power = DEFAULT_TIME_SIGNATURE
    add_track_event(0, _write_midi_time_signature(numerator, denominator_power))
    add_track_event(0, _write_midi_key_signature(*DEFAULT_KEY_SIGNATURE))
    add_track_event(0, _write_midi_track_name(title))
    add_track_event(
        0,
        _write_midi_text(
            f"{_ESEQ_TIMING_META_PREFIX} before={timing.delay_before_ticks} "
            f"after={timing.delay_after_ticks} duration={timing.duration_ticks}"
        ),
    )

    seen_tempos = set()
    for tick, mpqn in parsed.tempo_events:
        if (tick, mpqn) in seen_tempos or tick == 0:
            continue
        seen_tempos.add((tick, mpqn))
        add_track_event(tick, _write_midi_tempo(mpqn))

    seen_signatures = set()
    for tick, numerator, denominator_power in parsed.time_signature_events:
        marker = (tick, numerator, denominator_power)
        if marker in seen_signatures or tick == 0:
            continue
        seen_signatures.add(marker)
        add_track_event(tick, _write_midi_time_signature(numerator, denominator_power))

    cc7_indexes = _zero_cc7_indexes_needing_playback_fix(parsed.events)
    for index, (abs_tick, order, raw) in enumerate(parsed.events):
        raw = _apply_cc7_policy(raw, index in cc7_indexes, cc7_policy)
        if raw is None:
            continue
        if raw and raw[0] in (0xF0, 0xF7):
            raw = _encode_midi_sysex_event(raw)
        add_track_event(abs_tick, raw)

    track_events.sort(key=lambda item: (item[0], item[1]))
    track_bytes = _build_midi_track(
        ((abs_tick, raw) for abs_tick, _, raw in track_events),
        end_tick=parsed.end_tick,
    )
    header = struct.pack(">4sIHHH", b"MThd", 6, 0, 1, ESEQ_MIDI_DIVISION)
    return header + struct.pack(">4sI", b"MTrk", len(track_bytes)) + track_bytes


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


def _collect_merged_midi_events(midi_bytes, *, include_end_tick=False):
    format_type, division = _parse_midi_header(midi_bytes)
    if format_type == 2:
        raise EseqConversionError("MIDI format 2 files are not supported for E-SEQ conversion.")

    _, _, _, chunks = _parse_midi_chunks(midi_bytes)
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]
    if not track_chunks:
        raise EseqConversionError("No MIDI track chunks were found.")

    merged = []
    max_end_tick = 0
    for track_index, chunk in enumerate(track_chunks):
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, end_tick = _parse_track_events(track_data)
        max_end_tick = max(max_end_tick, end_tick)
        for abs_tick, order, raw in events:
            merged.append((abs_tick, track_index, order, raw))

    merged.sort(key=lambda item: (item[0], item[1], item[2]))
    if include_end_tick:
        return division, merged, max_end_tick
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
        title = title_bytes.decode("latin1", errors="replace")
        if title.strip():
            titles.append(title)
    return titles


def _decode_midi_meta_text(raw):
    if raw[:2] not in {b"\xFF\x01", b"\xFF\x06", b"\xFF\x7F"}:
        return ""
    meta_len, pos = _read_vlq_from_bytes(raw, 2)
    return raw[pos:pos + meta_len].decode("latin1", errors="replace")


def _extract_eseq_timing_hint(merged_events):
    for _, _, _, raw in merged_events:
        text = _decode_midi_meta_text(raw).strip()
        if not text.startswith(_ESEQ_TIMING_META_PREFIX):
            continue
        fields = {}
        for token in text[len(_ESEQ_TIMING_META_PREFIX):].strip().split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            try:
                fields[key.strip().lower()] = max(0, int(value.strip()))
            except ValueError:
                continue
        before = fields.get("before")
        after = fields.get("after")
        if before is not None or after is not None:
            return before, after
    return None


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
        if title_override.strip():
            return title_override

    for candidate in _extract_midi_titles(merged_events):
        if candidate.strip():
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
        chunk = min(remaining, ESEQ_DELAY15_MAX)
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
        markers.add((cursor, numerator, denominator_power))
        cursor += measure_ticks
        while cursor < stop_tick:
            markers.add((cursor, numerator, denominator_power))
            cursor += measure_ticks
    return sorted(markers, key=lambda item: (item[0], item[1], item[2]))


def _finalize_eseq_header(
    stream_bytes,
    base_bpm,
    title,
    filename_hint,
    time_signature_events,
    end_tick,
    delay_before_ticks,
    delay_after_ticks,
    has_notes,
    has_controllers,
):
    used_length = ESEQ_HEADER_SIZE + len(stream_bytes)
    if used_length > 0xFFFFFFFF:
        raise EseqConversionError("E-SEQ output is too large.")

    numerator, denominator_power = (
        time_signature_events[0][1],
        time_signature_events[0][2],
    ) if time_signature_events else DEFAULT_TIME_SIGNATURE
    denominator = 1 << denominator_power
    header = bytearray(_ESEQ_TEMPLATE)
    if len(header) != ESEQ_HEADER_SIZE:
        raise EseqConversionError("Internal E-SEQ header template has the wrong size.")
    header[0] = 0xFE
    header[7:15] = ESEQ_SIGNATURE
    header[3:7] = used_length.to_bytes(4, "little")
    header[0x1F:0x23] = len(stream_bytes).to_bytes(4, "little")
    header[0x24] = max(0, min(base_bpm - 29, 255))
    header[0x33] = max(0, min(base_bpm - 29, 255))
    header[0x27:0x32] = _sanitize_ascii_filename_key(filename_hint)
    header[ESEQ_TITLE_START:ESEQ_TITLE_END + 1] = _encode_title_bytes(title)
    header[0x34] = max(1, min(numerator, 255))
    header[0x35] = max(1, min(denominator, 255))
    header[0x37:0x3B] = max(0, min(int(end_tick), 0xFFFFFFFF)).to_bytes(4, "little")
    header[0x3B:0x3D] = _clamp_directory_u16(delay_before_ticks).to_bytes(2, "little")
    header[0x3F:0x41] = _clamp_directory_u16(delay_after_ticks).to_bytes(2, "little")
    header[0x43:0x47] = bytes([0x00, 0x77, 0x00, 0x00])
    header[0x51] = 1 if has_controllers else 0
    header[0x54] = (0x01 if has_notes else 0) | (0x04 if has_controllers else 0)
    return bytes(header)


def _pad_eseq_output(data):
    remainder = len(data) % 2048
    if remainder == 0:
        return data
    return data + bytes([_ESEQ_PADDING_BYTE]) * (2048 - remainder)


def convert_midi_bytes_to_eseq_bytes(
    midi_bytes,
    *,
    title_override=None,
    filename_hint="",
    cc7_policy=DEFAULT_CC7_POLICY,
):
    division, merged_events, midi_end_tick = _collect_merged_midi_events(midi_bytes, include_end_tick=True)
    timing_hint = _extract_eseq_timing_hint(merged_events)
    normalized_events = []
    tempo_events = []
    time_signature_events = []
    event_sequence = 0

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
                normalized_events.append((scaled_tick, event_sequence, b"\xFF" + bytes([raw[4] & 0x0F])))
                event_sequence += 1
            continue

        if raw and raw[0] == 0xFF:
            continue

        if raw and raw[0] in (0xF0, 0xF7):
            normalized_events.append((scaled_tick, event_sequence, _decode_midi_sysex_event(raw)))
            event_sequence += 1
            continue

        normalized_events.append((scaled_tick, event_sequence, raw))
        event_sequence += 1

    tempo_events.sort(key=lambda item: item[0])
    time_signature_events.sort(key=lambda item: item[0])
    normalized_events.sort(key=lambda item: (item[0], item[1]))
    cc7_indexes = _zero_cc7_indexes_needing_playback_fix(normalized_events)
    adjusted_events = []
    for index, (tick, order, raw) in enumerate(normalized_events):
        raw = _apply_cc7_policy(raw, index in cc7_indexes, cc7_policy)
        if raw is None:
            continue
        adjusted_events.append((tick, order, raw))
    normalized_events = adjusted_events

    if timing_hint and normalized_events:
        desired_before, _ = timing_hint
        if desired_before is not None:
            real_event_ticks = [tick for tick, _, _ in normalized_events]
            note_ticks = [tick for tick, _, raw in normalized_events if _is_note_on_event(raw)]
            current_before = min(note_ticks) if note_ticks else min(real_event_ticks)
            shift_ticks = max(0, int(desired_before) - int(current_before))
            if shift_ticks:
                normalized_events = [
                    (tick + shift_ticks, order, raw)
                    for tick, order, raw in normalized_events
                ]
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
    last_tick = max(last_tick, _rescale_tick(midi_end_tick, division, ESEQ_MIDI_DIVISION))
    if timing_hint and normalized_events:
        _, desired_after = timing_hint
        if desired_after is not None:
            last_real_event_tick = max(tick for tick, _, _ in normalized_events)
            last_tick = max(last_tick, last_real_event_tick + int(desired_after))

    marker_events = _build_time_signature_markers(time_signature_events, last_tick)
    combined_events = [(0, 0, b"\xF1\x00")]
    combined_events.extend((tick, 1, b"\xF9" + bytes([numerator, denominator_power])) for tick, numerator, denominator_power in marker_events)

    for tick, mpqn in tempo_events:
        factor = max(1, min(int(round((_mpqn_to_bpm(mpqn) * 1000.0) / base_bpm)), ESEQ_DELAY15_MAX))
        if tick == 0 and factor == 1000:
            continue
        combined_events.append((tick, 2, b"\xFB" + _encode_15(factor)))

    combined_events.extend((tick, order + 10, raw) for tick, order, raw in normalized_events)
    combined_events.sort(key=lambda item: (item[0], item[1]))
    has_notes, has_controllers = _event_class_flags(normalized_events)

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
    if last_tick > previous_tick:
        stream.extend(_encode_eseq_delta(last_tick - previous_tick))
        previous_tick = last_tick
    end_tick = previous_tick
    stream.extend(b"\xF2")
    if normalized_events:
        real_event_ticks = [tick for tick, _, _ in normalized_events]
        note_ticks = [tick for tick, _, raw in normalized_events if _is_note_on_event(raw)]
        delay_before_ticks = min(note_ticks) if note_ticks else min(real_event_ticks)
        delay_after_ticks = max(0, end_tick - max(real_event_ticks))
    else:
        delay_before_ticks = 0
        delay_after_ticks = 0

    header = _finalize_eseq_header(
        bytes(stream),
        base_bpm,
        title,
        filename_hint,
        marker_events,
        end_tick,
        delay_before_ticks,
        delay_after_ticks,
        has_notes,
        has_controllers,
    )
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


def convert_eseq_file_to_midi_path(
    source_path,
    dest_path,
    *,
    title_override=None,
    cc7_policy=DEFAULT_CC7_POLICY,
):
    with open(source_path, "rb") as handle:
        eseq_bytes = handle.read()
    payload = convert_eseq_bytes_to_midi_bytes(
        eseq_bytes,
        title_override=title_override,
        cc7_policy=cc7_policy,
    )
    _write_destination_bytes(dest_path, payload)


def convert_midi_file_to_eseq_path(
    source_path,
    dest_path,
    *,
    title_override=None,
    filename_hint="",
    cc7_policy=DEFAULT_CC7_POLICY,
):
    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()
    payload = convert_midi_bytes_to_eseq_bytes(
        midi_bytes,
        title_override=title_override,
        filename_hint=filename_hint or os.path.basename(dest_path),
        cc7_policy=cc7_policy,
    )
    _write_destination_bytes(dest_path, payload)
