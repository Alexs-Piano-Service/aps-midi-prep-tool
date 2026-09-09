"""MID2ESEQ 2.01-compatible timing and normal Disklavier E-SEQ output.

The integer timing, message scheduler, header fields, and trailer follow the
distributed converter's disassembly at 0x4015E2–0x4019CF. Its fixed clock is
44,928 / 60 = 748.8 ticks/second, not the rounded 750 in its README.
Reproduction from the two supplied original MIDI files matches their legacy
E-SEQ outputs byte for byte. That is software evidence, not a hardware claim.
"""


_HEADER_TEMPLATE = bytes.fromhex(
    "fe000000000000434f4d2d4553455100"
    "00000000000000800040005000000000"
    "00000001580000202020202020202046"
    "494c0058040400000000000000000000"
    "00000000770000107f00004101000080"
    "00000000000000202020202020202020"
    "20202020202020202020202020202020"
    "20202020202020"
)
_HEADER_SIZE = 0x77
_TRAILER_TICKS = 1498


def _encode_delay(ticks):
    """Emit the legacy seven-bit short/long delay representation."""
    result = bytearray()
    while ticks:
        if ticks <= 127:
            result.extend((0xF3, ticks))
            break
        chunk = min(ticks, 0x3FFF)
        result.extend((0xF4, chunk & 0x7F, chunk >> 7))
        ticks -= chunk
    return result


