import struct

import pytest

from aps_midi_prep_tool_app.conversion_review import compare_music_bytes, inspect_music_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError, convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes, convert_midi_file_to_eseq_path, parse_eseq_bytes,
)
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq, _parse_track_events


def _midi(events, *, division=384):
    track = bytearray()
    previous = 0
    for tick, event in events:
        track.extend(_encode_vlq(tick - previous))
        track.extend(event)
        previous = tick
    track.extend(b"\x00\xff\x2f\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, division) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _sysex(status, payload):
    return bytes([status]) + _encode_vlq(len(payload)) + payload


def _notes():
    return [(0, b"\x90\x3c\x40"), (96, b"\xb0\x40\x48"),
            (384, b"\x80\x3c\x00"), (384, b"\xb0\x40\x00")]


@pytest.mark.parametrize("payload", (
    b"\xf2\x00\x00",  # Song Position Pointer: valid F7 escape, no final F7.
    b"\xf1\x00", b"\xf3\x01", b"\xf8", b"\xf6", b"\xf0\x43\x01\xf7",
))
def test_valid_midi_f7_escapes_are_rejected_instead_of_swallowing_following_notes(payload):
    source = _midi([(0, _sysex(0xF7, payload)), *_notes()])
    assert inspect_music_bytes(source).notes == 1

    with pytest.raises(EseqConversionError, match="standalone MIDI F7 escape"):
        convert_midi_bytes_to_eseq_bytes(source)


@pytest.mark.parametrize("existing_output", (False, True))
def test_unsupported_escape_does_not_create_or_overwrite_output(tmp_path, existing_output):
    source = tmp_path / "source.mid"
    destination = tmp_path / "SONG.FIL"
    source_bytes = _midi([(0, _sysex(0xF7, b"\xf2\x00\x00")), *_notes()])
    source.write_bytes(source_bytes)
    if existing_output:
        destination.write_bytes(b"Existing customer song")

    with pytest.raises(EseqConversionError, match="Keep this song as MIDI"):
        convert_midi_file_to_eseq_path(source, destination)

    assert source.read_bytes() == source_bytes
    assert destination.exists() is existing_output
    if existing_output:
        assert destination.read_bytes() == b"Existing customer song"
    assert {path.name for path in tmp_path.iterdir()} == (
        {source.name, destination.name} if existing_output else {source.name}
    )


@pytest.mark.parametrize("split", (False, True))
def test_complete_or_same_tick_split_sysex_preserves_wire_bytes_and_following_music(split):
    payload = b"\x43\x10\x4c\x00\x00\x7e\x00\xf7"
    packets = (
        [(0, _sysex(0xF0, payload[:3])), (0, b"\xff\x01\x04Test"),
         (0, _sysex(0xF7, payload[3:6])), (0, _sysex(0xF7, payload[6:]))]
        if split else [(0, _sysex(0xF0, payload))]
    )
    source = _midi([*packets, *_notes()])

    eseq = convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve", pedal_policy="preserve")
    parsed = parse_eseq_bytes(eseq)
    restored = convert_eseq_bytes_to_midi_bytes(eseq)
    report = compare_music_bytes(source, restored)

    assert [raw for _tick, _order, raw in parsed.events if raw[0] == 0xF0] == [b"\xf0" + payload]
    events, _end = _parse_track_events(restored[22:])
    assert [raw for _tick, _order, raw in events if raw[0] == 0xF0] == [_sysex(0xF0, payload)]
    assert report.before.notes == report.after.notes == 1
    assert not report.notes_changed
    assert not report.pedals_changed
    assert report.before.duration_seconds == pytest.approx(report.after.duration_seconds, abs=0.002)


@pytest.mark.parametrize("continuation_tick", (1, 96, 247, 384, 3072))
def test_timed_sysex_continuations_preserve_packet_timing_and_skip_generated_barlines(continuation_tick):
    source = _midi([(0, b"\xff\x58\x04\x04\x02\x18\x08"),
                    (0, _sysex(0xF0, b"\x43\x01")),
                    (continuation_tick, _sysex(0xF7, b"\x02\xf7")),
                    (continuation_tick, b"\x90\x3c\x40"),
                    (continuation_tick + 384, b"\x80\x3c\x00")])

    eseq = convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve")
    parsed = parse_eseq_bytes(eseq)
    restored = convert_eseq_bytes_to_midi_bytes(eseq)
    events, _end = _parse_track_events(restored[22:])

    assert [(tick, raw) for tick, _order, raw in parsed.events] == [
        (0, b"\xf0\x43\x01"), (continuation_tick, b"\xf7\x02\xf7"),
        (continuation_tick, b"\x90\x3c\x40"), (continuation_tick + 384, b"\x80\x3c\x00"),
    ]
    assert [(tick, raw) for tick, _order, raw in events if raw[0] in (0xF0, 0xF7)] == [
        (0, _sysex(0xF0, b"\x43\x01")), (continuation_tick, _sysex(0xF7, b"\x02\xf7")),
    ]
    assert not compare_music_bytes(source, restored).notes_changed


def test_intervening_channel_event_does_not_become_part_of_sysex_payload():
    source = _midi([(0, _sysex(0xF0, b"\x43\x01")),
                    (0, b"\x90\x3c\x40"), (0, _sysex(0xF7, b"\x02\xf7"))])

    with pytest.raises(EseqConversionError, match="intervening MIDI events"):
        convert_midi_bytes_to_eseq_bytes(source)


def test_incomplete_sysex_is_rejected_before_emitting_an_eseq_stream():
    with pytest.raises(EseqConversionError, match="missing its terminating F7"):
        convert_midi_bytes_to_eseq_bytes(_midi([(0, _sysex(0xF0, b"\x43\x01"))]))


def test_eseq_parser_rejects_lone_f7_instead_of_scanning_through_song_events():
    complete = convert_midi_bytes_to_eseq_bytes(_midi([(0, _sysex(0xF0, b"\x43\x01\xf7")), *_notes()]))
    damaged = bytearray(complete)
    damaged[damaged.index(b"\xf0\x43\x01\xf7")] = 0xF7

    with pytest.raises(EseqConversionError, match="standalone F7 in E-SEQ"):
        parse_eseq_bytes(bytes(damaged))


def _external_eseq_sysex(body):
    plain = b"\xf0\x43\x01\x02\xf7"
    # This external-stream fixture deliberately retains raw PPQN ticks and
    # channels, without fresh-MIDI compatibility preparation.
    seed = convert_midi_bytes_to_eseq_bytes(
        _midi([(0, _sysex(0xF0, plain[1:])), *_notes()]), timing_policy="preserve",
        pedal_policy="preserve",
    )
    used = int.from_bytes(seed[3:7], "little")
    original = bytearray(seed[:used].replace(plain, b"\xf0" + body + b"\xf7", 1))
    original[3:7] = len(original).to_bytes(4, "little")
    original[0x1F:0x23] = (len(original) - 0x77).to_bytes(4, "little")
    return bytes(original)


@pytest.mark.parametrize("prefix,suffix,delay,delay_ticks", (
    # Yamaha EC001/EP002 examples put F4 03 00 before, within, or after
    # their 43 73 setup data. Use only these tiny synthetic message fixtures.
    (b"", b"\x43\x73\x01\x14", b"\xf4\x03\x00", 3),
    (b"\x43", b"\x73\x01\x14", b"\xf4\x03\x00", 3),
    (b"\x43\x73", b"\x13\x24\x01", b"\xf4\x03\x00", 3),
    (b"\x43\x73\x01", b"\x14", b"\xf4\x03\x00", 3),
    (b"\x43\x73\x13\x24", b"\x01", b"\xf4\x03\x00", 3),
    (b"\x43\x73\x01\x14", b"", b"\xf4\x03\x00", 3),
    (b"\x43\x73", b"\x01\x14", b"\xf3\x03", 3),
    (b"\x43\x73", b"\x01\x14", b"\xf3\xf7", 247),
))
def test_external_eseq_delays_become_valid_timed_midi_packets_and_roundtrip(prefix, suffix, delay, delay_ticks):
    original = _external_eseq_sysex(prefix + delay + suffix)
    before = parse_eseq_bytes(original)
    expected_packets = [(0, b"\xf0" + prefix), (delay_ticks, b"\xf7" + suffix + b"\xf7")]

    midi = convert_eseq_bytes_to_midi_bytes(original, midi_metadata_policy="archival")
    exported_events, _end = _parse_track_events(midi[22:])
    assert [(tick, raw) for tick, _order, raw in exported_events if raw[0] in (0xF0, 0xF7)] == [
        (tick, _sysex(raw[0], raw[1:])) for tick, raw in expected_packets
    ]
    restored = convert_midi_bytes_to_eseq_bytes(midi)
    after = parse_eseq_bytes(restored)
    expected = expected_packets + [(tick + delay_ticks, raw) for tick, raw in _notes()]

    assert [(tick, raw) for tick, _order, raw in before.events] == expected
    # Preserving a short-delay layout can split an old high-byte F3 into
    # several seven-bit delays. Empty continuation packets transmit no bytes;
    # all actual packet data and following channel events retain their ticks.
    assert [(tick, raw) for tick, _order, raw in after.events if raw != b"\xF7"] == expected
    assert after.end_tick == before.end_tick == 384 + delay_ticks
    assert after.tempo_events == before.tempo_events


@pytest.mark.parametrize("body", (
    b"\x43\x90\x3c\x40", b"\x43\xf2\x00\x00", b"\x43\xf0\x01",
    b"\x43\xf3", b"\x43\xf4\x03", b"\x43\xf7\x01", b"\x43\xf8",
))
def test_invalid_high_bit_midi_sysex_payload_is_rejected(body):
    source = _midi([(0, _sysex(0xF0, body + b"\xf7")), *_notes()])

    with pytest.raises(EseqConversionError, match="embedded status or premature F7"):
        convert_midi_bytes_to_eseq_bytes(source)


@pytest.mark.parametrize("interrupted", (
    b"\x90\x3c\x40", b"\xff\x20\x01\x00",
    b"\xff\x51\x03\x07\xa1\x20", b"\xff\x58\x04\x03\x02\x18\x08",
))
@pytest.mark.parametrize("other_track", (False, True))
def test_transmitted_events_between_sysex_packets_are_rejected_across_merged_tracks(interrupted, other_track):
    start, finish = (0, _sysex(0xF0, b"\x43\x01")), (96, _sysex(0xF7, b"\x02\xf7"))
    if other_track:
        track1 = _midi([start, finish])[14:]
        track2 = _midi([(48, interrupted)])[14:]
        source = struct.pack(">4sIHHH", b"MThd", 6, 1, 2, 384) + track1 + track2
    else:
        source = _midi([start, (48, interrupted), finish])

    with pytest.raises(EseqConversionError, match="intervening MIDI events"):
        convert_midi_bytes_to_eseq_bytes(source)


def test_nontransmitted_text_can_occur_between_timed_sysex_packets():
    source = _midi([(0, _sysex(0xF0, b"\x43\x01")), (48, b"\xff\x01\x04Text"),
                    (96, _sysex(0xF7, b"\x02\xf7")), (96, b"\x90\x3c\x40")])

    parsed = parse_eseq_bytes(convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve"))

    assert [(tick, raw) for tick, _order, raw in parsed.events] == [
        (0, b"\xf0\x43\x01"), (96, b"\xf7\x02\xf7"), (96, b"\x90\x3c\x40"),
    ]


def test_multiple_delays_inside_sysex_preserve_empty_packets_and_note_timing():
    source = _external_eseq_sysex(b"\x43\xf3\x01\xf4\x03\x00\x01")

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="archival")
    restored = convert_midi_bytes_to_eseq_bytes(midi)
    expected_packets = [(0, b"\xf0\x43"), (1, b"\xf7"), (4, b"\xf7\x01\xf7")]

    assert [(tick, raw) for tick, _order, raw in parse_eseq_bytes(restored).events] == (
        expected_packets + [(tick + 4, raw) for tick, raw in _notes()]
    )


@pytest.mark.parametrize("event", (
    b"\xff\x51\x03\x07\xa1\x20", b"\xff\x58\x04\x03\x02\x18\x08",
))
def test_preserved_tempo_or_meter_at_continuation_boundary_is_not_silently_dropped(event):
    source = _midi([(0, _sysex(0xF0, b"\x43\x01")),
                    (96, _sysex(0xF7, b"\x02\xf7")), (96, event)])

    with pytest.raises(EseqConversionError, match="tempo or meter change"):
        convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve")


def test_complete_packet_sequences_in_separate_tracks_cannot_overlap_on_one_eseq_stream():
    track1 = _midi([(0, _sysex(0xF0, b"\x43\x01")), (96, _sysex(0xF7, b"\x02\xf7"))])[14:]
    track2 = _midi([(48, _sysex(0xF0, b"\x43\x02\xf7"))])[14:]
    source = struct.pack(">4sIHHH", b"MThd", 6, 1, 2, 384) + track1 + track2

    with pytest.raises(EseqConversionError, match="preceding message ends"):
        convert_midi_bytes_to_eseq_bytes(source)


@pytest.mark.parametrize("tail", (b"\xf3", b"\xf4", b"\xf4\x03"))
def test_truncated_external_sysex_delay_is_rejected(tail):
    seed = _external_eseq_sysex(b"\x43\x01")
    pos = seed.index(b"\xf0\x43\x01\xf7")
    broken = seed[:pos] + b"\xf0\x43" + tail

    with pytest.raises(EseqConversionError, match="incomplete delay inside E-SEQ SysEx"):
        convert_eseq_bytes_to_midi_bytes(broken)
