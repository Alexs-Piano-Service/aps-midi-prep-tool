"""Synthetic MID2ESEQ compatibility contracts, without customer music fixtures."""

import struct
from fractions import Fraction

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_TO_MIDI_CONVERSION_TEXT,
    EseqConversionError,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_legacy import is_legacy_eseq_bytes


def _vlq(value):
    encoded = bytearray([value & 127])
    while value >> 7:
        value >>= 7
        encoded.insert(0, 128 | (value & 127))
    return bytes(encoded)


def _midi(events, *, division=384, end_tick=None):
    track = bytearray()
    previous = 0
    for tick, raw in events:
        track.extend(_vlq(tick - previous) + raw)
        previous = tick
    track.extend(_vlq((previous if end_tick is None else end_tick) - previous))
    track.extend(b"\xff\x2f\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, division) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _tempo(mpqn):
    return b"\xff\x51\x03" + mpqn.to_bytes(3, "big")


def _legacy_stream(data):
    """Independently walk the deliberately small fixture opcode vocabulary.

    In particular, fail if tempo/bar commands or padding occur. Expected event
    ticks below are hand-calculated; they do not reuse the writer's scheduler.
    """
    assert data[7:15] == b"COM-ESEQ"
    assert int.from_bytes(data[3:7], "little") == len(data)
    assert int.from_bytes(data[0x1F:0x23], "little") == len(data) - 0x77
    stream = data[0x77:]
    assert stream[:2] == b"\xf1\x00"
    pos, tick, messages, delays = 2, 0, [], []
    while pos < len(stream):
        opcode = stream[pos]
        pos += 1
        if opcode == 0xF2:
            assert pos == len(stream), "No bytes may follow the compatibility terminator"
            return messages, tick, delays
        if opcode == 0xF3:
            value = stream[pos]
            pos += 1
            assert 1 <= value <= 127
            tick += value
            delays.append(value)
        elif opcode == 0xF4:
            lo, hi = stream[pos:pos + 2]
            pos += 2
            assert lo < 128 and hi < 128
            value = lo + hi * 128
            assert value > 0
            tick += value
            delays.append(value)
        elif 0x80 <= opcode < 0xF0:
            size = 1 if opcode >> 4 in (0xC, 0xD) else 2
            body = stream[pos:pos + size]
            assert len(body) == size and all(value < 128 for value in body)
            pos += size
            messages.append((tick, bytes([opcode]) + body))
        else:
            raise AssertionError(f"Unexpected compatibility opcode {opcode:02x}")
    raise AssertionError("Missing E-SEQ end command")


def _events(data):
    return [(tick, raw) for tick, _order, raw in parse_eseq_bytes(data).events]


def test_fresh_midi_default_has_constant_117_clock_legacy_trailer_and_no_padding():
    source = _midi([
        (0, _tempo(500_000)), (0, b"\xff\x58\x04\x03\x02\x18\x08"),
        (0, b"\x90\x3c\x40"), (384, b"\x80\x3c\x25"),
    ], end_tick=3840)

    result = convert_midi_bytes_to_eseq_bytes(source, filename_hint="SYNTH.FIL", title_override="Synthetic")
    messages, end_tick, delays = _legacy_stream(result)

    assert messages == [(748, b"\x90\x3c\x40"), (1123, b"\x80\x3c\x25")]
    assert result[0x24] + 29 == result[0x33] + 29 == 117
    assert result[0x27:0x2F] == b"SYNTH   "
    assert result[0x57:0x77] == b"Synthetic".ljust(32, b" ")
    assert end_tick == int.from_bytes(result[0x37:0x3B], "little") == 2621
    assert delays[-1] == 1498
    assert result[-4:] == b"\xf4\x5a\x0b\xf2"
    assert len(result) < 2048
    assert is_legacy_eseq_bytes(result)


def test_fresh_legacy_default_equals_explicit_policy_and_ignores_eot_only_silence():
    events = [(0, b"\xc0\x00"), (384, b"\x90\x3c\x40"), (768, b"\x90\x3c\x00")]
    short = _midi(events)
    long = _midi(events, end_tick=384000)

    assert convert_midi_bytes_to_eseq_bytes(short) == convert_midi_bytes_to_eseq_bytes(short, timing_policy="mid2eseq")
    assert convert_midi_bytes_to_eseq_bytes(short) == convert_midi_bytes_to_eseq_bytes(long)


def test_dense_messages_keep_values_order_and_spread_each_third_crowded_message():
    events = [(0, b"\xc0\x12")]
    events += [(0, bytes([0x92, pitch, velocity])) for pitch, velocity in zip(range(60, 66), range(71, 77))]
    events += [(384, bytes([0x82, pitch, 37])) for pitch in range(60, 66)]

    result = convert_midi_bytes_to_eseq_bytes(_midi(events))
    messages, end_tick, _delays = _legacy_stream(result)

    assert [raw for tick, raw in messages] == [raw for tick, raw in events]
    assert [tick for tick, raw in messages] == [0, 748, 748, 748, 751, 751, 751, 1123, 1123, 1123, 1126, 1126, 1126]
    assert end_tick == 1126 + 1498
    assert result[0x50] == 2


@pytest.mark.parametrize("message,expected_tick", (
    (b"\x90\x3c\x01", 748), (b"\xb0\x40\x01", 748), (b"\xb0\x43\x01", 748),
    (b"\x90\x3c\x00", 0), (b"\xb0\x40\x00", 0), (b"\xb0\x42\x7f", 0),
))
def test_onset_clamp_applies_only_to_first_positive_note_or_sustain_soft_pedal(message, expected_tick):
    result = convert_midi_bytes_to_eseq_bytes(_midi([(2304, message)]), pedal_policy="preserve")

    messages, end_tick, _delays = _legacy_stream(result)

    assert messages == [(expected_tick, message)]
    assert end_tick == expected_tick + 1498


def test_leading_silence_is_trimmed_before_setup_and_existing_onset_gap_is_retained():
    events = [(4000, b"\xc0\x00"), (7000, b"\x90\x3c\x40"), (8000, b"\x80\x3c\x00")]
    source = _midi(events, division=1000)
    shifted = _midi([(tick + 5000, raw) for tick, raw in events], division=1000)

    result = convert_midi_bytes_to_eseq_bytes(source)

    assert result == convert_midi_bytes_to_eseq_bytes(shifted)
    assert _legacy_stream(result)[0] == [(0, b"\xc0\x00"), (1123, b"\x90\x3c\x40"), (1497, b"\x80\x3c\x00")]


def test_tempo_changes_are_integrated_into_elapsed_time_without_tempo_or_bar_commands():
    source = _midi([
        (0, _tempo(500_000)), (0, b"\xc0\x00"), (960, b"\x90\x3c\x40"),
        (1440, _tempo(250_000)), (1440, b"\xff\x58\x04\x05\x02\x18\x08"),
        (2400, b"\x80\x3c\x00"), (2880, b"\x90\x40\x50"), (3360, b"\x80\x40\x00"),
    ], division=480)

    messages, end_tick, _delays = _legacy_stream(convert_midi_bytes_to_eseq_bytes(source))

    assert [tick for tick, raw in messages] == [0, 748, 1497, 1684, 1872]
    # The four musical events are at 1, 2, 2.25, 2.5 seconds. The fixed clock
    # is exactly 748.8 ticks/second, with integer truncation below one tick.
    for (tick, _raw), seconds in zip(messages[1:], (1, 2, Fraction(9, 4), Fraction(5, 2))):
        assert 0 <= seconds - Fraction(tick * 5, 3744) < Fraction(5, 3744)
    assert end_tick == 1872 + 1498


@pytest.mark.parametrize("metadata_policy,include_notice", (("clean", True), ("archival", True), ("archival", False)))
def test_eseq_origin_auto_preserves_note_ticks_tempo_and_end_without_another_trailer(metadata_policy, include_notice):
    seed = convert_midi_bytes_to_eseq_bytes(_midi([
        (0, _tempo(500_000)), (127, b"\x90\x3c\x40"),
        (384, _tempo(1_000_000)), (768, b"\x80\x3c\x00"),
    ], end_tick=900), timing_policy="preserve")
    exported = convert_eseq_bytes_to_midi_bytes(seed, midi_metadata_policy=metadata_policy, include_conversion_text=include_notice)
    if metadata_policy == "clean":
        assert ESEQ_TO_MIDI_CONVERSION_TEXT.encode() in exported
        assert b"APS-ESEQ-TIMING" not in exported
        assert b"APS-ESEQ-HEADER" not in exported
    elif not include_notice:
        assert ESEQ_TO_MIDI_CONVERSION_TEXT.encode() not in exported

    result = convert_midi_bytes_to_eseq_bytes(exported)
    original, restored = parse_eseq_bytes(seed), parse_eseq_bytes(result)

    assert result == convert_midi_bytes_to_eseq_bytes(exported, timing_policy="preserve")
    assert _events(result) == _events(seed) == [(127, b"\x90\x3c\x40"), (768, b"\x80\x3c\x00")]
    assert restored.tempo_events == original.tempo_events
    assert restored.end_tick == original.end_tick == 900
    assert not is_legacy_eseq_bytes(result)


def test_explicit_mid2eseq_overrides_clean_origin_marker_while_preserve_keeps_fresh_ticks():
    text = ESEQ_TO_MIDI_CONVERSION_TEXT.encode()
    events = [(0, b"\xff\x01" + _vlq(len(text)) + text), (7, b"\x90\x3c\x40"), (391, b"\x80\x3c\x00")]
    marked, fresh = _midi(events), _midi(events[1:])

    assert _events(convert_midi_bytes_to_eseq_bytes(marked)) == events[1:]
    assert _events(convert_midi_bytes_to_eseq_bytes(fresh, timing_policy="preserve")) == events[1:]
    explicit = convert_midi_bytes_to_eseq_bytes(marked, timing_policy="mid2eseq")
    assert _legacy_stream(explicit)[0] == [(748, b"\x90\x3c\x40"), (1123, b"\x80\x3c\x00")]


def test_mda_auto_retains_its_container_and_ppqn_timing():
    source = _midi([(7, b"\x90\x3c\x40"), (391, b"\x80\x3c\x00")], end_tick=768)

    result = convert_midi_bytes_to_eseq_bytes(source, container_variant="clavinova_mda")

    assert result == convert_midi_bytes_to_eseq_bytes(source, container_variant="clavinova_mda", timing_policy="preserve")
    assert result[:7] == b"\xfe\x00\x00\xff\xff\x00\x00"
    assert result[0x57:0x59] == b"\xf1\x00"
    assert _events(result) == [(7, b"\x90\x3c\x40"), (391, b"\x80\x3c\x00")]
    assert parse_eseq_bytes(result).end_tick == 768
    assert not is_legacy_eseq_bytes(result)


@pytest.mark.parametrize("policy", ("legacy", "typo", "fast", 123))
def test_unknown_timing_policy_is_rejected(policy):
    with pytest.raises(EseqConversionError, match="Unsupported E-SEQ timing policy"):
        convert_midi_bytes_to_eseq_bytes(_midi([(0, b"\xc0\x00")]), timing_policy=policy)


def test_explicit_mid2eseq_cannot_silently_change_an_mda_destination():
    with pytest.raises(EseqConversionError, match="requires the Disklavier FIL container"):
        convert_midi_bytes_to_eseq_bytes(_midi([(0, b"\xc0\x00")]), container_variant="clavinova_mda", timing_policy="mid2eseq")


@pytest.mark.parametrize("policy,first_volume", (
    (None, b"\xb0\x07\x00"), ("preserve", b"\xb0\x07\x00"), ("warn_only", b"\xb0\x07\x00"),
    ("playback_fix_100", b"\xb0\x07\x64"), ("playback_fix_127", b"\xb0\x07\x7f"),
    ("drop_early_cc7_zero", None),
))
def test_legacy_cc7_edits_require_explicit_policy_and_leave_restored_channels_untouched(policy, first_volume):
    source_messages = [b"\xb0\x07\x00", b"\xb1\x07\x00", b"\xb1\x07\x50", b"\x90\x3c\x40", b"\x91\x40\x40"]
    source = _midi([(0, raw) for raw in source_messages])
    kwargs = {} if policy is None else {"cc7_policy": policy}

    messages, _end, _delays = _legacy_stream(convert_midi_bytes_to_eseq_bytes(source, **kwargs))

    assert [raw for tick, raw in messages] == ([first_volume] if first_volume is not None else []) + source_messages[1:]


def test_default_legacy_preserves_timed_sysex_wire_data_at_fixed_clock_positions():
    source = _midi([(0, b"\xf0\x02\x43\x01"), (384, b"\xf7\x02\x02\xf7"),
                    (768, b"\x90\x3c\x40"), (1152, b"\x80\x3c\x00")])

    result = convert_midi_bytes_to_eseq_bytes(source)

    assert _events(result) == [(0, b"\xf0\x43\x01"), (374, b"\xf7\x02\xf7"),
                               (748, b"\x90\x3c\x40"), (1123, b"\x80\x3c\x00")]
    assert result[-4:] == b"\xf4\x5a\x0b\xf2"


@pytest.mark.parametrize("mutation", ("padding", "truncation", "tempo", "length_marker"))
def test_legacy_envelope_detector_rejects_mismatched_container_fields(mutation):
    result = convert_midi_bytes_to_eseq_bytes(_midi([(0, b"\x90\x3c\x40")]))
    assert is_legacy_eseq_bytes(result)
    if mutation == "padding":
        altered = result + b"\xf6"
    elif mutation == "truncation":
        altered = result[:-1]
    else:
        altered = bytearray(result)
        altered[0x33 if mutation == "tempo" else 0x41] ^= 1

    assert not is_legacy_eseq_bytes(altered)
