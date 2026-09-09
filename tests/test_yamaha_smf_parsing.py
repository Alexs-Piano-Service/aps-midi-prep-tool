"""SMF boundaries and payload offsets used by Yamaha format conversion."""

import struct

import pytest

from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes, parse_eseq_bytes
from aps_midi_prep_tool_app.midi_type0_converter import (
    _convert_midi_bytes_to_type0,
    _encode_vlq,
    _parse_track_events,
)


def _midi(*tracks):
    header = struct.pack(">4sIHHH", b"MThd", 6, int(len(tracks) > 1), len(tracks), 384)
    return header + b"".join(b"MTrk" + len(track).to_bytes(4, "big") + track for track in tracks)


def _track(events):
    data = bytearray()
    previous_tick = 0
    for tick, raw in events:
        data.extend(_encode_vlq(tick - previous_tick) + raw)
        previous_tick = tick
    return bytes(data)


@pytest.mark.parametrize("length_prefix", (b"", b"\x80", b"\x80\x80", b"\x80\x80\x80"))
@pytest.mark.parametrize("timing_policy", ("preserve", "mid2eseq"))
def test_tempo_and_meter_payloads_are_independent_of_vlq_length_encoding(length_prefix, timing_policy):
    def source(prefix):
        return _midi(_track([
            (0, b"\xFF\x51" + prefix + b"\x03\x07\xA1\x20"),
            (0, b"\xFF\x58" + prefix + b"\x04\x06\x03\x18\x08"),
            (0, b"\x90\x3C\x40"),
            (384, b"\xFF\x51" + prefix + b"\x03\x09\x27\xC0"),
            (384, b"\xFF\x58" + prefix + b"\x04\x03\x02\x18\x08"),
            (768, b"\x80\x3C\x00"),
            (900, b"\xFF\x2F\x00"),
        ]))

    kwargs = {"timing_policy": timing_policy, "pedal_policy": "preserve"}
    expected = convert_midi_bytes_to_eseq_bytes(source(b""), **kwargs)
    actual = convert_midi_bytes_to_eseq_bytes(source(length_prefix), **kwargs)

    assert actual == expected
    if timing_policy == "preserve":
        parsed = parse_eseq_bytes(actual)
        assert parsed.time_signature_events == [(0, 6, 3), (384, 3, 2)]
        assert parsed.tempo_events[0] == (0, 500000)
        # An E-SEQ FB represents the later tempo with an integer ratio.
        assert parsed.tempo_events[-1][0] == 384
        assert abs(parsed.tempo_events[-1][1] - 600000) < 300


@pytest.mark.parametrize("trailer", (b"\x00\x90\x40\x40\x83\x00\x80\x40\x00", b"\xFF\xFF"))
def test_track_end_stops_before_trailing_events_or_non_midi_padding(trailer):
    track = _track([
        (0, b"\x90\x3C\x40"), (384, b"\x80\x3C\x00"), (480, b"\xFF\x2F\x00"),
    ]) + trailer

    events, end_tick = _parse_track_events(track)

    assert [(tick, raw) for tick, _order, raw in events] == [
        (0, b"\x90\x3C\x40"), (384, b"\x80\x3C\x00"),
    ]
    assert end_tick == 480


@pytest.mark.parametrize("output_format", ("smf0", "eseq"))
def test_ended_track_does_not_hide_later_music_or_end_time_from_another_track(output_format):
    first_track = _track([
        (0, b"\x90\x3C\x40"), (384, b"\x80\x3C\x00"), (480, b"\xFF\x2F\x00"),
        (1200, b"\x90\x40\x40"), (1600, b"\x80\x40\x00"),
    ])
    second_track = _track([
        (600, b"\x91\x43\x50"), (900, b"\x81\x43\x00"), (912, b"\xFF\x2F\x00"),
    ])
    source = _midi(first_track, second_track)

    if output_format == "smf0":
        output, changed = _convert_midi_bytes_to_type0(source)
        assert changed
        events, end_tick = _parse_track_events(output[22:])
    else:
        output = convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve", pedal_policy="preserve")
        parsed = parse_eseq_bytes(output)
        events, end_tick = parsed.events, parsed.end_tick

    assert [(tick, raw) for tick, _kind, raw in events] == [
        (0, b"\x90\x3C\x40"), (384, b"\x80\x3C\x00"),
        (600, b"\x91\x43\x50"), (900, b"\x81\x43\x00"),
    ]
    assert end_tick == 912


def test_track_end_payload_bounds_are_checked_before_accepting_the_boundary():
    with pytest.raises(ValueError, match="Meta event exceeds track bounds"):
        _parse_track_events(b"\x00\xFF\x2F\x03\x00")
