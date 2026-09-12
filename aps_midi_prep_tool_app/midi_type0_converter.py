import os
import shutil
import uuid
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil


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

DISKLAVIER_PIANO_CHANNEL = 0
DISKLAVIER_LEGACY_PEDAL_CHANNEL = 2
DISKLAVIER_ACOUSTIC_GRAND_PROGRAM = 0
DISKLAVIER_PEDAL_CONTROLLERS = {64, 66, 67}
VIRTUAL_PIANO_ROLL_SUSTAIN_NOTE = 18
VIRTUAL_PIANO_ROLL_SUSTAIN_VELOCITY = 1
MIDI_BANK_SELECT_CONTROLLERS = {0, 32}
MIDI_CHANNEL_MODE_CONTROLLERS = set(range(120, 128))
MIDI_NOTE_TERMINATION_CONTROLLERS = {120, 123, 124, 125, 126, 127}
SUSTAIN_PEDAL_CONTROLLER = 64
SUSTAIN_PEDAL_ON_THRESHOLD = 64
DEFAULT_MIDI_TEMPO_US = 500000
DEFAULT_PEDAL_RAMP_STEP_MS = 12


@dataclass(frozen=True)
class Type0ConversionResult:
    converted: list[str]
    unchanged: list[str]
    backups_created: list[str]
    failed: list[tuple[str, str]]


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

    format_type = int.from_bytes(midi_bytes[8:10], "big")
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
        chunks.append(
            {
                "id": chunk_id,
                "start": pos,
                "data_start": data_start,
                "data_end": data_end,
            }
        )
        pos = data_end

    return header_end, format_type, declared_track_count, chunks


def _parse_track_events(track_data):
    pos = 0
    end = len(track_data)
    abs_tick = 0
    running_status = None
    order = 0
    events = []

    while pos < end:
        delta, pos = _parse_vlq(track_data, pos, end)
        abs_tick += delta
        if pos >= end:
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
            if pos >= end:
                raise ValueError("Unexpected end of meta event.")

            meta_type = track_data[pos]
            pos += 1
            length_start = pos
            meta_len, pos = _parse_vlq(track_data, pos, end)
            payload_start = pos
            payload_end = payload_start + meta_len
            if payload_end > end:
                raise ValueError("Meta event exceeds track bounds.")

            if meta_type == 0x2F:
                # The declared track end is a boundary, even if its chunk
                # contains trailing padding or another recoverable fragment.
                # Preserve its delta while leaving later bytes out of music.
                break
            raw = b"\xFF" + bytes([meta_type]) + track_data[length_start:payload_end]
            events.append((abs_tick, order, raw))
            order += 1
            pos = payload_end
            continue

        if status in (0xF0, 0xF7):
            if not status_from_stream:
                raise ValueError("SysEx events cannot use running status.")
            length_start = pos
            sysex_len, pos = _parse_vlq(track_data, pos, end)
            payload_start = pos
            payload_end = payload_start + sysex_len
            if payload_end > end:
                raise ValueError("SysEx event exceeds track bounds.")
            raw = bytes([status]) + track_data[length_start:payload_end]
            events.append((abs_tick, order, raw))
            order += 1
            pos = payload_end
            running_status = None
            continue

        if 0x80 <= status <= 0xEF:
            msg_type = status & 0xF0
            data_len = 1 if msg_type in (0xC0, 0xD0) else 2
            if pos + data_len > end:
                raise ValueError("Channel event exceeds track bounds.")
            data = track_data[pos:pos + data_len]
            pos += data_len
            raw = bytes([status]) + data
            events.append((abs_tick, order, raw))
            order += 1
            running_status = status
            continue

        if not status_from_stream:
            raise ValueError("System messages cannot use running status.")

        data_len = _SYSTEM_MESSAGE_DATA_LENGTHS.get(status)
        if data_len is None:
            raise ValueError(f"Unsupported system status byte: 0x{status:02X}")
        if pos + data_len > end:
            raise ValueError("System message exceeds track bounds.")
        data = track_data[pos:pos + data_len]
        pos += data_len
        raw = bytes([status]) + data
        events.append((abs_tick, order, raw))
        order += 1
        running_status = None

    return events, abs_tick


def normalize_disklavier_raw_midi_event(raw):
    if (
        len(raw) >= 3
        and (raw[0] & 0xF0) == 0xB0
        and (raw[0] & 0x0F) == DISKLAVIER_LEGACY_PEDAL_CHANNEL
        and raw[1] in DISKLAVIER_PEDAL_CONTROLLERS
    ):
        return bytes([(raw[0] & 0xF0) | DISKLAVIER_PIANO_CHANNEL]) + raw[1:], True
    return raw, False


def is_disklavier_channel_note_event(raw, channel):
    return (
        len(raw) >= 3
        and (raw[0] & 0x0F) == channel
        and (raw[0] & 0xF0) in (0x80, 0x90)
    )


def _is_channel1_note_on(raw):
    return (
        len(raw) >= 3
        and (raw[0] & 0xF0) == 0x90
        and (raw[0] & 0x0F) == DISKLAVIER_PIANO_CHANNEL
        and raw[2] > 0
    )


def _is_channel1_program_change(raw):
    return (
        len(raw) >= 2
        and (raw[0] & 0xF0) == 0xC0
        and (raw[0] & 0x0F) == DISKLAVIER_PIANO_CHANNEL
    )


def _is_pedal_controller(raw, channel=None):
    if not (
        len(raw) >= 3
        and (raw[0] & 0xF0) == 0xB0
        and raw[1] in DISKLAVIER_PEDAL_CONTROLLERS
    ):
        return False
    return channel is None or (raw[0] & 0x0F) == channel


def _is_sustain_controller(raw):
    return len(raw) >= 3 and (raw[0] & 0xF0) == 0xB0 and raw[1] == 64


