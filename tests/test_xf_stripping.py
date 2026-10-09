import struct
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.midi_type0_converter import (
    _encode_vlq,
    _parse_midi_chunks,
    _parse_track_events,
)
from aps_midi_prep_tool_app.xf_stripper import (
    strip_xf_from_midi_bytes,
    strip_xf_from_midi_path,
)


def _track(events, end_tick=None):
    payload = bytearray()
    previous_tick = 0
    for tick, raw in events:
        payload.extend(_encode_vlq(tick - previous_tick))
        payload.extend(raw)
        previous_tick = tick
    final_tick = previous_tick if end_tick is None else end_tick
    payload.extend(_encode_vlq(final_tick - previous_tick))
    payload.extend(b"\xFF\x2F\x00")
    return b"MTrk" + len(payload).to_bytes(4, "big") + bytes(payload)


def _midi(*tracks, format_type=1, division=480, trailing=b""):
    return struct.pack(">4sIHHH", b"MThd", 6, format_type, len(tracks), division) + b"".join(tracks) + trailing


def _events(midi_bytes):
    _header_end, _format_type, _track_count, chunks = _parse_midi_chunks(midi_bytes)
    track_chunk = next(chunk for chunk in chunks if chunk["id"] == b"MTrk")
    return _parse_track_events(
        midi_bytes[track_chunk["data_start"]:track_chunk["data_end"]]
    )


def test_explicit_broad_cleanup_removes_sequencer_metadata_and_appended_chunks_only():
    musical_events = [
        (0, b"\xFF\x03\x05Piano"),
        (0, b"\x90\x3C\x50"),
        (120, b"\xFF\x7F\x06YAMAHA"),
        (240, b"\xB0\x40\x57"),
        (480, b"\x80\x3C\x40"),
    ]
    trailing = b"XFIH" + (5).to_bytes(4, "big") + b"extra"
    source = _midi(_track(musical_events, end_tick=600), trailing=trailing)

    stripped, changed = strip_xf_from_midi_bytes(source, cleanup_mode="broad")

    assert changed
    assert b"YAMAHA" not in stripped
    assert b"XFIH" not in stripped
    events, end_tick = _events(stripped)
    assert end_tick == 600
    assert [(tick, raw) for tick, _order, raw in events] == [
        (0, b"\xFF\x03\x05Piano"),
        (0, b"\x90\x3C\x50"),
        (240, b"\xB0\x40\x57"),
        (480, b"\x80\x3C\x40"),
    ]


def test_strip_xf_preserves_tracks_sysex_tempo_and_continuous_pedal():
    first = _track(
        [
            (0, b"\xFF\x51\x03\x07\xA1\x20"),
            (0, b"\xF0\x03\x43\x12\xF7"),
            (120, b"\xFF\x7F\x09\x43\x7b\x00XF02\x00\x00"),
        ],
        end_tick=240,
    )
    second = _track(
        [
            (0, b"\xB1\x40\x00"),
            (120, b"\xB1\x40\x24"),
            (240, b"\xB1\x40\x58"),
            (360, b"\xB1\x40\x7F"),
            (480, b"\xB1\x40\x00"),
        ]
    )

    stripped, changed = strip_xf_from_midi_bytes(_midi(first, second))

    assert changed
    _header_end, format_type, track_count, chunks = _parse_midi_chunks(stripped)
    assert (format_type, track_count) == (1, 2)
    assert [chunk["id"] for chunk in chunks] == [b"MTrk", b"MTrk"]
    first_events, _end_tick = _parse_track_events(
        stripped[chunks[0]["data_start"]:chunks[0]["data_end"]]
    )
    second_events, _end_tick = _parse_track_events(
        stripped[chunks[1]["data_start"]:chunks[1]["data_end"]]
    )
    assert [(tick, raw) for tick, _order, raw in first_events] == [
        (0, b"\xFF\x51\x03\x07\xA1\x20"),
        (0, b"\xF0\x03\x43\x12\xF7"),
    ]
    assert [raw[2] for _tick, _order, raw in second_events] == [0, 36, 88, 127, 0]


def test_targeted_cleanup_preserves_unknown_records_and_trailing_data():
    unknown = [
        b"\xff\x7f\x03XF1",  # text is not a manufacturer identifier
        b"\xff\x7f\x04\x43\x73\x01\x00",  # Yamaha, but not XF
        b"\xff\x7f\x04\x43\x7b\x66\x00",  # unknown XF record type
        b"\xff\x7f\x04\x43\x7b\x01\x00",  # incomplete chord record
        b"\xff\x7f\x04\x41\x7b\x01\x00",  # another manufacturer
    ]
    known_xf = b"\xff\x7f\x07\x43\x7b\x01\x31\x00\x7f\x7f"
    events = [(0, b"\x90\x3c\x50")] + [(120, item) for item in unknown]
    events += [(240, known_xf), (480, b"\x80\x3c\x40")]
    trailing = b"XFIH\x00\x00\x00\x05extra"
    source = _midi(_track(events, end_tick=600), trailing=trailing)

    result, changed = strip_xf_from_midi_bytes(source)

    assert changed
    assert result.endswith(trailing)
    retained, end_tick = _events(result)
    assert [(tick, raw) for tick, _, raw in retained] == [
        (tick, raw) for tick, raw in events if raw != known_xf
    ]
    assert end_tick == 600


