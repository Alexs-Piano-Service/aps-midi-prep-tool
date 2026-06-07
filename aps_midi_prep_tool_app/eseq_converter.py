import os
import struct
from dataclasses import dataclass

from .midi_type0_converter import _parse_midi_chunks, _parse_track_events


ESEQ_SIGNATURE = b"COM-ESEQ"
Q11_SIGNATURE = b"Q11V1.00"
ESEQ_HEADER_SIZE = 0x77
Q11_EVENT_STREAM_START = 0x200
CLAVINOVA_MDA_HEADER_SIZE = 0x57
CLAVINOVA_MDA_HEADER_PREFIX = b"\xFE\x00\x00\xFF\xFF\x00\x00"
CLAVINOVA_MDA_CONST_17 = bytes.fromhex("80 00 21 00 30 00 00 00")
ESEQ_ORDER_KEY_START = 0x27
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
ESEQ_CONTAINER_DISKLAVIER = "disklavier"
ESEQ_CONTAINER_CLAVINOVA_MDA = "clavinova_mda"
MIDI_METADATA_POLICY_CLEAN = "clean"
MIDI_METADATA_POLICY_ARCHIVAL = "archival"
DEFAULT_MIDI_METADATA_POLICY = MIDI_METADATA_POLICY_CLEAN
_ESEQ_PADDING_BYTE = 0xF6
_ESEQ_TIMING_META_PREFIX = "APS-ESEQ-TIMING"
_ESEQ_HEADER_META_PREFIX = "APS-ESEQ-HEADER"
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


@dataclass(frozen=True)
class EseqTimingHint:
    before_ticks: int | None = None
    after_ticks: int | None = None
    visible_before_ticks: int | None = None
    visible_after_ticks: int | None = None
    source_title_blank: bool = False


@dataclass(frozen=True)
class EseqHeaderHint:
    slice_27_77: bytes | None = None
    prefix_00_stream: bytes | None = None


def is_eseq_file(file_path):
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(15)
        return len(header) >= 15 and header[7:15] == ESEQ_SIGNATURE
    except OSError:
        return False


def _is_q11_eseq(data):
    return len(data) >= ESEQ_TITLE_END + 1 and data[7:15] == ESEQ_SIGNATURE and data[0x0F:0x17] == Q11_SIGNATURE


def is_clavinova_mda_eseq_bytes(data, filename=""):
    data = bytes(data or b"")
    if len(data) < CLAVINOVA_MDA_HEADER_SIZE + 1:
        return False
    if data[7:15] != ESEQ_SIGNATURE:
        return False
    if (
        data[0:7] == CLAVINOVA_MDA_HEADER_PREFIX
        and data[0x17:0x1F] == CLAVINOVA_MDA_CONST_17
        and data[0x57:0x5A] == b"\xF1\x00\xF9"
    ):
        return True
    return filename.upper().endswith(".MDA") and data[0x57:0x5A] == b"\xF1\x00\xF9"


def _eseq_base_bpm(data):
    if _is_q11_eseq(data):
        return _clamp_base_bpm(data[0x24] + 29)
    if is_clavinova_mda_eseq_bytes(data):
        return _clamp_base_bpm(data[0x24] + 29)
    return _clamp_base_bpm(data[0x33] + 29)


def _eseq_event_stream_start(data):
    if _is_q11_eseq(data):
        return min(len(data), Q11_EVENT_STREAM_START)
    if is_clavinova_mda_eseq_bytes(data):
        return min(len(data), CLAVINOVA_MDA_HEADER_SIZE)
    return ESEQ_HEADER_SIZE


def _decode_15(lo, hi):
    return ((hi & 0x7F) << 7) | (lo & 0x7F)


def _encode_15(value):
    if value < 0 or value > ESEQ_DELAY15_MAX:
        raise EseqConversionError("14-bit E-SEQ value out of range.")
    return bytes([value & 0x7F, (value >> 7) & 0x7F])


def _declared_stream_end(data, stream_start):
    if is_clavinova_mda_eseq_bytes(data) and len(data) >= 0x23:
        used_length = int.from_bytes(data[0x1F:0x23], "little")
        if stream_start < used_length <= len(data):
            return used_length
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


