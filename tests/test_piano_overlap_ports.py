"""Overlap repair keeps independent MIDI destinations and unrelated events intact."""

import io
from collections import defaultdict

import mido
import pytest

from aps_midi_prep_tool_app.midi_channel_merger import (
    merge_midi_channels_to_channel0_bytes,
)
from aps_midi_prep_tool_app.midi_type0_converter import _convert_midi_bytes_to_type0
from aps_midi_prep_tool_app.piano_overlap import resolve_note_overlaps
from test_midi_channel_merging import _midi, _track


def _port(number):
    return bytes([0xFF, 0x21, 0x01, number])


def _on(channel, velocity=80, pitch=60):
    return bytes([0x90 | channel, pitch, velocity])


def _off(channel, pitch=60):
    return bytes([0x80 | channel, pitch, 42])


def _timeline(*tracks):
    return sorted(
        [(tick, track, order, raw)
         for track, events in enumerate(tracks)
         for order, (tick, raw) in enumerate(events)],
        key=lambda event: event[:-1],
    )


def _routed_notes(events):
    ports = defaultdict(int)
    notes = []
    for tick, track, _order, raw in sorted(events, key=lambda event: event[:-1]):
        if len(raw) == 4 and raw[:3] == b"\xff\x21\x01":
            ports[track] = raw[3]
        elif len(raw) == 3 and raw[0] & 0xF0 in (0x80, 0x90):
            notes.append((tick, ports[track], raw))
    return sorted(notes)


def _midi_intervals(data):
    """Read port routing per track before combining the note timeline."""
    events = []
    for track in mido.MidiFile(file=io.BytesIO(data)).tracks:
        tick, port = 0, 0
        for message in track:
            tick += message.time
            if message.type == "midi_port":
                port = message.port
            elif message.type in {"note_on", "note_off"}:
                attack = message.type == "note_on" and message.velocity > 0
                events.append((tick, bool(attack), port, message.note, message.velocity))
    held, intervals = {}, []
    for tick, attack, port, pitch, velocity in sorted(events):
        key = port, pitch
        if attack:
            assert key not in held, "Output has overlapping notes on one MIDI port"
            held[key] = tick, velocity
        else:
            assert key in held, "Output has an unmatched release on a MIDI port"
            start, attack_velocity = held.pop(key)
            intervals.append((port, start, tick, pitch, attack_velocity))
    assert not held, "Output leaves a note held on a MIDI port"
    return sorted(intervals)


@pytest.fixture(params=["merge", "type0"])
def convert(request):
    if request.param == "merge":
        return merge_midi_channels_to_channel0_bytes

    def type0(data, *, overlap_mode, overlap_handler=None):
        return _convert_midi_bytes_to_type0(
            data, remap_all_instruments_to_channel0=True,
            overlap_handler=overlap_handler or (lambda _count: overlap_mode),
        )

    return type0


@pytest.mark.parametrize("mode", ["smart", "retrigger"])
def test_different_ports_do_not_prompt_or_change_note_lifetimes(mode, convert):
    tracks = [
        [(0, _on(1)), (100, _off(1))],
        [(0, _port(1)), (20, _on(2, 90)), (40, _off(2))],
    ]
    events = _timeline(*tracks)
    [repaired] = resolve_note_overlaps(
        [(events, (120, 0, 0, b""), True)], mode,
        lambda _count: pytest.fail("Separate MIDI ports were counted as an overlap"),
    )
    assert repaired == events

    source = _midi(*[_track(track, end_tick=120) for track in tracks], format_type=1)
    output, changed = convert(
        source, overlap_mode=mode,
        overlap_handler=lambda _count: pytest.fail("Separate MIDI ports prompted for repair"),
    )
    assert changed
    assert _midi_intervals(output) == [(0, 0, 100, 60, 80), (1, 20, 40, 60, 90)]


