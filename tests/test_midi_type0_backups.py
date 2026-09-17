"""Batch Type 0 conversion must preserve every selected original and output."""

import builtins
import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import midi_type0_converter as converter
from aps_midi_prep_tool_app.helpers import file_backup


def _recording(note, *, format_type=1):
    midi = mido.MidiFile(type=format_type)
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", note=note, velocity=80),
        mido.Message("note_off", note=note, time=480),
    ]))
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _assert_recording(path, note, format_type):
    midi = mido.MidiFile(path)
    assert midi.type == format_type
    assert [event.note for track in midi.tracks for event in track
            if event.type == "note_on" and event.velocity] == [note]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_batch_preserves_selected_backup_named_recording_and_existing_backups(tmp_path, reverse, existing):
    first = tmp_path / "song.mid"
    second = tmp_path / "song_backup.mid"
    originals = {first: _recording(60), second: _recording(72)}
    for path, payload in originals.items():
        path.write_bytes(payload)
    older = tmp_path / "song_backup_2.mid"
    if existing:
        older.write_bytes(b"previous backup")
    paths = [first, second][::-1] if reverse else [first, second]

    result = converter.convert_midi_files_to_type0(paths, create_backups=True)

    assert result.failed == []
    assert result.converted == [str(path) for path in paths]
    assert result.unchanged == []
    assert len(set(result.backups_created)) == 2
    assert not set(result.backups_created).intersection(map(str, originals))
    for path, backup in zip(paths, result.backups_created):
        assert Path(backup).read_bytes() == originals[path]
    _assert_recording(first, 60, 0)
    _assert_recording(second, 72, 0)
    if existing:
        assert older.read_bytes() == b"previous backup"


def test_custom_backup_builder_reserves_unchanged_and_missing_sources(tmp_path):
    source = tmp_path / "song.mid"
    unchanged = tmp_path / "already_type0.mid"
    missing = tmp_path / "missing.mid"
    source.write_bytes(_recording(60))
    unchanged.write_bytes(_recording(72, format_type=0))

    result = converter.convert_midi_files_to_type0(
        [source, unchanged, missing], create_backups=True,
        backup_path_builder=lambda _: missing,
    )

    assert result.converted == [str(source)]
    assert result.unchanged == [str(unchanged)]
    assert result.failed == [(str(missing), "File does not exist.")]
    assert result.backups_created == [str(tmp_path / "missing_2.mid")]
    assert not missing.exists()
    _assert_recording(source, 60, 0)
    _assert_recording(unchanged, 72, 0)


def test_backup_planning_failure_leaves_entire_batch_untouched(tmp_path):
    paths = [tmp_path / "first.mid", tmp_path / "second.mid"]
    originals = {path: _recording(note) for path, note in zip(paths, [60, 72])}
    for path, data in originals.items():
        path.write_bytes(data)

    def builder(source):
        if source == str(paths[1]):
            raise ValueError("injected planning failure")
        return tmp_path / "backup.mid"

    result = converter.convert_midi_files_to_type0(paths, create_backups=True, backup_path_builder=builder)

    assert result.converted == result.backups_created == []
    assert len(result.failed) == 2
    assert all("injected planning failure" in error for _, error in result.failed)
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals


@pytest.mark.parametrize("arrival", ["file", "symlink"])
def test_backup_creation_race_refuses_arriving_destination(tmp_path, monkeypatch, arrival):
    source = tmp_path / "song.mid"
    original = _recording(60)
    source.write_bytes(original)
    destination = tmp_path / "song_backup.mid"
    victim = tmp_path / "victim.mid"
    victim.write_bytes(b"other recording")

    def racing_open(path, mode="r", *args, **kwargs):
        if Path(path) == destination and mode == "xb":
            if arrival == "file":
                destination.write_bytes(b"arrived after planning")
            else:
                try:
                    destination.symlink_to(victim)
                except OSError as exc:
                    pytest.skip(f"Symlinks unavailable: {exc}")
        return builtins.open(path, mode, *args, **kwargs)

    monkeypatch.setattr(file_backup, "open", racing_open, raising=False)
    result = converter.convert_midi_files_to_type0([source], create_backups=True)

    assert result.converted == result.backups_created == []
    assert len(result.failed) == 1
    assert source.read_bytes() == original
    assert victim.read_bytes() == b"other recording"
    assert destination.read_bytes() == (b"arrived after planning" if arrival == "file" else b"other recording")


def test_partial_backup_failure_leaves_source_and_removes_incomplete_backup(tmp_path, monkeypatch):
    source = tmp_path / "song.mid"
    original = _recording(60)
    source.write_bytes(original)

    def fail_copy(_source, destination):
        destination.write(b"partial backup")
        raise OSError("backup device failed")

    monkeypatch.setattr(file_backup.shutil, "copyfileobj", fail_copy)
    result = converter.convert_midi_files_to_type0([source], create_backups=True)

    assert result.converted == result.backups_created == []
    assert result.failed == [(str(source), "backup device failed")]
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == {source: original}