def _is_note_event_for_number(raw, note_number):
    return (
        len(raw) >= 3
        and (raw[0] & 0xF0) in (0x80, 0x90)
        and raw[1] == note_number
    )


def _replace_event_raw(event, raw):
    return (*event[:-1], raw)


def _event_sequence_value(event):
    if len(event) == 4:
        return event[2]
    return event[1]


def _make_synthetic_event_like(reference_event, abs_tick, sequence, raw):
    if len(reference_event) == 4:
        return (abs_tick, reference_event[1], sequence, raw)
    return (abs_tick, sequence, raw)


def _is_midi_system_reset(raw):
    """Recognize complete GM/GM2, GS, XG and escaped System Reset messages."""
    if raw == b"\xFF":
        return True
    if not raw or raw[0] not in (0xF0, 0xF7):
        return False
    try:
        length, start = _parse_vlq(raw, 1, len(raw))
    except ValueError:
        return False
    if start + length != len(raw):
        return False
    payload = raw[start:]
    if raw[0] == 0xF7:
        # F7 escape packets contain wire bytes, including the initial status.
        if payload == b"\xFF":
            return True
        if not payload.startswith(b"\xF0"):
            return False
        payload = payload[1:]
    return (
        len(payload) == 5
        and payload[0] == 0x7E
        and payload[1] < 0x80
        and payload[2] == 0x09
        and payload[3] in (0x01, 0x03)
        and payload[4] == 0xF7
    ) or (
        len(payload) == 10
        and payload[0] == 0x41
        and payload[1] < 0x20
        and payload[2:] == b"\x42\x12\x40\x00\x7F\x00\x41\xF7"
    ) or (
        len(payload) == 8
        and payload[0] == 0x43
        and (payload[1] & 0xF0) == 0x10
        and payload[2:] == b"\x4C\x00\x00\x7E\x00\xF7"
    )


def _expand_channel_note_terminations(events):
    """Prepare source note lifetimes before their channels are collapsed.

    Keep every attack, but defer releases of an overlapping pitch until its
    last source voice ends. Then balance every emitted Note On with a Note Off
    for receivers that stack repeated notes. CC120 uses note releases because
    a channel-wide immediate silence would also stop the other merged parts.
    """
    active_notes = Counter()
    active_pitches = Counter()
    deferred_releases = defaultdict(list)
    seen_notes = set()
    expanded = []
    changed = False

    def release_pitch(pitch, releases):
        deferred_releases[pitch].extend(releases)
        if active_pitches[pitch]:
            return []
        return deferred_releases.pop(pitch)

    for event in sorted(events, key=lambda item: item[:-1]):
        raw = event[-1]
        replacements = [raw]
        if _is_midi_system_reset(raw):
            active_notes.clear()
            active_pitches.clear()
            deferred_releases.clear()
        elif len(raw) >= 3 and 0x80 <= raw[0] <= 0xEF:
            message_type = raw[0] & 0xF0
            channel = raw[0] & 0x0F
            pitch = raw[1]
            key = (channel, pitch)
            if message_type == 0x90 and raw[2] > 0:
                active_notes[key] += 1
                active_pitches[pitch] += 1
                seen_notes.add(key)
            elif message_type in (0x80, 0x90):
                if active_notes[key]:
                    active_notes[key] -= 1
                    active_pitches[pitch] -= 1
                    if not active_notes[key]:
                        del active_notes[key]
                    replacements = release_pitch(pitch, [raw])
                elif key in seen_notes or active_pitches[pitch]:
                    # A late individual release after All Notes Off must not
                    # release a different part that now owns this pitch.
                    replacements = []
            elif message_type == 0xB0 and raw[1] in MIDI_NOTE_TERMINATION_CONTROLLERS:
                replacements = []
                for source_channel, note in sorted(active_notes):
                    if source_channel != channel:
                        continue
                    count = active_notes.pop((channel, note))
                    active_pitches[note] -= count
                    replacements.extend(
                        release_pitch(note, [bytes([0x80 | channel, note, 0])] * count)
                    )

        changed = changed or replacements != [raw]
        for replacement in replacements:
            expanded.append(
                _make_synthetic_event_like(event, event[0], len(expanded), replacement)
            )

    return (expanded, True) if changed else (events, False)


def _non_pedal_note_channels(events):
    channels = set()
    for event in events:
        raw = event[-1]
        if (
            len(raw) >= 3
            and (raw[0] & 0xF0) == 0x90
            and raw[2] > 0
            and raw[1] != VIRTUAL_PIANO_ROLL_SUSTAIN_NOTE
        ):
            channels.add(raw[0] & 0x0F)
    return channels


def _virtual_piano_roll_target_channel(source_channel, note_channels):
    if source_channel in note_channels:
        return source_channel
    if DISKLAVIER_PIANO_CHANNEL in note_channels:
        return DISKLAVIER_PIANO_CHANNEL
    if len(note_channels) == 1:
        return next(iter(note_channels))
    return source_channel


