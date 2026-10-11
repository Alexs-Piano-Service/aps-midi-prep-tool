"""SMF tracks accept channel messages, length-prefixed SysEx, and meta events."""

import struct

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    convert_midi_bytes_to_eseq_bytes,
    convert_midi_file_to_eseq_path,
)
from aps_midi_prep_tool_app.midi_channel_merger import (
    merge_midi_channels_to_channel0_bytes,
    merge_midi_channels_to_channel0_path,
)
from aps_midi_prep_tool_app.midi_type0_converter import (
    _convert_midi_bytes_to_type0,
    _parse_track_events,
    convert_midi_file_to_type0_path,
)


CHANNEL_MESSAGES = [(status | 3, 1 if status in (0xC0, 0xD0) else 2)
                    for status in range(0x80, 0xF0, 0x10)]
INVALID_SMF_STATUSES = [status for status in range(0xF1, 0xFF) if status != 0xF7]
END_OF_TRACK = b"\x00\xff\x2f\x00"
INVALID_EVENTS = [
    pytest.param(b"\x00\x93\x3c\x80", id="explicit-channel"),
    pytest.param(b"\x00\x93\x3c\x40\x00\x3e\xff", id="running-status"),
]
META_EVENTS = [
    pytest.param(b"\xff\x01\x01X", id="text"),
    pytest.param(b"\xff\x03\x01X", id="track-name"),
    pytest.param(b"\xff\x51\x03\x07\xa1\x20", id="tempo"),
    pytest.param(b"\xff\x7f\x00", id="empty-sequencer-specific"),
    pytest.param(b"\xff\x70\x01X", id="unknown"),
]


def _midi(events):
    track = events + END_OF_TRACK
    return (struct.pack(">4sIHHH", b"MThd", 6, 1, 1, 480)
            + b"MTrk" + len(track).to_bytes(4, "big") + track)


@pytest.mark.parametrize("status,data_length,operand", [
    (status, data_length, operand)
    for status, data_length in CHANNEL_MESSAGES
    for operand in range(data_length)
])
@pytest.mark.parametrize("invalid", [0x80, 0xFF])
def test_every_channel_operand_rejects_status_bytes(status, data_length, operand, invalid):
    data = bytearray([60] * data_length)
    data[operand] = invalid

    with pytest.raises(ValueError, match="Invalid data byte in a MIDI channel event"):
        _parse_track_events(b"\x00" + bytes([status]) + data + END_OF_TRACK)


@pytest.mark.parametrize("status", [status for status, size in CHANNEL_MESSAGES if size == 2])
@pytest.mark.parametrize("invalid", [0x80, 0xFF])
def test_running_status_rejects_status_bytes_in_the_second_operand(status, invalid):
    track = b"\x00" + bytes([status, 60, 64]) + bytes([1, 62, invalid]) + END_OF_TRACK

    with pytest.raises(ValueError, match="Invalid data byte in a MIDI channel event"):
        _parse_track_events(track)


@pytest.mark.parametrize("status,data_length", CHANNEL_MESSAGES)
@pytest.mark.parametrize("value", [0, 0x7F])
def test_channel_operand_limits_work_with_explicit_and_running_status(status, data_length, value):
    raw = bytes([status] + [value] * data_length)
    track = b"\x00" + raw + b"\x02" + raw[1:] + END_OF_TRACK

    events, end_tick = _parse_track_events(track)

    assert [(tick, event) for tick, _order, event in events] == [(0, raw), (2, raw)]
    assert end_tick == 2


def _wire_message(status):
    return bytes([status]) + bytes({0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0))


@pytest.mark.parametrize("status", INVALID_SMF_STATUSES)
def test_direct_system_statuses_are_not_smf_track_events(status):
    with pytest.raises(ValueError, match=f"Unsupported system status byte: 0x{status:02X}"):
        _parse_track_events(b"\x00" + _wire_message(status) + END_OF_TRACK)


@pytest.mark.parametrize("status", INVALID_SMF_STATUSES)
@pytest.mark.parametrize("convert", [
    _convert_midi_bytes_to_type0,
    convert_midi_bytes_to_eseq_bytes,
    lambda source: convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve"),
    merge_midi_channels_to_channel0_bytes,
])
def test_converters_reject_direct_system_statuses_before_transformation(status, convert):
    # In particular, F2 must not become an E-SEQ terminator that silently
    # drops the second note while returning a successful conversion.
    before = b"\x00\x93\x3c\x40\x60\x83\x3c\x00"
    after = b"\x00\x93\x40\x40\x60\x83\x40\x00"
    source = _midi(before + b"\x00" + _wire_message(status) + after)
    with pytest.raises(ValueError, match=f"Unsupported system status byte: 0x{status:02X}"):
        convert(source)


@pytest.mark.parametrize("prefix", [b"\xff\x01", b"\xf0", b"\xf7"])
def test_meta_and_sysex_payloads_keep_high_bit_bytes_opaque(prefix):
    payload = b"\x80\x90\xf0\xf7\xff" + bytes(INVALID_SMF_STATUSES)
    raw = prefix + bytes([len(payload)]) + payload

    events, end_tick = _parse_track_events(b"\x00" + raw + END_OF_TRACK)

    assert events == [(0, 0, raw)]
    assert end_tick == 0
    for convert in (_convert_midi_bytes_to_type0, merge_midi_channels_to_channel0_bytes):
        converted, _changed = convert(_midi(b"\x00" + raw))
        assert _parse_track_events(converted[22:])[0] == [(0, 0, raw)]


