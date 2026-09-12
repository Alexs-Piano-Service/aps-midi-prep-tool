"""A controller reset makes a repeated pedal value meaningful again."""

import pytest

from aps_midi_prep_tool_app.midi_type0_converter import (
    apply_pedal_controller_options_to_midi_events,
)


@pytest.mark.parametrize("controller", [64, 66, 67])
@pytest.mark.parametrize("tuple_size", [3, 4])
@pytest.mark.parametrize("binary_pedal", [False, True])
def test_identical_pedal_press_after_cc121_is_retained(controller, tuple_size, binary_pedal):
    raws = [
        bytes([0xB2, controller, 100]), bytes([0xB2, 121, 0]),
        bytes([0xB2, controller, 100]), bytes([0xB2, controller, 100]),
        bytes([0xB2, controller, 0]),
    ]
    events = [
        (tick, order, raw) if tuple_size == 3 else (tick, 1, order, raw)
        for order, (tick, raw) in enumerate(zip([0, 480, 480, 720, 960], raws))
    ]

    cleaned, changed = apply_pedal_controller_options_to_midi_events(
        events, pedal_cleanup=True, binary_pedal=binary_pedal, end_tick=1200,
    )

    assert changed
    value = 127 if binary_pedal else 100
    assert [(event[0], event[-1]) for event in cleaned] == [
        (0, bytes([0xB2, controller, value])), (480, bytes([0xB2, 121, 0])),
        (480, bytes([0xB2, controller, value])), (960, bytes([0xB2, controller, 0])),
    ]


def test_cc121_invalidates_only_its_channel_and_clears_final_release_state():
    events = [
        (0, 0, b"\xB2\x40\x7F"), (0, 1, b"\xB5\x40\x7F"),
        (480, 2, b"\xB2\x79\x00"), (720, 3, b"\xB5\x40\x7F"),
    ]

    cleaned, changed = apply_pedal_controller_options_to_midi_events(
        events, pedal_cleanup=True, end_tick=960,
    )

    assert changed
    assert [(event[0], event[-1]) for event in cleaned] == [
        (0, b"\xB2\x40\x7F"), (0, b"\xB5\x40\x7F"),
        (480, b"\xB2\x79\x00"), (960, b"\xB5\x40\x00"),
    ]


@pytest.mark.parametrize("reset", [
    b"\xF0\x05\x7E\x7F\x09\x01\xF7",  # GM System On
    b"\xF0\x05\x7E\x10\x09\x03\xF7",  # GM2 System On, device ID 16
    b"\xF0\x0A\x41\x10\x42\x12\x40\x00\x7F\x00\x41\xF7",  # GS Reset
    b"\xF0\x08\x43\x12\x4C\x00\x00\x7E\x00\xF7",  # XG System On
    b"\xF7\x01\xFF",  # SMF escape containing System Reset
    b"\xF7\x06\xF0\x7E\x7F\x09\x01\xF7",  # Escaped complete GM message
])
def test_system_reset_invalidates_all_channels(reset):
    events = [
        (0, 0, 0, b"\xB2\x40\x7F"), (0, 1, 0, b"\xB5\x42\x7F"),
        (480, 0, 1, reset), (480, 1, 1, b"\xB5\x42\x7F"),
        (960, 1, 2, b"\xB5\x42\x00"),
    ]

    cleaned, changed = apply_pedal_controller_options_to_midi_events(events, pedal_cleanup=True)

    assert not changed
    assert cleaned is events


@pytest.mark.parametrize("raw", [
    b"\xB2\x7B\x00",  # All Notes Off does not reset sustain.
    b"\xFF\x01\x01\xFF",  # A meta event is not the System Reset byte.
    b"\xF0\x07\x7F\x7F\x04\x01\x00\x40\xF7",  # Master volume
])
def test_unrelated_messages_do_not_reset_pedal_state(raw):
    events = [
        (0, 0, b"\xB2\x40\x7F"), (480, 1, raw),
        (720, 2, b"\xB2\x40\x7F"), (960, 3, b"\xB2\x40\x00"),
    ]

    cleaned, changed = apply_pedal_controller_options_to_midi_events(events, pedal_cleanup=True)

    assert changed
    assert cleaned == [events[0], events[1], events[3]]