def apply_pedal_controller_options_to_midi_events(
    events,
    *,
    binary_pedal=False,
    pedal_cleanup=False,
    end_tick=None,
):
    if not events or not (binary_pedal or pedal_cleanup):
        return events, False

    tuple_size = len(events[0])
    if tuple_size not in (3, 4):
        raise ValueError("Unsupported MIDI event tuple shape for pedal cleanup.")

    source_events = sorted(events, key=lambda item: item[:-1])
    changed = False
    adjusted = []

    for event in source_events:
        raw = event[-1]
        if binary_pedal and _is_pedal_controller(raw):
            binary_value = 127 if raw[2] >= 64 else 0
            if raw[2] != binary_value:
                raw = raw[:2] + bytes([binary_value])
                event = _replace_event_raw(event, raw)
                changed = True
        adjusted.append(event)

    if not pedal_cleanup:
        return (adjusted, True) if changed else (events, False)

    cleaned = []
    previous_values = {}
    last_values = {}
    last_events = {}
    for event in adjusted:
        raw = event[-1]
        if len(raw) >= 3 and (raw[0] & 0xF0) == 0xB0 and raw[1] == 121:
            # The receiver reset these pedals, so neither deduplication nor
            # final pedal-up synthesis may rely on their pre-reset values.
            channel = raw[0] & 0x0F
            for controller in DISKLAVIER_PEDAL_CONTROLLERS:
                key = (channel, controller)
                previous_values.pop(key, None)
                last_values.pop(key, None)
                last_events.pop(key, None)
        elif _is_midi_system_reset(raw):
            previous_values.clear()
            last_values.clear()
            last_events.clear()
        if _is_pedal_controller(raw):
            key = (raw[0] & 0x0F, raw[1])
            value = raw[2]
            if previous_values.get(key) == value:
                changed = True
                continue
            previous_values[key] = value
            last_values[key] = value
            last_events[key] = event
        cleaned.append(event)

    if last_values:
        close_tick = max(
            max((event[0] for event in cleaned), default=0),
            int(end_tick or 0),
        )
        sequence = max((_event_sequence_value(event) for event in cleaned), default=-1) + 1
        for channel, controller in sorted(last_values):
            if last_values[(channel, controller)] <= 0:
                continue
            cleaned.append(
                _make_synthetic_event_like(
                    last_events[(channel, controller)],
                    close_tick,
                    sequence,
                    bytes([0xB0 | channel, controller, 0]),
                )
            )
            sequence += 1
            changed = True

    return (cleaned, True) if changed else (events, False)


def add_virtual_piano_roll_pedal_notes_to_midi_events(events, *, end_tick=None):
    if not events:
        return events, False

    tuple_size = len(events[0])
    if tuple_size not in (3, 4):
        raise ValueError("Unsupported MIDI event tuple shape for pedal-note conversion.")

    source_events = sorted(events, key=lambda item: item[:-1])
    note_channels = _non_pedal_note_channels(source_events)
    channels_with_note18 = {
        raw[0] & 0x0F
        for *_, raw in source_events
        if _is_note_event_for_number(raw, VIRTUAL_PIANO_ROLL_SUSTAIN_NOTE)
    }

    output = []
    active_targets = {}
    sequence = 0
    last_tick = 0
    changed = False

    for event in source_events:
        abs_tick = event[0]
        raw = event[-1]
        last_tick = max(last_tick, abs_tick)
        output.append(_make_synthetic_event_like(event, abs_tick, sequence, raw))
        sequence += 1

        if not _is_sustain_controller(raw):
            continue

        source_channel = raw[0] & 0x0F
        target_channel = _virtual_piano_roll_target_channel(source_channel, note_channels)
        if target_channel in channels_with_note18:
            continue

        if raw[2] > 0:
            if target_channel in active_targets:
                continue
            active_targets[target_channel] = event
            output.append(
                _make_synthetic_event_like(
                    event,
                    abs_tick,
                    sequence,
                    bytes([
                        0x90 | target_channel,
                        VIRTUAL_PIANO_ROLL_SUSTAIN_NOTE,
                        VIRTUAL_PIANO_ROLL_SUSTAIN_VELOCITY,
                    ]),
                )
            )
            sequence += 1
            changed = True
        elif target_channel in active_targets:
            source_event = active_targets.pop(target_channel)
            output.append(
                _make_synthetic_event_like(
                    source_event,
                    abs_tick,
                    sequence,
                    bytes([
                        0x80 | target_channel,
                        VIRTUAL_PIANO_ROLL_SUSTAIN_NOTE,
                        VIRTUAL_PIANO_ROLL_SUSTAIN_VELOCITY,
                    ]),
                )
            )
            sequence += 1
            changed = True

    close_tick = max(last_tick, int(end_tick or 0))
    for target_channel, source_event in sorted(active_targets.items()):
        output.append(
            _make_synthetic_event_like(
                source_event,
                close_tick,
                sequence,
                bytes([
                    0x80 | target_channel,
                    VIRTUAL_PIANO_ROLL_SUSTAIN_NOTE,
                    VIRTUAL_PIANO_ROLL_SUSTAIN_VELOCITY,
                ]),
            )
        )
        sequence += 1
        changed = True

    return output, changed


def _tempo_from_raw_midi_event(raw):
    if len(raw) < 6 or raw[:2] != b"\xFF\x51":
        return None
    try:
        payload_length, payload_start = _parse_vlq(raw, 2, len(raw))
    except ValueError:
        return None
    payload_end = payload_start + payload_length
    if payload_length != 3 or payload_end > len(raw):
        return None
    tempo = int.from_bytes(raw[payload_start:payload_end], "big")
    return tempo if tempo > 0 else None


