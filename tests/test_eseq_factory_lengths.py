"""Factory E-SEQ lengths preserve complete performances and ignore allocation slack."""

import pytest

from aps_midi_prep_tool_app.eseq_channel_merger import merge_eseq_channels_to_channel0_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError, _declared_stream_end, convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes, parse_eseq_bytes,
)
from test_eseq_inspection import _fixture
from test_eseq_legacy_lengths import PRELUDE, TAIL, _legacy
from aps_midi_prep_tool_app import floppy_image as image
from test_yamaha_root_reconstruction import _rebuild


def _placeholder(stream):
    source = _fixture(stream)
    source[3:7] = (0xFFFF).to_bytes(4, 'little')
    source[0x1F:0x23] = (0x50).to_bytes(4, 'little')
    source[0x43:0x47] = bytes.fromhex('00 77 01 00')
    return bytes(source)


def _q11_total(stream, *, total=71680):
    source = _fixture(stream, variant='q11')
    assert len(source) <= total
    source[0x17:0x1F] = bytes.fromhex('80 00 00 00 D9 01 00 00')
    source[3:7] = min(total, 0xFFFF).to_bytes(4, 'little')
    source[0x1F:0x23] = total.to_bytes(4, 'little')
    return bytes(source).ljust(total, b'\xF6')


