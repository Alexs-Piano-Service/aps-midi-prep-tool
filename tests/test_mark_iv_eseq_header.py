"""Synthetic contracts derived from the Mark IV's E-SEQ reader and writer."""

import struct

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_header import analyze_eseq_playback_flags
from aps_midi_prep_tool_app.eseq_legacy import is_legacy_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import PianodirTrackEntry, build_pianodir_bytes
from aps_midi_prep_tool_app.midi_type0_converter import _parse_track_events


def _vlq(value):
    result = bytearray((value & 127,))
    while value >> 7:
        value >>= 7
        result.insert(0, 128 | (value & 127))
    return bytes(result)


def _midi(events):
    track = bytearray()
    previous = 0
    for tick, raw in events:
        track.extend(_vlq(tick - previous) + raw)
        previous = tick
    track.extend(b"\x00\xFF\x2F\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 384) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _fil(stream, *, meter=(4, 4)):
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = header[0x33] = 88
    header[0x27:0x32] = b"HEADER  FIL"
    header[0x34:0x36] = bytes(meter)
    header[0x57:0x77] = b"Header fixture".ljust(32, b" ")
    return bytes(header) + stream


def _midi_meters(data):
    assert data[:4] == b"MThd" and data[14:18] == b"MTrk"
    events, _end = _parse_track_events(data[22:])
    return [(tick, raw[3], raw[4]) for tick, _order, raw in events if raw[:3] == b"\xFF\x58\x04"]


def test_flags_record_each_note_channel_and_ignore_controller_only_channels():
    flags = analyze_eseq_playback_flags([
        b"\x90\x3C\x40", b"\x83\x40\x00", b"\x9F\x43\x00",
        b"\xB1\x07\x64", b"\xB2\x01\x30", b"\xC7\x00",
    ])

    assert flags.note_channel_mask == 0x8009
    assert not flags.has_half_pedal
    assert not flags.has_xg


@pytest.mark.parametrize("controller", (64, 67))
@pytest.mark.parametrize("value", (0, 32, 127))
def test_native_detailed_pedals_set_pedal_flag_without_note_channel_bit(controller, value):
    flags = analyze_eseq_playback_flags([bytes((0xB2, controller, value))])

    assert flags.has_half_pedal
    assert flags.note_channel_mask == 0


def test_unrelated_pedals_do_not_claim_proven_half_pedal_representation():
    flags = analyze_eseq_playback_flags([
        b"\xB0\x40\x20", b"\xB1\x43\x20", b"\xB2\x42\x20",
    ])

    assert not flags.has_half_pedal
    assert flags.note_channel_mask == 0


def test_notes_on_channel_three_prevent_claiming_its_controllers_as_detailed_pedals():
    flags = analyze_eseq_playback_flags([
        b"\xB2\x40\x20", b"\x92\x3C\x40", b"\x82\x3C\x00",
    ])

    assert flags.note_channel_mask == 0x0004
    assert not flags.has_half_pedal


@pytest.mark.parametrize("packets", (
    [b"\xF0\x43\x10\x4C\x00\x00\x7E\xF7"],
    [b"\xF0\x43\x1F", b"\xF7\x4C\x00", b"\xF7\x00\x7E\xF7"],
))
def test_xg_on_is_recognized_across_packets_and_device_numbers(packets):
    flags = analyze_eseq_playback_flags(iter(packets))

    assert flags.has_xg
    assert flags.note_channel_mask == 0
    assert not flags.has_half_pedal


@pytest.mark.parametrize("packets", (
    [b"\xF0\x7E\x7F\x09\x01\xF7"],
    [b"\xF0\x43\x20\x4C\x00\x00\x7E\xF7"],
    [b"\xF0\x43\x10\x4C\x00\x00\x7F\xF7"],
    [b"\xF0\x43\x10\x4C\x00\x00\x7E"],
))
def test_other_or_unfinished_sysex_does_not_set_xg(packets):
    assert not analyze_eseq_playback_flags(packets).has_xg


@pytest.mark.parametrize("timing_policy", ("preserve", "auto"))
def test_generated_header_describes_actual_notes_and_routed_pedals(timing_policy):
    source = _midi([
        (0, b"\xF0\x03\x43\x1F\x4C"),
        (0, b"\xF7\x04\x00\x00\x7E\xF7"),
        (0, b"\xB0\x40\x20"), (0, b"\xB1\x07\x64"),
        (0, b"\x90\x3C\x40"), (0, b"\x93\x40\x40"), (0, b"\x9F\x43\x40"),
        (384, b"\x80\x3C\x00"), (384, b"\x83\x40\x00"), (384, b"\x8F\x43\x00"),
        (384, b"\xB0\x40\x00"),
    ])

    result = convert_midi_bytes_to_eseq_bytes(source, timing_policy=timing_policy)

    assert result[0x45] == 1
    assert result[0x51] == 1
    assert result[0x54:0x56] == b"\x09\x80"
    assert any(raw == b"\xB2\x40\x20" for _tick, _order, raw in parse_eseq_bytes(result).events)


@pytest.mark.parametrize("timing_policy", ("preserve", "auto"))
def test_ordinary_controllers_do_not_set_pedal_or_note_header_flags(timing_policy):
    result = convert_midi_bytes_to_eseq_bytes(
        _midi([(0, b"\xB0\x07\x64"), (384, b"\xB2\x01\x30")]),
        timing_policy=timing_policy,
    )

    assert result[0x45] == result[0x51] == 0
    assert result[0x54:0x56] == b"\x00\x00"


@pytest.mark.parametrize("timing_policy", ("preserve", "auto"))
def test_occupied_channel_three_retains_instrument_pedals_without_half_pedal_flag(timing_policy):
    result = convert_midi_bytes_to_eseq_bytes(
        _midi([
            (0, b"\xB0\x40\x20"), (0, b"\xB2\x43\x30"), (0, b"\x92\x3C\x40"),
            (384, b"\x82\x3C\x00"), (384, b"\xB0\x40\x00"), (384, b"\xB2\x43\x00"),
        ]),
        timing_policy=timing_policy,
    )

    assert result[0x51] == 0
    assert result[0x54:0x56] == b"\x04\x00"
    controllers = [raw for _tick, _order, raw in parse_eseq_bytes(result).events if raw[0] & 0xF0 == 0xB0]
    assert controllers == [b"\xB0\x40\x20", b"\xB2\x43\x30", b"\xB0\x40\x00", b"\xB2\x43\x00"]


def test_explicit_legacy_preservation_keeps_original_converter_flag_conventions():
    source = _midi([
        (0, b"\xF0\x07\x43\x10\x4C\x00\x00\x7E\xF7"),
        (0, b"\xB2\x40\x20"), (0, b"\x9F\x3C\x40"), (384, b"\x8F\x3C\x00"),
    ])

    result = convert_midi_bytes_to_eseq_bytes(source, timing_policy="mid2eseq", pedal_policy="preserve")

    assert result[0x45] == result[0x51] == 0
    assert result[0x54:0x56] == b"\x00\x00"
    assert is_legacy_eseq_bytes(result)


def test_archival_roundtrip_retains_opaque_source_flags():
    source = bytearray(_fil(b"\xF1\x00\x90\x3C\x40\xF4\x00\x03\x80\x3C\x00\xF2"))
    source[0x45] = 0x81
    source[0x51:0x57] = bytes.fromhex("80 22 41 A5 C3 01")
    exported = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="archival")

    result = convert_midi_bytes_to_eseq_bytes(exported)

    assert result[0x45] == 0x81
    assert result[0x51:0x57] == source[0x51:0x57]


