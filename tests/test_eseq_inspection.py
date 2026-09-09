"""Header inspection distinguishes source bytes from decoded performance."""

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    ParsedEseqFile,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_inspection import inspect_eseq_header


def _word14(value):
    return bytes((value & 127, value >> 7))


def _fixture(stream=b"\xF1\x00\xF2", *, variant="fil", tempo24=88, tempo33=88):
    size = {"fil": 0x77, "q11": 0x200, "mda": 0x57}[variant]
    data = bytearray(size)
    data[0] = 0xFE
    data[3:7] = (size + len(stream)).to_bytes(4, "little")
    data[7:15] = b"COM-ESEQ"
    data[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    data[0x1F:0x23] = len(stream).to_bytes(4, "little")
    data[0x24] = tempo24
    data[0x33] = tempo33
    data[0x34:0x36] = b"\x03\x08"
    if variant != "mda":
        data[0x57:0x77] = b"Header inspection".ljust(32, b" ")
    if variant == "q11":
        data[0x0F:0x17] = b"Q11V1.00"
        data[0x19] = 0x21
    elif variant == "mda":
        data[:7] = bytes.fromhex("FE 00 00 FF FF 00 00")
        data[0x17:0x1F] = bytes.fromhex("80 00 21 00 30 00 00 00")
        data[0x1F:0x23] = (size + len(stream)).to_bytes(4, "little")
    return data + stream


def _fields(info):
    return {field.key: field for field in info.fields}


def test_normal_fil_reports_proven_fields_without_rewriting_opaque_values():
    stream = b"\xF1\x00\x90\x3C\x40\xB2\x40\x17\xBF\x42\x7F\xF3\x06\x9F\x30\x00\xF2"
    data = _fixture(stream)
    data[0x37:0x3B] = (123456).to_bytes(4, "little")
    data[0x3B:0x43] = bytes.fromhex("1C 0D FF E1 0C 00 04 37")
    data[0x45] = 1
    data[0x4D] = 1
    data[0x4F] = 0x81
    data[0x50] = 0x82
    data[0x51] = 0x80
    data[0x53] = 0x41
    data[0x54:0x56] = b"\x05\x80"
    data[0x56] = 0x80
    original = bytes(data)

    info = inspect_eseq_header(data)
    fields = _fields(info)

    assert bytes(data) == original
    assert (info.variant, info.stream_offset, info.resolution) == ("fil", 0x77, 384)
    assert info.title == "Header inspection"
    assert info.normal_program_header
    assert info.header_meter == (3, 8)
    assert fields["write_protect"].value is True
    assert fields["write_protect"].raw == b"\x81"
    assert fields["arrangement"].value == 2
    assert fields["xg_marker"].value is fields["ensemble_parts"].value is True
    assert fields["piano_parts"].value is None
    assert fields["detailed_pedal"].value is None
    assert fields["detailed_pedal"].raw == b"\x80"
    assert fields["detailed_pedal"].interpretation == "raw"
    assert fields["note_channel_mask"].value == (1, 3, 16)
    assert fields["display_mode"].value == "measure"
    assert fields["duration_ticks"].value == 123456
    assert info.end_tick == 6
    assert fields["timing_bookkeeping"].raw == bytes.fromhex("1C 0D FF E1 0C 00")
    assert fields["timing_bookkeeping"].value is None
    assert fields["tail_position"].raw == bytes.fromhex("04 37")
    assert fields["tail_position"].value is None
    assert fields["raw_53"].raw == b"\x41"
    assert fields["raw_53"].value is None
    # A header's claims and actual stream contents remain separate.
    assert info.actual_note_channels == (1, 16)
    assert info.actual_pedal_lanes == ((3, 64), (16, 66))
    assert not info.actual_xg


@pytest.mark.parametrize("raw,expected", [(0, False), (1, True), (2, None), (0x80, None), (0xFF, None)])
def test_converter_boolean_fields_do_not_guess_at_historical_flag_values(raw, expected):
    data = _fixture()
    for offset in (0x45, 0x4D, 0x50, 0x51):
        data[offset] = raw

    fields = _fields(inspect_eseq_header(data))

    for name in ("xg_marker", "ensemble_parts", "piano_parts", "detailed_pedal"):
        assert fields[name].value is expected
        assert fields[name].raw == bytes((raw,))
        assert fields[name].interpretation == ("raw" if expected is None else "boolean")


@pytest.mark.parametrize("variant,offset", [("q11", 0x200), ("mda", 0x57)])
def test_other_variants_do_not_inherit_normal_fil_status_or_meter_layout(variant, offset):
    data = _fixture(b"\xF1\x00\xF9\x05\x02\x90\x3C\x40\xF2", variant=variant, tempo24=31, tempo33=88)
    data[0x45:0x57] = b"\xFF" * 18

    info = inspect_eseq_header(data)

    assert (info.variant, info.stream_offset) == (variant, offset)
    assert not info.normal_program_header
    assert info.fields == ()
    assert info.header_meter is None
    assert info.tempo_offset == 0x24
    assert info.tempo_raw == 31
    assert info.tempo_mirror_raw is None
    assert info.base_bpm == 60
    assert info.effective_initial_mpqn == 1000000
    assert info.actual_note_channels == (1,)


def test_zero_selected_header_overrides_mirror_and_last_tick_zero_factor_wins():
    stream = b"\xF1\x00\xFB" + _word14(333) + b"\xFB" + _word14(777)
    stream += b"\xF3\x60\xFB" + _word14(2000) + b"\xF3\x60\xFB" + _word14(1000) + b"\xF2"
    data = _fixture(stream, tempo24=31, tempo33=0)

    info = inspect_eseq_header(data)

    assert info.tempo_offset == 0x33
    assert info.tempo_raw == 0
    assert info.tempo_mirror_raw == 31
    assert (info.base_bpm, info.base_mpqn) == (117, 512820)
    assert info.effective_initial_mpqn == 660000
    assert info.tempo_factors == ((0, 333), (0, 777), (96, 2000), (192, 1000))
    assert info.tempo_changes == ((0, 1540000), (0, 660000), (96, 256410), (192, 512820))


def test_future_fb_does_not_change_reported_startup_tempo():
    data = _fixture(b"\xF1\x00\xF3\x60\xFB" + _word14(2000) + b"\xF2")

    info = inspect_eseq_header(data)

    assert info.effective_initial_mpqn == info.base_mpqn == 512820
    assert info.tempo_factors == ((96, 2000),)
    assert info.tempo_changes == ((96, 256410),)


def test_exact_raw_zero_factor_is_retained_without_changing_tolerant_parser_arithmetic():
    data = _fixture(b"\xF1\x00\xFB\x00\x00\xF2")
    parsed = parse_eseq_bytes(data)
    info = inspect_eseq_header(data)

    assert parsed.tempo_factors == [(0, 0)]
    assert parsed.tempo_events == [(0, 512820), (0, 512820000)]
    assert info.tempo_factors == ((0, 0),)
    assert info.tempo_changes == ((0, 512820000),)
    assert info.effective_initial_mpqn == 512820000


def test_sysex_packet_delays_and_actual_xg_share_the_existing_stream_parser():
    stream = b"\xF1\x00\xF0\x43\x1F\x4C\x00\x00\x7E\xF4\x03\x00\xF7"
    stream += b"\xFB" + _word14(333) + b"\x90\x3C\x40\xF2"
    data = _fixture(stream)

    info = inspect_eseq_header(data)

    assert info.actual_xg
    assert _fields(info)["xg_marker"].value is False
    assert info.actual_note_channels == (1,)
    assert info.tempo_factors == ((3, 333),)
    assert info.tempo_changes == ((3, 1540000),)
    assert info.effective_initial_mpqn == 512820
    assert info.end_tick == 3


def test_header_and_bytes_after_end_are_not_misread_as_tempo_commands():
    data = _fixture(b"\xF1\x00\xF2\xFB\x50\x0F")
    data[0x45] = 0xFB

    info = inspect_eseq_header(data)

    assert info.tempo_factors == info.tempo_changes == ()
    assert info.end_tick == 0


def test_unknown_program_length_keeps_supported_preview_but_omits_status_interpretation():
    data = _fixture(b"\xF1\x00\x90\x3C\x40\xF2")
    data[0x1B:0x1F] = (0x60).to_bytes(4, "little")

    info = inspect_eseq_header(data)

    assert parse_eseq_bytes(data).events == [(0, 2, b"\x90\x3C\x40")]
    assert not info.normal_program_header
    assert info.fields == ()
    assert info.header_meter is None
    assert info.actual_note_channels == (1,)


@pytest.mark.parametrize("numerator,denominator", [(0, 4), (3, 0), (3, 3)])
def test_invalid_header_meter_remains_uninterpreted(numerator, denominator):
    data = _fixture()
    data[0x34:0x36] = bytes((numerator, denominator))
    assert inspect_eseq_header(data).header_meter is None


@pytest.mark.parametrize("damage", ["short", "signature", "marker", "q11_short", "unknown_type"])
def test_malformed_or_unestablished_headers_fail_cleanly(damage):
    data = _fixture()
    if damage == "short":
        data = data[:60]
    elif damage == "signature":
        data[7] = 0
    elif damage == "marker":
        data[0] = 0
    elif damage == "q11_short":
        data[0x0F:0x17] = b"Q11V1.00"
    elif damage == "unknown_type":
        data[0x19] = 0x21

    with pytest.raises(EseqConversionError):
        inspect_eseq_header(data)


def test_parsed_file_default_tempo_factor_lists_are_independent():
    first = ParsedEseqFile([], [], [], 117, "", 0)
    second = ParsedEseqFile([], [], [], 117, "", 0)
    first.tempo_factors.append((0, 1000))
    assert second.tempo_factors == []