class _MidiTimeMap:
    def __init__(self, division, tempo_events):
        self.division = int(division)
        self.smpte = bool(self.division & 0x8000)
        self.segment_ticks = []
        self.segment_milliseconds = []
        self.segment_tempos = []

        if self.smpte:
            raw_frames = (self.division >> 8) & 0xFF
            signed_frames = raw_frames - 0x100 if raw_frames >= 0x80 else raw_frames
            frame_code = abs(signed_frames)
            frames_per_second = 29.97 if frame_code == 29 else frame_code
            ticks_per_frame = self.division & 0xFF
            if not frames_per_second or not ticks_per_frame:
                raise ValueError("The MIDI file contains an invalid SMPTE time division.")
            self.ticks_per_second = frames_per_second * ticks_per_frame
            return

        self.ppqn = self.division & 0x7FFF
        if not self.ppqn:
            raise ValueError("The MIDI file contains an invalid time division of zero.")

        tempo_by_tick = {}
        for tick, track_index, order, tempo in sorted(
            tempo_events,
            key=lambda item: (item[0], item[1], item[2]),
        ):
            tempo_by_tick[int(tick)] = int(tempo)

        current_tick = 0
        current_milliseconds = 0.0
        current_tempo = tempo_by_tick.get(0, DEFAULT_MIDI_TEMPO_US)
        self.segment_ticks.append(0)
        self.segment_milliseconds.append(0.0)
        self.segment_tempos.append(current_tempo)

        for tick in sorted(value for value in tempo_by_tick if value > 0):
            current_milliseconds += (
                (tick - current_tick) * current_tempo / self.ppqn / 1000.0
            )
            current_tick = tick
            current_tempo = tempo_by_tick[tick]
            self.segment_ticks.append(current_tick)
            self.segment_milliseconds.append(current_milliseconds)
            self.segment_tempos.append(current_tempo)

    def tick_to_milliseconds(self, tick):
        safe_tick = max(0.0, float(tick or 0))
        if self.smpte:
            return safe_tick * 1000.0 / self.ticks_per_second
        index = max(0, bisect_right(self.segment_ticks, safe_tick) - 1)
        return self.segment_milliseconds[index] + (
            (safe_tick - self.segment_ticks[index])
            * self.segment_tempos[index]
            / self.ppqn
            / 1000.0
        )

    def milliseconds_to_tick(self, milliseconds):
        safe_milliseconds = max(0.0, float(milliseconds or 0))
        if self.smpte:
            return safe_milliseconds * self.ticks_per_second / 1000.0
        index = max(
            0,
            bisect_right(self.segment_milliseconds, safe_milliseconds) - 1,
        )
        return self.segment_ticks[index] + (
            (safe_milliseconds - self.segment_milliseconds[index])
            * 1000.0
            * self.ppqn
            / self.segment_tempos[index]
        )


def _pedal_softening_time_maps(format_type, division, track_event_groups):
    all_tempo_events = []
    per_track_tempo_events = [[] for _ in track_event_groups]
    for track_index, track_info in enumerate(track_event_groups):
        for tick, order, raw in track_info["events"]:
            tempo = _tempo_from_raw_midi_event(raw)
            if tempo is None:
                continue
            event = (tick, track_index, order, tempo)
            all_tempo_events.append(event)
            per_track_tempo_events[track_index].append(event)

    return [
        _MidiTimeMap(
            division,
            per_track_tempo_events[track_index]
            if format_type == 2
            else all_tempo_events,
        )
        for track_index in range(len(track_event_groups))
    ]


def _compact_pedal_values(events):
    values = []
    for _tick, _order, raw in events:
        value = raw[2]
        if not values or values[-1] != value:
            values.append(value)
    return values


def _has_monotonic_pedal_ramp(values):
    if len(values) < 4:
        return False

    for start in range(len(values) - 3):
        direction = 0
        minimum = values[start]
        maximum = values[start]
        count = 1
        for index in range(start + 1, len(values)):
            difference = values[index] - values[index - 1]
            if difference == 0:
                continue
            next_direction = 1 if difference > 0 else -1
            if direction == 0:
                direction = next_direction
            elif next_direction != direction:
                break
            minimum = min(minimum, values[index])
            maximum = max(maximum, values[index])
            count += 1
            if (
                count >= 4
                and maximum - minimum >= 28
                and any(3 < value < 124 for value in values[start:index + 1])
            ):
                return True
    return False


def _pedal_state_transitions(events):
    transitions = []
    state = 0
    for event in events:
        next_state = 1 if event[-1][2] >= SUSTAIN_PEDAL_ON_THRESHOLD else 0
        if next_state == state:
            continue
        transitions.append({"event": event, "from": state, "to": next_state})
        state = next_state
    return transitions


def _classify_pedal_stream(events):
    compact_values = _compact_pedal_values(events)
    unique_values = sorted(set(compact_values))
    transitions = _pedal_state_transitions(events)
    if not transitions:
        return "static", unique_values, transitions

    intermediate_values = [value for value in unique_values if 3 < value < 124]
    looks_continuous = (
        _has_monotonic_pedal_ramp(compact_values)
        or len(intermediate_values) >= 3
        or len(unique_values) >= 6
    )
    return (
        "continuous" if looks_continuous else "binary",
        unique_values,
        transitions,
    )


def _pedal_softening_streams(track_event_groups, time_maps):
    streams = []
    for track_index, track_info in enumerate(track_event_groups):
        events_by_channel = {}
        for event in track_info["events"]:
            raw = event[-1]
            if not _is_sustain_controller(raw):
                continue
            events_by_channel.setdefault(raw[0] & 0x0F, []).append(event)

        for channel, events in sorted(events_by_channel.items()):
            events.sort(key=lambda item: (item[0], item[1]))
            classification, unique_values, transitions = _classify_pedal_stream(events)
            streams.append(
                {
                    "track_index": track_index,
                    "channel": channel,
                    "events": events,
                    "classification": classification,
                    "unique_values": unique_values,
                    "transitions": transitions,
                    "time_map": time_maps[track_index],
                }
            )
    return streams


def analyze_pedal_softening_midi_bytes(midi_bytes):
    """Describe which sustain-pedal streams can safely be softened."""
    _header_end, format_type, _, chunks = _parse_midi_chunks(midi_bytes)
    if format_type not in (0, 1, 2):
        raise ValueError(f"MIDI format {format_type} is not a Standard MIDI File type.")
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]
    if not track_chunks:
        raise ValueError("No track chunks were found in this MIDI file.")
    division = int.from_bytes(midi_bytes[12:14], "big")
    track_event_groups, _max_end_tick = _read_track_event_groups(
        midi_bytes,
        track_chunks,
    )
    time_maps = _pedal_softening_time_maps(
        format_type,
        division,
        track_event_groups,
    )
    streams = _pedal_softening_streams(track_event_groups, time_maps)
    counts = {
        kind: sum(stream["classification"] == kind for stream in streams)
        for kind in ("binary", "continuous", "static")
    }
    if counts["binary"] and counts["continuous"]:
        classification = "mixed"
    elif counts["binary"]:
        classification = "binary"
    elif counts["continuous"]:
        classification = "continuous"
    elif counts["static"]:
        classification = "static"
    else:
        classification = "none"
    return {
        "classification": classification,
        "stream_count": len(streams),
        "binary_stream_count": counts["binary"],
        "continuous_stream_count": counts["continuous"],
        "static_stream_count": counts["static"],
        "pedal_message_count": sum(len(stream["events"]) for stream in streams),
        "channels": sorted({stream["channel"] + 1 for stream in streams}),
    }