def test_targeted_cleanup_is_byte_identical_without_recognized_xf():
    # Running status, extended headers, noncanonical VLQs and unknown tails stay intact.
    track_data = b"\x80\x00\x90\x3c\x40\x60\x3c\x00\x00\xff\x2f\x00hidden"
    track = b"MTrk" + len(track_data).to_bytes(4, "big") + track_data
    header = struct.pack(">4sIHHH", b"MThd", 8, 0, 1, 480) + b"ab"
    source = header + track + b"unknown appended data"

    assert strip_xf_from_midi_bytes(source) == (source, False)


def test_clean_canonical_midi_is_left_unchanged(tmp_path):
    source_bytes = _midi(_track([(0, b"\x90\x3C\x50"), (480, b"\x80\x3C\x40")]))
    source_path = tmp_path / "clean.mid"
    destination_path = tmp_path / "staged.mid"
    source_path.write_bytes(source_bytes)

    stripped, changed = strip_xf_from_midi_bytes(source_bytes)

    assert stripped == source_bytes
    assert not changed
    assert not strip_xf_from_midi_path(source_path, destination_path)
    assert not destination_path.exists()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"not midi", "valid Standard MIDI File"),
        (struct.pack(">4sIHHH", b"MThd", 6, 1, 1, 0xE728), "SMPTE"),
        (struct.pack(">4sIHHH", b"MThd", 6, 1, 1, 480), "missing or malformed"),
    ],
)
def test_strip_xf_rejects_invalid_or_unsupported_midi(source, message):
    with pytest.raises(ValueError, match=message):
        strip_xf_from_midi_bytes(source)


@pytest.mark.parametrize("status", [0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF8,
                                   0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE])
@pytest.mark.parametrize("cleanup_mode", ["targeted", "broad"])
@pytest.mark.parametrize("destination_kind", ["new", "existing", "in_place"])
def test_xf_cleanup_rejects_direct_system_status_without_changing_files(
    tmp_path, status, cleanup_mode, destination_kind,
):
    known_xf = b"\xff\x7f\x04\x43\x7b\x02\x00"
    wire_event = bytes([status]) + b"\x00" * {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0)
    source_bytes = _midi(_track([(0, known_xf), (0, wire_event)]))
    source = tmp_path / "source.mid"
    source.write_bytes(source_bytes)
    destination = source if destination_kind == "in_place" else tmp_path / "destination.mid"
    if destination_kind == "existing":
        destination.write_bytes(b"existing output")

    with pytest.raises(ValueError, match=f"Unsupported system status byte: 0x{status:02X}"):
        strip_xf_from_midi_path(source, destination, cleanup_mode=cleanup_mode)

    assert source.read_bytes() == source_bytes
    if destination_kind == "new":
        assert not destination.exists()
    elif destination_kind == "existing":
        assert destination.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".aps_write_*"))


@pytest.mark.parametrize("event_prefix", [b"\xf7", b"\xff\x01"], ids=["escaped", "meta"])
@pytest.mark.parametrize("cleanup_mode", ["targeted", "broad"])
def test_xf_cleanup_preserves_system_status_bytes_in_length_delimited_payloads(event_prefix, cleanup_mode):
    known_xf = b"\xff\x7f\x04\x43\x7b\x02\x00"
    payload = bytes(range(0xF0, 0x100))
    retained_event = event_prefix + bytes([len(payload)]) + payload
    source = _midi(_track([(0, known_xf), (10, retained_event)]))

    stripped, changed = strip_xf_from_midi_bytes(source, cleanup_mode=cleanup_mode)

    assert changed
    assert stripped == _midi(_track([(10, retained_event)]))


@pytest.mark.parametrize(
    ("target_index", "expected_rows"),
    [(-1, [(2, "first.mid"), (5, "second.mid")]), (1, [(5, "second.mid")])],
)
def test_xf_utility_targets_all_rows_or_one_selected_song(target_index, expected_rows):
    rows = [(2, "first.mid"), (5, "second.mid")]
    captured = {}
    window = SimpleNamespace(
        choose_button=SimpleNamespace(isEnabled=lambda: True),
        _midi_rows_for_xf_stripping=lambda: rows,
        _xf_stripping_options_dialog=lambda _rows: target_index,
        is_image_mode=lambda: False,
        _strip_xf_from_regular_rows=lambda selected: captured.update(rows=selected),
    )

    MidiTitleWindow.show_xf_stripping_utility(window)

    assert captured["rows"] == expected_rows
