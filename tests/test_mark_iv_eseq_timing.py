"""Timing expectations recovered independently from Yamaha's Mark IV seq.

songEseqSetInfo uses 384 PPQN, a zero-byte default of 512820 us/quarter,
and FIL[0x33] rather than non-FIL[0x24]. SetRelTempo/songTimeRtempo
divide the integer initial tempo by the FB factor with 1000 as neutral.
These fixtures and readers do not use APS templates or event parsers.
"""

from fractions import Fraction

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
)


def _word14(value):
    return bytes((value & 0x7F, value >> 7))


def _fixture(kind, stream, *, tempo24=31, tempo33=0):
    size = {"FIL": 0x77, "Q11": 0x200, "MDA": 0x57}[kind]
    header = bytearray(size)
    header[0] = 0xFE
    header[3:7] = (size + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = tempo24
    header[0x27:0x32] = b"MARKIV  FIL"
    header[0x33] = tempo33
    header[0x34:0x36] = b"\x04\x04"
    if kind != "MDA":
        header[0x57:0x77] = b"Mark IV timing fixture".ljust(32, b" ")
    if kind == "Q11":
        header[0x0F:0x17] = b"Q11V1.00"
        header[0x19] = 0x21
        header[0x77:0x200] = bytes(index % 128 for index in range(0x189))
    elif kind == "MDA":
        header[:7] = bytes.fromhex("FE 00 00 FF FF 00 00")
        header[0x17:0x1F] = bytes.fromhex("80 00 21 00 30 00 00 00")
        header[0x1F:0x23] = (size + len(stream)).to_bytes(4, "little")
    return bytes(header) + stream


def _read_midi(data):
    assert data[:4] == b"MThd"
    assert data[8:12] == b"\x00\x00\x00\x01"
    division = int.from_bytes(data[12:14], "big")
    pos = 8 + int.from_bytes(data[4:8], "big")
    assert data[pos:pos + 4] == b"MTrk"
    end = pos + 8 + int.from_bytes(data[pos + 4:pos + 8], "big")
    pos += 8

    def vlq():
        nonlocal pos
        value = 0
        while True:
            byte = data[pos]
            pos += 1
            value = (value << 7) | (byte & 0x7F)
            if byte < 0x80:
                return value

    tick, mpqn, running = 0, 500_000, None
    seconds, tempos, events = Fraction(), [], []
    while pos < end:
        delta = vlq()
        tick += delta
        seconds += Fraction(delta * mpqn, division * 1_000_000)
        status = data[pos]
        if status >= 0x80:
            pos += 1
        else:
            assert running is not None
            status = running
        if status == 0xFF:
            meta = data[pos]
            pos += 1
            size = vlq()
            payload = data[pos:pos + size]
            pos += size
            if meta == 0x51:
                mpqn = int.from_bytes(payload, "big")
                tempos.append((tick, mpqn))
            elif meta == 0x2F:
                return division, tempos, events, tick, seconds
        else:
            assert 0x80 <= status < 0xF0
            running = status
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            events.append((tick, bytes((status,)) + data[pos:pos + size], seconds))
            pos += size
    raise AssertionError("Missing MIDI end of track")


def _read_eseq(data):
    if data[0x0F:0x17] == b"Q11V1.00":
        pos = 0x200
    elif data[0x17:0x1F] == bytes.fromhex("80 00 21 00 30 00 00 00"):
        pos = 0x57
    else:
        pos = 0x77
    tempo_byte = data[0x33 if data[0x19] == 0x40 else 0x24]
    initial_mpqn = 60_000_000 // (tempo_byte + 29) if tempo_byte else 512_820
    mpqn, tick, seconds = initial_mpqn, 0, Fraction()
    events, factors = [], []
    while pos < len(data):
        status = data[pos]
        pos += 1
        if status == 0xF2:
            return initial_mpqn, factors, events, tick, seconds
        if status == 0xF1:
            pos += 1
        elif status == 0xF9:
            pos += 2
        elif status in (0xF3, 0xF4):
            delta = data[pos]
            pos += 1
            if status == 0xF4:
                delta += data[pos] << 7
                pos += 1
            tick += delta
            seconds += Fraction(delta * mpqn, 384 * 1_000_000)
        elif status == 0xFB:
            factor = data[pos] + (data[pos + 1] << 7)
            pos += 2
            factors.append((tick, factor))
            mpqn = initial_mpqn * 1000 // factor
        else:
            assert 0x80 <= status < 0xF0
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            events.append((tick, bytes((status,)) + data[pos:pos + size], seconds))
            pos += size
    raise AssertionError("Missing E-SEQ end command")


@pytest.mark.parametrize("policy", ("clean", "archival"))
@pytest.mark.parametrize(
    "kind,tempo24,tempo33,expected_mpqn",
    [
        ("FIL", 31, 0, 512_820),  # Zero 0x33 overrides a nonzero 0x24.
        ("FIL", 0, 31, 1_000_000),
        ("FIL", 31, 88, 512_820),
        ("Q11", 0, 31, 512_820),
        ("Q11", 31, 88, 1_000_000),
        ("MDA", 0, 31, 512_820),
        ("MDA", 31, 88, 1_000_000),
    ],
)
def test_yamaha_header_tempo_selection(kind, tempo24, tempo33, expected_mpqn, policy):
    stream = b"\xF1\x00\x90\x3C\x40\xF4\x00\x03\x80\x3C\x00\xF2"
    source = _fixture(kind, stream, tempo24=tempo24, tempo33=tempo33)

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy=policy)
    division, tempos, events, end_tick, seconds = _read_midi(midi)

    assert division == 384
    assert tempos == [(0, expected_mpqn)]
    assert [(tick, raw) for tick, raw, _ in events] == [
        (0, b"\x90\x3C\x40"), (384, b"\x80\x3C\x00"),
    ]
    assert end_tick == 384
    assert seconds == Fraction(expected_mpqn, 1_000_000)