def _normalized_pedal_softening_options(down_ms, release_ms, step_ms):
    def normalized(value, default, minimum, maximum):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = default
        if numeric <= 0:
            numeric = default
        return max(minimum, min(maximum, numeric))

    return (
        normalized(down_ms, 100, 20, 2000),
        normalized(release_ms, 180, 20, 2000),
        normalized(step_ms, DEFAULT_PEDAL_RAMP_STEP_MS, 5, 50),
    )


def _smoothstep(value):
    clamped = max(0.0, min(1.0, float(value)))
    return clamped * clamped * (3.0 - (2.0 * clamped))


def _make_pedal_ramp_half(
    *,
    start_ms,
    end_ms,
    start_value,
    end_value,
    time_map,
    step_ms,
    anchor_ms,
    anchor_order,
    channel,
    include_start=True,
):
    points = []
    duration = max(0.0, end_ms - start_ms)
    steps = max(1, int(ceil(duration / step_ms)))
    first_index = 0 if include_start else 1
    for index in range(first_index, steps + 1):
        ratio = index / steps
        point_ms = start_ms + (duration * ratio)
        value = round(start_value + ((end_value - start_value) * _smoothstep(ratio)))
        relative = max(
            -0.45,
            min(0.45, (point_ms - anchor_ms) / max(1.0, duration * 4.0)),
        )
        points.append(
            (
                max(0, round(time_map.milliseconds_to_tick(point_ms))),
                anchor_order + relative,
                bytes([
                    0xB0 | channel,
                    SUSTAIN_PEDAL_CONTROLLER,
                    max(0, min(127, value)),
                ]),
                point_ms,
            )
        )
    return points


def _soften_pedal_stream_events(stream, *, down_ms, release_ms, step_ms):
    transitions = stream["transitions"]
    if not transitions:
        return []

    generated = []
    first_event = stream["events"][0]
    if (
        first_event[-1][2] < SUSTAIN_PEDAL_ON_THRESHOLD
        and first_event[0] <= transitions[0]["event"][0]
    ):
        generated.append(
            (
                first_event[0],
                first_event[1],
                bytes([0xB0 | stream["channel"], SUSTAIN_PEDAL_CONTROLLER, 0]),
                stream["time_map"].tick_to_milliseconds(first_event[0]),
            )
        )

    transition_times = [
        stream["time_map"].tick_to_milliseconds(transition["event"][0])
        for transition in transitions
    ]
    for index, transition in enumerate(transitions):
        center_ms = transition_times[index]
        duration = down_ms if transition["to"] == 1 else release_ms
        previous_center = transition_times[index - 1] if index > 0 else None
        next_center = (
            transition_times[index + 1]
            if index < len(transition_times) - 1
            else None
        )
        left_limit = 0.0 if previous_center is None else (previous_center + center_ms) / 2.0
        right_limit = float("inf") if next_center is None else (center_ms + next_center) / 2.0
        start_ms = max(0.0, left_limit, center_ms - (duration / 2.0))
        end_ms = max(center_ms, min(right_limit, center_ms + (duration / 2.0)))
        anchor_order = transition["event"][1]
        threshold_event = (
            transition["event"][0],
            anchor_order,
            bytes([
                0xB0 | stream["channel"],
                SUSTAIN_PEDAL_CONTROLLER,
                64 if transition["to"] == 1 else 63,
            ]),
            center_ms,
        )

        if transition["to"] == 1:
            before_threshold = _make_pedal_ramp_half(
                start_ms=start_ms,
                end_ms=center_ms,
                start_value=0,
                end_value=63,
                time_map=stream["time_map"],
                step_ms=step_ms,
                anchor_ms=center_ms,
                anchor_order=anchor_order,
                channel=stream["channel"],
            )
            after_threshold = _make_pedal_ramp_half(
                start_ms=center_ms,
                end_ms=end_ms,
                start_value=64,
                end_value=127,
                time_map=stream["time_map"],
                step_ms=step_ms,
                anchor_ms=center_ms,
                anchor_order=anchor_order,
                channel=stream["channel"],
                include_start=False,
            )
        else:
            before_threshold = _make_pedal_ramp_half(
                start_ms=start_ms,
                end_ms=center_ms,
                start_value=127,
                end_value=64,
                time_map=stream["time_map"],
                step_ms=step_ms,
                anchor_ms=center_ms,
                anchor_order=anchor_order,
                channel=stream["channel"],
            )
            after_threshold = _make_pedal_ramp_half(
                start_ms=center_ms,
                end_ms=end_ms,
                start_value=63,
                end_value=0,
                time_map=stream["time_map"],
                step_ms=step_ms,
                anchor_ms=center_ms,
                anchor_order=anchor_order,
                channel=stream["channel"],
                include_start=False,
            )

        if before_threshold:
            last_tick, _last_order, last_raw, last_ms = before_threshold[-1]
            before_threshold[-1] = (last_tick, anchor_order - 0.001, last_raw, last_ms)
        generated.extend(before_threshold)
        generated.append(threshold_event)
        generated.extend(after_threshold)

    generated.sort(key=lambda item: (item[3], item[0], item[1]))
    cleaned = []
    for event in generated:
        if cleaned:
            previous = cleaned[-1]
            if (
                previous[0] == event[0]
                and previous[2] == event[2]
                and abs(previous[1] - event[1]) < 0.00001
            ):
                continue
        cleaned.append(event)
    return [(tick, order, raw) for tick, order, raw, _point_ms in cleaned]


