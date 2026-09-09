"""Recovered Yamaha pedal edges and preservation boundaries, without vendor code."""

import pytest

from aps_midi_prep_tool_app.eseq_pedals import (
    YamahaPedalConverter,
    yamaha_pedal_controllers,
)


def _cc(controller, value, channel=0):
    return bytes((0xB0 | channel, controller, value))


@pytest.mark.parametrize("controller,values,expected", (
    (64, (0, 81, 82, 83, 84, 84, 83, 84, 82, 81, 81, 82, 81, 0), (
        (), ((2, 81),), ((2, 82),), ((2, 83),), ((0, 127), (2, 84)),
        (), ((2, 83),), ((0, 127), (2, 84)), ((2, 82),),
        ((0, 0), (2, 81)), (), ((2, 82),), ((0, 0), (2, 81)), ((2, 0),),
    )),
    (67, (0, 60, 61, 63, 64, 64, 63, 64, 61, 60, 60, 61, 60, 0), (
        (), ((2, 60),), ((2, 61),), ((2, 63),), ((0, 127), (2, 64)),
        (), ((2, 63),), ((0, 127), (2, 64)), ((2, 61),),
        ((0, 0), (2, 60)), (), ((2, 61),), ((0, 0), (2, 60)), ((2, 0),),
    )),
))
def test_raw_edges_threshold_boundaries_and_binary_before_detail(controller, values, expected):
    # The repeated press/release cases distinguish Yamaha's previous-raw rule
    # from a conventional latch that remembers the last emitted binary state.
    converter = YamahaPedalConverter((controller,))

    actual = [converter.convert_message(_cc(controller, value)) for value in values]

    assert actual == [tuple(_cc(controller, value, channel) for channel, value in outputs)
                      for outputs in expected]


def test_initial_zero_and_duplicates_do_not_set_detail_flag_until_detail_is_emitted():
    converter = YamahaPedalConverter((64,))

    assert converter.has_half_pedal is False
    assert converter.convert_message(_cc(64, 0)) == ()
    assert converter.convert_message(_cc(64, 0)) == ()
    assert converter.has_half_pedal is False
    assert converter.convert_message(_cc(64, 40)) == (_cc(64, 40, 2),)
    assert converter.has_half_pedal is True
    assert converter.convert_message(_cc(64, 40)) == ()
    assert converter.convert_message(_cc(64, 0)) == (_cc(64, 0, 2),)
    assert converter.has_half_pedal is True


def test_controller_histories_and_converter_instances_are_independent():
    first = YamahaPedalConverter((64, 67))
    second = YamahaPedalConverter((64, 67))

    assert first.convert_message(_cc(64, 84)) == (_cc(64, 127), _cc(64, 84, 2))
    assert first.convert_message(_cc(67, 64)) == (_cc(67, 127), _cc(67, 64, 2))
    assert first.convert_message(_cc(64, 81)) == (_cc(64, 0), _cc(64, 81, 2))
    assert first.convert_message(_cc(67, 64)) == ()
    assert first.convert_message(_cc(67, 60)) == (_cc(67, 0), _cc(67, 60, 2))
    assert second.has_half_pedal is False
    assert second.convert_message(_cc(64, 84)) == (_cc(64, 127), _cc(64, 84, 2))


@pytest.mark.parametrize("channel", range(16))
def test_nonselected_lanes_and_sostenuto_are_unchanged(channel):
    converter = YamahaPedalConverter((64,))
    messages = [_cc(66, 55, channel), _cc(67, 65, channel),
                bytes((0x90 | channel, 64, 84)), bytes((0xC0 | channel, 64))]
    if channel:
        messages.extend((_cc(64, 85, channel), _cc(64, 0, channel)))

    assert [converter.convert_message(raw) for raw in messages] == [(raw,) for raw in messages]
    assert converter.has_half_pedal is False


def test_existing_native_lane_wins_without_blocking_the_other_controller():
    messages = [
        _cc(64, 0), _cc(64, 80, 2), _cc(64, 127), _cc(64, 127, 2),
        _cc(67, 0), _cc(67, 63), _cc(67, 64), _cc(64, 0), _cc(64, 0, 2),
    ]
    converter = YamahaPedalConverter(yamaha_pedal_controllers(iter(messages)))

    assert converter.controllers == frozenset((67,))
    assert [converter.convert_message(raw) for raw in messages] == [
        (messages[0],), (messages[1],), (messages[2],), (messages[3],),
        (), (_cc(67, 63, 2),), (_cc(67, 127), _cc(67, 64, 2)),
        (messages[7],), (messages[8],),
    ]


@pytest.mark.parametrize("reservation", (b"\x92\x3c\x40", b"\x92\x3c\x00", b"\x82\x3c\x20"))
def test_channel_three_notes_preserve_the_entire_input(reservation):
    messages = [_cc(64, 0), _cc(64, 85), _cc(67, 64), reservation,
                _cc(64, 0), _cc(67, 0)]
    converter = YamahaPedalConverter(yamaha_pedal_controllers(messages))

    assert not converter.controllers
    assert [converter.convert_message(raw) for raw in messages] == [(raw,) for raw in messages]


def test_binary_only_lanes_keep_initial_zero_and_repeated_values():
    messages = [_cc(controller, value) for controller in (64, 67)
                for value in (0, 0, 127, 127, 0)]
    converter = YamahaPedalConverter(yamaha_pedal_controllers(messages))

    assert not converter.controllers
    assert [converter.convert_message(raw) for raw in messages] == [(raw,) for raw in messages]


def test_constructor_rejects_unsupported_pedal_controller():
    with pytest.raises(ValueError, match="only CC64 and CC67"):
        YamahaPedalConverter((64, 66))
