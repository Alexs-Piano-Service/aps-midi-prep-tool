"""Legacy block estimates must neither truncate songs nor hide damaged tails."""

import pytest

from aps_midi_prep_tool_app.eseq_channel_merger import merge_eseq_channels_to_channel0_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    _declared_stream_end,
    _explorer_visible_delay_ticks,
    convert_eseq_bytes_to_midi_bytes,
    convert_eseq_file_to_midi_path,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_inspection import inspect_eseq_header
from test_eseq_inspection import _fixture


PRELUDE = bytes.fromhex("F1 00 95 3C 60 F3 30 85 3C 40")
TAIL = bytes.fromhex("95 48 60 F3 30 85 48 40 F4 20 03 F2")
OPERATIONS = (
    parse_eseq_bytes, inspect_eseq_header, convert_eseq_bytes_to_midi_bytes,
    merge_eseq_channels_to_channel0_bytes,
)


def _legacy(stream, *, block_length=2048, adjustment=0x100, suffix=b""):
    source = _fixture(stream)
    source[3:7] = min(0x77 + block_length - adjustment, 0xFFFF).to_bytes(4, "little")
    source[0x1F:0x23] = block_length.to_bytes(4, "little")
    return bytes(source) + suffix


@pytest.mark.parametrize("block_length", [2048, 65536], ids=["block-count", "saturated-total"])
@pytest.mark.parametrize("adjustment", [0x100, 0x200])
@pytest.mark.parametrize("cut", [0, 1, 2], ids=["between-events", "after-status", "inside-payload"])
@pytest.mark.parametrize("suffix", [b"", b"\0" * 100 + b"\xF6" * 100], ids=["compact", "padded"])
def test_legacy_lengths_preserve_complete_events_and_timing(block_length, adjustment, cut, suffix):
    # Position the inaccurate boundary before/inside the final note. Previously
    # the first case silently succeeded while the other two reported truncation.
    stream = PRELUDE.ljust(block_length - cut, b"\xF6") + TAIL
    source = _legacy(stream, block_length=block_length, adjustment=adjustment, suffix=suffix)
    expected = bytes(_fixture(stream))
    parsed = parse_eseq_bytes(source)
    assert parsed == parse_eseq_bytes(expected)
    assert (48, 2, b"\x95\x48\x60") in parsed.events
    assert (96, 2, b"\x85\x48\x40") in parsed.events
    assert parsed.end_tick == 512
    assert inspect_eseq_header(source).end_tick == 512
    assert _declared_stream_end(source, 0x77) == len(expected)
    assert _explorer_visible_delay_ticks(source, "after") == 416
    assert convert_eseq_bytes_to_midi_bytes(source) == convert_eseq_bytes_to_midi_bytes(expected)


@pytest.mark.parametrize("adjustment", [0x100, 0x200])
def test_legacy_total_length_is_not_used_when_block_length_exceeds_file(adjustment):
    # FRIEND.FIL has a block estimate beyond EOF and a shorter total/control
    # word inside an event. Reject both estimates, not just the preferred one.
    stream = PRELUDE.ljust(2048 - adjustment - 1, b"\xF6") + TAIL
    source = _legacy(stream, adjustment=adjustment)
    assert len(source) < 0x77 + 2048
    assert parse_eseq_bytes(source) == parse_eseq_bytes(bytes(_fixture(stream)))
    assert _declared_stream_end(source, 0x77) == len(source)


@pytest.mark.parametrize("command", [
    bytes.fromhex(value) for value in (
        "F1 F2", "F3 F2", "F4 F2 00", "F4 00 F2", "F9 F2 02", "FB F2 07", "FF F2",
        "F0 43 F3 F2 01 F7", "F0 43 F4 F2 00 01 F7",
    )
])
def test_f2_operands_are_not_mistaken_for_end_markers(command):
    stream = PRELUDE.ljust(2048, b"\xF6") + command + TAIL
    source = _legacy(stream)
    assert _declared_stream_end(source, 0x77) == len(source)
    assert parse_eseq_bytes(source) == parse_eseq_bytes(bytes(_fixture(stream)))


@pytest.mark.parametrize("tail,match", [
    (b"\x95", "incomplete"),
    (b"\x95\x48", "incomplete"),
    (b"\xF4\x20", "incomplete"),
    (b"\x95\xF2\x40\xF2", "Invalid data byte"),
    (b"\xF0\x43\xF2\x01\xF7\xF2", "embedded E-SEQ SysEx status"),
    (b"\xF0\x43", "unterminated"),
    (b"\xF0\x43\xF4\x20", "incomplete delay"),
    (b"\xF5\xF2", "Unsupported E-SEQ opcode"),
    (b"\xF7\xF2", "standalone F7"),
    (b"\x95\x48\x60", "Missing end marker"),
    (b"\xF2\x95\x48\x60", "Unexpected trailing data"),
])
def test_legacy_recognition_never_hides_invalid_or_missing_tail(tail, match):
    # An ordinary completed phrase reaches the bogus length exactly: falling
    # back to it would turn this damage into a plausible successful conversion.
    source = _legacy(PRELUDE.ljust(2048, b"\xF6") + tail)
    for operation in OPERATIONS:
        with pytest.raises(EseqConversionError, match=match):
            operation(source)


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
def test_consistent_exact_block_length_still_excludes_a_musical_trailer(variant):
    stream = PRELUDE.ljust(2048, b"\xF6")
    source = bytes(_fixture(stream, variant=variant))
    assert parse_eseq_bytes(source + TAIL) == parse_eseq_bytes(source)
    assert convert_eseq_bytes_to_midi_bytes(source + TAIL) == convert_eseq_bytes_to_midi_bytes(source)


@pytest.mark.parametrize("variant", ["mda", "q11"])
def test_legacy_fil_length_exception_does_not_apply_to_other_containers(variant):
    source = _fixture(PRELUDE.ljust(2200, b"\xF6") + TAIL, variant=variant)
    if variant != "mda":  # MDA's first seven bytes are its container signature.
        source[3:7] = (0x77 + 2048 - 0x100).to_bytes(4, "little")
    source[0x1F:0x23] = (2048).to_bytes(4, "little")
    expected_end = 2048 if variant == "mda" else 0x200 + 2048
    start = 0x57 if variant == "mda" else 0x200
    assert _declared_stream_end(source, start) == expected_end
    assert parse_eseq_bytes(source).end_tick == 48


@pytest.mark.parametrize("existing", [False, True])
def test_damaged_legacy_tail_never_publishes_partial_midi(tmp_path, existing):
    source = tmp_path / "LEGACY.FIL"
    destination = tmp_path / "output.mid"
    source.write_bytes(_legacy(PRELUDE.ljust(2048, b"\xF6") + b"\x95\x48"))
    if existing:
        destination.write_bytes(b"keep existing output")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(EseqConversionError, match="incomplete"):
        convert_eseq_file_to_midi_path(source, destination)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