def _soften_binary_sustain_pedal_streams(
    track_event_groups,
    *,
    format_type,
    division,
    down_ms,
    release_ms,
    step_ms=DEFAULT_PEDAL_RAMP_STEP_MS,
):
    down_ms, release_ms, step_ms = _normalized_pedal_softening_options(
        down_ms,
        release_ms,
        step_ms,
    )
    time_maps = _pedal_softening_time_maps(
        format_type,
        division,
        track_event_groups,
    )
    streams = _pedal_softening_streams(track_event_groups, time_maps)
    binary_streams = [
        stream for stream in streams if stream["classification"] == "binary"
    ]
    if not binary_streams:
        return False

    for stream in binary_streams:
        track_info = track_event_groups[stream["track_index"]]
        track_info["events"] = [
            event
            for event in track_info["events"]
            if not (
                _is_sustain_controller(event[-1])
                and (event[-1][0] & 0x0F) == stream["channel"]
            )
        ]
        track_info["events"].extend(
            _soften_pedal_stream_events(
                stream,
                down_ms=down_ms,
                release_ms=release_ms,
                step_ms=step_ms,
            )
        )
        track_info["events"].sort(key=lambda item: (item[0], item[1]))
    return True


def _build_raw_midi_track(events, end_tick=0):
    track = bytearray()
    prev_tick = 0
    for abs_tick, order, raw in sorted(events, key=lambda item: (item[0], item[1])):
        if abs_tick < prev_tick:
            raise ValueError("MIDI events are out of order.")
        track.extend(_encode_vlq(abs_tick - prev_tick))
        track.extend(raw)
        prev_tick = abs_tick

    close_tick = max(prev_tick, int(end_tick or 0))
    track.extend(_encode_vlq(close_tick - prev_tick))
    track.extend(b"\xFF\x2F\x00")
    return bytes(track)


def _read_track_event_groups(midi_bytes, track_chunks):
    track_event_groups = []
    max_end_tick = 0
    for chunk in track_chunks:
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, end_tick = _parse_track_events(track_data)
        track_event_groups.append({"events": list(events), "end_tick": end_tick})
        max_end_tick = max(max_end_tick, end_tick)
    return track_event_groups, max_end_tick


def _merge_track_event_groups(track_event_groups):
    merged = []
    for track_index, track_info in enumerate(track_event_groups):
        for abs_tick, order, raw in track_info["events"]:
            merged.append((abs_tick, track_index, order, raw))
    merged.sort(key=lambda item: (item[0], item[1], item[2]))
    return merged


def _replace_track_event_groups_from_merged(track_event_groups, merged_events):
    grouped = [[] for _ in track_event_groups]
    for abs_tick, track_index, order, raw in merged_events:
        if track_index < 0 or track_index >= len(grouped):
            raise ValueError("Pedal transform produced an invalid MIDI track index.")
        grouped[track_index].append((abs_tick, order, raw))
    for track_index, events in enumerate(grouped):
        track_event_groups[track_index]["events"] = events


def _rebuild_midi_with_track_event_groups(midi_bytes, header_end, chunks, track_event_groups):
    rebuilt = bytearray(midi_bytes[:header_end])
    track_index = 0
    for chunk in chunks:
        if chunk["id"] == b"MTrk":
            track_info = track_event_groups[track_index]
            track_data = _build_raw_midi_track(
                track_info["events"],
                end_tick=track_info["end_tick"],
            )
            rebuilt.extend(b"MTrk")
            rebuilt.extend(len(track_data).to_bytes(4, "big"))
            rebuilt.extend(track_data)
            track_index += 1
            continue
        rebuilt.extend(midi_bytes[chunk["start"]:chunk["data_end"]])

    trailing_start = chunks[-1]["data_end"] if chunks else header_end
    rebuilt.extend(midi_bytes[trailing_start:])
    return bytes(rebuilt)


def apply_pedal_compatibility_to_midi_bytes(
    midi_bytes,
    *,
    repair_disklavier_pedal=False,
    binary_pedal=False,
    pedal_cleanup=False,
    virtual_piano_roll_pedal=False,
    soften_sustain_pedal=False,
    pedal_down_ms=100,
    pedal_release_ms=180,
    pedal_ramp_step_ms=DEFAULT_PEDAL_RAMP_STEP_MS,
):
    if not (
        repair_disklavier_pedal
        or binary_pedal
        or pedal_cleanup
        or virtual_piano_roll_pedal
        or soften_sustain_pedal
    ):
        return midi_bytes, False

    if binary_pedal and soften_sustain_pedal:
        raise ValueError(
            "Pedal softening cannot be combined with conversion to on/off pedal values."
        )

    header_end, format_type, _, chunks = _parse_midi_chunks(midi_bytes)
    if soften_sustain_pedal and format_type not in (0, 1, 2):
        raise ValueError(f"MIDI format {format_type} is not a Standard MIDI File type.")
    if format_type == 2 and (
        repair_disklavier_pedal
        or binary_pedal
        or pedal_cleanup
        or virtual_piano_roll_pedal
    ):
        raise ValueError("MIDI format 2 files are not supported for pedal compatibility utilities.")

    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]
    if not track_chunks:
        raise ValueError("No track chunks were found in this MIDI file.")

    track_event_groups, max_end_tick = _read_track_event_groups(midi_bytes, track_chunks)
    merged_events = _merge_track_event_groups(track_event_groups)
    changed = False

    if repair_disklavier_pedal:
        merged_events, normalization_changed = _normalize_disklavier_merged_events(merged_events)
        changed = changed or normalization_changed

    if binary_pedal or pedal_cleanup:
        merged_events, pedal_options_changed = apply_pedal_controller_options_to_midi_events(
            merged_events,
            binary_pedal=binary_pedal,
            pedal_cleanup=pedal_cleanup,
            end_tick=max_end_tick,
        )
        changed = changed or pedal_options_changed

    if virtual_piano_roll_pedal:
        merged_events, pedal_note_changed = add_virtual_piano_roll_pedal_notes_to_midi_events(
            merged_events,
            end_tick=max_end_tick,
        )
        changed = changed or pedal_note_changed

    if changed:
        _replace_track_event_groups_from_merged(track_event_groups, merged_events)

    if soften_sustain_pedal:
        division = int.from_bytes(midi_bytes[12:14], "big")
        softening_changed = _soften_binary_sustain_pedal_streams(
            track_event_groups,
            format_type=format_type,
            division=division,
            down_ms=pedal_down_ms,
            release_ms=pedal_release_ms,
            step_ms=pedal_ramp_step_ms,
        )
        changed = changed or softening_changed

    if not changed:
        return midi_bytes, False

    rebuilt = _rebuild_midi_with_track_event_groups(midi_bytes, header_end, chunks, track_event_groups)
    return rebuilt, rebuilt != midi_bytes


