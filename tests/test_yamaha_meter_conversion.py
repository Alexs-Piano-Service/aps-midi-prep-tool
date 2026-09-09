"""MIDI meter changes keep their source timing through the E-SEQ writer."""

import struct

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.midi_type0_converter import _parse_track_events


def _vlq(value):
    result = bytearray((value & 127,))
    while value >> 7:
        value >>= 7
        result.insert(0, 128 | (value & 127))
    return bytes(result)


def _midi(*tracks):
    data = bytearray(struct.pack(">4sIHHH", b"MThd", 6, int(len(tracks) > 1), len(tracks), 384))
    for events in tracks:
        track = bytearray()
        previous_tick = 0
        for tick, raw in events:
            track.extend(_vlq(tick - previous_tick) + raw)
            previous_tick = tick
        track.extend(b"\x00\xFF\x2F\x00")
        data.extend(b"MTrk" + len(track).to_bytes(4, "big") + track)
    return bytes(data)


def _meter(numerator, denominator_power):
    return bytes((0xFF, 0x58, 4, numerator, denominator_power, 24, 8))


@pytest.mark.parametrize(
    "changes, expected",
    [
        ([(384, 3, 3)], [(0, 4, 2), (384, 3, 3)]),
        ([(0, 7, 3), (0, 3, 2)], [(0, 3, 2)]),
        ([(0, 3, 2), (0, 7, 3)], [(0, 7, 3)]),
        ([(0, 6, 3), (384, 7, 3), (384, 3, 2)], [(0, 6, 3), (384, 3, 2)]),
    ],
)
def test_meter_preserves_future_changes_and_final_same_tick_value(changes, expected):
    events = [(tick, _meter(numerator, denominator)) for tick, numerator, denominator in changes]
    events.extend([(768, b"\x90\x3C\x40"), (1536, b"\x80\x3C\x00")])

    output = convert_midi_bytes_to_eseq_bytes(
        _midi(events), timing_policy="preserve", pedal_policy="preserve",
    )

    parsed = parse_eseq_bytes(output)
    assert tuple(output[0x34:0x36]) == (expected[0][1], 1 << expected[0][2])
    assert parsed.time_signature_events == expected
    assert [(tick, raw) for tick, _kind, raw in parsed.events] == [
        (768, b"\x90\x3C\x40"), (1536, b"\x80\x3C\x00"),
    ]
    assert parsed.end_tick == 1536

    midi = convert_eseq_bytes_to_midi_bytes(output, cc7_policy="preserve")
    roundtrip_events, _end = _parse_track_events(midi[22:])
    assert [(tick, raw[3], raw[4]) for tick, _order, raw in roundtrip_events
            if raw[:3] == b"\xFF\x58\x04"] == expected


def test_same_tick_meter_precedence_follows_the_merged_track_order():
    source = _midi(
        [(0, _meter(7, 3)), (768, b"\x90\x3C\x40"), (1536, b"\x80\x3C\x00")],
        [(0, _meter(3, 2))],
    )

    output = convert_midi_bytes_to_eseq_bytes(
        source, timing_policy="preserve", pedal_policy="preserve",
    )

    assert tuple(output[0x34:0x36]) == (3, 4)
    assert parse_eseq_bytes(output).time_signature_events == [(0, 3, 2)]


@pytest.mark.parametrize(
    "changes, first_note",
    [
        ([(100, 7, 3), (200, 3, 2)], 384),
        ([(4000, 7, 3), (4500, 3, 2)], 5000),
        ([(384, 3, 2)], 384),
    ],
)
def test_archival_prelude_keeps_actual_meter_changes_and_the_leading_delay(changes, first_note):
    def delay(ticks):
        return b"\xF4" + bytes((ticks & 127, ticks >> 7)) if ticks else b""

    stream = bytearray(b"\xF1\x00")
    previous_tick = 0
    for tick, numerator, denominator in changes:
        stream.extend(delay(tick - previous_tick) + bytes((0xF9, numerator, denominator)))
        previous_tick = tick
    stream.extend(delay(first_note - previous_tick) + b"\x90\x3C\x40")
    stream.extend(delay(384) + b"\x80\x3C\x00\xF2")
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = header[0x33] = 88
    header[0x34:0x36] = bytes((4, 4))
    original = bytes(header) + stream
    source_midi = convert_eseq_bytes_to_midi_bytes(original, midi_metadata_policy="archival")

    output = convert_midi_bytes_to_eseq_bytes(source_midi, timing_policy="preserve", pedal_policy="preserve")

    assert parse_eseq_bytes(output).time_signature_events == [(0, 4, 2)] + changes
    assert parse_eseq_bytes(output).events == parse_eseq_bytes(original).events
    assert parse_eseq_bytes(output).end_tick == first_note + 384
    # No generated barline may split or hide the source's initial F4 delay.
    assert output[0x77:0x7C] == original[0x77:0x7C]