@pytest.mark.parametrize("source_half_pedal_flag", (0, 0x80))
def test_explicit_archival_pedal_routing_sets_missing_flag_and_keeps_opaque_fields(source_half_pedal_flag):
    source = bytearray(_fil(
        b"\xF1\x00\xB0\x40\x20\x90\x3C\x40"
        b"\xF4\x00\x03\x80\x3C\x00\xB0\x40\x00\xF2"
    ))
    source[0x45] = 0x81
    source[0x51:0x57] = bytes((source_half_pedal_flag, 0x22, 0x41, 0xA5, 0xC3, 0x01))
    exported = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="archival")

    automatic = convert_midi_bytes_to_eseq_bytes(exported)
    routed = convert_midi_bytes_to_eseq_bytes(exported, pedal_policy="yamaha")

    assert automatic[0x45] == routed[0x45] == source[0x45]
    assert automatic[0x51:0x57] == source[0x51:0x57]
    assert routed[0x51] == (source_half_pedal_flag or 1)
    assert routed[0x52:0x57] == source[0x52:0x57]
    automatic_events = parse_eseq_bytes(automatic).events
    routed_events = parse_eseq_bytes(routed).events
    automatic_pedals = [(tick, raw) for tick, _order, raw in automatic_events if raw[0] & 0xF0 == 0xB0]
    routed_pedals = [(tick, raw) for tick, _order, raw in routed_events if raw[0] & 0xF0 == 0xB0]
    assert automatic_pedals == [(0, b"\xB0\x40\x20"), (384, b"\xB0\x40\x00")]
    assert routed_pedals == [(0, b"\xB2\x40\x20"), (384, b"\xB2\x40\x00")]


