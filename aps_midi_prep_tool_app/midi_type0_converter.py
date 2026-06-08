import os
import shutil
import uuid
from dataclasses import dataclass


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

            if meta_type != 0x2F:
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
    has_channel1_program_before_notes = False
    legacy_pedal_channel_has_notes = False

    for abs_tick, track_index, order, raw in merged_events:
        if _is_channel1_note_on(raw):
            if first_channel1_note_tick is None or abs_tick < first_channel1_note_tick:
                first_channel1_note_tick = abs_tick
        if is_disklavier_channel_note_event(raw, DISKLAVIER_LEGACY_PEDAL_CHANNEL):
            legacy_pedal_channel_has_notes = True

    should_remap_legacy_pedal = first_channel1_note_tick is not None and not legacy_pedal_channel_has_notes
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
            normalized.append((0, -1, -1, _channel1_acoustic_grand_event()))
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


def _convert_midi_bytes_to_type0(midi_bytes, *, normalize_disklavier=True):
    header_end, format_type, _, chunks = _parse_midi_chunks(midi_bytes)
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]

    if format_type == 0 and not normalize_disklavier:
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


def convert_midi_file_to_type0_path(source_path, dest_path, *, normalize_disklavier=True):
    if not os.path.isfile(source_path):
        raise ValueError("File does not exist.")

    with open(source_path, "rb") as handle:
        midi_bytes = handle.read()

    converted_bytes, changed = _convert_midi_bytes_to_type0(
        midi_bytes,
        normalize_disklavier=normalize_disklavier,
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
    normalize_disklavier=True,
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
