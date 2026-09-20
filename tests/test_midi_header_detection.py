"""Header detection must preserve extended SMF headers and report bad songs."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import emulator_image_builder, midi_metadata
from aps_midi_prep_tool_app.floppy_image import FloppyImageError


def _midi_bytes(*, format_type=0, header_length=8):
    title = b"Extended header"
    track = (
        b"\x00\xff\x03" + bytes([len(title)]) + title
        + b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    )
    header = format_type.to_bytes(2, "big") + b"\x00\x01\x00\x60"
    return (
        b"MThd" + header_length.to_bytes(4, "big")
        + header + b"\x00" * (header_length - len(header))
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


@pytest.mark.parametrize("format_type", [0, 1, 2])
@pytest.mark.parametrize("header_length", [6, 8, 64 * 1024])
def test_detector_accepts_complete_extended_headers(tmp_path, format_type, header_length):
    song = tmp_path / "song.mid"
    original = _midi_bytes(format_type=format_type, header_length=header_length)
    song.write_bytes(original)

    assert midi_metadata.probe_midi_file_type(song) == format_type
    assert midi_metadata.is_midi_file(song)
    assert midi_metadata.extract_midi_type_label_from_midi(song) == f"Type {format_type}"
    assert midi_metadata.extract_first_title_from_midi(song) == "Extended header"
    assert song.read_bytes() == original


_INVALID_HEADERS = [
    pytest.param(b"MThd", "Corrupt", id="missing-length"),
    pytest.param(b"MThd\x00\x00\x00", "Corrupt", id="truncated-length"),
    pytest.param(b"MThd\x00\x00\x00\x05" + b"\x00" * 6, "Invalid", id="short-header"),
    pytest.param(b"MThd\x00\x00\x00\x08" + b"\x00" * 6, "Corrupt", id="truncated-extension"),
    pytest.param(b"MThd\x00\x01\x00\x01" + b"\x00" * 6, "Invalid", id="over-limit"),
    pytest.param(b"MThd\xff\xff\xff\xff" + b"\x00" * 6, "Invalid", id="absurd-length"),
]


@pytest.mark.parametrize("payload, message", _INVALID_HEADERS)
def test_detector_rejects_invalid_header_without_reading_declared_size(
    tmp_path, monkeypatch, payload, message,
):
    song = tmp_path / "broken.mid"
    song.write_bytes(payload)
    read_sizes = []
    real_open = open

    @contextmanager
    def guarded_open(path, mode):
        with real_open(path, mode) as handle:
            def guarded_read(size=-1):
                read_sizes.append(size)
                assert 0 <= size <= 8, "Invalid lengths must be rejected before a payload read"
                return handle.read(size)

            yield SimpleNamespace(read=guarded_read, fileno=handle.fileno)

    monkeypatch.setattr(midi_metadata, "open", guarded_open, raising=False)

    with pytest.raises(midi_metadata.MidiTitleFormatError, match=message):
        midi_metadata.probe_midi_file_type(song)
    assert midi_metadata.is_midi_file(song) is False
    assert read_sizes == [8, 8]


@pytest.mark.parametrize("include_subfolders", [False, True])
def test_discovery_keeps_extended_header_songs(tmp_path, include_subfolders):
    source = tmp_path / "songs"
    source.mkdir()
    originals = {}
    for name in ("Song 1.mid", "Song 2.MIDI", "Song 3"):
        song = source / name
        originals[song] = _midi_bytes()
        song.write_bytes(originals[song])
    (source / "notes.fil").write_text("Not a song", encoding="utf-8")
    (source / "notes").write_text("Not a song", encoding="utf-8")

    assert emulator_image_builder.discover_song_files(
        source, include_subfolders=include_subfolders,
    ) == [str(song) for song in originals]
    assert emulator_image_builder.discover_midi_files(
        source, include_subfolders=include_subfolders,
    ) == [str(song) for song in originals]
    assert all(song.read_bytes() == original for song, original in originals.items())


@pytest.mark.parametrize("payload, message", _INVALID_HEADERS)
@pytest.mark.parametrize("filename", ["Broken.mid", "Broken"])
def test_discovery_reports_malformed_candidates(tmp_path, payload, message, filename):
    song = tmp_path / filename
    song.write_bytes(payload)

    with pytest.raises(FloppyImageError, match=f"Could not inspect song {filename}") as caught:
        emulator_image_builder.discover_song_files(tmp_path)
    assert message in str(caught.value)


@pytest.mark.parametrize("include_subfolders", [False, True])
def test_discovery_reports_non_midi_files_with_midi_extensions(tmp_path, include_subfolders):
    (tmp_path / "Notes.MID").write_text("This is not a MIDI file", encoding="utf-8")

    with pytest.raises(FloppyImageError, match="Could not inspect song Notes.MID: Missing MThd"):
        emulator_image_builder.discover_song_files(tmp_path, include_subfolders=include_subfolders)


@pytest.mark.parametrize("include_subfolders", [False, True])
def test_discovery_reports_unreadable_candidates(tmp_path, monkeypatch, include_subfolders):
    song = tmp_path / "Unreadable.mid"
    song.write_bytes(_midi_bytes())

    def unreadable(path):
        assert Path(path) == song
        raise PermissionError("Access denied to song")

    monkeypatch.setattr(emulator_image_builder, "probe_midi_file_type", unreadable)

    with pytest.raises(FloppyImageError, match="Unreadable.mid: Access denied to song"):
        emulator_image_builder.discover_song_files(tmp_path, include_subfolders=include_subfolders)


def test_probe_distinguishes_non_midi_content_and_unreadable_file(tmp_path):
    notes = tmp_path / "notes"
    notes.write_text("Not a MIDI file", encoding="utf-8")
    missing = tmp_path / "missing.mid"

    assert midi_metadata.probe_midi_file_type(notes) is None
    assert midi_metadata.is_midi_file(notes) is False
    with pytest.raises(FileNotFoundError):
        midi_metadata.probe_midi_file_type(missing)
    assert midi_metadata.is_midi_file(missing) is False
