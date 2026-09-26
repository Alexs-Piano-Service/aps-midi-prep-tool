"""Overlap detection and repair operate on source note lifetimes."""

import io

import mido
import pytest

from aps_midi_prep_tool_app.eseq_channel_merger import merge_eseq_channels_to_channel0_bytes
from aps_midi_prep_tool_app.eseq_converter import parse_eseq_bytes
from aps_midi_prep_tool_app.midi_channel_merger import (
    merge_midi_channels_to_channel0_bytes, merge_midi_channels_to_channel0_path,
)
from aps_midi_prep_tool_app.piano_overlap import ChannelMergeCancelled
from test_eseq_channel_merging import _eseq
from test_midi_channel_merging import _midi, _track


def song(notes, midi_type=1):
    """Each source note has its own track; channel, start, end, pitch, velocity."""
    tracks = [_track([(start, bytes([0x90 | channel, pitch, velocity])),
                      (end, bytes([0x80 | channel, pitch, 42]))], end_tick=240)
              for channel, start, end, pitch, velocity in notes]
    if midi_type == 0:
        events = [(tick, raw) for channel, start, end, pitch, velocity in notes
                  for tick, raw in [(start, bytes([0x90 | channel, pitch, velocity])),
                                    (end, bytes([0x80 | channel, pitch, 42]))]]
        tracks = [_track(sorted(events, key=lambda event: event[0]), end_tick=240)]
    return _midi(*tracks, format_type=midi_type)


def intervals(data):
    midi = mido.MidiFile(file=io.BytesIO(data))
    tick = 0
    held = {}
    notes = []
    for message in mido.merge_tracks(midi.tracks):
        tick += message.time
        if message.type == "note_on" and message.velocity:
            assert message.note not in held, "Output still has a same-key overlap"
            held[message.note] = (tick, message.velocity)
        elif message.type in {"note_off", "note_on"}:
            assert message.note in held, "Output has an unmatched release"
            start, velocity = held.pop(message.note)
            notes.append((start, tick, message.note, velocity))
    assert not held
    return sorted(notes)


@pytest.mark.parametrize("midi_type", [0, 1])
@pytest.mark.parametrize("mode, expected", [
    ("smart", [(20, 40, 60, 70), (60, 80, 60, 90)]),
    ("retrigger", [(0, 20, 60, 80), (20, 40, 60, 70), (60, 80, 60, 90)]),
])
def test_repairs_use_original_pairs_and_keep_attacks(midi_type, mode, expected):
    source = song([(4, 0, 120, 60, 80), (5, 20, 40, 60, 70), (6, 60, 80, 60, 90)], midi_type)
    seen = []
    output, changed = merge_midi_channels_to_channel0_bytes(
        source, overlap_handler=lambda count: seen.append(count) or mode,
    )
    assert changed and seen == [2]
    assert intervals(output) == expected
    parsed = mido.MidiFile(file=io.BytesIO(output))
    assert parsed.type == midi_type
    assert len(parsed.tracks) == (3 if midi_type == 1 else 1)
    assert sum(message.time for message in mido.merge_tracks(parsed.tracks)) == 240
    assert merge_midi_channels_to_channel0_bytes(output, overlap_handler=lambda _: pytest.fail("No-op prompt")) == (output, False)


@pytest.mark.parametrize("mode", ["smart", "retrigger"])
def test_duplicate_unisons_prefer_stronger_velocity_without_counting_as_two_strikes(mode):
    source = song([(1, 0, 120, 60, 80), (2, 20, 40, 60, 60), (3, 20, 40, 60, 100)])
    output, _ = merge_midi_channels_to_channel0_bytes(source, overlap_mode=mode)
    assert intervals(output) == [(0, 20, 60, 80), (20, 40, 60, 100)]


@pytest.mark.parametrize("mode, end", [("smart", 30), ("retrigger", 120)])
def test_simultaneous_notes_choose_shortest_or_longest(mode, end):
    output, _ = merge_midi_channels_to_channel0_bytes(
        song([(1, 0, 120, 60, 90), (2, 0, 30, 60, 70)]), overlap_mode=mode,
    )
    assert intervals(output) == [(0, end, 60, 70 if mode == "smart" else 90)]


@pytest.mark.parametrize("notes, midi_type", [
    ([(1, 0, 100, 60, 80), (2, 0, 100, 64, 80)], 1),  # Chord.
    ([(1, 0, 40, 60, 80), (2, 40, 80, 60, 80)], 1),  # Touching.
    ([(1, 0, 40, 60, 80), (2, 50, 80, 60, 80)], 1),  # Separate.
    ([(1, 0, 100, 60, 80), (2, 0, 100, 60, 80)], 2),  # Separate sequences.
])
def test_no_prompt_without_a_positive_same_key_overlap(notes, midi_type):
    merge_midi_channels_to_channel0_bytes(
        song(notes, midi_type), overlap_handler=lambda _: pytest.fail("Unexpected overlap prompt"),
    )


def test_type2_asks_once_for_overlaps_within_independent_tracks():
    track = _track([(0, b"\x91\x3c\x50"), (10, b"\x92\x3c\x60"),
                    (20, b"\x82\x3c\x00"), (40, b"\x81\x3c\x00")])
    seen = []
    output, _ = merge_midi_channels_to_channel0_bytes(
        _midi(track, track, format_type=2), overlap_handler=lambda count: seen.append(count) or "retrigger",
    )
    assert seen == [2]
    parsed = mido.MidiFile(file=io.BytesIO(output))
    for track in parsed.tracks:
        one = mido.MidiFile(type=0, tracks=[track])
        data = io.BytesIO()
        one.save(file=data)
        assert intervals(data.getvalue()) == [(0, 10, 60, 80), (10, 20, 60, 96)]


