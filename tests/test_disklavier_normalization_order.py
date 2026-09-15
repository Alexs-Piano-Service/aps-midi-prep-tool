"""Same-tick normalization must preserve the receiver's ordered state changes."""

import struct

import pytest

from aps_midi_prep_tool_app.midi_type0_converter import (
    _convert_midi_bytes_to_type0,
    _normalize_disklavier_merged_events,
    _parse_midi_chunks,
    _parse_track_events,
)


def _events(*messages):
    return [(0, 0, order, raw) for order, raw in enumerate(messages)]


@pytest.mark.parametrize("controller", [64, 66, 67])
@pytest.mark.parametrize("values", [(127, 0, 127), (0, 127, 0)])
def test_same_tick_pedal_value_reversal_is_preserved(controller, values):
    events = _events(*(bytes([0xB0, controller, value]) for value in values))

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert normalized == events
    assert not changed


def test_same_tick_program_value_reversal_is_preserved():
    events = _events(*(bytes([0xC0, value]) for value in (40, 73, 40)))

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert normalized == events
    assert not changed


@pytest.mark.parametrize("message", [
    b"\xB0\x40\x7F", b"\xB0\x42\x7F", b"\xB0\x43\x7F", b"\xC0\x28",
])
def test_adjacent_identical_same_tick_state_is_deduplicated(message):
    events = _events(message, message)

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert normalized == events[:1]
    assert changed


@pytest.mark.parametrize("message", [b"\xB0\x40\x7F", b"\xC0\x28"])
@pytest.mark.parametrize("barrier", [
    b"\x90\x3C\x64",  # Note On
    b"\x80\x3C\x00",  # Note Off
    b"\x90\x3C\x00",  # Note Off using zero velocity
    b"\xB0\x79\x00",  # Reset All Controllers
    b"\xB0\x7B\x00",  # All Notes Off
    b"\xB0\x00\x01",  # Bank Select makes a repeated program meaningful.
    b"\xF0\x05\x7E\x7F\x09\x01\xF7",  # GM System On
    b"\xF7\x01\xFF",  # Escaped System Reset
])
def test_same_tick_state_is_retained_after_receiver_activity(message, barrier):
    # An existing program avoids the separate default-piano insertion path.
    events = _events(b"\xC0\x00", message, barrier, message)

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert normalized == events
    assert not changed


def test_independent_state_and_metadata_do_not_prevent_safe_deduplication():
    events = _events(
        b"\xB0\x40\x7F", b"\xB0\x43\x7F", b"\xB5\x79\x00",
        b"\xFF\x01\x01x", b"\xB0\x40\x7F",
    )

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert normalized == events[:-1]
    assert changed


def test_identical_state_at_different_ticks_is_retained():
    events = [(tick, 0, order, b"\xB0\x40\x7F") for order, tick in enumerate((0, 1))]

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert normalized == events
    assert not changed


def test_legacy_pedal_remapping_preserves_same_tick_value_reversal():
    events = _events(
        b"\xC0\x00", b"\xB2\x40\x00", b"\xB2\x40\x7F",
        b"\xB2\x40\x00", b"\x90\x3C\x64",
    )

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert [event[-1] for event in normalized] == [
        b"\xC0\x00", b"\xB0\x40\x00", b"\xB0\x40\x7F",
        b"\xB0\x40\x00", b"\x90\x3C\x64",
    ]
    assert changed


@pytest.mark.parametrize("controller", [64, 66, 67])
@pytest.mark.parametrize("reset_tick,repress_tick", [(0, 0), (10, 20)])
def test_legacy_pedal_reset_releases_routed_pedal_before_repress(controller, reset_tick, repress_tick):
    press = bytes([0xB2, controller, 127])
    reset = b"\xB2\x79\x00"
    events = [
        (0, 0, 0, b"\xC0\x00"), (0, 0, 1, press),
        (reset_tick, 0, 2, reset), (repress_tick, 0, 3, press),
        (repress_tick, 0, 4, b"\x90\x3C\x64"),
    ]

    normalized, changed = _normalize_disklavier_merged_events(events)

    assert [(event[0], event[-1]) for event in normalized] == [
        (0, b"\xC0\x00"), (0, bytes([0xB0, controller, 127])),
        (reset_tick, reset), (reset_tick, bytes([0xB0, controller, 0])),
        (repress_tick, bytes([0xB0, controller, 127])),
        (repress_tick, b"\x90\x3C\x64"),
    ]
    assert changed
    assert _normalize_disklavier_merged_events(normalized) == (normalized, False)


def test_global_reset_clears_routed_pedal_state_before_legacy_cc121():
    events = _events(
        b"\xC0\x00", b"\xB2\x40\x7F",
        b"\xF0\x05\x7E\x7F\x09\x01\xF7", b"\xB2\x79\x00",
        b"\x90\x3C\x64",
    )

    normalized, changed = _normalize_disklavier_merged_events(events)

    expected = list(events)
    expected[1] = (0, 0, 1, b"\xB0\x40\x7F")
    assert normalized == expected
    assert changed


def test_type0_normalization_preserves_cross_track_state_order_and_is_idempotent():
    tracks = []
    messages = [bytes([0xB0, 64, value]) for value in (0, 127, 0)]
    for raw in messages:
        payload = b"\x00" + raw + b"\x00\xFF\x2F\x00"
        tracks.append(b"MTrk" + len(payload).to_bytes(4, "big") + payload)
    source = struct.pack(">4sIHHH", b"MThd", 6, 1, len(tracks), 480) + b"".join(tracks)

    converted, changed = _convert_midi_bytes_to_type0(source, normalize_disklavier=True)

    assert changed
    _header_end, format_type, track_count, chunks = _parse_midi_chunks(converted)
    assert (format_type, track_count) == (0, 1)
    track = next(chunk for chunk in chunks if chunk["id"] == b"MTrk")
    events, end_tick = _parse_track_events(converted[track["data_start"]:track["data_end"]])
    assert [(tick, raw) for tick, _order, raw in events] == [(0, raw) for raw in messages]
    assert end_tick == 0
    assert _convert_midi_bytes_to_type0(converted, normalize_disklavier=True) == (converted, False)
