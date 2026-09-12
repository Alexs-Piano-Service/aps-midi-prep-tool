"""Channel collapse must retain source note endings without silencing other parts."""

from io import BytesIO

import mido
import pytest

from aps_midi_prep_tool_app.midi_channel_merger import merge_midi_channels_to_channel0_bytes
from aps_midi_prep_tool_app.midi_type0_converter import _convert_midi_bytes_to_type0


def _midi(*tracks, format_type=None):
    midi = mido.MidiFile(type=(1 if len(tracks) > 1 else 0) if format_type is None else format_type)
    midi.ticks_per_beat = 480
    for events in tracks:
        track = mido.MidiTrack()
        previous_tick = 0
        for tick, raw in events:
            track.append(mido.Message.from_bytes(raw).copy(time=tick - previous_tick))
            previous_tick = tick
        track.append(mido.MetaMessage("end_of_track", time=960 - previous_tick))
        midi.tracks.append(track)
    output = BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _notes(data):
    midi = mido.MidiFile(file=BytesIO(data))
    tick = 0
    notes = []
    for message in mido.merge_tracks(midi.tracks):
        tick += message.time
        if message.type in ("note_on", "note_off"):
            assert message.channel == 0
            notes.append((tick, message.type, message.note, message.velocity))
        if message.type == "control_change":
            assert message.control < 120
    assert tick == 960
    return notes


@pytest.fixture(params=["standalone", "type0"])
def convert(request):
    if request.param == "standalone":
        return merge_midi_channels_to_channel0_bytes
    return lambda data: _convert_midi_bytes_to_type0(data, remap_all_instruments_to_channel0=True)


@pytest.mark.parametrize("controller,value", [(120, 0), (123, 0), (124, 0), (125, 0), (126, 4), (127, 0)])
@pytest.mark.parametrize("layout", ["one_track", "separate_parts", "separate_stop", "conductor_stop"])
def test_channel_stop_releases_middle_c_at_half_second_only(convert, controller, value, layout):
    c = [(0, [0x92, 60, 100])]
    stop = [(480, [0xB2, controller, value])]
    e = [(0, [0x95, 64, 90]), (960, [0x85, 64, 40])]
    if layout == "one_track":
        tracks = [sorted(c + stop + e, key=lambda event: event[0])]
    elif layout == "separate_parts":
        tracks = [c + stop, e]
    elif layout == "separate_stop":
        tracks = [c, stop, e]
    else:
        tracks = [stop, c, e]

    converted, changed = convert(_midi(*tracks))

    assert changed
    assert _notes(converted) == [
        (0, "note_on", 60, 100), (0, "note_on", 64, 90),
        (480, "note_off", 60, 0), (960, "note_off", 64, 40),
    ]
    converted_again, _changed_again = convert(converted)
    assert converted_again == converted


@pytest.mark.parametrize("stop_first", [True, False])
def test_same_pitch_overlap_waits_for_last_source_and_balances_note_ons(convert, stop_first):
    stop_tick, off_tick = (480, 960) if stop_first else (960, 480)
    source = _midi(
        [(0, [0x92, 60, 100]), (stop_tick, [0xB2, 123, 0])],
        [(240, [0x95, 60, 90]), (off_tick, [0x85, 60, 40])],
    )

    converted, _changed = convert(source)

    notes = _notes(converted)
    assert notes[:2] == [(0, "note_on", 60, 100), (240, "note_on", 60, 90)]
    assert [(tick, kind, note) for tick, kind, note, _velocity in notes[2:]] == [
        (960, "note_off", 60), (960, "note_off", 60),
    ]
    assert [velocity for _tick, _kind, _note, velocity in notes[2:]] == (
        [0, 40] if stop_first else [40, 0]
    )
    converted_again, _changed_again = convert(converted)
    assert converted_again == converted


def test_repeated_note_ons_and_velocity_zero_off_leave_one_active_voice(convert):
    source = _midi([
        (0, [0x92, 60, 100]), (120, [0x92, 60, 90]),
        (240, [0x92, 60, 0]), (480, [0xB2, 123, 0]),
    ])

    converted, _changed = convert(source)

    assert _notes(converted) == [
        (0, "note_on", 60, 100), (120, "note_on", 60, 90),
        (480, "note_on", 60, 0), (480, "note_off", 60, 0),
    ]


def test_late_note_off_after_channel_stop_does_not_cut_another_part(convert):
    source = _midi([
        (0, [0x92, 60, 100]), (240, [0xB2, 123, 0]),
        (480, [0x95, 60, 90]), (600, [0x82, 60, 0]),
        (720, [0xB2, 123, 0]), (960, [0x95, 60, 0]),
    ])

    converted, _changed = convert(source)

    assert _notes(converted) == [
        (0, "note_on", 60, 100), (240, "note_off", 60, 0),
        (480, "note_on", 60, 90), (960, "note_on", 60, 0),
    ]


def test_stop_expansion_stays_before_same_tick_retrigger(convert):
    source = _midi([
        (0, [0x92, 64, 100]), (0, [0x92, 60, 90]),
        (480, [0xB2, 123, 0]), (480, [0x92, 60, 80]),
        (960, [0x82, 60, 40]),
    ])

    converted, _changed = convert(source)

    assert _notes(converted) == [
        (0, "note_on", 64, 100), (0, "note_on", 60, 90),
        (480, "note_off", 60, 0), (480, "note_off", 64, 0),
        (480, "note_on", 60, 80), (960, "note_off", 60, 40),
    ]


def test_system_reset_clears_note_state_before_later_channel_stop(convert):
    source = _midi([
        (0, [0x92, 60, 100]), (240, [0xF0, 0x7E, 0x7F, 0x09, 0x01, 0xF7]),
        (480, [0x95, 60, 90]), (720, [0xB2, 123, 0]), (960, [0x85, 60, 40]),
    ])

    converted, _changed = convert(source)

    assert _notes(converted) == [
        (0, "note_on", 60, 100), (480, "note_on", 60, 90), (960, "note_off", 60, 40),
    ]


@pytest.mark.parametrize("controller", [121, 122])
def test_non_terminating_channel_modes_do_not_release_held_notes(convert, controller):
    source = _midi([
        (0, [0x92, 60, 100]), (480, [0xB2, controller, 0]), (960, [0x82, 60, 40]),
    ])

    converted, _changed = convert(source)

    assert _notes(converted) == [(0, "note_on", 60, 100), (960, "note_off", 60, 40)]


def test_type2_sequences_have_independent_note_state():
    source = _midi(
        [(0, [0x92, 60, 100]), (480, [0xB2, 123, 0])],
        [(0, [0x92, 64, 90]), (960, [0x82, 64, 40])],
        format_type=2,
    )

    converted, changed = merge_midi_channels_to_channel0_bytes(source)

    assert changed
    midi = mido.MidiFile(file=BytesIO(converted))
    assert midi.type == 2
    for track, pitch, release_tick in zip(midi.tracks, [60, 64], [480, 960]):
        tick = 0
        releases = []
        for message in track:
            tick += message.time
            if message.type == "note_off":
                releases.append((tick, message.note))
        assert releases == [(release_tick, pitch)]