@pytest.mark.parametrize("mode", ["smart", "retrigger"])
def test_releases_precede_attacks_across_tracks_even_when_note_order_is_reversed(mode):
    source = song([(2, 40, 80, 60, 90), (1, 0, 120, 60, 80), (3, 80, 100, 60, 70)])
    output, _ = merge_midi_channels_to_channel0_bytes(source, overlap_mode=mode)
    expected = [(40, 80, 60, 90), (80, 100, 60, 70)]
    if mode == "retrigger":
        expected.insert(0, (0, 40, 60, 80))
    assert intervals(output) == expected


@pytest.mark.parametrize("termination", [b"\xb1\x7b\x00", b"\xb1\x78\x00",
                                         b"\xf0\x05\x7e\x7f\x09\x01\xf7"])
def test_channel_and_system_termination_end_source_lifetimes(termination):
    source = _midi(_track([(0, b"\x91\x3c\x50"), (20, termination),
                           (30, b"\x92\x3c\x60"), (40, b"\x82\x3c\x00")]))
    merge_midi_channels_to_channel0_bytes(source, overlap_handler=lambda _: pytest.fail("False overlap"))


def test_zero_velocity_releases_and_missing_release_are_repaired():
    source = _midi(_track([(0, b"\x91\x3c\x50"), (10, b"\x92\x3c\x60"),
                           (20, b"\x92\x3c\x00"), (30, b"\x93\x3c\x70")], end_tick=50))
    output, _ = merge_midi_channels_to_channel0_bytes(source, overlap_mode="retrigger")
    assert intervals(output) == [(0, 10, 60, 80), (10, 20, 60, 96), (30, 50, 60, 112)]


def test_off_preserves_legacy_merge_and_cancel_leaves_destination_untouched(tmp_path):
    source = song([(1, 0, 120, 60, 80), (2, 20, 40, 60, 90)])
    assert merge_midi_channels_to_channel0_bytes(source, overlap_handler=lambda _: "off") == merge_midi_channels_to_channel0_bytes(source)
    path, destination = tmp_path / "song.mid", tmp_path / "output.mid"
    path.write_bytes(source)
    destination.write_bytes(b"existing")
    with pytest.raises(ChannelMergeCancelled):
        merge_midi_channels_to_channel0_path(path, destination, overlap_handler=lambda _: None)
    assert path.read_bytes() == source and destination.read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.mid", "song.mid"]


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("mode, starts", [("smart", [20, 60]), ("retrigger", [0, 20, 60])])
def test_native_eseq_repairs_keep_delay_tempo_title_and_suffix(variant, mode, starts):
    source = _eseq(b"\xf1\x00\x91\x3c\x50\xf3\x14\x92\x3c\x60"
                   b"\xf3\x14\x82\x3c\x00\xf9\x04\x00\xf3\x14\x93\x3c\x70"
                   b"\xf3\x14\x83\x3c\x00\xf3\x28\x81\x3c\x00\xf2", variant)
    calls = []
    output, changed = merge_eseq_channels_to_channel0_bytes(
        source, filename="SONG.MDA" if variant == "mda" else "SONG.FIL",
        overlap_handler=lambda count: calls.append(count) or mode,
    )
    assert changed and calls == [2]
    before, after = parse_eseq_bytes(source), parse_eseq_bytes(output)
    assert [tick for tick, _, raw in after.events if raw[0] == 0x90 and raw[2]] == starts
    assert after.title == before.title and after.end_tick == before.end_tick
    assert after.tempo_events == before.tempo_events
    assert output.endswith(b"opaque trailing data")
    held = False
    for tick, _, raw in after.events:
        if raw[0] == 0x90 and raw[2]:
            assert not held
            held = True
        elif raw[0] == 0x80:
            assert held
            held = False
    assert not held


def test_eseq_missing_release_is_closed_before_end_command():
    source = _eseq(b"\xf1\x00\x91\x3c\x50\xf3\x14\x92\x3c\x60\xf3\x14\xf2")
    output, _ = merge_eseq_channels_to_channel0_bytes(source, overlap_mode="retrigger")
    notes = [(tick, raw) for tick, _, raw in parse_eseq_bytes(output).events if raw[0] & 0xF0 in (0x80, 0x90)]
    assert notes == [(0, b"\x90\x3c\x50"), (20, b"\x80\x3c\x00"),
                     (20, b"\x90\x3c\x60"), (40, b"\x80\x3c\x00")]


def test_eseq_reset_with_embedded_delay_releases_at_its_completion():
    reset = b"\xf0\x7e\x7f\x09\x01\xf3\x0a\xf7"
    source = _eseq(b"\xf1\x00\x91\x3c\x50\xf3\x05\x92\x3c\x60\xf3\x05" + reset + b"\xf2")
    output, _ = merge_eseq_channels_to_channel0_bytes(source, overlap_mode="retrigger")
    assert reset in output
    notes = [(tick, raw) for tick, _, raw in parse_eseq_bytes(output).events if raw[0] & 0xF0 in (0x80, 0x90)]
    assert notes == [(0, b"\x90\x3c\x50"), (5, b"\x80\x3c\x00"),
                     (5, b"\x90\x3c\x60"), (20, b"\x80\x3c\x00")]