def apply_pedal_compatibility_to_midi_path(
    source_path,
    dest_path,
    *,
    repair_disklavier_pedal=False,
    binary_pedal=False,
    pedal_cleanup=False,
    virtual_piano_roll_pedal=False,
    soften_sustain_pedal=False,
    pedal_down_ms=100,
    pedal_release_ms=180,
    pedal_ramp_step_ms=DEFAULT_PEDAL_RAMP_STEP_MS,
):
    if not os.path.isfile(source_path):
        raise ValueError("File does not exist.")

    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()

    converted_bytes, changed = apply_pedal_compatibility_to_midi_bytes(
        midi_bytes,
        repair_disklavier_pedal=repair_disklavier_pedal,
        binary_pedal=binary_pedal,
        pedal_cleanup=pedal_cleanup,
        virtual_piano_roll_pedal=virtual_piano_roll_pedal,
        soften_sustain_pedal=soften_sustain_pedal,
        pedal_down_ms=pedal_down_ms,
        pedal_release_ms=pedal_release_ms,
        pedal_ramp_step_ms=pedal_ramp_step_ms,
    )
    if not changed:
        return False

    temp_path = f"{dest_path}.aps_pedal_{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(converted_bytes)
        os.replace(temp_path, dest_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return True


def _channel1_acoustic_grand_event():
    return bytes([0xC0 | DISKLAVIER_PIANO_CHANNEL, DISKLAVIER_ACOUSTIC_GRAND_PROGRAM])


def _disklavier_normalized_event_dedupe_key(abs_tick, raw):
    if not raw or not (0x80 <= raw[0] <= 0xEF):
        return None
    status = raw[0] & 0xF0
    channel = raw[0] & 0x0F
    if (
        len(raw) >= 3
        and status == 0xB0
        and channel == DISKLAVIER_PIANO_CHANNEL
        and raw[1] in DISKLAVIER_PEDAL_CONTROLLERS
    ):
        return abs_tick, raw
    if status == 0xC0 and channel == DISKLAVIER_PIANO_CHANNEL:
        return abs_tick, raw
    return None


def _normalize_disklavier_merged_events(merged_events):
    normalized = []
    changed = False
    first_channel1_note_tick = None
    first_channel1_note_track = 0
    has_channel1_program_before_notes = False
    legacy_pedal_channel_has_notes = False
    channel1_has_pedal_controller = False

    for abs_tick, track_index, order, raw in merged_events:
        if _is_channel1_note_on(raw):
            if first_channel1_note_tick is None or abs_tick < first_channel1_note_tick:
                first_channel1_note_tick = abs_tick
                first_channel1_note_track = track_index
        if is_disklavier_channel_note_event(raw, DISKLAVIER_LEGACY_PEDAL_CHANNEL):
            legacy_pedal_channel_has_notes = True
        if _is_pedal_controller(raw, DISKLAVIER_PIANO_CHANNEL):
            channel1_has_pedal_controller = True

    should_remap_legacy_pedal = (
        first_channel1_note_tick is not None
        and not legacy_pedal_channel_has_notes
        and not channel1_has_pedal_controller
    )
    for abs_tick, track_index, order, raw in merged_events:
        if should_remap_legacy_pedal:
            normalized_raw, event_changed = normalize_disklavier_raw_midi_event(raw)
            changed = changed or event_changed
        else:
            normalized_raw = raw
        normalized.append((abs_tick, track_index, order, normalized_raw))

    if first_channel1_note_tick is not None:
        for abs_tick, _, _, raw in normalized:
            if abs_tick <= first_channel1_note_tick and _is_channel1_program_change(raw):
                has_channel1_program_before_notes = True
                break
        if not has_channel1_program_before_notes:
            normalized.append((0, first_channel1_note_track, -1, _channel1_acoustic_grand_event()))
            changed = True

    deduped = []
    seen_channel_events = set()
    for event in sorted(normalized, key=lambda item: (item[0], item[1], item[2])):
        abs_tick, _, _, raw = event
        key = _disklavier_normalized_event_dedupe_key(abs_tick, raw)
        if key is not None:
            if key in seen_channel_events:
                changed = True
                continue
            seen_channel_events.add(key)
        deduped.append(event)

    return deduped, changed


def _remap_merged_events_to_piano_channel0(merged_events):
    merged_events, changed = _expand_channel_note_terminations(merged_events)
    remapped = []
    first_note_track = 0
    has_notes = False

    for abs_tick, track_index, order, raw in merged_events:
        if not raw or not (0x80 <= raw[0] <= 0xEF):
            remapped.append((abs_tick, track_index, order, raw))
            continue

        message_type = raw[0] & 0xF0
        channel = raw[0] & 0x0F
        if message_type in (0x80, 0x90):
            if not has_notes:
                first_note_track = track_index
            has_notes = True

        # Once channels are combined, bank changes and channel-mode commands
        # from one former part can change the instrument or silence every part.
        if message_type == 0xB0 and len(raw) >= 3:
            if raw[1] in MIDI_BANK_SELECT_CONTROLLERS | MIDI_CHANNEL_MODE_CONTROLLERS:
                changed = True
                continue

        # Replace all source program changes with one deterministic piano
        # selection at tick zero after the complete event stream is remapped.
        if message_type == 0xC0:
            changed = True
            continue

        remapped_raw = bytes([message_type | DISKLAVIER_PIANO_CHANNEL]) + raw[1:]
        changed = changed or channel != DISKLAVIER_PIANO_CHANNEL
        remapped.append((abs_tick, track_index, order, remapped_raw))

    if has_notes:
        remapped.append((0, first_note_track, -2, _channel1_acoustic_grand_event()))
        changed = True

    remapped.sort(key=lambda item: (item[0], item[1], item[2]))
    return remapped, changed


def _convert_midi_bytes_to_type0(
    midi_bytes,
    *,
    normalize_disklavier=False,
    remap_all_instruments_to_channel0=False,
):
    header_end, format_type, _, chunks = _parse_midi_chunks(midi_bytes)
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]

    if (
        format_type == 0
        and not normalize_disklavier
        and not remap_all_instruments_to_channel0
    ):
        return midi_bytes, False
    if format_type == 2:
        raise ValueError("MIDI format 2 files are not supported for Type 0 conversion.")
    if not track_chunks:
        raise ValueError("No track chunks were found in this MIDI file.")

    merged_events = []
    max_end_tick = 0
    for track_index, chunk in enumerate(track_chunks):
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, end_tick = _parse_track_events(track_data)
        if end_tick > max_end_tick:
            max_end_tick = end_tick
        for abs_tick, order, raw in events:
            merged_events.append((abs_tick, track_index, order, raw))

    merged_events.sort(key=lambda item: (item[0], item[1], item[2]))
    changed = format_type != 0
    if normalize_disklavier:
        merged_events, normalization_changed = _normalize_disklavier_merged_events(merged_events)
        changed = changed or normalization_changed
    if remap_all_instruments_to_channel0:
        merged_events, remap_changed = _remap_merged_events_to_piano_channel0(merged_events)
        changed = changed or remap_changed

    if not changed:
        return midi_bytes, False

    merged_track = bytearray()
    prev_tick = 0
    for abs_tick, _, _, raw in merged_events:
        merged_track.extend(_encode_vlq(abs_tick - prev_tick))
        merged_track.extend(raw)
        prev_tick = abs_tick

    merged_track.extend(_encode_vlq(max_end_tick - prev_tick))
    merged_track.extend(b"\xFF\x2F\x00")
    merged_chunk = b"MTrk" + len(merged_track).to_bytes(4, "big") + bytes(merged_track)

    header = bytearray(midi_bytes[:header_end])
    header[8:10] = (0).to_bytes(2, "big")
    header[10:12] = (1).to_bytes(2, "big")

    rebuilt = bytearray(header)
    inserted_track = False
    for chunk in chunks:
        chunk_bytes = midi_bytes[chunk["start"]:chunk["data_end"]]
        if chunk["id"] == b"MTrk":
            if not inserted_track:
                rebuilt.extend(merged_chunk)
                inserted_track = True
            continue
        rebuilt.extend(chunk_bytes)

    return bytes(rebuilt), True


