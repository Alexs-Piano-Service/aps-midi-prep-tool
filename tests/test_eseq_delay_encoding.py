"""Delay encoding compatibility, independently decoded back to commanded ticks."""

import struct
from fractions import Fraction

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    _encode_eseq_delta,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq, _parse_track_events


def _read_stream(stream):
    """Read only the opcodes used by these fixtures, without APS E-SEQ parsing."""
    pos, tick = 0, 0
    delays, events = [], []
    while pos < len(stream):
        status = stream[pos]
        pos += 1
        if status == 0xF2:
            break
        if status == 0xF3:
            value = stream[pos]
            pos += 1
            delays.append((status, value))
            tick += value
        elif status == 0xF4:
            lo, hi = stream[pos:pos + 2]
            assert lo < 128 and hi < 128
            pos += 2
            value = lo | (hi << 7)
            delays.append((status, value))
            tick += value
        elif status in (0xF9, 0xFB):
            pos += 2
        elif status == 0xF1:
            pos += 1
        elif 0x80 <= status <= 0xEF:
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            events.append((tick, bytes([status]) + stream[pos:pos + size]))
            pos += size
        else:
            raise AssertionError(f"Unexpected fixture opcode {status:02X}")
    return tick, delays, events


@pytest.mark.parametrize("ticks,expected", (
    (0, b""), (1, b"\xF3\x01"), (127, b"\xF3\x7F"),
    (128, b"\xF4\x00\x01"), (255, b"\xF4\x7F\x01"), (256, b"\xF4\x00\x02"),
))
def test_short_delay_uses_only_seven_bit_operands(ticks, expected):
    encoded = _encode_eseq_delta(ticks)

    assert encoded == expected
    assert _read_stream(encoded)[0] == ticks


@pytest.mark.parametrize("ticks", (1, 127, 128, 255, 256, 0x3FFF + 128))
@pytest.mark.parametrize("prefer_long", (False, True))
def test_avoid_long_splits_delay_without_losing_ticks(ticks, prefer_long):
    encoded = _encode_eseq_delta(ticks, avoid_long=True, prefer_long=prefer_long)
    total, delays, _events = _read_stream(encoded)

    assert total == ticks
    assert all(status == 0xF3 and 1 <= value <= 127 for status, value in delays)
    assert len(delays) == (ticks + 126) // 127


@pytest.mark.parametrize("remainder", (1, 127, 128, 255, 256))
@pytest.mark.parametrize("prefer_long", (False, True))
def test_long_delay_split_keeps_remainder_and_short_operand_boundaries(remainder, prefer_long):
    ticks = 0x3FFF + remainder
    total, delays, _events = _read_stream(_encode_eseq_delta(ticks, prefer_long=prefer_long))

    assert total == ticks
    assert len(delays) == 2
    assert all(1 <= value <= (127 if status == 0xF3 else 0x3FFF) for status, value in delays)
    if prefer_long:
        assert all(status == 0xF4 for status, _value in delays)
        assert delays[-1][1] >= 128
    elif remainder <= 127:
        assert delays[-1] == (0xF3, remainder)
    else:
        assert delays[-1] == (0xF4, remainder)


def test_multiple_long_chunks_preserve_all_ticks():
    ticks = 2 * 0x3FFF + 255
    total, delays, _events = _read_stream(_encode_eseq_delta(ticks))

    assert total == ticks
    assert delays == [(0xF4, 0x3FFF), (0xF4, 0x3FFF), (0xF4, 255)]


@pytest.mark.parametrize("ticks", (1, 127, 128, 255, 256))
def test_prefer_long_keeps_explicit_long_delay_layout(ticks):
    total, delays, _events = _read_stream(_encode_eseq_delta(ticks, prefer_long=True))

    assert total == ticks
    assert delays == [(0xF4, ticks)]


@pytest.mark.parametrize("ticks", (128, 247, 255))
def test_existing_full_byte_short_delays_remain_readable(ticks):
    stream = b"\xF1\x00\xF3" + bytes([ticks]) + b"\x90\x3C\x40\xF3\x01\x80\x3C\x00\xF2"
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = header[0x33] = 117 - 29
    source = bytes(header) + stream

    parsed = parse_eseq_bytes(source)
    exported = convert_eseq_bytes_to_midi_bytes(source)
    midi_events, end_tick = _parse_track_events(exported[22:])
    expected = [(ticks, b"\x90\x3C\x40"), (ticks + 1, b"\x80\x3C\x00")]

    assert [(tick, raw) for tick, _order, raw in parsed.events] == expected
    assert [(tick, raw) for tick, _order, raw in midi_events if raw[0] < 0xF0] == expected
    assert parsed.end_tick == end_tick == ticks + 1


def test_song_conversion_changes_delay_encoding_without_changing_music_or_elapsed_time():
    expected, track = [], bytearray()
    tick = 0
    for delta, raw in (
        (127, b"\x90\x3C\x40"), (128, b"\xB0\x40\x7F"),
        (255, b"\x80\x3C\x00"), (256, b"\xB0\x40\x00"),
        (0x3FFF + 128, b"\x90\x40\x50"), (255, b"\x80\x40\x00"),
    ):
        tick += delta
        expected.append((tick, raw))
        track.extend(_encode_vlq(delta) + raw)
    track.extend(b"\x00\xFF\x2F\x00")
    midi = struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 384) + b"MTrk" + len(track).to_bytes(4, "big") + track

    converted = convert_midi_bytes_to_eseq_bytes(midi, timing_policy="preserve")
    total, delays, actual = _read_stream(converted[0x77:])

    assert actual == expected
    assert total == tick
    assert all(value <= 127 for status, value in delays if status == 0xF3)
    # Default MIDI 500,000 MPQN is 120 BPM. No tempo-model change is involved.
    assert converted[0x33] + 29 == 120
    assert Fraction(total * 60, 384 * 120) == Fraction(tick * 500000, 384 * 1000000)