def is_legacy_eseq_bytes(data):
    """Recognize the legacy container envelope, not validate playback content.

    This is suitable for describing expected compatibility timing in a review.
    It must not replace event validation or suppress actual timing differences.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return False
    data = bytes(data)
    if len(data) < _HEADER_SIZE + 6:
        return False
    if int.from_bytes(data[3:7], "little") != len(data):
        return False
    if int.from_bytes(data[0x1F:0x23], "little") != len(data) - _HEADER_SIZE:
        return False
    if data[_HEADER_SIZE:_HEADER_SIZE + 2] != b"\xF1\x00" or data[-4:] != b"\xF4\x5A\x0B\xF2":
        return False
    if data[0x41:0x43] != ((len(data) - 1) & 0x7FF).to_bytes(2, "big"):
        return False
    if data[0x50] > 2 or int.from_bytes(data[0x37:0x3B], "little") < _TRAILER_TICKS:
        return False
    if data[0x45] > 1 or data[0x51] > 1:
        return False
    expected = bytearray(_HEADER_TEMPLATE)
    # Yamaha preparation may describe XG, detailed pedals, and the actual
    # note channels without altering this timing/container convention.
    for start, end in ((3, 7), (0x1F, 0x23), (0x27, 0x2F), (0x37, 0x3B),
                       (0x41, 0x43), (0x45, 0x46), (0x50, 0x52),
                       (0x54, 0x56), (0x57, 0x77)):
        expected[start:end] = data[start:end]
    return data[:_HEADER_SIZE] == expected


def build_legacy_eseq_bytes(
    merged_events, division, *, title, filename_hint="", channel_message_transform=None,
):
    """Build an unpadded normal E-SEQ file from validated, merged MIDI events.

    ``merged_events`` contains ``(absolute_tick, track_index, order, raw)``
    tuples in the order returned by ``_collect_merged_midi_events``. ``raw``
    uses SMF length prefixes for meta/SysEx events. Callers select the title,
    target filename, conversion policy, and any explicitly requested controller
    edits. This helper does not infer a destination profile or modify values.
    An optional channel-message transform runs after scheduling each source
    event. Its zero or more returned messages share that event's output tick;
    added pedal companions never consume another scheduler step, and omitted
    duplicate positions never move subsequent source events.

    Legacy timing intentionally trims time before the first output message,
    applies its minimum first-onset delay and crowded-message scheduler, and
    adds 1498 ticks after the last output message. Input EOT-only silence does
    not extend that trailer. MIDI tempo changes affect elapsed-time integration
    rather than emitting FB commands. Meter/text metadata does not generate
    stream events; the header retains the legacy template's display fields.
    """
    # Local imports allow the main converter to select this policy without a
    # module import cycle, and retain the shared SysEx escape protections.
    from .eseq_converter import (
        EseqConversionError,
        _decode_midi_sysex_event,
        _encode_title_bytes,
        _normalize_midi_sysex_packets,
        _read_vlq_from_bytes,
        _sanitize_ascii_filename_key,
    )

    if not isinstance(division, int) or not 0 < division <= 0x7FFF:
        raise EseqConversionError("Legacy E-SEQ conversion requires a positive PPQN MIDI division.")
    merged_events = list(merged_events)
    _normalize_midi_sysex_packets(
        [(tick, order, raw) for tick, _track, order, raw in merged_events], track_index=None,
    )

    elapsed_numerator = 0
    previous_input_tick = 0
    mpqn = 500_000
    output_tick = 0
    crowded_events = 0
    first_output = True
    first_onset = True
    highest_note_channel = 0
    stream = bytearray(b"\xF1\x00")

    for absolute_tick, _track, _order, source_raw in merged_events:
        if not isinstance(absolute_tick, int) or absolute_tick < previous_input_tick:
            raise EseqConversionError("MIDI events are out of order for legacy E-SEQ conversion.")
        elapsed_numerator += (absolute_tick - previous_input_tick) * mpqn
        previous_input_tick = absolute_tick
        raw = bytes(source_raw)
        if not raw:
            raise EseqConversionError("An empty MIDI event cannot be converted to legacy E-SEQ.")

        if raw[0] == 0xFF:
            size, offset = _read_vlq_from_bytes(raw, 2)
            payload = raw[offset:]
            if len(payload) != size:
                raise EseqConversionError("Malformed MIDI meta event in legacy E-SEQ conversion.")
            if raw[1] == 0x51:
                if size != 3 or int.from_bytes(payload, "big") == 0:
                    raise EseqConversionError("Invalid MIDI tempo in legacy E-SEQ conversion.")
                mpqn = int.from_bytes(payload, "big")
                continue
            if raw[1] != 0x20:
                continue
            if size != 1 or payload[0] > 15:
                raise EseqConversionError("Invalid MIDI channel prefix in legacy E-SEQ conversion.")
            raw = b"\xFF" + payload
        elif raw[0] in (0xF0, 0xF7):
            packet = _decode_midi_sysex_event(raw)
            # An SMF F7 continuation marker is not a transmitted wire byte.
            raw = packet[1:] if packet[0] == 0xF7 else packet
        elif 0x80 <= raw[0] < 0xF0:
            size = 2 if raw[0] >> 4 in (0xC, 0xD) else 3
            if len(raw) != size or any(byte > 127 for byte in raw[1:]):
                raise EseqConversionError("Invalid MIDI channel message in legacy E-SEQ conversion.")
        else:
            raise EseqConversionError("Unsupported MIDI system event in legacy E-SEQ conversion.")

        # Inspect the original channel status: a continuation payload can start
        # with data or EOX and must never be classified as a channel message.
        status = source_raw[0] & 0xF0
        note_on = status == 0x90 and source_raw[2] > 0
        pedal_on = status == 0xB0 and source_raw[1] in (64, 67) and source_raw[2] > 0
        clamp_onset = first_onset and (note_on or pedal_on)
        if clamp_onset:
            first_onset = False
        if note_on:
            highest_note_channel = max(highest_note_channel, source_raw[0] & 0x0F)

        if first_output:
            elapsed_numerator = 0
            first_output = False
        if clamp_onset:
            elapsed_numerator = max(elapsed_numerator, 1_000_000 * division)

        # Preserve both integer divisions and the persistent counter. Positive
        # gaps of 1–2 ticks do not emit a delay or reset that counter.
        elapsed_microseconds = elapsed_numerator // division
        desired_tick = elapsed_microseconds * 44_928 // 60_000_000
        if desired_tick > output_tick:
            delay = desired_tick - output_tick
        else:
            crowded_events += 1
            delay = crowded_events
        if delay >= 3:
            crowded_events = 0
            output_tick += delay
            if output_tick > 0xFFFFFFFF - _TRAILER_TICKS:
                raise EseqConversionError("Legacy E-SEQ duration exceeds the file format limit.")
            stream.extend(_encode_delay(delay))
        if channel_message_transform is not None and 0x80 <= source_raw[0] < 0xF0:
            for message in channel_message_transform(raw):
                stream.extend(message)
        else:
            stream.extend(raw)

    output_tick += _TRAILER_TICKS
    stream.extend(_encode_delay(_TRAILER_TICKS))
    stream.append(0xF2)
    used_length = _HEADER_SIZE + len(stream)
    if used_length > 0xFFFFFFFF:
        raise EseqConversionError("Legacy E-SEQ output exceeds the file format limit.")

    header = bytearray(_HEADER_TEMPLATE)
    header[3:7] = used_length.to_bytes(4, "little")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x27:0x2F] = _sanitize_ascii_filename_key(filename_hint)[:8]
    header[0x37:0x3B] = output_tick.to_bytes(4, "little")
    header[0x41:0x43] = ((used_length - 1) & 0x7FF).to_bytes(2, "big")
    header[0x50] = min(highest_note_channel, 2)
    header[0x57:0x77] = _encode_title_bytes(title)
    return bytes(header) + bytes(stream)