def _unique_abs_paths(file_paths):
    seen = set()
    unique = []
    for path in file_paths:
        abs_path = os.path.abspath(path)
        key = os.path.normcase(abs_path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(abs_path)
    return unique


def _default_backup_path(file_path):
    stem, ext = os.path.splitext(file_path)
    return f"{stem}_backup{ext}"


def convert_midi_file_to_type0_path(
    source_path,
    dest_path,
    *,
    normalize_disklavier=False,
    remap_all_instruments_to_channel0=False,
):
    if not os.path.isfile(source_path):
        raise ValueError("File does not exist.")

    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()

    converted_bytes, changed = _convert_midi_bytes_to_type0(
        midi_bytes,
        normalize_disklavier=normalize_disklavier,
        remap_all_instruments_to_channel0=remap_all_instruments_to_channel0,
    )
    if not changed:
        return False

    temp_path = f"{dest_path}.aps_type0_{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(converted_bytes)
        os.replace(temp_path, dest_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return True


def convert_midi_files_to_type0(
    file_paths,
    create_backups=False,
    backup_path_builder=None,
    *,
    normalize_disklavier=False,
    remap_all_instruments_to_channel0=False,
):
    unique_paths = _unique_abs_paths(file_paths)
    backup_path_builder = backup_path_builder or _default_backup_path

    converted = []
    unchanged = []
    backups_created = []
    failed = []

    for file_path in unique_paths:
        if not os.path.isfile(file_path):
            failed.append((file_path, "File does not exist."))
            continue

        try:
            with open(file_path, "rb") as handle:
                midi_bytes = handle.read()

            converted_bytes, changed = _convert_midi_bytes_to_type0(
                midi_bytes,
                normalize_disklavier=normalize_disklavier,
                remap_all_instruments_to_channel0=remap_all_instruments_to_channel0,
            )
            if not changed:
                unchanged.append(file_path)
                continue

            if create_backups:
                backup_path = backup_path_builder(file_path)
                shutil.copy2(file_path, backup_path)
                backups_created.append(backup_path)

            temp_path = f"{file_path}.aps_type0_{uuid.uuid4().hex}.tmp"
            try:
                with open(temp_path, "wb") as handle:
                    handle.write(converted_bytes)
                os.replace(temp_path, file_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            converted.append(file_path)
        except Exception as exc:
            failed.append((file_path, str(exc)))

    return Type0ConversionResult(
        converted=converted,
        unchanged=unchanged,
        backups_created=backups_created,
        failed=failed,
    )
