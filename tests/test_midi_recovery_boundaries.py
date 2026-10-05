"""MIDI salvage keeps complete tracks within their original song boundaries."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.midi_type0_converter import (
    _parse_midi_chunks,
    _parse_track_events,
)


UNKNOWN_CHUNK = b"JUNK" + (8).to_bytes(4, "big") + b"metadata"


def _header(track_count, *, format_type=1):
    return (
        b"MThd" + (6).to_bytes(4, "big")
        + format_type.to_bytes(2, "big")
        + track_count.to_bytes(2, "big")
        + (480).to_bytes(2, "big")
    )


def _track(pitch, *, text=b""):
    events = (
        (b"\0\xff\x01" + bytes((len(text),)) + text if text else b"")
        + bytes((0, 0x90, pitch, 80, 32, 0x80, pitch, 0, 0, 0xFF, 0x2F, 0))
    )
    return b"MTrk" + len(events).to_bytes(4, "big") + events


def _midi(pitch):
    return _header(1, format_type=0) + _track(pitch)


def _note_pitches(data):
    _header_end, _format_type, _track_count, chunks = _parse_midi_chunks(data)
    pitches = []
    for chunk in chunks:
        if chunk["id"] == b"MTrk":
            events, _end_tick = _parse_track_events(data[chunk["data_start"]:chunk["data_end"]])
            pitches.extend(raw[1] for _tick, _order, raw in events if raw[0] & 0xF0 == 0x90)
    return pitches


@pytest.mark.parametrize("has_first_track", [False, True], ids=["no-tracks", "one-track"])
@pytest.mark.parametrize("declared_tracks", [2, 3])
@pytest.mark.parametrize("with_unknown_chunk", [False, True], ids=["adjacent", "after-unknown-chunk"])
def test_carving_stops_at_an_adjacent_song_header(has_first_track, declared_tracks, with_unknown_chunk):
    first_track = _track(60) if has_first_track else b""
    truncated = _header(declared_tracks) + first_track
    if with_unknown_chunk:
        truncated += UNKNOWN_CHUNK
    neighbor = _midi(67)
    prefix = bytes(16)
    source = prefix + truncated + neighbor

    if not has_first_track:
        assert image._extract_midi_blob_for_recovery(source, len(prefix)) is None
    recovered = image._carve_recovery_files_from_bytes(source)

    expected = [(len(prefix) + len(truncated), neighbor, [67])]
    if has_first_track:
        expected.insert(0, (len(prefix), _midi(60), [60]))
    assert [song.source_offset for song in recovered] == [offset for offset, _data, _pitches in expected]
    for song, (_offset, data, pitches) in zip(recovered, expected):
        assert song.kind == "MIDI"
        assert song.origin == "carve"
        assert song.data == data
        assert _note_pitches(song.data) == pitches


def test_complete_midi_retains_unknown_chunks_between_its_tracks():
    original = _header(2) + _track(60) + UNKNOWN_CHUNK + _track(64)
    neighbor = _midi(67)

    recovered = image._carve_recovery_files_from_bytes(original + neighbor)

    assert [song.data for song in recovered] == [original, neighbor]
    assert [_note_pitches(song.data) for song in recovered] == [[60, 64], [67]]


@pytest.mark.parametrize("declared_tracks", [1, 3], ids=["complete", "truncated"])
def test_header_bytes_inside_a_track_do_not_end_the_song(declared_tracks):
    # A valid header in a text meta-event is payload, not a chunk boundary.
    track = _track(60, text=_header(1, format_type=0))
    original = _header(declared_tracks, format_type=0 if declared_tracks == 1 else 1) + track
    expected = _header(1, format_type=0) + track
    neighbor = _midi(67)

    recovered = image._carve_recovery_files_from_bytes(original + neighbor)

    assert [song.data for song in recovered] == [expected, neighbor]
    assert [_note_pitches(song.data) for song in recovered] == [[60], [67]]


def test_damaged_image_recovery_keeps_neighbor_events_out_of_salvage(tmp_path):
    truncated = _header(3) + _track(60)
    neighbor = _midi(67)
    raw_image = bytearray(720 * 1024)
    offset = 8192
    raw_image[offset:offset + len(truncated) + len(neighbor)] = truncated + neighbor
    original = bytes(raw_image)
    source = tmp_path / "damaged.img"
    source.write_bytes(original)

    session = image.FloppyImageSession.recover("image", str(source))
    try:
        paths = [entry.path for entry in session.list_entries().entries]
        assert paths == ["REC001.MID", "REC002.MID"]
        songs = [Path(session.extract_file(path)).read_bytes() for path in paths]
        assert songs == [_midi(60), neighbor]
        assert [_note_pitches(song) for song in songs] == [[60], [67]]
    finally:
        session.cleanup()
        assert source.read_bytes() == original