def _normalize_midi_metadata_policy(midi_metadata_policy):
    policy = str(midi_metadata_policy or DEFAULT_MIDI_METADATA_POLICY).strip().lower()
    policy_aliases = {
        "clean_canonical": MIDI_METADATA_POLICY_CLEAN,
        "minimal_exe": MIDI_METADATA_POLICY_CLEAN,
        "archival_verbose": MIDI_METADATA_POLICY_ARCHIVAL,
        "reference_pairs": MIDI_METADATA_POLICY_ARCHIVAL,
    }
    policy = policy_aliases.get(policy, policy)
    if policy in {MIDI_METADATA_POLICY_CLEAN, MIDI_METADATA_POLICY_ARCHIVAL}:
        return policy
    raise EseqConversionError(f"Unsupported MIDI metadata policy '{midi_metadata_policy}'.")


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


def _effective_initial_mpqn(tempo_events):
    initial_mpqn = DEFAULT_MIDI_MPQN
    for tick, mpqn in tempo_events:
        if tick != 0:
            continue
        initial_mpqn = mpqn
    return initial_mpqn


def parse_eseq_bytes(eseq_bytes):
    if len(eseq_bytes) < CLAVINOVA_MDA_HEADER_SIZE:
        raise EseqConversionError("File is too small to be a valid Yamaha E-SEQ file.")
    if eseq_bytes[7:15] != ESEQ_SIGNATURE:
        raise EseqConversionError("This does not look like a Yamaha E-SEQ file; the COM-ESEQ signature is missing.")

    data = eseq_bytes
    is_clavinova_mda = is_clavinova_mda_eseq_bytes(data)
    if not is_clavinova_mda and len(data) < ESEQ_HEADER_SIZE:
        raise EseqConversionError("File is too small to be a valid Yamaha E-SEQ file.")
    title = "" if is_clavinova_mda else _decode_title_bytes(data[ESEQ_TITLE_START:ESEQ_TITLE_END + 1])
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

        if status == _ESEQ_PADDING_BYTE:
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
            if numerator == 0 and denominator_power == 0:
                continue
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


def _explorer_visible_delay_ticks(eseq_bytes, which):
    data = bytes(eseq_bytes)
    stream_start = _eseq_event_stream_start(data)
    stream_end = _declared_stream_end(data, stream_start)
    if which == "before":
        pos = stream_start + 2
    elif which == "after":
        if stream_end is None:
            return 0
        pos = stream_end - 4
        if stream_end <= 0 or data[stream_end - 1] != 0xF2:
            return 0
    else:
        return 0

    if pos < 0 or pos + 2 >= len(data) or data[pos] != 0xF4:
        return 0
    return _decode_15(data[pos + 1], data[pos + 2])


def _clamp_directory_u16(value):
    return max(0, min(int(value or 0), ESEQ_DIRECTORY_U16_MAX))


def refresh_eseq_timing_fields_in_bytes(eseq_bytes):
    if _is_q11_eseq(eseq_bytes) or is_clavinova_mda_eseq_bytes(eseq_bytes):
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


