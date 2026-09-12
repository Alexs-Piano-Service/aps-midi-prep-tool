import os
import uuid

from .midi_type0_converter import (
    MIDI_BANK_SELECT_CONTROLLERS,
    MIDI_CHANNEL_MODE_CONTROLLERS,
    _build_raw_midi_track,
    _expand_channel_note_terminations,
    _merge_track_event_groups,
    _parse_track_events,
    _parse_vlq,
    _replace_event_raw,
    _replace_track_event_groups_from_merged,
)


PIANO_CHANNEL = 0
ACOUSTIC_GRAND_PROGRAM = 0
_CHANNEL_PREFIX_META_TYPE = 0x20


def _parse_smf_layout(midi_bytes):
    if len(midi_bytes) < 14 or midi_bytes[:4] != b"MThd":
        raise ValueError("This is not a valid Standard MIDI File.")

    header_length = int.from_bytes(midi_bytes[4:8], "big")
    header_end = 8 + header_length
    if header_length < 6 or header_end > len(midi_bytes):
        raise ValueError("The MIDI header is invalid or truncated.")

    format_type = int.from_bytes(midi_bytes[8:10], "big")
    track_count = int.from_bytes(midi_bytes[10:12], "big")
    division = int.from_bytes(midi_bytes[12:14], "big")
    if format_type not in (0, 1, 2):
        raise ValueError(f"MIDI format {format_type} is not a Standard MIDI File type.")
    if (format_type == 0 and track_count != 1) or (
        format_type in (1, 2) and track_count < 1
    ):
        raise ValueError("The MIDI header contains an invalid track count.")

    if division & 0x8000:
        frame_code = 0x100 - ((division >> 8) & 0xFF)
        if frame_code not in (24, 25, 29, 30) or (division & 0xFF) == 0:
            raise ValueError("The MIDI header contains an invalid SMPTE time division.")
    elif division == 0:
        raise ValueError("The MIDI header contains an invalid time division of zero.")

    chunks = []
    found_tracks = 0
    offset = header_end
    while found_tracks < track_count:
        if offset + 8 > len(midi_bytes):
            raise ValueError("A declared MIDI track is missing or malformed.")
        chunk_length = int.from_bytes(midi_bytes[offset + 4:offset + 8], "big")
        data_start = offset + 8
        data_end = data_start + chunk_length
        if data_end > len(midi_bytes):
            raise ValueError("A MIDI chunk is truncated.")
        chunk = {
            "id": midi_bytes[offset:offset + 4],
            "start": offset,
            "data_start": data_start,
            "data_end": data_end,
        }
        chunks.append(chunk)
        if chunk["id"] == b"MTrk":
            found_tracks += 1
        offset = data_end

    return header_end, format_type, chunks, offset


def _remap_channel_prefix(raw):
    if len(raw) < 4 or raw[:2] != bytes([0xFF, _CHANNEL_PREFIX_META_TYPE]):
        return raw, False

    payload_length, payload_start = _parse_vlq(raw, 2, len(raw))
    payload_end = payload_start + payload_length
    if payload_length != 1 or payload_end != len(raw):
        return raw, False

    channel = raw[payload_start]
    if channel == PIANO_CHANNEL or channel > 0x0F:
        return raw, False
    return raw[:payload_start] + bytes([PIANO_CHANNEL]), True


def _merge_track_events(events):
    events, changed = _expand_channel_note_terminations(events)
    merged = []
    has_notes = False

    for event in events:
        raw = event[-1]
        remapped_meta, meta_changed = _remap_channel_prefix(raw)
        if meta_changed:
            merged.append(_replace_event_raw(event, remapped_meta))
            changed = True
            continue

        if not raw or not (0x80 <= raw[0] <= 0xEF):
            merged.append(event)
            continue

        message_type = raw[0] & 0xF0
        channel = raw[0] & 0x0F
        if message_type in (0x80, 0x90):
            has_notes = True

        if message_type == 0xB0 and raw[1] in (
            MIDI_BANK_SELECT_CONTROLLERS | MIDI_CHANNEL_MODE_CONTROLLERS
        ):
            changed = True
            continue
        if message_type == 0xC0:
            changed = True
            continue

        remapped = bytes([message_type | PIANO_CHANNEL]) + raw[1:]
        merged.append(_replace_event_raw(event, remapped))
        changed = changed or channel != PIANO_CHANNEL

    return merged, changed, has_notes


def _acoustic_grand_program_event():
    return bytes([0xC0 | PIANO_CHANNEL, ACOUSTIC_GRAND_PROGRAM])


