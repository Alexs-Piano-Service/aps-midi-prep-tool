"""Channel collapse must apply controller resets to their source's state."""

from io import BytesIO

import mido
import pytest

from aps_midi_prep_tool_app.midi_channel_merger import merge_midi_channels_to_channel0_bytes
from aps_midi_prep_tool_app.midi_type0_converter import _convert_midi_bytes_to_type0


def _midi(*tracks, format_type=None):
    midi = mido.MidiFile(type=(1 if len(tracks) > 1 else 0) if format_type is None else format_type)
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


def _track_events(track):
    tick = 0
    events = []
    for message in track:
        tick += message.time
        if message.is_meta:
            continue
        assert not hasattr(message, "channel") or message.channel == 0
        assert message.type != "control_change" or message.control != 121
        if message.type != "program_change":
            events.append((tick, message.bytes()))
    assert tick == 960
    return events


def _events(data):
    midi = mido.MidiFile(file=BytesIO(data))
    return _track_events(mido.merge_tracks(midi.tracks))


@pytest.fixture(params=["standalone", "type0"])
def convert(request):
    if request.param == "standalone":
        return merge_midi_channels_to_channel0_bytes
    return lambda data: _convert_midi_bytes_to_type0(data, remap_all_instruments_to_channel0=True)


def test_reset_restores_sustain_and_bend_before_same_tick_note_without_releasing_held_key(convert):
    source = _midi([
        (0, [0xB2, 64, 127]), (0, [0xE2, 0, 96]), (0, [0x92, 60, 100]),
        (480, [0xB2, 121, 0]), (480, [0x92, 67, 90]),
        (720, [0x82, 60, 40]), (960, [0x82, 67, 40]),
    ])

    converted, changed = convert(source)

    assert changed
    events = _events(converted)
    assert events[:3] == [
        (0, [0xB0, 64, 127]), (0, [0xE0, 0, 96]), (0, [0x90, 60, 100]),
    ]
    assert {tuple(raw) for tick, raw in events[3:5]} == {(0xB0, 64, 0), (0xE0, 0, 64)}
    assert all(tick == 480 for tick, _raw in events[3:5])
    assert events[5:] == [
        (480, [0x90, 67, 90]), (720, [0x80, 60, 40]), (960, [0x80, 67, 40]),
    ]
    assert convert(converted)[0] == converted


@pytest.mark.parametrize("controller,value,default", [
    (1, 90, 0), (2, 40, 127), (4, 70, 127), (11, 40, 127),
    (64, 127, 0), (65, 127, 0), (66, 127, 0), (67, 127, 0),
])
def test_reset_restores_observed_controller_default(convert, controller, value, default):
    source = _midi([(0, [0xB2, controller, value]), (480, [0xB2, 121, 0])])

    converted, _changed = convert(source)

    assert _events(converted) == [
        (0, [0xB0, controller, value]), (480, [0xB0, controller, default]),
    ]


@pytest.mark.parametrize("controller", [2, 4])
def test_breath_and_foot_reset_before_next_note_and_preserve_other_source(convert, controller):
    source = _midi([
        (0, [0xB2, controller, 90]), (120, [0xB5, controller, 30]),
        (240, [0xB5, 121, 0]), (240, [0x92, 60, 100]),
        (480, [0x82, 60, 0]), (480, [0xB2, 121, 0]),
        (480, [0x92, 67, 100]), (960, [0x82, 67, 0]),
    ])
    converted, _changed = convert(source)
    assert _events(converted) == [
        (0, [0xB0, controller, 90]), (120, [0xB0, controller, 30]),
        (240, [0xB0, controller, 90]), (240, [0x90, 60, 100]),
        (480, [0x80, 60, 0]), (480, [0xB0, controller, 127]),
        (480, [0x90, 67, 100]), (960, [0x80, 67, 0]),
    ]


def test_device_specific_controller_reset_loss_is_reported(convert):
    from aps_midi_prep_tool_app.conversion_review import ConversionReport, compare_music_bytes
    from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES

    source = _midi([(0, [0xB2, 16, 90]), (480, [0xB2, 121, 0]),
                    (480, [0x92, 60, 100]), (960, [0x82, 60, 0])])
    converted, _changed = convert(source)
    report = compare_music_bytes(source, converted)
    assert report.controller_resets_removed
    assert "device-specific reset effects may be lost" in report.to_text()
    for language in SUPPORTED_LANGUAGES:
        assert "CC121" in report.to_text(language.code)
    assert ConversionReport.from_dict(report.as_dict()) == report
    old_report = report.as_dict()
    old_report.pop("controller_resets_removed")
    assert not ConversionReport.from_dict(old_report).controller_resets_removed
    assert not compare_music_bytes(source, source).controller_resets_removed


