"""Channel collapse must keep each MIDI destination's state independent."""

from io import BytesIO

import mido
import pytest

from aps_midi_prep_tool_app.midi_channel_merger import merge_midi_channels_to_channel0_bytes
from aps_midi_prep_tool_app.midi_type0_converter import (
    _convert_midi_bytes_to_type0,
    _expand_channel_note_terminations,
)


PORT_0 = b"\xff\x21\x01\x00"
PORT_1 = b"\xff\x21\x01\x01"
GM_RESET = b"\xf0\x05\x7e\x7f\x09\x01\xf7"


def _midi(*tracks):
    midi = mido.MidiFile(type=1 if len(tracks) > 1 else 0)
    for events in tracks:
        track = mido.MidiTrack()
        previous_tick = 0
        for tick, raw in events:
            if raw[0] == 0xFF:
                message = mido.MetaMessage.from_bytes(raw)
            elif raw[0] == 0xF0:
                message = mido.Message.from_bytes(raw[:1] + raw[2:])
            else:
                message = mido.Message.from_bytes(raw)
            track.append(message.copy(time=tick - previous_tick))
            previous_tick = tick
        track.append(mido.MetaMessage("end_of_track", time=960 - previous_tick))
        midi.tracks.append(track)
    output = BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _routed_events(data):
    events = []
    midi = mido.MidiFile(file=BytesIO(data))
    for track in midi.tracks:
        tick = 0
        port = 0
        for message in track:
            tick += message.time
            if message.type == "midi_port":
                port = message.port
            elif not message.is_meta and message.type != "program_change":
                events.append((tick, port, tuple(message.bytes())))
        assert tick == 960
    return sorted(events)


@pytest.fixture(params=["standalone", "type0"])
def convert(request):
    if request.param == "standalone":
        return merge_midi_channels_to_channel0_bytes
    return lambda data: _convert_midi_bytes_to_type0(
        data, remap_all_instruments_to_channel0=True,
    )


@pytest.mark.parametrize("source_tracks", [True, False])
def test_deferred_note_releases_stay_on_their_port(source_tracks):
    # The three-field shape also covers port changes within one Type 2 track.
    source = [
        (0, PORT_0), (0, b"\x92\x3c\x64"),
        (20, PORT_1), (20, b"\x95\x3c\x5a"), (40, b"\x85\x3c\x0a"),
        (100, PORT_0), (100, b"\x82\x3c\x14"),
    ]
    events = [
        (tick, 0, order, raw) if source_tracks else (tick, order, raw)
        for order, (tick, raw) in enumerate(source)
    ]

    expanded, changed = _expand_channel_note_terminations(events)

    assert not changed
    assert expanded == events


@pytest.mark.parametrize("controller", [123, 124, 125, 126, 127])
def test_note_termination_only_releases_its_destination(convert, controller):
    source = _midi(
        [(0, b"\x92\x3c\x64"), (100, b"\x82\x3c\x14")],
        [(0, PORT_1), (20, b"\x92\x3c\x5a"), (40, bytes([0xB2, controller, 0]))],
    )

    converted, _changed = convert(source)

    assert _routed_events(converted) == [
        (0, 0, (0x90, 60, 100)), (20, 1, (0x90, 60, 90)),
        (40, 1, (0x80, 60, 0)), (100, 0, (0x80, 60, 20)),
    ]


def test_all_sound_off_ignores_voices_on_other_destinations(convert):
    source = _midi(
        [(0, b"\x92\x3c\x64"), (100, b"\x82\x3c\x14")],
        [(0, PORT_1), (20, b"\x95\x40\x5a"), (40, b"\xb5\x78\x00")],
    )

    converted, _changed = convert(source)

    assert _routed_events(converted) == [
        (0, 0, (0x90, 60, 100)), (20, 1, (0x90, 64, 90)),
        (40, 1, (0xB0, 120, 0)), (100, 0, (0x80, 60, 20)),
    ]


def test_system_reset_does_not_drop_other_ports_deferred_releases(convert):
    source = _midi(
        [(0, b"\x92\x3c\x64"), (10, b"\x95\x3c\x5a"),
         (40, b"\x85\x3c\x0a"), (100, b"\x82\x3c\x14")],
        [(0, PORT_1), (50, GM_RESET)],
    )

    converted, _changed = convert(source)

    assert _routed_events(converted) == [
        (0, 0, (0x90, 60, 100)), (10, 0, (0x90, 60, 90)),
        (50, 1, (0xF0, 0x7E, 0x7F, 0x09, 0x01, 0xF7)),
        (100, 0, (0x80, 60, 10)), (100, 0, (0x80, 60, 20)),
    ]


@pytest.mark.parametrize("channel", [2, 5])
def test_controller_reset_does_not_restore_another_ports_values(convert, channel):
    source = _midi(
        [(0, b"\xb2\x40\x64"), (0, b"\xe2\x00\x20")],
        [(0, PORT_1), (10, bytes([0xB0 | channel, 64, 50])),
         (10, bytes([0xE0 | channel, 0, 96])),
         (20, bytes([0xB0 | channel, 121, 0]))],
    )

    converted, _changed = convert(source)

    assert _routed_events(converted) == [
        (0, 0, (0xB0, 64, 100)), (0, 0, (0xE0, 0, 32)),
        (10, 1, (0xB0, 64, 50)), (10, 1, (0xE0, 0, 96)),
        (20, 1, (0xB0, 64, 0)), (20, 1, (0xE0, 0, 64)),
    ]


def test_system_reset_keeps_other_ports_controller_history(convert):
    source = _midi(
        [(0, b"\xb2\x40\x64"), (100, b"\xb2\x79\x00")],
        [(0, PORT_1), (50, GM_RESET)],
    )

    converted, _changed = convert(source)

    assert _routed_events(converted) == [
        (0, 0, (0xB0, 64, 100)),
        (50, 1, (0xF0, 0x7E, 0x7F, 0x09, 0x01, 0xF7)),
        (100, 0, (0xB0, 64, 0)),
    ]


def test_parameter_reset_does_not_restore_another_ports_selection(convert):
    source = _midi(
        [(0, b"\xb2\x65\x00"), (0, b"\xb2\x64\x00")],
        [(0, PORT_1), (10, b"\xb5\x63\x01"), (10, b"\xb5\x62\x02"),
         (20, b"\xb5\x79\x00")],
    )

    converted, _changed = convert(source)

    reset_events = [event for event in _routed_events(converted) if event[0] == 20]
    assert reset_events == [
        (20, 1, (0xB0, controller, 127)) for controller in (98, 99, 100, 101)
    ]


@pytest.mark.parametrize("remap", [False, True])
def test_type0_restores_default_port_and_midtrack_destination_changes(remap):
    source = _midi(
        [(0, b"\x92\x3c\x64"), (20, PORT_1), (20, b"\x92\x40\x5a"),
         (40, b"\x82\x40\x0a"), (100, PORT_0), (100, b"\x82\x3c\x14")],
        [(10, b"\x95\x43\x50"), (60, b"\x85\x43\x1e")],
    )

    converted, changed = _convert_midi_bytes_to_type0(
        source, remap_all_instruments_to_channel0=remap,
    )

    assert changed
    expected = _routed_events(source)
    if remap:
        expected = [(tick, port, (raw[0] & 0xF0, *raw[1:])) for tick, port, raw in expected]
    assert _routed_events(converted) == expected
    assert _convert_midi_bytes_to_type0(converted)[0] == converted