@pytest.mark.parametrize("mode", ["smart", "retrigger"])
def test_repair_on_one_port_keeps_same_pitch_on_another_port(mode, convert):
    tracks = [
        [(0, _on(1)), (100, _off(1))],
        [(20, _on(2, 90)), (40, _off(2))],
        [(0, _port(1)), (10, _on(3, 70)), (80, _off(3))],
    ]
    events = _timeline(*tracks)
    seen = []
    [repaired] = resolve_note_overlaps(
        [(events, (120, 0, 0, b""), True)], mode,
        lambda count: seen.append(count) or mode,
    )
    assert seen == [1]
    assert _routed_notes(repaired) == sorted([
        (0, 0, _on(1)), (20, 0, _off(1)),
        (20, 0, _on(2, 90)), (40, 0, _off(2)),
        (10, 1, _on(3, 70)), (80, 1, _off(3)),
    ])

    source = _midi(*[_track(track, end_tick=120) for track in tracks], format_type=1)
    output, _changed = convert(source, overlap_mode=mode)
    assert _midi_intervals(output) == [
        (0, 0, 20, 60, 80), (0, 20, 40, 60, 90), (1, 10, 80, 60, 70),
    ]


def test_missing_release_uses_note_port_when_end_anchor_routes_elsewhere():
    events = _timeline(
        [(0, _port(2)), (1, _on(3, pitch=64)), (2, _off(3, pitch=64))],
        [(0, _port(1)), (0, _on(1)), (20, _on(2, 90)), (40, _off(1))],
    )
    [repaired] = resolve_note_overlaps(
        [(events, (100, 0, 0, b""), True)], "retrigger",
    )
    assert _routed_notes(repaired) == sorted([
        (0, 1, _on(1)), (20, 1, _off(1)),
        (20, 1, _on(2, 90)), (100, 1, bytes([0x82, 60, 0])),
        (1, 2, _on(3, pitch=64)), (2, 2, _off(3, pitch=64)),
    ])


@pytest.mark.parametrize("mode", ["smart", "retrigger"])
def test_port_changes_within_a_track_keep_independent_note_lifetimes(mode, convert):
    track = [
        (0, _on(1)), (10, _port(1)), (20, _on(2, 90)), (40, _off(2)),
        (50, _port(0)), (100, _off(1)),
    ]
    events = _timeline(track)
    [repaired] = resolve_note_overlaps(
        [(events, (120, 0, 0, b""), True)], mode,
        lambda _count: pytest.fail("A port change conflated different destinations"),
    )
    assert repaired == events
    output, _changed = convert(
        _midi(_track(track, end_tick=120)), overlap_mode=mode,
    )
    assert _midi_intervals(output) == [(0, 0, 100, 60, 80), (1, 20, 40, 60, 90)]


def test_system_reset_ends_notes_only_on_its_midi_port():
    events = _timeline(
        [(0, _on(1)), (30, _on(2, 90)), (40, _off(2)), (100, _off(1))],
        [(0, _port(1)), (20, b"\xf0\x05\x7e\x7f\x09\x01\xf7")],
    )
    seen = []
    [repaired] = resolve_note_overlaps(
        [(events, (120, 0, 0, b""), True)], "retrigger",
        lambda count: seen.append(count) or "retrigger",
    )
    assert seen == [1]
    assert _routed_notes(repaired) == sorted([
        (0, 0, _on(1)), (30, 0, _off(1)),
        (30, 0, _on(2, 90)), (40, 0, _off(2)),
    ])


@pytest.mark.parametrize("mode", ["smart", "retrigger"])
def test_zero_duration_note_and_unmatched_release_survive_other_same_key_repair(mode):
    events = _timeline([
        (0, _on(1)), (20, _on(2, 90)), (40, _off(2)), (100, _off(1)),
        (150, _on(3, 70)), (150, _off(3)), (180, _off(4)),
    ])
    [repaired] = resolve_note_overlaps(
        [(events, (200, 0, 0, b""), True)], mode,
    )
    assert _routed_notes(repaired) == sorted([
        (0, 0, _on(1)), (20, 0, _off(1)),
        (20, 0, _on(2, 90)), (40, 0, _off(2)),
        (150, 0, _on(3, 70)), (150, 0, _off(3)), (180, 0, _off(4)),
    ])
