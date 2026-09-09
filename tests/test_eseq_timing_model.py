"""Timing fixtures independent of the APS writer and its event parsers.

The distributed MID2ESEQ 2.01 uses 44,928 / 60,000,000 ticks per
microsecond and header BPM 117: exactly 384 PPQN at 117 BPM (748.8 Hz).
Its paired ESEQ2MID uses header[0x33] + 29 and FB factors / 1000.
These tests describe that software evidence, not physical piano tests.
"""

from fractions import Fraction

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
)


def _word14(value):
    assert 0 <= value <= 0x3FFF
    return bytes((value & 0x7F, value >> 7))


def _handcrafted_eseq(base_bpm, stream, *, q11=False):
    """Construct the external container without using an APS header template."""
    header = bytearray(0x200 if q11 else 0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = header[0x33] = base_bpm - 29
    header[0x27:0x32] = b"TIMING  FIL"
    header[0x34:0x36] = bytes((4, 4))
    header[0x43:0x47] = bytes((0, 0x77, 0, 0))
    header[0x57:0x77] = b"Independent timing fixture".ljust(32, b" ")
    if q11:
        header[0x0F:0x17] = b"Q11V1.00"
        # Q11 reads its base at 0x24. A different value at the standard
        # location catches accidentally using/restoring the wrong field.
        header[0x33] = 117 - 29
        header[0x77:0x200] = bytes((index % 128 for index in range(0x200 - 0x77)))
    return bytes(header) + stream


def _scan_eseq(data):
    """Read commanded ticks/factors; deliberately do not call parse_eseq_bytes."""
    assert data[7:15] == b"COM-ESEQ"
    q11 = data[0x0F:0x17] == b"Q11V1.00"
    base_bpm = data[0x24 if q11 else 0x33] + 29
    events, factors = [], []
    tick, pos = 0, 0x200 if q11 else 0x77
    while pos < len(data):
        status = data[pos]
        pos += 1
        if status == 0xF2:
            return base_bpm, events, factors, tick
        if status == 0xF1:
            pos += 1
        elif status == 0xF3:
            tick += data[pos]
            pos += 1
        elif status in (0xF4, 0xFB):
            value = data[pos] | (data[pos + 1] << 7)
            pos += 2
            if status == 0xF4:
                tick += value
            else:
                factors.append((tick, value))
        elif status == 0xF9:
            pos += 2
        elif 0x80 <= status < 0xF0:
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            events.append((tick, bytes((status,)) + data[pos:pos + size]))
            pos += size
        else:
            raise AssertionError(f"Unexpected fixture opcode {status:02x}")
    raise AssertionError("Missing E-SEQ end command")


def _eseq_seconds_at(data, end_tick):
    base, _, factors, _ = _scan_eseq(data)
    seconds, previous = Fraction(), 0
    bpm = Fraction(base)
    for tick, factor in factors:
        if tick > end_tick:
            break
        seconds += Fraction((tick - previous) * 60, 384) / bpm
        previous, bpm = tick, Fraction(base * factor, 1000)
    return seconds + Fraction((end_tick - previous) * 60, 384) / bpm


def _scan_midi(data):
    """Minimal independent Type 0 reader; no optional mido dependency required."""
    assert data[:4] == b"MThd"
    header_length = int.from_bytes(data[4:8], "big")
    assert int.from_bytes(data[8:10], "big") == 0
    assert int.from_bytes(data[10:12], "big") == 1
    division = int.from_bytes(data[12:14], "big")
    pos = 8 + header_length
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
    seconds, events, tempos = Fraction(), [], []
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
                assert size == 3
                mpqn = int.from_bytes(payload, "big")
                tempos.append((tick, mpqn))
            elif meta == 0x2F:
                return division, events, tempos, tick, seconds
        elif 0x80 <= status < 0xF0:
            running = status
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            events.append((tick, bytes((status,)) + data[pos:pos + size], seconds))
            pos += size
        else:
            raise AssertionError(f"Unexpected fixture MIDI status {status:02x}")
    raise AssertionError("Missing MIDI end-of-track event")


@pytest.mark.parametrize("policy", ("clean", "archival"))
def test_header_60_with_384_ticks_decodes_to_one_second(policy):
    source = _handcrafted_eseq(
        60, b"\xF1\x00\x90\x3C\x40\xF4" + _word14(384) + b"\x80\x3C\x00\xF2",
    )

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy=policy)
    division, events, tempos, end_tick, seconds = _scan_midi(midi)

    assert division == 384
    assert tempos == [(0, 1_000_000)]
    assert events == [(0, b"\x90\x3C\x40", 0), (384, b"\x80\x3C\x00", 1)]
    assert end_tick == 384
    assert seconds == 1