def _is_canonical_channel_merge(format_type, tracks):
    note_track_indexes = []
    program_events = []

    for track_index, track in enumerate(tracks):
        has_notes = False
        for abs_tick, order, raw in track["original_events"]:
            _remapped_meta, meta_changed = _remap_channel_prefix(raw)
            if meta_changed:
                return False
            if not raw or not (0x80 <= raw[0] <= 0xEF):
                continue

            message_type = raw[0] & 0xF0
            if (raw[0] & 0x0F) != PIANO_CHANNEL:
                return False
            if message_type in (0x80, 0x90):
                has_notes = True
            if message_type == 0xB0 and raw[1] in (
                MIDI_BANK_SELECT_CONTROLLERS | MIDI_CHANNEL_MODE_CONTROLLERS
            ):
                return False
            if message_type == 0xC0:
                program_events.append((track_index, abs_tick, order, raw))
        if has_notes:
            note_track_indexes.append(track_index)

    required_tracks = (
        note_track_indexes if format_type == 2 else note_track_indexes[:1]
    )
    if len(program_events) != len(required_tracks):
        return False
    return all(
        track_index == required_track
        and abs_tick == 0
        and raw == _acoustic_grand_program_event()
        for (track_index, abs_tick, _order, raw), required_track in zip(
            program_events,
            required_tracks,
        )
    )


def merge_midi_channels_to_channel0_bytes(midi_bytes):
    """Merge every MIDI channel into channel 0 without changing the SMF type."""
    header_end, format_type, chunks, trailing_start = _parse_smf_layout(midi_bytes)

    tracks = []
    for chunk in chunks:
        if chunk["id"] != b"MTrk":
            continue
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, end_tick = _parse_track_events(track_data)
        tracks.append(
            {
                "original_events": events,
                "events": events,
                "end_tick": end_tick,
                "changed": False,
            }
        )

    if _is_canonical_channel_merge(format_type, tracks):
        return midi_bytes, False

    if format_type == 2:
        # Type 2 tracks are independent sequences, each with its own channels.
        for track in tracks:
            track["events"], track["changed"], _has_notes = _merge_track_events(
                track["events"]
            )
    else:
        # Type 1 tracks share channel state. A controller track can terminate
        # notes on another track, so process the complete timeline together.
        merged, _changed, _has_notes = _merge_track_events(
            _merge_track_event_groups(tracks)
        )
        _replace_track_event_groups_from_merged(tracks, merged)
        for track in tracks:
            track["changed"] = (
                [(tick, raw) for tick, _, raw in track["events"]]
                != [(tick, raw) for tick, _, raw in track["original_events"]]
            )

    note_track_indexes = [
        index for index, track in enumerate(tracks)
        if any(raw and (raw[0] & 0xF0) in (0x80, 0x90) for _, _, raw in track["events"])
    ]
    if format_type != 2 and note_track_indexes:
        note_track_indexes = note_track_indexes[:1]

    for track_index in note_track_indexes:
        tracks[track_index]["events"].append(
            (0, -1, _acoustic_grand_program_event())
        )
        tracks[track_index]["changed"] = True

    if not any(track["changed"] for track in tracks):
        return midi_bytes, False

    rebuilt = bytearray(midi_bytes[:header_end])
    track_index = 0
    for chunk in chunks:
        if chunk["id"] != b"MTrk":
            rebuilt.extend(midi_bytes[chunk["start"]:chunk["data_end"]])
            continue

        track = tracks[track_index]
        if track["changed"]:
            track_data = _build_raw_midi_track(
                track["events"],
                end_tick=track["end_tick"],
            )
            rebuilt.extend(b"MTrk")
            rebuilt.extend(len(track_data).to_bytes(4, "big"))
            rebuilt.extend(track_data)
        else:
            rebuilt.extend(midi_bytes[chunk["start"]:chunk["data_end"]])
        track_index += 1

    rebuilt.extend(midi_bytes[trailing_start:])
    rebuilt_bytes = bytes(rebuilt)
    if rebuilt_bytes == midi_bytes:
        return midi_bytes, False
    return rebuilt_bytes, True


def merge_midi_channels_to_channel0_path(source_path, dest_path):
    """Atomically write a channel-merged MIDI file and report whether it changed."""
    source_path = os.fspath(source_path)
    dest_path = os.fspath(dest_path)
    if not os.path.isfile(source_path):
        raise ValueError("File does not exist.")

    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()

    merged_bytes, changed = merge_midi_channels_to_channel0_bytes(midi_bytes)
    if not changed:
        return False

    suffix = f".aps_channel_merge_{uuid.uuid4().hex}.tmp"
    temp_path = dest_path + (suffix.encode() if isinstance(dest_path, bytes) else suffix)
    try:
        with open(temp_path, "wb") as handle:
            handle.write(merged_bytes)
        os.replace(temp_path, dest_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return True