@pytest.mark.parametrize('slack', [b'\xE5', b'\x95\x48\x60\xF3\x30\x85', b'\xFF\x00\xA1\xD9'])
def test_legacy_final_allocation_block_is_not_part_of_the_performance(slack):
    stream = PRELUDE.ljust(2200, b'\xF6') + TAIL
    source = _legacy(stream)
    padding = -len(source) % 1024
    source += (slack * (padding // len(slack) + 1))[:padding]
    expected = bytes(_fixture(stream))
    assert _declared_stream_end(source, 0x77) == len(expected)
    assert parse_eseq_bytes(source) == parse_eseq_bytes(expected)
    assert convert_eseq_bytes_to_midi_bytes(source) == convert_eseq_bytes_to_midi_bytes(expected)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert merged.endswith(source[len(expected):])
    assert parse_eseq_bytes(merged).end_tick == 512


@pytest.mark.parametrize('workflow', ['fat', 'carve'])
@pytest.mark.parametrize('header', ['block', 'placeholder'])
def test_factory_song_recovery_uses_validated_end_before_opaque_slack(workflow, header):
    stream = PRELUDE.ljust(1800 - 0x77 - len(TAIL), b'\xF6') + TAIL
    source = bytearray(_legacy(stream) if header == 'block' else _placeholder(stream))
    source[0x27:0x32] = b'SONG       '
    source = bytes(source).ljust(2048, b'\xE5')
    if header == 'block':
        assert int.from_bytes(source[3:7], 'little') == 1911
        assert parse_eseq_bytes(source).end_tick == 512
        with pytest.raises(EseqConversionError, match='Unexpected trailing data'):
            parse_eseq_bytes(source[:1911])
    expected = source[:1800]
    if workflow == 'fat':
        entry, recovered, allocated = _rebuild(source)
        assert allocated == 2048
        assert entry['size'] == 1800
    else:
        # A carve includes unrelated disk space; its length is not the FIL EOF.
        neighbor = bytes(_fixture(PRELUDE + TAIL))
        raw = bytes(8192) + source + b'\xE5' * 4096 + neighbor + bytes(3072)
        songs = image._carve_recovery_files_from_bytes(raw)
        assert len(songs) == 2
        recovered = songs[0].data
        assert songs[1].data == neighbor
    assert recovered == expected
    assert convert_eseq_bytes_to_midi_bytes(recovered) == convert_eseq_bytes_to_midi_bytes(expected)
    assert parse_eseq_bytes(recovered).end_tick == 512


@pytest.mark.parametrize('workflow', ['fat', 'carve'])
@pytest.mark.parametrize('tail', [b'\x95\xF2\x40\xF2', b'\xF0\x43\xF2\xF7', b'\xF5\xF2'])
def test_legacy_recovery_does_not_validate_an_end_inside_damaged_events(workflow, tail):
    source = bytearray(_legacy(PRELUDE + tail))
    source[0x27:0x32] = b'SONG       '
    source = bytes(source).ljust(2048, b'\xE5')
    recovered = _rebuild(source)[1] if workflow == 'fat' else image._carve_recovery_files_from_bytes(source)[0].data
    assert recovered == source
    with pytest.raises(EseqConversionError):
        parse_eseq_bytes(recovered)


@pytest.mark.parametrize('extra', [1, 1024])
def test_opaque_legacy_trailer_must_be_confined_to_a_rounded_final_block(extra):
    source = _legacy(PRELUDE.ljust(2200, b'\xF6') + TAIL)
    source += b'\xE5' * (-len(source) % 1024 + extra)
    with pytest.raises(EseqConversionError, match='Unexpected trailing data'):
        parse_eseq_bytes(source)


@pytest.mark.parametrize('cut', [0, 1, 2])
def test_saturated_64k_block_estimate_does_not_cut_off_late_notes(cut):
    stream = PRELUDE.ljust(65536 - cut, b'\xF6') + TAIL
    source = bytearray(_legacy(stream, block_length=65536))
    source[3:7] = (0xFFFF).to_bytes(4, 'little')
    assert parse_eseq_bytes(source) == parse_eseq_bytes(bytes(_fixture(stream)))
    assert convert_eseq_bytes_to_midi_bytes(source) == convert_eseq_bytes_to_midi_bytes(bytes(_fixture(stream)))


@pytest.mark.parametrize('cut', [0, 1, 2])
def test_placeholder_length_does_not_silently_export_only_the_first_80_bytes(cut):
    stream = PRELUDE.ljust(80 - cut, b'\xF6') + TAIL
    source = _placeholder(stream)
    expected = bytes(_fixture(stream))
    assert parse_eseq_bytes(source) == parse_eseq_bytes(expected)
    assert convert_eseq_bytes_to_midi_bytes(source) == convert_eseq_bytes_to_midi_bytes(expected)


def test_placeholder_exception_requires_its_identified_header_shape():
    stream = PRELUDE.ljust(80, b'\xF6') + TAIL
    source = bytearray(_placeholder(stream))
    source[0x45] = 0
    assert _declared_stream_end(source, 0x77) == 0x77 + 80
    assert parse_eseq_bytes(source).end_tick == 48


@pytest.mark.parametrize('total', [4096, 71680])
def test_q11_total_size_includes_its_header_and_never_uses_saturated_word_as_a_stop(total):
    stream = PRELUDE.ljust(total - 0x200 - 160, b'\xF6') + TAIL
    source = _q11_total(stream, total=total)
    expected = bytes(_fixture(stream, variant='q11'))
    assert _declared_stream_end(source, 0x200) == len(expected)
    assert parse_eseq_bytes(source) == parse_eseq_bytes(expected)
    assert convert_eseq_bytes_to_midi_bytes(source) == convert_eseq_bytes_to_midi_bytes(expected)
    # Bytes beyond the declared padded container remain opaque.
    assert parse_eseq_bytes(source + b'\x95\x01') == parse_eseq_bytes(expected)


def test_q11_total_size_exception_requires_the_identified_header_shape():
    source = bytearray(_q11_total(PRELUDE.ljust(66000, b'\xF6') + TAIL))
    source[0x1B] = 0x50
    assert _declared_stream_end(source, 0x200) == 0xFFFF
    assert parse_eseq_bytes(source).end_tick == 48


@pytest.mark.parametrize('make_source', [
    _placeholder,
    lambda stream: _q11_total(stream, total=4096),
    lambda stream: _legacy(stream.ljust(1024 - 0x77, b'\xF6')),
])
@pytest.mark.parametrize('tail,match', [
    (b'\x95', 'incomplete|Invalid data byte'),
    (b'\x95\x48', 'incomplete|Invalid data byte'),
    (b'\x95\xF2\x40\xF2', 'Invalid data byte'),
    (b'\xF0\x43', 'unterminated|embedded E-SEQ SysEx status'),
    (b'\xF5\xF2', 'Unsupported E-SEQ opcode'),
    (b'\x95\x48\x60', 'Missing end marker'),
])
def test_factory_length_recovery_requires_valid_commands_and_an_actual_stop(make_source, tail, match):
    with pytest.raises(EseqConversionError, match=match):
        parse_eseq_bytes(make_source(PRELUDE + tail))


def test_q11_total_size_rejects_physical_truncation_even_after_an_earlier_stop():
    source = _q11_total(PRELUDE + TAIL, total=4096)
    with pytest.raises(EseqConversionError, match='container is incomplete'):
        parse_eseq_bytes(source[:-1])


@pytest.mark.parametrize('make_source', [_placeholder, lambda stream: _q11_total(stream, total=4096)])
def test_length_changing_channel_merge_normalizes_factory_lengths_and_reopens(make_source):
    # Duplicate same-tick controllers collapse during the merge, changing length.
    stream = bytes.fromhex('F1 00 B5 07 64 B6 07 64 95 3C 60 F3 30 85 3C 40 F2')
    source = make_source(stream)
    original = parse_eseq_bytes(source)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    parsed = parse_eseq_bytes(merged)
    assert parsed.end_tick == original.end_tick
    assert [(tick, raw) for tick, _, raw in parsed.events if raw[0] & 0xF0 in (0x80, 0x90)] == [
        (0, b'\x90\x3C\x60'), (48, b'\x80\x3C\x40'),
    ]
    assert merge_eseq_channels_to_channel0_bytes(merged) == (merged, False)


@pytest.mark.parametrize('make_source', [_placeholder, _q11_total])
def test_factory_length_variants_keep_late_performance_through_archival_roundtrip(make_source):
    source = make_source(PRELUDE.ljust(66000 if make_source is _q11_total else 80, b'\xF6') + TAIL)
    parsed = parse_eseq_bytes(source)
    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy='archival')
    restored = parse_eseq_bytes(convert_midi_bytes_to_eseq_bytes(midi))
    assert restored.end_tick == parsed.end_tick
    assert [(tick, raw) for tick, _, raw in restored.events if raw[0] < 0xF0] == [
        (tick, raw) for tick, _, raw in parsed.events if raw[0] < 0xF0]