def convert_eseq_bytes_to_midi_bytes(
    eseq_bytes,
    *,
    title_override=None,
    cc7_policy=DEFAULT_CC7_POLICY,
    midi_metadata_policy=DEFAULT_MIDI_METADATA_POLICY,
):
    midi_metadata_policy = _normalize_midi_metadata_policy(midi_metadata_policy)
    parsed = parse_eseq_bytes(eseq_bytes)
    track_events = []
    event_sequence = 0
    title_candidate = title_override if title_override is not None else parsed.title
    title = title_candidate if title_candidate and title_candidate.strip() else "Yamaha File"

    def add_track_event(abs_tick, raw):
        nonlocal event_sequence
        track_events.append((abs_tick, event_sequence, raw))
        event_sequence += 1

    initial_mpqn = _effective_initial_mpqn(parsed.tempo_events)
    add_track_event(0, _write_midi_tempo(initial_mpqn))
    if parsed.time_signature_events:
        _, numerator, denominator_power = parsed.time_signature_events[0]
        add_track_event(0, _write_midi_time_signature(numerator, denominator_power))
    else:
        numerator, denominator_power = DEFAULT_TIME_SIGNATURE
    add_track_event(0, _write_midi_key_signature(*DEFAULT_KEY_SIGNATURE))
    add_track_event(0, _write_midi_track_name(title))

    if midi_metadata_policy == MIDI_METADATA_POLICY_ARCHIVAL:
        timing = derive_eseq_timing_fields(eseq_bytes)
        visible_before_ticks = _explorer_visible_delay_ticks(eseq_bytes, "before")
        visible_after_ticks = _explorer_visible_delay_ticks(eseq_bytes, "after")
        add_track_event(
            0,
            _write_midi_text(
                f"{_ESEQ_TIMING_META_PREFIX} before={timing.delay_before_ticks} "
                f"after={timing.delay_after_ticks} duration={timing.duration_ticks} "
                f"visible_before={visible_before_ticks} visible_after={visible_after_ticks} "
                f"source_title_blank={1 if not parsed.title.strip() else 0}"
            ),
        )
        add_track_event(
            0,
            _write_midi_text(
                f"{_ESEQ_HEADER_META_PREFIX} slice27_77="
                f"{bytes(eseq_bytes[ESEQ_ORDER_KEY_START:ESEQ_TITLE_END + 1]).hex()} "
                f"prefix00_stream="
                f"{bytes(eseq_bytes[:_eseq_event_stream_start(eseq_bytes)]).hex()}"
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
        raise EseqConversionError("This does not look like a standard MIDI file; the MThd header chunk is missing.")
    header_length = int.from_bytes(midi_bytes[4:8], "big")
    if header_length < 6 or 8 + header_length > len(midi_bytes):
        raise EseqConversionError("The MIDI header length is invalid; the file may be corrupt or incomplete.")
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
        visible_before = fields.get("visible_before", before)
        visible_after = fields.get("visible_after", after)
        if any(value is not None for value in (before, after, visible_before, visible_after)):
            return EseqTimingHint(
                before_ticks=before,
                after_ticks=after,
                visible_before_ticks=visible_before,
                visible_after_ticks=visible_after,
                source_title_blank=bool(fields.get("source_title_blank", 0)),
            )
    return None


def _extract_eseq_header_hint(merged_events):
    expected_length = ESEQ_TITLE_END + 1 - ESEQ_ORDER_KEY_START
    header_slice = None
    header_prefix = None
    for _, _, _, raw in merged_events:
        text = _decode_midi_meta_text(raw).strip()
        if not text.startswith(_ESEQ_HEADER_META_PREFIX):
            continue
        for token in text[len(_ESEQ_HEADER_META_PREFIX):].strip().split():
            if token.startswith("slice27_77="):
                value = token.split("=", 1)[1].strip()
                try:
                    candidate = bytes.fromhex(value)
                except ValueError:
                    continue
                if len(candidate) == expected_length:
                    header_slice = candidate
                continue
            if token.startswith("prefix00_stream="):
                value = token.split("=", 1)[1].strip()
                try:
                    candidate = bytes.fromhex(value)
                except ValueError:
                    continue
                if len(candidate) >= ESEQ_HEADER_SIZE and candidate[7:15] == ESEQ_SIGNATURE:
                    header_prefix = candidate
        if header_slice is not None or header_prefix is not None:
            return EseqHeaderHint(
                slice_27_77=header_slice,
                prefix_00_stream=header_prefix,
            )
    return None


def _should_write_header_title(header_title_bytes, merged_events, timing_hint, title_override):
    if title_override is not None:
        return True
    header_title = _decode_title_bytes(header_title_bytes)
    midi_titles = _extract_midi_titles(merged_events)
    if not midi_titles:
        return False
    midi_title = midi_titles[0]
    is_blank_source_fallback = (
        timing_hint
        and timing_hint.source_title_blank
        and midi_title.strip() == "Yamaha File"
    )
    return not is_blank_source_fallback and midi_title != header_title


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


def _encode_eseq_delta(delta, *, prefer_long=False, avoid_long=False):
    if delta < 0:
        raise EseqConversionError("Negative E-SEQ deltas are not supported.")
    out = bytearray()
    remaining = int(delta)
    if avoid_long:
        while remaining > 0:
            chunk = min(remaining, 0xFF)
            out.extend(b"\xF3" + bytes([chunk]))
            remaining -= chunk
        return bytes(out)
    if prefer_long and 0 < remaining <= ESEQ_DELAY15_MAX:
        return b"\xF4" + _encode_15(remaining)
    while remaining > 0:
        if prefer_long and remaining <= ESEQ_DELAY15_MAX:
            out.extend(b"\xF4" + _encode_15(remaining))
            remaining = 0
            continue
        if remaining <= 0xFF:
            out.extend(b"\xF3" + bytes([remaining]))
            remaining = 0
            continue
        chunk = min(remaining, ESEQ_DELAY15_MAX)
        if prefer_long and 0 < remaining - chunk <= 0xFF:
            chunk = max(1, remaining - 0x100)
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
        raise EseqConversionError("E-SEQ output is too large for the Yamaha E-SEQ file format.")

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


def _finalize_clavinova_mda_header(stream_bytes, base_bpm, filename_hint):
    used_length = CLAVINOVA_MDA_HEADER_SIZE + len(stream_bytes)
    if used_length > 0xFFFFFFFF:
        raise EseqConversionError("MDA output is too large for the Yamaha Clavinova E-SEQ file format.")

    header = bytearray(CLAVINOVA_MDA_HEADER_SIZE)
    header[0:7] = CLAVINOVA_MDA_HEADER_PREFIX
    header[7:15] = ESEQ_SIGNATURE
    header[0x0F:0x17] = b" " * 8
    header[0x17:0x1F] = CLAVINOVA_MDA_CONST_17
    header[0x1F:0x23] = used_length.to_bytes(4, "little")
    header[0x24] = max(0, min(base_bpm - 29, 255))
    header[0x27:0x32] = _sanitize_ascii_filename_key(filename_hint or "CLP_01.MDA")
    header[0x33] = 0x0B
    header[0x39:0x3B] = b"\x03\x80"
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
    container_variant=ESEQ_CONTAINER_DISKLAVIER,
):
    container_variant = (container_variant or ESEQ_CONTAINER_DISKLAVIER).strip().lower()
    if container_variant not in {ESEQ_CONTAINER_DISKLAVIER, ESEQ_CONTAINER_CLAVINOVA_MDA}:
        raise EseqConversionError(f"Unsupported E-SEQ container variant '{container_variant}'.")

    division, merged_events, midi_end_tick = _collect_merged_midi_events(midi_bytes, include_end_tick=True)
    timing_hint = _extract_eseq_timing_hint(merged_events)
    header_hint = _extract_eseq_header_hint(merged_events)
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
            try:
                meta_len, payload_start = _read_vlq_from_bytes(raw, 2)
            except EseqConversionError:
                continue
            payload = raw[payload_start:payload_start + meta_len]
            if payload:
                normalized_events.append((scaled_tick, event_sequence, b"\xFF" + bytes([payload[0] & 0x0F])))
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
        desired_before = timing_hint.before_ticks
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

    initial_mpqn = _effective_initial_mpqn(tempo_events)
    base_bpm = _clamp_base_bpm(_mpqn_to_bpm(initial_mpqn))
    title = _choose_eseq_title(merged_events, title_override, filename_hint)
    if timing_hint and timing_hint.source_title_blank and title_override is None:
        midi_titles = _extract_midi_titles(merged_events)
        if not midi_titles or midi_titles[0].strip() in {"", "Yamaha File"}:
            title = ""

    last_tick = 0
    if normalized_events:
        last_tick = max(last_tick, normalized_events[-1][0])
    if tempo_events:
        last_tick = max(last_tick, tempo_events[-1][0])
    if time_signature_events:
        last_tick = max(last_tick, time_signature_events[-1][0])
    last_tick = max(last_tick, _rescale_tick(midi_end_tick, division, ESEQ_MIDI_DIVISION))
    if timing_hint and normalized_events:
        desired_after = timing_hint.after_ticks
        if desired_after is not None:
            last_real_event_tick = max(tick for tick, _, _ in normalized_events)
            last_tick = max(last_tick, last_real_event_tick + int(desired_after))

    marker_events = _build_time_signature_markers(time_signature_events, last_tick) if time_signature_events else []
    if timing_hint and normalized_events and (
        timing_hint.visible_before_ticks is None or timing_hint.visible_before_ticks > 0
    ):
        first_stream_tick = min(tick for tick, _, _ in normalized_events)
        if first_stream_tick > 0:
            marker_events = [
                (
                    first_stream_tick if tick < first_stream_tick else tick,
                    numerator,
                    denominator_power,
                )
                for tick, numerator, denominator_power in marker_events
            ]
            marker_events = sorted(set(marker_events), key=lambda item: (item[0], item[1], item[2]))
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
    first_nonzero_delta = True
    visible_before = timing_hint.visible_before_ticks if timing_hint is not None else None
    visible_after = timing_hint.visible_after_ticks if timing_hint is not None else None
    for abs_tick, _, raw in combined_events:
        if first_event:
            first_event = False
        else:
            delta = abs_tick - previous_tick
            stream.extend(
                _encode_eseq_delta(
                    delta,
                    prefer_long=first_nonzero_delta and delta > 0 and visible_before is not None and visible_before > 0,
                    avoid_long=first_nonzero_delta and delta > 0 and visible_before == 0,
                )
            )
            if delta > 0:
                first_nonzero_delta = False
        stream.extend(raw)
        previous_tick = abs_tick
    if last_tick > previous_tick:
        stream.extend(
            _encode_eseq_delta(
                last_tick - previous_tick,
                prefer_long=visible_after is None or visible_after > 0,
                avoid_long=visible_after == 0,
            )
        )
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

    if (
        container_variant == ESEQ_CONTAINER_DISKLAVIER
        and header_hint is not None
        and header_hint.prefix_00_stream is not None
        and _is_q11_eseq(header_hint.prefix_00_stream)
    ):
        prefix = bytearray(header_hint.prefix_00_stream)
        if len(prefix) < Q11_EVENT_STREAM_START:
            prefix.extend(b"\x00" * (Q11_EVENT_STREAM_START - len(prefix)))
        used_length = len(prefix) + len(stream)
        if used_length > 0xFFFFFFFF:
            raise EseqConversionError("E-SEQ output is too large for the Yamaha E-SEQ file format.")
        prefix[3:7] = used_length.to_bytes(4, "little")
        if len(prefix) >= 0x23:
            prefix[0x1F:0x23] = used_length.to_bytes(4, "little")
        prefix[0x24] = max(0, min(base_bpm - 29, 255))
        if _should_write_header_title(
            prefix[ESEQ_TITLE_START:ESEQ_TITLE_END + 1],
            merged_events,
            timing_hint,
            title_override,
        ):
            prefix[ESEQ_TITLE_START:ESEQ_TITLE_END + 1] = _encode_title_bytes(title)
        return _pad_eseq_output(bytes(prefix) + bytes(stream))

    if container_variant == ESEQ_CONTAINER_CLAVINOVA_MDA:
        header = _finalize_clavinova_mda_header(bytes(stream), base_bpm, filename_hint)
        return bytes(header) + bytes(stream)

    header = bytearray(_finalize_eseq_header(
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
    ))
    if header_hint is not None and header_hint.slice_27_77 is not None:
        header[ESEQ_ORDER_KEY_START:ESEQ_TITLE_END + 1] = header_hint.slice_27_77
        header[0x27:0x32] = _sanitize_ascii_filename_key(filename_hint)
        header_title_start = ESEQ_TITLE_START - ESEQ_ORDER_KEY_START
        if _should_write_header_title(
            header_hint.slice_27_77[header_title_start:],
            merged_events,
            timing_hint,
            title_override,
        ):
            header[ESEQ_TITLE_START:ESEQ_TITLE_END + 1] = _encode_title_bytes(title)
    return _pad_eseq_output(bytes(header) + bytes(stream))


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
    midi_metadata_policy=DEFAULT_MIDI_METADATA_POLICY,
):
    with open(source_path, "rb") as handle:
        eseq_bytes = handle.read()
    payload = convert_eseq_bytes_to_midi_bytes(
        eseq_bytes,
        title_override=title_override,
        cc7_policy=cc7_policy,
        midi_metadata_policy=midi_metadata_policy,
    )
    _write_destination_bytes(dest_path, payload)


def convert_midi_file_to_eseq_path(
    source_path,
    dest_path,
    *,
    title_override=None,
    filename_hint="",
    cc7_policy=DEFAULT_CC7_POLICY,
    container_variant=None,
):
    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()
    resolved_variant = container_variant
    if resolved_variant is None:
        hint = filename_hint or dest_path
        resolved_variant = (
            ESEQ_CONTAINER_CLAVINOVA_MDA
            if os.path.splitext(hint)[1].lower() == ".mda"
            else ESEQ_CONTAINER_DISKLAVIER
        )
    payload = convert_midi_bytes_to_eseq_bytes(
        midi_bytes,
        title_override=title_override,
        filename_hint=filename_hint or os.path.basename(dest_path),
        cc7_policy=cc7_policy,
        container_variant=resolved_variant,
    )
    _write_destination_bytes(dest_path, payload)
