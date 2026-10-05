"""E-SEQ state changes at a shared tick keep their final effective value."""

import pytest

from aps_midi_prep_tool_app.eseq_converter import convert_eseq_bytes_to_midi_bytes
from aps_midi_prep_tool_app.midi_type0_converter import _parse_track_events


def _fixture(commands):
    stream = (
        b"\xF1\x00\xF4\x00\x03" + commands + b"\x90\x3C\x40"
        + b"\xF4\x00\x03\x80\x3C\x00\xF2"
    )
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = header[0x33] = 31  # 60 BPM, or 1,000,000 us/quarter.
    header[0x34:0x36] = b"\x04\x04"
    return bytes(header) + stream


def _converted_events(commands, policy):
    midi = convert_eseq_bytes_to_midi_bytes(_fixture(commands), midi_metadata_policy=policy)
    events, end_tick = _parse_track_events(midi[22:])
    assert end_tick == 768
    assert [(tick, raw) for tick, _order, raw in events if 0x80 <= raw[0] <= 0xEF] == [
        (384, b"\x90\x3C\x40"), (768, b"\x80\x3C\x00"),
    ]
    return events


@pytest.mark.parametrize("policy", ["clean", "archival"])
def test_repeated_nonzero_tick_tempo_restores_the_final_source_state(policy):
    # Relative factors 500, 2000, 500 produce tempo A, B, A at tick 384.
    commands = b"".join(b"\xFB" + bytes((factor & 127, factor >> 7)) for factor in (500, 2000, 500))

    events = _converted_events(commands, policy)

    assert [(tick, int.from_bytes(raw[3:], "big")) for tick, _order, raw in events
            if raw[:3] == b"\xFF\x51\x03"] == [
        (0, 1_000_000), (384, 2_000_000), (384, 500_000), (384, 2_000_000),
    ]


@pytest.mark.parametrize("policy", ["clean", "archival"])
def test_repeated_nonzero_tick_meter_restores_the_final_source_state(policy):
    commands = b"\xF9\x03\x02\xF9\x07\x03\xF9\x03\x02"  # 3/4, 7/8, 3/4.

    events = _converted_events(commands, policy)

    assert [(tick, raw[3], raw[4]) for tick, _order, raw in events
            if raw[:3] == b"\xFF\x58\x04"] == [
        (0, 4, 2), (384, 3, 2), (384, 7, 3), (384, 3, 2),
    ]
