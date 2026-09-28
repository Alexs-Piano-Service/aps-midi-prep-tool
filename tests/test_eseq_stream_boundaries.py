"""Declared streams exclude opaque trailers and malformed channel payloads."""

import pytest

from aps_midi_prep_tool_app.eseq_channel_merger import _stream_tokens, merge_eseq_channels_to_channel0_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    _detect_eseq_container,
    _eseq_event_stream_start,
    convert_eseq_bytes_to_midi_bytes,
    convert_eseq_file_to_midi_path,
    parse_eseq_bytes,
)
from test_eseq_inspection import _fixture


PRELUDE = bytes.fromhex("F1 00 95 3C 60 F3 30 85 3C 40")
OPERATIONS = (parse_eseq_bytes, convert_eseq_bytes_to_midi_bytes, merge_eseq_channels_to_channel0_bytes)


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("ending", [b"", b"\xF2"])
@pytest.mark.parametrize("suffix", [
    bytes.fromhex("95 48 60 F3 30 85 48 40"),
    bytes.fromhex("F5 F7 FD FE FF"),
    bytes.fromhex("00 F6 95 48 60 B7 07 00 C3 28 F5 FF"),
])
def test_binary_trailer_is_ignored_by_conversion_and_preserved_by_merge(variant, ending, suffix):
    source = bytes(_fixture(PRELUDE + ending, variant=variant))
    assert parse_eseq_bytes(source + suffix) == parse_eseq_bytes(source)
    assert convert_eseq_bytes_to_midi_bytes(source + suffix) == convert_eseq_bytes_to_midi_bytes(source)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert merge_eseq_channels_to_channel0_bytes(source + suffix) == (merged + suffix, True)
    assert merge_eseq_channels_to_channel0_bytes(merged + suffix) == (merged + suffix, False)


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("invalid_length", [0, 0xFFFFFFFF])
def test_without_trustworthy_length_parsers_continue_to_physical_eof(variant, invalid_length):
    source = bytes(_fixture(PRELUDE, variant=variant))
    unknown_length = bytearray(source)
    unknown_length[0x1F:0x23] = invalid_length.to_bytes(4, "little")
    if variant != "mda":
        unknown_length[3:7] = invalid_length.to_bytes(4, "little")
    assert parse_eseq_bytes(unknown_length) == parse_eseq_bytes(source)
    assert convert_eseq_bytes_to_midi_bytes(unknown_length) == convert_eseq_bytes_to_midi_bytes(source)
    container = _detect_eseq_container(source)
    start = _eseq_event_stream_start(source, container_variant=container)
    assert _stream_tokens(bytes(unknown_length), start, container) == _stream_tokens(source, start, container)
    for operation in OPERATIONS:
        with pytest.raises(EseqConversionError, match="Unsupported E-SEQ opcode 0xF5"):
            operation(bytes(unknown_length) + b"\xF5")


@pytest.mark.parametrize("variant", ["fil", "q11"])
def test_used_length_fallback_also_excludes_binary_trailer(variant):
    source = _fixture(PRELUDE, variant=variant)
    source[0x1F:0x23] = bytes(4)
    source = bytes(source)
    assert parse_eseq_bytes(source + b"\xF5") == parse_eseq_bytes(source)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert merge_eseq_channels_to_channel0_bytes(source + b"\xF5") == (merged + b"\xF5", True)


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("command", [
    bytes.fromhex(value) for value in (
        "F1 00", "F3 06", "F4 06 01", "FB 68 07", "F9 03 02", "FF 05",
        "85 3C 40", "95 3C 60", "A5 3C 40", "B5 40 7F", "E5 00 40", "C5 00", "D5 40",
        "F0 43 F7", "F0 43 F3 06 F7", "F0 43 F4 06 01 F7",
    )
])
def test_command_payload_cannot_consume_bytes_beyond_declared_boundary(variant, command):
    for size in range(1, len(command)):
        # Physical bytes can complete the command, but are outside the stream.
        source = bytes(_fixture(PRELUDE + command[:size], variant=variant)) + command[size:]
        for operation in OPERATIONS:
            with pytest.raises(EseqConversionError, match="incomplete|unterminated"):
                operation(source)


INVALID_CHANNEL_MESSAGES = [
    bytes([status]) + bytes(high_bit if index == damaged_index else 0x3C for index in range(size))
    for status, size in ((0x85, 2), (0x95, 2), (0xA5, 2), (0xB5, 2), (0xC5, 1), (0xD5, 1), (0xE5, 2))
    for damaged_index in range(size)
    for high_bit in (0x80, 0x90, 0xFF)
]


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("command", INVALID_CHANNEL_MESSAGES, ids=lambda command: command.hex())
def test_channel_data_bytes_must_be_seven_bit(variant, command):
    source = bytes(_fixture(PRELUDE + command + b"\xF2", variant=variant))
    for operation in OPERATIONS:
        with pytest.raises(EseqConversionError, match="Invalid data byte in an E-SEQ channel event"):
            operation(source)


@pytest.mark.parametrize("existing_destination", [False, True])
@pytest.mark.parametrize("command", INVALID_CHANNEL_MESSAGES, ids=lambda command: command.hex())
def test_invalid_channel_conversion_leaves_destination_untouched(tmp_path, existing_destination, command):
    source = tmp_path / "DAMAGED.FIL"
    destination = tmp_path / "OUTPUT.MID"
    source.write_bytes(_fixture(PRELUDE + command + b"\xF2"))
    if existing_destination:
        destination.write_bytes(b"existing destination")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(EseqConversionError, match="Invalid data byte in an E-SEQ channel event"):
        convert_eseq_file_to_midi_path(source, destination)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
