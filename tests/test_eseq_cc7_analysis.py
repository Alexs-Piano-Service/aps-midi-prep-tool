"""CC7 candidate detection keeps event order and scales with song length."""

import random
import time

import pytest

from aps_midi_prep_tool_app.eseq_converter import _zero_cc7_indexes_needing_playback_fix


def _forward_scan(events, tick_limit):
    """Original forward-search behavior, retained as a small-input oracle."""
    candidates = set()
    for index, (tick, _order, raw) in enumerate(events):
        if tick_limit is not None and tick > tick_limit:
            continue
        if len(raw) != 3 or raw[0] & 0xF0 != 0xB0 or raw[1:] != b"\x07\x00":
            continue
        channel = raw[0] & 0x0F
        for later_tick, _later_order, later in events[index + 1:]:
            if later_tick < tick or not later or not 0x80 <= later[0] <= 0xEF or later[0] & 0x0F != channel:
                continue
            if len(later) == 3 and later[0] & 0xF0 == 0xB0 and later[1] == 7 and later[2] > 0:
                break
            if len(later) == 3 and later[0] & 0xF0 == 0x90 and later[2] > 0:
                candidates.add(index)
                break
    return candidates


@pytest.mark.parametrize("tick_limit", [None, -1, 0, 30, 500])
def test_reverse_scan_matches_forward_search(tick_limit):
    rng = random.Random(0xCC7)
    for _ in range(20):
        events = []
        tick = 0
        for index in range(400):
            tick += rng.randrange(3)
            channel = rng.randrange(16)
            raw = rng.choice((
                bytes((0xB0 | channel, 7, 0)),
                bytes((0xB0 | channel, 7, 100)),
                bytes((0x90 | channel, 60, 64)),
                bytes((0x90 | channel, 60, 0)),
                bytes((0x80 | channel, 60, 64)),
                bytes((0xB0 | channel, 64, 127)),
                b"\xF0\x43\xF7", b"\xFF\x20\x01\x00", b"",
            ))
            events.append((tick, index, raw))
        assert _zero_cc7_indexes_needing_playback_fix(events, tick_limit) == _forward_scan(events, tick_limit)


def test_tick_limit_only_limits_candidates_and_same_tick_order_is_preserved():
    events = [
        (0, 0, b"\xB0\x07\x00"),
        (0, 1, b"\x90\x3C\x40"),
        (0, 2, b"\xB0\x07\x00"),
        (1, 3, b"\xB0\x07\x64"),
        (1, 4, b"\x90\x3C\x40"),
        (1, 5, b"\xB0\x07\x00"),
        (2, 6, b"\x90\x3C\x40"),
    ]
    assert _zero_cc7_indexes_needing_playback_fix(events) == {0, 5}
    assert _zero_cc7_indexes_needing_playback_fix(events, tick_limit=0) == {0}


@pytest.mark.parametrize("last_event,expected_count", [(b"\x90\x3C\x40", 30_000), (b"\xB0\x07\x64", 0)])
def test_control_dense_song_has_linear_analysis_cost(last_event, expected_count):
    events = [(index, index, b"\xB0\x07\x00") for index in range(30_000)]
    events.append((30_000, 30_000, last_event))
    started = time.monotonic()
    candidates = _zero_cc7_indexes_needing_playback_fix(events)
    elapsed = time.monotonic() - started
    assert candidates == set(range(expected_count))
    # Deliberately generous: this stream needs hundreds of millions of checks
    # under the old scan, but one pass should finish well below this limit.
    assert elapsed < 5.0