@pytest.mark.parametrize("controller", [98, 99, 100, 101])
def test_reset_nulls_observed_parameter_selection(convert, controller):
    source = _midi([(0, [0xB2, controller, 5]), (480, [0xB2, 121, 0])])

    converted, _changed = convert(source)

    events = _events(converted)
    assert events[0] == (0, [0xB0, controller, 5])
    assert (480, [0xB0, controller, 127]) in events[1:]
    assert all(tick == 480 and raw in [[0xB0, control, 127] for control in (98, 99, 100, 101)]
               for tick, raw in events[1:])


@pytest.mark.parametrize("reset_owner", [True, False])
def test_reset_restores_parameter_selection_as_one_source_state(convert, reset_owner):
    reset_channel, data_channel = (5, 2) if reset_owner else (2, 5)
    source = _midi([
        (0, [0xB2, 101, 0]), (0, [0xB2, 100, 0]),
        (240, [0xB5, 99, 1]), (240, [0xB5, 98, 2]),
        (480, [0xB0 | reset_channel, 121, 0]),
        (480, [0xB0 | data_channel, 6, 12]),
    ])

    converted, _changed = convert(source)

    events = _events(converted)
    assert events[:4] == [
        (0, [0xB0, 101, 0]), (0, [0xB0, 100, 0]),
        (240, [0xB0, 99, 1]), (240, [0xB0, 98, 2]),
    ]
    assert events[-1] == (480, [0xB0, 6, 12])
    selector_resets = events[4:-1]
    if reset_owner:
        assert all(tick == 480 for tick, _raw in selector_resets)
        # Null the inactive NRPN pair first, then select the surviving RPN.
        # Reversing these families would redirect the subsequent Data Entry.
        assert {tuple(raw) for _tick, raw in selector_resets[:2]} == {(0xB0, 98, 127), (0xB0, 99, 127)}
        assert {tuple(raw) for _tick, raw in selector_resets[2:]} == {(0xB0, 100, 0), (0xB0, 101, 0)}
        assert len(selector_resets) == 4
    else:
        assert selector_resets == []
    assert convert(converted)[0] == converted


def test_reset_restores_channel_pressure(convert):
    source = _midi([(0, [0xD2, 80]), (480, [0xB2, 121, 0])])

    converted, _changed = convert(source)

    assert _events(converted) == [(0, [0xD0, 80]), (480, [0xD0, 0])]


def test_reset_restores_poly_pressure_for_each_observed_note(convert):
    source = _midi([
        (0, [0xA2, 60, 80]), (0, [0xA2, 67, 32]), (480, [0xB2, 121, 0]),
    ])

    converted, _changed = convert(source)

    events = _events(converted)
    assert events[:2] == [(0, [0xA0, 60, 80]), (0, [0xA0, 67, 32])]
    assert {(tick, tuple(raw)) for tick, raw in events[2:]} == {
        (480, (0xA0, 60, 0)), (480, (0xA0, 67, 0)),
    }


def test_reset_keeps_volume_pan_effects_and_parameter_data(convert):
    controls = [(7, 50), (10, 100), (91, 48), (6, 12), (38, 2)]
    source = _midi([
        *[(0, [0xB2, controller, value]) for controller, value in controls],
        (480, [0xB2, 121, 0]), (480, [0x92, 60, 100]), (960, [0x82, 60, 40]),
    ])

    converted, _changed = convert(source)

    assert _events(converted) == [
        *[(0, [0xB0, controller, value]) for controller, value in controls],
        (480, [0x90, 60, 100]), (960, [0x80, 60, 40]),
    ]


def test_reset_only_emits_observed_changes(convert):
    source = _midi([
        (0, [0xB2, 64, 0]), (0, [0xE2, 0, 64]),
        (240, [0xB2, 121, 0]), (480, [0xB5, 121, 0]),
    ])

    converted, _changed = convert(source)

    assert _events(converted) == [(0, [0xB0, 64, 0]), (0, [0xE0, 0, 64])]


@pytest.mark.parametrize("reset_owner", [True, False])
def test_reset_preserves_other_source_overrides_and_restores_defaults_after_last_reset(convert, reset_owner):
    first_reset, last_reset = (5, 2) if reset_owner else (2, 5)
    source = _midi([
        (0, [0xB2, 64, 127]), (0, [0xE2, 0, 32]),
        (240, [0xB5, 64, 32]), (240, [0xE5, 0, 96]),
        (480, [0xB0 | first_reset, 121, 0]),
        (720, [0xB0 | last_reset, 121, 0]),
    ])

    converted, _changed = convert(source)

    events = _events(converted)
    assert events[:4] == [
        (0, [0xB0, 64, 127]), (0, [0xE0, 0, 32]),
        (240, [0xB0, 64, 32]), (240, [0xE0, 0, 96]),
    ]
    assert {tuple(raw) for tick, raw in events if tick == 480} == (
        {(0xB0, 64, 127), (0xE0, 0, 32)} if reset_owner else set()
    )
    assert {tuple(raw) for tick, raw in events if tick == 720} == {(0xB0, 64, 0), (0xE0, 0, 64)}
    assert len(events) == (8 if reset_owner else 6)
    assert convert(converted)[0] == converted