@pytest.mark.parametrize("existing", [False, True], ids=["new-output", "existing-output"])
@pytest.mark.parametrize("convert", [
    convert_midi_file_to_type0_path,
    lambda source, dest: convert_midi_file_to_eseq_path(source, dest, timing_policy="preserve"),
    merge_midi_channels_to_channel0_path,
])
def test_direct_f2_cannot_publish_or_overwrite_a_conversion(tmp_path, existing, convert):
    source = tmp_path / "source.mid"
    original = _midi(b"\x00\xf2\x00\x00\x00\x93\x3c\x40\x60\x83\x3c\x00")
    source.write_bytes(original)
    destination = tmp_path / "converted.song"
    if existing:
        destination.write_bytes(b"existing destination")

    with pytest.raises(ValueError, match="Unsupported system status byte: 0xF2"):
        convert(source, destination)

    assert source.read_bytes() == original
    assert destination.exists() is existing
    if existing:
        assert destination.read_bytes() == b"existing destination"
    assert set(tmp_path.iterdir()) == ({source, destination} if existing else {source})


@pytest.mark.parametrize("events", INVALID_EVENTS)
@pytest.mark.parametrize("convert", [
    _convert_midi_bytes_to_type0,
    convert_midi_bytes_to_eseq_bytes,
    merge_midi_channels_to_channel0_bytes,
])
def test_converters_reject_invalid_operands_before_transforming(events, convert):
    with pytest.raises(ValueError, match="Invalid data byte in a MIDI"):
        convert(_midi(events))


@pytest.mark.parametrize("events", INVALID_EVENTS)
@pytest.mark.parametrize("existing", [False, True], ids=["new-output", "existing-output"])
def test_invalid_type0_conversion_preserves_source_and_destination(tmp_path, events, existing):
    source = tmp_path / "source.mid"
    original = _midi(events)
    source.write_bytes(original)
    destination = tmp_path / "converted.mid"
    if existing:
        destination.write_bytes(b"existing destination")

    with pytest.raises(ValueError, match="Invalid data byte in a MIDI"):
        convert_midi_file_to_type0_path(source, destination)

    assert source.read_bytes() == original
    assert destination.exists() is existing
    if existing:
        assert destination.read_bytes() == b"existing destination"
    assert {path.name for path in tmp_path.iterdir()} == (
        {"source.mid", "converted.mid"} if existing else {"source.mid"}
    )


@pytest.mark.parametrize("meta", META_EVENTS)
def test_meta_events_cancel_running_status(meta):
    track = b"\x00\x90\x3c\x40\x00" + meta + b"\x00\x3e\x40" + END_OF_TRACK

    with pytest.raises(ValueError, match="Invalid running status in track data"):
        _parse_track_events(track)


@pytest.mark.parametrize("meta", META_EVENTS)
def test_explicit_channel_status_after_meta_reestablishes_running_status(meta):
    track = (b"\x00\x90\x3c\x40\x01" + meta
             + b"\x02\x91\x3e\x40\x03\x40\x40" + END_OF_TRACK)

    events, end_tick = _parse_track_events(track)

    assert events == [
        (0, 0, b"\x90\x3c\x40"), (1, 1, meta),
        (3, 2, b"\x91\x3e\x40"), (6, 3, b"\x91\x40\x40"),
    ]
    assert end_tick == 6


@pytest.mark.parametrize("events,error", [
    pytest.param(
        b"\x00\xff\x2f\x01\x00\x00\x90\x3c\x40\x60\x80\x3c\x00",
        "End of Track meta event must have zero length",
        id="nonzero-end-of-track-length",
    ),
    pytest.param(
        b"\x00\x90\x3c\x40\x00\xff\x01\x01X\x00\x3e\x40\x60\x80\x3e\x00",
        "Invalid running status in track data",
        id="running-status-after-meta",
    ),
])
@pytest.mark.parametrize("destination_kind", ["new", "existing", "in_place"])
@pytest.mark.parametrize("convert", [
    pytest.param(convert_midi_file_to_type0_path, id="type0"),
    pytest.param(convert_midi_file_to_eseq_path, id="eseq-default"),
    pytest.param(
        lambda source, dest: convert_midi_file_to_eseq_path(source, dest, timing_policy="preserve"),
        id="eseq-preserve",
    ),
    pytest.param(merge_midi_channels_to_channel0_path, id="channel-merge"),
])
def test_invalid_meta_events_cannot_publish_a_conversion(
    tmp_path, events, error, destination_kind, convert,
):
    source = tmp_path / "source.mid"
    original = _midi(events)
    source.write_bytes(original)
    destination = source if destination_kind == "in_place" else tmp_path / "converted.song"
    if destination_kind == "existing":
        destination.write_bytes(b"existing destination")

    with pytest.raises(ValueError, match=error):
        convert(source, destination)

    assert source.read_bytes() == original
    if destination_kind == "new":
        assert not destination.exists()
    elif destination_kind == "existing":
        assert destination.read_bytes() == b"existing destination"
    assert set(tmp_path.iterdir()) == (
        {source, destination} if destination_kind == "existing" else {source}
    )