def _relative_tempo_fixture(kind, *, initial_factor=None, zero_header=True):
    stream = b"\xF1\x00"
    if initial_factor is not None:
        stream += b"\xFB" + _word14(initial_factor)
    stream += b"\xF3\x60\x90\x3C\x40"
    for factor in (333, 2000, 777, 1000):
        stream += b"\xF4\x00\x03\xFB" + _word14(factor)
    stream += b"\x80\x3C\x00\xF3\x60\xF2"
    selected_tempo = 0 if zero_header else 88
    return _fixture(
        kind, stream,
        tempo24=31 if kind == "FIL" else selected_tempo,
        tempo33=selected_tempo if kind == "FIL" else 31,
    )


@pytest.mark.parametrize("kind", ("FIL", "Q11", "MDA"))
@pytest.mark.parametrize("policy", ("clean", "archival"))
def test_relative_tempo_uses_integer_initial_mpqn_and_is_not_cumulative(kind, policy):
    source = _relative_tempo_fixture(kind, zero_header=False)
    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy=policy)
    _, tempos, events, end_tick, seconds = _read_midi(midi)

    # The rational-BPM formula gives 1540001 and 660001 for factors 333/777.
    # Yamaha first truncates the header tempo to 512820, then applies each FB.
    assert tempos == [
        (0, 512_820), (480, 1_540_000), (864, 256_410),
        (1248, 660_000), (1632, 512_820),
    ]
    assert (events, end_tick, seconds) == _read_eseq(source)[2:]


@pytest.mark.parametrize("kind", ("FIL", "Q11"))
@pytest.mark.parametrize("initial_factor", (None, 333))
def test_archival_roundtrip_preserves_zero_tempo_and_relative_factors(kind, initial_factor):
    source = _relative_tempo_fixture(kind, initial_factor=initial_factor)
    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="archival")

    restored = convert_midi_bytes_to_eseq_bytes(midi, filename_hint="MARKIV.FIL", timing_policy="preserve")

    offset = 0x33 if kind == "FIL" else 0x24
    assert restored[offset] == source[offset] == 0
    assert _read_eseq(restored) == _read_eseq(source)
    if kind == "Q11":
        assert restored[0x0F:0x17] == b"Q11V1.00"
        assert restored[0x77:0x200] == source[0x77:0x200]


@pytest.mark.parametrize("kind", ("FIL", "Q11", "MDA"))
def test_clean_preserved_timing_roundtrip_keeps_yamaha_playback(kind):
    source = _relative_tempo_fixture(kind)
    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="clean")

    restored = convert_midi_bytes_to_eseq_bytes(
        midi, filename_hint="MARKIV.FIL", timing_policy="preserve",
        container_variant="clavinova_mda" if kind == "MDA" else "disklavier",
    )

    assert _read_eseq(restored) == _read_eseq(source)


def test_29_bpm_midi_uses_nonzero_header_and_relative_tempo():
    tempo = (60_000_000 // 29).to_bytes(3, "big")
    track = b"\x00\xFF\x51\x03" + tempo + b"\x00\x90\x3C\x40\x83\x00\x80\x3C\x00\x00\xFF\x2F\x00"
    midi = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\x80MTrk" + len(track).to_bytes(4, "big") + track

    restored = convert_midi_bytes_to_eseq_bytes(midi, filename_hint="SLOW.FIL", timing_policy="preserve")
    _, factors, events, end_tick, seconds = _read_eseq(restored)

    assert restored[0x33] != 0  # Yamaha interprets zero as 117 BPM, never 29.
    assert factors and factors[0][0] == 0
    assert [tick for tick, _, _ in events] == [0, 384]
    assert end_tick == 384
    assert abs(seconds / Fraction(60, 29) - 1) < Fraction(2, 1000)


def test_midi_without_tempo_retains_standard_120_bpm_default():
    track = b"\x00\x90\x3C\x40\x83\x00\x80\x3C\x00\x00\xFF\x2F\x00"
    midi = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\x80MTrk" + len(track).to_bytes(4, "big") + track

    converted = convert_midi_bytes_to_eseq_bytes(midi, filename_hint="DEFAULT.FIL", timing_policy="preserve")

    assert converted[0x33] == 120 - 29
    assert _read_eseq(converted)[-1] == Fraction(1, 2)