@pytest.mark.parametrize("policy", ("clean", "archival"))
def test_legacy_header_117_with_7488_ticks_is_ten_seconds(policy):
    source = _handcrafted_eseq(
        117, b"\xF1\x00\x90\x3C\x40\xF4" + _word14(7488) + b"\x80\x3C\x00\xF2",
    )
    assert _eseq_seconds_at(source, 7488) == 10

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy=policy)
    division, events, tempos, end_tick, seconds = _scan_midi(midi)

    assert division == 384
    assert tempos == [(0, 60_000_000 // 117)]
    assert [(tick, raw) for tick, raw, _ in events] == [
        (0, b"\x90\x3C\x40"), (7488, b"\x80\x3C\x00"),
    ]
    assert end_tick == 7488
    # MIDI stores whole microseconds per quarter, so allow less than one
    # microsecond of tempo truncation for each quarter in this fixture.
    assert abs(seconds - 10) < Fraction(7488, 384 * 1_000_000)


def _changing_tempo_fixture(*, initial_factor=None, q11=False):
    initial = b"" if initial_factor is None else b"\xFB" + _word14(initial_factor)
    return _handcrafted_eseq(
        60,
        b"\xF1\x00" + initial + b"\x90\x3C\x40"
        + b"\xF4" + _word14(384) + b"\xFB" + _word14(500)
        + b"\xB0\x40\x7F"
        + b"\xF4" + _word14(384) + b"\x80\x3C\x00"
        + b"\xF4" + _word14(192) + b"\xFB" + _word14(2000)
        + b"\xB0\x40\x00"
        + b"\xF4" + _word14(576) + b"\xF2",
        q11=q11,
    )


@pytest.mark.parametrize("policy", ("clean", "archival"))
def test_roundtrip_keeps_commanded_ticks_end_and_nonzero_tempo_changes(policy):
    source = _changing_tempo_fixture()
    before = _scan_eseq(source)
    assert before[2] == [(384, 500), (960, 2000)]
    assert before[3] == 1536

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy=policy)
    _, events, tempos, end_tick, seconds = _scan_midi(midi)
    assert [(tick, raw) for tick, raw, _ in events] == before[1]
    assert tempos == [(0, 1_000_000), (384, 2_000_000), (960, 500_000)]
    assert end_tick == 1536
    assert seconds == Fraction(19, 4)

    restored = convert_midi_bytes_to_eseq_bytes(midi, filename_hint="TIMING.FIL")
    assert _scan_eseq(restored) == before
    assert _eseq_seconds_at(restored, 1536) == Fraction(19, 4)


@pytest.mark.parametrize("policy", ("clean", "archival"))
def test_initial_factor_different_from_header_keeps_effective_roundtrip_tempo(policy):
    source = _changing_tempo_fixture(initial_factor=2000)
    original_base, original_events, original_factors, original_end = _scan_eseq(source)
    assert original_base == 60
    assert original_factors == [(0, 2000), (384, 500), (960, 2000)]

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy=policy)
    _, _, tempos, _, seconds = _scan_midi(midi)
    assert tempos == [(0, 500_000), (384, 2_000_000), (960, 500_000)]
    assert seconds == Fraction(17, 4)

    restored = convert_midi_bytes_to_eseq_bytes(midi, filename_hint="TIMING.FIL")
    _, restored_events, _, restored_end = _scan_eseq(restored)
    assert restored_events == original_events
    assert restored_end == original_end
    # Header restoration must not silently reinterpret factors calculated
    # against a different base BPM. Compare exact rational commanded time.
    for tick in (384, 768, 960, 1536):
        assert _eseq_seconds_at(restored, tick) == _eseq_seconds_at(source, tick)


@pytest.mark.parametrize("q11", (False, True), ids=("standard", "Q11"))
@pytest.mark.parametrize("initial_factor", (333, 2000))
def test_archival_roundtrip_preserves_original_base_and_exact_tempo_factors(q11, initial_factor):
    source = _changing_tempo_fixture(initial_factor=initial_factor, q11=q11)
    before = _scan_eseq(source)
    assert before[0] == 60
    assert before[2] == [(0, initial_factor), (384, 500), (960, 2000)]

    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="archival")
    restored = convert_midi_bytes_to_eseq_bytes(midi, filename_hint="TIMING.FIL")

    assert _scan_eseq(restored) == before
    assert restored[0x24] == source[0x24]
    assert restored[0x33] == source[0x33]
    if q11:
        assert restored[0x0F:0x17] == b"Q11V1.00"
        assert restored[0x77:0x200] == source[0x77:0x200]
        assert restored[0x200:0x202] == b"\xF1\x00"
    for tick in (384, 768, 960, 1536):
        assert _eseq_seconds_at(restored, tick) == _eseq_seconds_at(source, tick)


@pytest.mark.parametrize("q11", (False, True), ids=("standard", "Q11"))
@pytest.mark.parametrize(
    "changed_tick, old_mpqn, new_mpqn, new_factor, expected_duration",
    ((0, 500_000, 250_000, 4000, Fraction(4)),
     (384, 2_000_000, 800_000, 1250, Fraction(49, 20))),
)
def test_archival_header_preservation_honors_later_midi_tempo_edits(
    q11, changed_tick, old_mpqn, new_mpqn, new_factor, expected_duration,
):
    source = _changing_tempo_fixture(initial_factor=2000, q11=q11)
    midi = convert_eseq_bytes_to_midi_bytes(source, midi_metadata_policy="archival")
    old_event = b"\xFF\x51\x03" + old_mpqn.to_bytes(3, "big")
    new_event = b"\xFF\x51\x03" + new_mpqn.to_bytes(3, "big")
    assert old_event in midi
    # Only the first matching tempo changes; the later tick-960 tempo and
    # archival source metadata remain intact. Payload lengths do not change.
    edited = midi.replace(old_event, new_event, 1)
    expected_tempos = [(0, 500_000), (384, 2_000_000), (960, 500_000)]
    expected_tempos = [
        (tick, new_mpqn if tick == changed_tick else mpqn)
        for tick, mpqn in expected_tempos
    ]
    assert _scan_midi(edited)[2] == expected_tempos

    restored = convert_midi_bytes_to_eseq_bytes(edited, filename_hint="TIMING.FIL")
    base, events, factors, end = _scan_eseq(restored)
    original_base, original_events, original_factors, original_end = _scan_eseq(source)
    assert base == original_base == 60
    assert events == original_events
    assert end == original_end
    assert factors == [
        (tick, new_factor if tick == changed_tick else factor)
        for tick, factor in original_factors
    ]
    assert _eseq_seconds_at(restored, end) == expected_duration