@pytest.mark.parametrize("reset_track_first", [True, False])
def test_type1_controller_track_resets_shared_source_state_in_same_tick_order(convert, reset_track_first):
    performance = [
        (0, [0xB2, 64, 127]), (0, [0x92, 60, 100]),
        (480, [0x92, 67, 90]), (720, [0x82, 60, 40]), (960, [0x82, 67, 40]),
    ]
    reset = [(480, [0xB2, 121, 0])]
    tracks = [reset, performance] if reset_track_first else [performance, reset]

    converted, _changed = convert(_midi(*tracks))

    expected_at_reset = [(480, [0xB0, 64, 0]), (480, [0x90, 67, 90])]
    if not reset_track_first:
        expected_at_reset.reverse()
    assert _events(converted) == [
        (0, [0xB0, 64, 127]), (0, [0x90, 60, 100]),
        *expected_at_reset, (720, [0x80, 60, 40]), (960, [0x80, 67, 40]),
    ]


def test_type2_controller_state_stays_independent_between_sequences():
    source = _midi(
        [(0, [0xB2, 64, 127]), (480, [0xB2, 121, 0])],
        [(0, [0xB2, 64, 32]), (720, [0xB2, 121, 0])],
        format_type=2,
    )

    converted, changed = merge_midi_channels_to_channel0_bytes(source)

    assert changed
    midi = mido.MidiFile(file=BytesIO(converted))
    assert midi.type == 2
    assert [_track_events(track) for track in midi.tracks] == [
        [(0, [0xB0, 64, 127]), (480, [0xB0, 64, 0])],
        [(0, [0xB0, 64, 32]), (720, [0xB0, 64, 0])],
    ]
    assert merge_midi_channels_to_channel0_bytes(converted)[0] == converted


def test_combined_disklavier_normalization_applies_reset_before_legacy_pedal_remap():
    source = _midi([
        (0, [0xB2, 64, 127]), (0, [0xE2, 0, 96]), (0, [0x90, 60, 100]),
        (480, [0xB2, 121, 0]), (480, [0x90, 67, 90]),
        (720, [0x80, 60, 40]), (960, [0x80, 67, 40]),
    ])

    converted, changed = _convert_midi_bytes_to_type0(
        source, normalize_disklavier=True, remap_all_instruments_to_channel0=True,
    )

    assert changed
    events = _events(converted)
    resets = [(tick, raw) for tick, raw in events if tick == 480]
    assert {tuple(raw) for _tick, raw in resets[:-1]} == {(0xB0, 64, 0), (0xE0, 0, 64)}
    assert resets[-1] == (480, [0x90, 67, 90])
    assert _convert_midi_bytes_to_type0(
        converted, normalize_disklavier=True, remap_all_instruments_to_channel0=True,
    )[0] == converted


def test_combined_disklavier_normalization_preserves_cross_source_same_tick_pedal_reversal():
    source = _midi([
        (0, [0x90, 60, 100]),
        (480, [0xB0, 64, 127]), (480, [0xB5, 64, 0]), (480, [0xB0, 64, 127]),
        (480, [0x90, 67, 90]), (720, [0x80, 60, 40]), (960, [0x80, 67, 40]),
    ])

    converted, changed = _convert_midi_bytes_to_type0(
        source, normalize_disklavier=True, remap_all_instruments_to_channel0=True,
    )

    assert changed
    assert _events(converted) == [
        (0, [0x90, 60, 100]),
        (480, [0xB0, 64, 127]), (480, [0xB0, 64, 0]), (480, [0xB0, 64, 127]),
        (480, [0x90, 67, 90]), (720, [0x80, 60, 40]), (960, [0x80, 67, 40]),
    ]
    assert _convert_midi_bytes_to_type0(
        converted, normalize_disklavier=True, remap_all_instruments_to_channel0=True,
    )[0] == converted


def test_system_reset_discards_prior_source_controller_overrides(convert):
    gm_reset = [0xF0, 0x7E, 0x7F, 0x09, 0x01, 0xF7]
    source = _midi([
        (0, [0xB2, 64, 127]), (0, [0xE2, 0, 32]), (240, gm_reset),
        (360, [0xB5, 64, 64]), (360, [0xE5, 0, 96]),
        (480, [0xB5, 121, 0]), (720, [0xB2, 121, 0]),
    ])

    converted, _changed = convert(source)

    events = _events(converted)
    assert events[:5] == [
        (0, [0xB0, 64, 127]), (0, [0xE0, 0, 32]), (240, gm_reset),
        (360, [0xB0, 64, 64]), (360, [0xE0, 0, 96]),
    ]
    assert {(tick, tuple(raw)) for tick, raw in events[5:]} == {
        (480, (0xB0, 64, 0)), (480, (0xE0, 0, 64)),
    }
