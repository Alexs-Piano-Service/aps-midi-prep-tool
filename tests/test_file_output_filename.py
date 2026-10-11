"""Imported-file renames own the filename that folder exports actually use."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import main_window
from test_preparation_file_output import (  # noqa: F401
    _catalog_image, _digest, _load_floppy, _make_image, _settle_import, _song, destination_window,
)


def _stage_read_names(window):
    window.pendingFloppyReadLongFilenames = True
    window.pendingFloppyReadConvertToMidi = False
    assert window._apply_pending_floppy_read_long_filenames()


@pytest.mark.parametrize("eseq", (False, True))
def test_import_rename_replaces_automatic_export_name_and_undo_restores_it(
    destination_window, monkeypatch, tmp_path, eseq,
):
    window = destination_window("enspire")
    source_image, original_digest, _original_song = _catalog_image(tmp_path / "source", eseq=eseq)
    _load_floppy(window, source_image)
    _stage_read_names(window)
    row, source, kind, _filename = next(window._preparation_song_rows())
    assert kind == "midi"
    automatic_name = window.pendingImageExportFilenames[source]
    prompted = []

    def rename(current_name, **kwargs):
        prompted.append((current_name, kwargs["dialog_title"]))
        return "My chosen song.mid", True

    monkeypatch.setattr(window, "_prompt_for_image_filename", rename)
    window.edit_image_filename(row)

    assert prompted == [(automatic_name, "Rename File")]
    assert source not in window.pendingImageExportFilenames
    assert window.table.item(row, 3).text() == "My chosen song.mid"
    exported = window._export_image_session_files_to_folder(str(tmp_path / "renamed"))
    assert [Path(path).name for path in exported] == ["My chosen song.mid"]
    assert not (tmp_path / "renamed" / automatic_name).exists()

    window.undo_last_staged_batch()

    assert window.pendingImageExportFilenames[source] == automatic_name
    assert window.table.item(row, 3).text() == automatic_name
    restored = window._export_image_session_files_to_folder(str(tmp_path / "restored"))
    assert [Path(path).name for path in restored] == [automatic_name]
    assert _digest(source_image) == original_digest


def test_import_rename_to_original_filename_clears_automatic_export_name(
    destination_window, monkeypatch, tmp_path,
):
    window = destination_window("enspire")
    source_image, original_digest, _original_song = _catalog_image(tmp_path / "source")
    _load_floppy(window, source_image)
    _stage_read_names(window)
    row, source, _kind, _filename = next(window._preparation_song_rows())
    assert window.pendingImageExportFilenames[source] != "SONG.MID"
    monkeypatch.setattr(window, "_prompt_for_image_filename", lambda *_a, **_k: ("SONG.MID", True))

    window.edit_image_filename(row)

    assert source not in window.pendingImageExportFilenames
    assert window.table.item(row, 3).text() == "SONG.MID"
    exported = window._export_image_session_files_to_folder(str(tmp_path / "renamed"))
    assert [Path(path).name for path in exported] == ["SONG.MID"]
    assert _digest(source_image) == original_digest


@pytest.mark.parametrize("use_export_name", (False, True))
def test_import_rename_rejects_another_songs_filename(
    destination_window, monkeypatch, tmp_path, use_export_name,
):
    window = destination_window("enspire")
    songs = [tmp_path / "FIRST.MID", tmp_path / "SECOND.MID"]
    for path in songs:
        path.write_bytes(_song())
    source_image = _make_image(tmp_path, songs)
    original_digest = _digest(source_image)
    _load_floppy(window, source_image)
    _stage_read_names(window)
    rows = list(window._preparation_song_rows())
    row, source = rows[0][:2]
    other_source = rows[1][1]
    original_name = window.pendingImageExportFilenames[source]
    other_name = window.pendingImageExportFilenames[other_source]
    assert other_name != Path(window._final_image_path(other_source)).name
    conflict_name = other_name if use_export_name else Path(window._final_image_path(other_source)).name
    monkeypatch.setattr(window, "_prompt_for_image_filename", lambda *_a, **_k: (conflict_name, True))
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *_args: warnings.append(_args[2]))

    window.edit_image_filename(row)

    assert len(warnings) == 1
    assert conflict_name in warnings[0]
    assert window.table.item(row, 3).text() == original_name
    assert window.pendingImageExportFilenames[source] == original_name
    assert source not in window.pendingImageRenames
    exported = window._export_image_session_files_to_folder(str(tmp_path / "unchanged"))
    assert {Path(path).name for path in exported} == {original_name, other_name}
    assert _digest(source_image) == original_digest


def test_added_file_cannot_take_an_existing_imports_hidden_source_identity(
    destination_window, monkeypatch, tmp_path,
):
    window = destination_window("enspire")
    source_image, original_digest, original_song = _catalog_image(tmp_path / "source", catalog=False)
    _load_floppy(window, source_image)
    original_row, original_source, _kind, _name = next(window._preparation_song_rows())
    assert window._stage_image_filename_change(original_row, "Original renamed.mid")
    addition = tmp_path / "ADDED.MID"
    added_song = _song(note=72)
    addition.write_bytes(added_song)
    window.queue_image_additions([str(addition)])
    _settle_import()
    _stage_read_names(window)
    row, source, _kind, _name = next(
        entry for entry in window._preparation_song_rows() if entry[1] in window.pendingImageAdditions
    )
    original_export = window.pendingImageExportFilenames[original_source]
    added_export = window.pendingImageExportFilenames[source]
    monkeypatch.setattr(window, "_prompt_for_image_filename", lambda *_a, **_k: ("SONG.MID", True))
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *_args: warnings.append(_args[2]))

    window.edit_image_filename(row)

    assert len(warnings) == 1
    assert source in window.pendingImageAdditions
    assert original_source not in window.pendingImageAdditions
    assert Path(window._pending_or_extracted_image_path(original_source)).read_bytes() == original_song
    assert Path(window._pending_or_extracted_image_path(source)).read_bytes() == added_song
    exported = window._export_image_session_files_to_folder(str(tmp_path / "safe-export"))
    assert {Path(path).name for path in exported} == {original_export, added_export}
    assert (tmp_path / "safe-export" / original_export).read_bytes() == original_song
    assert (tmp_path / "safe-export" / added_export).read_bytes() == added_song
    assert _digest(source_image) == original_digest