def test_pianodir_records_the_generated_song_playback_flags(tmp_path):
    source = _midi([
        (0, b"\xF0\x07\x43\x10\x4C\x00\x00\x7E\xF7"),
        (0, b"\xB2\x40\x20"), (0, b"\x9F\x3C\x40"), (384, b"\x8F\x3C\x00"),
    ])
    song = convert_midi_bytes_to_eseq_bytes(source, filename_hint="PIANO001.FIL")
    path = tmp_path / "PIANO001.FIL"
    path.write_bytes(song)

    catalog = build_pianodir_bytes([PianodirTrackEntry(path.name, str(path), "Header fixture")])
    first_record = catalog[0x10:0x60]

    assert len(catalog) == 6144
    assert first_record == song[0x27:0x77]
    assert first_record[0x45 - 0x27] == 1
    assert first_record[0x51 - 0x27] == 1
    assert first_record[0x54 - 0x27:0x56 - 0x27] == b"\x00\x80"


def test_header_only_meter_is_exported_at_tick_zero():
    source = _fil(b"\xF1\x00\x90\x3C\x40\xF2", meter=(3, 8))

    assert _midi_meters(convert_eseq_bytes_to_midi_bytes(source)) == [(0, 3, 3)]


def test_later_meter_change_does_not_replace_the_initial_header_meter():
    source = _fil(b"\xF1\x00\xF4\x00\x03\xF9\x06\x03\x90\x3C\x40\xF2", meter=(3, 4))

    assert _midi_meters(convert_eseq_bytes_to_midi_bytes(source)) == [(0, 3, 2), (384, 6, 3)]


def test_tick_zero_meter_command_overrides_header_meter():
    source = _fil(b"\xF1\x00\xF9\x06\x03\x90\x3C\x40\xF2", meter=(3, 4))

    assert _midi_meters(convert_eseq_bytes_to_midi_bytes(source)) == [(0, 6, 3)]


@pytest.mark.parametrize("meter", ((0, 4), (3, 0), (3, 3)))
def test_invalid_header_meter_keeps_standard_fallback(meter):
    source = _fil(b"\xF1\x00\x90\x3C\x40\xF2", meter=meter)

    meters = _midi_meters(convert_eseq_bytes_to_midi_bytes(source))

    # Omitting a meter event also means standard MIDI's default 4/4.
    assert (meters or [(0, 4, 2)]) == [(0, 4, 2)]
