import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.pending_changes import staged_batch


def midi_bytes(title):
    encoded = title.encode("ascii")
    track = b"\x00\xff\x03" + bytes([len(encoded)]) + encoded + b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "test.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *args: settings)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *args, **kwargs: None)
    window = main_window.MidiTitleWindow()
    paths = [tmp_path / "one.mid", tmp_path / "two.mid"]
    for index, path in enumerate(paths):
        path.write_bytes(midi_bytes(f"  Song {index}  "))
    window._load_regular_files([str(path) for path in paths], "Loaded test files")
    yield window, paths
    window._clear_staging_history()
    window._cleanup_midi_scratch_dir()
    if window.image_session is not None:
        window.image_session.cleanup()
    window.deleteLater()
    app.processEvents()


def test_undo_restores_entire_title_batch_and_preserves_disk_files(window):
    w, paths = window
    before = [path.read_bytes() for path in paths]
    old_titles = [w._row_raw_title(row) for row in w._regular_file_rows()]
    w.trim_title_spaces_for_all(show_summary=False)
    assert len(w.pendingEdits) == 2
    assert w.editUndoAction.isEnabled()
    assert "2" in w.editReviewChangesAction.toolTip()

    w.undo_last_staged_batch()

    assert not w.pendingEdits
    assert not w.editUndoAction.isEnabled()
    assert [w._row_raw_title(row) for row in w._regular_file_rows()] == old_titles
    assert [path.read_bytes() for path in paths] == before


def test_discard_selected_title_preserves_other_edits_and_is_undoable(window):
    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    w.discard_staged_song_changes([str(paths[0])])
    assert set(w.pendingEdits) == {str(paths[1])}
    assert w._row_raw_title(0) == "  Song 0  "
    w.undo_last_staged_batch()
    assert set(w.pendingEdits) == {str(path) for path in paths}


def test_undo_retains_previous_staged_file_even_when_later_batch_removes_it(window, tmp_path):
    w, paths = window
    old_staged = tmp_path / "old_staged.mid"
    old_staged.write_bytes(midi_bytes("Previous conversion"))
    new_staged = tmp_path / "new_staged.mid"
    new_staged.write_bytes(midi_bytes("Next conversion"))
    source = str(paths[0])
    w._apply_regular_row_pending_conversion(0, source, "one.mid", str(old_staged), "midi_type0", overwrite_original=True)

    @staged_batch
    def next_batch(window):
        window._apply_regular_row_pending_conversion(0, source, "one.mid", str(new_staged), "midi_type0", overwrite_original=True)
        old_staged.unlink()

    next_batch(w)
    w.undo_last_staged_batch()
    restored = Path(w.pendingRegularConversions[source]["temp_path"])
    assert restored.read_bytes() == midi_bytes("Previous conversion")
    assert paths[0].read_bytes() == midi_bytes("  Song 0  ")


def test_successful_save_invalidates_staged_undo_and_default_backups_preserve_original(window):
    w, paths = window
    original = paths[0].read_bytes()
    assert w.backup_checkbox.isChecked()
    w.trim_title_spaces_for_all(show_summary=False)
    w.save_pending_changes()
    assert not w.pendingEdits
    assert not w.editUndoAction.isEnabled()
    assert (paths[0].parent / "backup" / paths[0].name).read_bytes() == original
    assert paths[0].read_bytes() != original


def test_review_shows_original_and_proposed_title_without_materializing_edits(window):
    w, paths = window
    original = paths[0].read_bytes()
    w.trim_title_spaces_for_all(show_summary=False)
    rows = w._pending_review_rows()
    assert rows[0][1] == "one.mid\n  Song 0  "
    assert rows[0][2] == "one.mid\nSong 0"
    assert paths[0].read_bytes() == original


def test_save_destination_and_disabled_reason_are_visible(window):
    w, paths = window
    w.saveButton.setEnabled(False)
    w.saveButton.setToolTip("Write protection is enabled")
    w._refresh_pending_changes_ui()
    assert str(paths[0].parent) in w.saveDestinationLabel.text()
    assert "Write protection is enabled" in w.saveDestinationLabel.text()


@pytest.mark.parametrize("cancel_after", [0, 1, 2])
def test_save_as_cancellation_keeps_original_context_and_skips_followup_metadata(window, monkeypatch, tmp_path, cancel_after):
    w, paths = window
    output = tmp_path / "export"
    w.trim_title_spaces_for_all(show_summary=False)
    pending = dict(w.pendingEdits)
    originals = [path.read_bytes() for path in paths]
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *args: str(output))
    monkeypatch.setattr(w, "_prepare_progress_dialog", lambda *args: None)

    class Progress:
        def __init__(self, *args):
            self.value = 0
        def setAutoReset(self, value):
            pass
        def setAutoClose(self, value):
            pass
        def setValue(self, value):
            self.value = value
        def wasCanceled(self):
            return self.value >= cancel_after
        def close(self):
            pass

    monkeypatch.setattr(main_window, "QProgressDialog", Progress)
    def unexpected(*args, **kwargs):
        pytest.fail("Cancellation continued into metadata writing or context reload")
    monkeypatch.setattr(w, "_write_tag_sidecars_for_regular_rows", unexpected)
    monkeypatch.setattr(w, "_write_metadata_summary_for_regular_rows", unexpected)
    monkeypatch.setattr(w, "_load_regular_files", unexpected)
    w.save_as_changes()
    assert w.pendingEdits == pending
    assert [path.read_bytes() for path in paths] == originals
    assert len(list(output.glob("*.mid"))) == cancel_after


def test_save_as_declining_source_overwrite_preserves_pending_edits(window, monkeypatch):
    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    originals = [path.read_bytes() for path in paths]
    errors = []
    questions = []
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *args: str(paths[0].parent))
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *args: (
        questions.append(args) or main_window.QMessageBox.No
    ))
    monkeypatch.setattr(w, "_show_error_list", lambda *args, **kwargs: errors.append(args))
    w.save_as_changes()
    assert len(questions) == 1 and not errors
    assert questions[0][4] == main_window.QMessageBox.No
    assert len(w.pendingEdits) == 2 and w.editUndoAction.isEnabled()
    assert [path.read_bytes() for path in paths] == originals
    assert not (paths[0].parent / "backup").exists()


def test_save_as_rejects_duplicate_output_names(window, monkeypatch, tmp_path):
    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    output = tmp_path / "export"
    w._stage_regular_row_pending_rename(1, str(paths[1]), paths[0].name)
    originals = [path.read_bytes() for path in paths]
    errors = []
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *args: str(output))
    monkeypatch.setattr(w, "_show_error_list", lambda *args, **kwargs: errors.append(args))
    w.save_as_changes()
    assert errors
    assert len(w.pendingEdits) == 2
    assert [path.read_bytes() for path in paths] == originals
    assert not list(output.iterdir())


def test_many_save_destinations_show_common_folder_and_count(window, tmp_path):
    w, _paths = window
    w.listedFileInfo = {str(tmp_path / f"Album {index}" / "song.mid"): {} for index in range(200)}

    w._refresh_pending_changes_ui()

    assert f"{tmp_path} (200 folders)" in w.saveDestinationLabel.text()
    assert "Album 199" not in w.saveDestinationLabel.text()
    assert "Album 199" in w.saveDestinationLabel.toolTip()


def test_review_retains_other_rows_when_one_original_cannot_be_read(window, monkeypatch):
    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    real_probe = w._probe_regular_file

    def probe(path):
        if path == str(paths[0]):
            raise OSError("Source is temporarily unavailable")
        return real_probe(path)

    monkeypatch.setattr(w, "_probe_regular_file", probe)

    rows = w._pending_review_rows()

    assert len(rows) == 2
    assert "temporarily unavailable" in rows[0][4]
    assert rows[1][1] == "two.mid\n  Song 1  "


def test_review_displays_original_format_and_serialized_conversion_details(window):
    w, paths = window
    source = str(paths[0])
    w.pendingRegularConversions[source] = {"change_report": {"notes_removed": 2}, "target_filename": "ONE.FIL"}
    w.listedFileInfo[source]["midi_type"] = "FIL"
    w.listedFileInfo[source]["title_mode"] = "eseq"

    row = w._pending_review_rows()[0]

    assert "→ FIL" in row[3]
    assert '"notes_removed": 2' in row[4]


def test_discard_selected_reorder_restores_its_slot_and_preserves_other_edits(window, tmp_path):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_file_to_eseq_path
    from aps_midi_prep_tool_app.eseq_pianodir import update_eseq_order_key

    w, sources = window
    paths = []
    for index, name in enumerate(("FIRST.FIL", "SECOND.FIL", "THIRD.FIL"), start=1):
        path = tmp_path / name
        convert_midi_file_to_eseq_path(sources[0], path)
        update_eseq_order_key(path, f"{index:03d}")
        paths.append(str(path))
    w._load_regular_files(paths, "E-SEQ order test")
    w.table.setSortingEnabled(False)
    slots = w._regular_eseq_rows()
    w._move_table_row(slots[2], slots[0])
    w._move_table_row(slots[2], slots[1])
    w.pendingEdits[paths[1]] = "Keep this title"
    assert [w.table.item(row, 1).text() for row in slots] == paths[::-1]
    before_review = {row[0]: row for row in w._pending_review_rows()}
    assert "Playback order: 1 → 3." in before_review[paths[0]][4]

    w.discard_staged_song_changes([paths[0]])

    assert [w.table.item(row, 1).text() for row in w._regular_eseq_rows()] == [paths[0], paths[2], paths[1]]
    assert paths[0] not in w._regular_eseq_order_key_edits()
    assert w.pendingEdits == {paths[1]: "Keep this title"}
    assert set(w._regular_eseq_order_key_edits()) == {paths[1], paths[2]}


def test_album_only_batch_is_undoable(window):
    w, _paths = window
    original = w.imagePianodirTitleEdit.text()

    @staged_batch
    def change_album(window):
        window.imagePianodirTitleEdit.setText("Changed album")

    change_album(w)
    assert w._staged_undo
    w.undo_last_staged_batch()
    assert w.imagePianodirTitleEdit.text() == original


def test_edit_menu_undo_reverses_title_and_filename_dialog_edits_one_at_a_time(window, monkeypatch):
    w, paths = window
    original = w._row_raw_title(0)
    monkeypatch.setattr(w, "_prompt_for_title", lambda *args, **kwargs: ("Edited title", True))
    monkeypatch.setattr(w, "_prompt_for_image_filename", lambda *args, **kwargs: ("RENAMED.MID", True))

    w.edit_via_dialog(0)
    w.edit_regular_filename(0)

    assert len(w._staged_undo_stack) == 2
    assert w.pendingRegularRenames[str(paths[0])] == "RENAMED.MID"
    assert "Ctrl+Z" in w.editUndoAction.text()
    w.editUndoAction.trigger()
    assert not w.pendingRegularRenames
    assert w.pendingEdits[str(paths[0])] == "Edited title"
    assert w.table.item(0, 3).text() == paths[0].name
    assert w.editUndoAction.isEnabled()
    w.editUndoAction.trigger()
    assert not w.pendingEdits
    assert w._row_raw_title(0) == original
    assert not w.editUndoAction.isEnabled()
    assert not w.editUndoAllAction.isEnabled()
    assert not hasattr(w, "pendingChangesButton")
    assert not hasattr(w, "undoBatchButton")


def test_undo_all_restores_first_unsaved_state_and_does_not_rewrite_sources(window, monkeypatch):
    w, paths = window
    originals = [path.read_bytes() for path in paths]
    original_titles = [w._row_raw_title(row) for row in w._regular_file_rows()]
    w.trim_title_spaces_for_all(show_summary=False)
    monkeypatch.setattr(w, "_prompt_for_image_filename", lambda *args, **kwargs: ("RENAMED.MID", True))
    w.edit_regular_filename(0)
    w.discard_staged_song_changes([str(paths[1])])
    snapshot_directories = [Path(snapshot["assets"].name) for snapshot in w._staged_undo_stack]

    w.editUndoAllAction.trigger()

    assert not w.pendingEdits
    assert not w.pendingRegularRenames
    assert [w._row_raw_title(row) for row in w._regular_file_rows()] == original_titles
    assert [path.read_bytes() for path in paths] == originals
    assert not w._can_undo_staged_changes()
    assert all(not directory.exists() for directory in snapshot_directories[1:])
    w._clear_staging_history()
    assert not snapshot_directories[0].exists()


def test_multistep_conversion_undo_keeps_material_after_scratch_files_are_removed(window, tmp_path):
    w, paths = window
    source = str(paths[0])
    originals = paths[0].read_bytes()

    @staged_batch
    def convert(window, title, previous=None):
        output = tmp_path / (title + ".mid")
        output.write_bytes(midi_bytes(title))
        window._apply_regular_row_pending_conversion(0, source, paths[0].name, str(output), "midi_type0", overwrite_original=True)
        if previous is not None:
            previous.unlink()
        return output

    first = convert(w, "First")
    second = convert(w, "Second", first)
    convert(w, "Third", second)

    w.undo_last_staged_batch()
    assert Path(w.pendingRegularConversions[source]["temp_path"]).read_bytes() == midi_bytes("Second")
    w.undo_last_staged_batch()
    assert Path(w.pendingRegularConversions[source]["temp_path"]).read_bytes() == midi_bytes("First")
    w.undo_all_staged_changes()
    assert not w.pendingRegularConversions
    assert paths[0].read_bytes() == originals


def test_save_establishes_new_undo_all_baseline(window, monkeypatch):
    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    w.save_pending_changes()
    saved = paths[0].read_bytes()
    assert not w._staged_undo_stack
    monkeypatch.setattr(w, "_prompt_for_title", lambda *args, **kwargs: ("After save", True))
    w.edit_via_dialog(0)
    assert w.pendingEdits[str(paths[0])] == "After save"

    w.undo_all_staged_changes()

    assert not w.pendingEdits
    assert w._row_raw_title(0) == "Song 0"
    assert paths[0].read_bytes() == saved


@pytest.mark.parametrize("interruption", ("failure", "cancel"))
@pytest.mark.parametrize("edit_after_save", (False, True))
def test_undo_all_after_partial_save_discards_only_unfinished_changes(window, monkeypatch, interruption, edit_after_save):
    w, paths = window
    second_original = paths[1].read_bytes()
    w.trim_title_spaces_for_all(show_summary=False)
    monkeypatch.setattr(w, "_show_error_list", lambda *args, **kwargs: None)
    if interruption == "failure":
        real_update = main_window.update_midi_title
        monkeypatch.setattr(main_window, "update_midi_title", lambda path, title:
                            "Injected second-file failure" if path == str(paths[1]) else real_update(path, title))
    else:
        class Progress:
            def __init__(self, *args):
                self.value = 0
            def setAutoReset(self, value):
                pass
            def setAutoClose(self, value):
                pass
            def setValue(self, value):
                self.value = value
            def wasCanceled(self):
                return self.value >= 1
            def close(self):
                pass
        monkeypatch.setattr(main_window, "QProgressDialog", Progress)
        monkeypatch.setattr(w, "_prepare_progress_dialog", lambda *args: None)

    w.save_pending_changes()

    assert w.pendingEdits == {str(paths[1]): "Song 1"}
    assert not w.editUndoAction.isEnabled()
    assert w.editUndoAllAction.isEnabled()
    first_saved = paths[0].read_bytes()
    if edit_after_save:
        monkeypatch.setattr(w, "_prompt_for_title", lambda *args, **kwargs: ("New unsaved edit", True))
        w.edit_via_dialog(0)
    w.editUndoAllAction.trigger()
    assert not w.pendingEdits
    assert w._row_raw_title(0) == "Song 0"
    assert w._row_raw_title(1) == "  Song 1  "
    assert paths[0].read_bytes() == first_saved
    assert paths[1].read_bytes() == second_original
    assert not w.editUndoAllAction.isEnabled()


def test_undo_all_after_partial_save_keeps_edits_when_original_cannot_be_read(window, monkeypatch):
    from aps_midi_prep_tool_app import pending_changes

    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    real_update = main_window.update_midi_title
    monkeypatch.setattr(main_window, "update_midi_title", lambda path, title:
                        "Injected second-file failure" if path == str(paths[1]) else real_update(path, title))
    monkeypatch.setattr(w, "_show_error_list", lambda *args, **kwargs: None)
    w.save_pending_changes()
    monkeypatch.setattr(w, "_prompt_for_title", lambda *args, **kwargs: ("Another pending title", True))
    w.edit_via_dialog(0)
    pending = dict(w.pendingEdits)
    source_bytes = [path.read_bytes() for path in paths]
    real_probe = w._probe_regular_file

    def unavailable(path):
        if path == str(paths[1]):
            raise OSError("Source temporarily unavailable")
        return real_probe(path)

    monkeypatch.setattr(w, "_probe_regular_file", unavailable)
    warnings = []
    monkeypatch.setattr(pending_changes.QMessageBox, "warning", lambda *args: warnings.append(args))

    w.undo_all_staged_changes()

    assert w.pendingEdits == pending
    assert w._row_raw_title(0) == "Another pending title"
    assert w._staged_undo_stack
    assert [path.read_bytes() for path in paths] == source_bytes
    assert warnings and "Pending edits were kept" in warnings[0][2]
    assert "Could not undo all changes" in w.status_label.text()


def test_rename_only_save_clears_history_and_keeps_saved_names_when_undo_all_is_called(window, monkeypatch):
    w, paths = window
    original = paths[0].read_bytes()
    monkeypatch.setattr(w, "_prompt_for_image_filename", lambda *args, **kwargs: ("RENAMED.MID", True))
    w.edit_regular_filename(0)

    w.save_pending_changes()
    w.undo_all_staged_changes()

    renamed = paths[0].with_name("RENAMED.MID")
    assert renamed.read_bytes() == original
    assert not paths[0].exists()
    assert str(renamed) in w.listedFileInfo
    assert not w._staged_undo_stack
    assert not w._can_undo_all_staged_changes()


def test_catalog_only_save_clears_undo_history_without_reverting_saved_album(window, tmp_path):
    from types import SimpleNamespace
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_file_to_eseq_path
    from aps_midi_prep_tool_app.eseq_pianodir import (
        PianodirMetadata, build_pianodir_bytes, read_pianodir_metadata_from_file,
    )

    w, sources = window
    song = tmp_path / "SONG.FIL"
    directory = tmp_path / "PIANODIR.FIL"
    convert_midi_file_to_eseq_path(sources[0], song)
    directory.write_bytes(build_pianodir_bytes(
        [SimpleNamespace(image_path=song.name, local_path=song)],
        PianodirMetadata(disk_title="Original Album"),
    ))
    w._load_regular_files([str(song), str(directory)], "Catalog-only save", prepare_destination=False)

    @staged_batch
    def edit_album(window):
        window.imagePianodirTitleEdit.setText("Saved Album")

    edit_album(w)
    assert w._staged_undo_stack
    w.save_pending_changes()
    saved_directory = directory.read_bytes()

    w.undo_all_staged_changes()

    assert read_pianodir_metadata_from_file(directory).disk_title == "Saved Album"
    assert w.imagePianodirTitleEdit.text() == "Saved Album"
    assert directory.read_bytes() == saved_directory
    assert not w._staged_undo_stack
    assert not w._can_undo_all_staged_changes()


def test_undo_menu_and_failure_translations_cover_every_supported_language():
    from string import Formatter
    from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr, translate_text
    from aps_midi_prep_tool_app.pending_changes_translations import PENDING_CHANGE_MESSAGES

    codes = {language.code for language in SUPPORTED_LANGUAGES}
    for key in ("undo", "undo_all", "review", "undone", "all_undone", "undo_all_failed"):
        translations = PENDING_CHANGE_MESSAGES["pending." + key]
        assert set(translations) == codes
        fields = {field for _text, field, _spec, _conversion in Formatter().parse(translations["en"]) if field}
        for language, text in translations.items():
            assert {field for _text, field, _spec, _conversion in Formatter().parse(text) if field} == fields
            assert tr("pending." + key, language, error="test error") == text.format(error="test error")
    for language in codes:
        for source, key in (("Undo", "undo"), ("Undo All", "undo_all"), ("Review Changes", "review")):
            assert translate_text(source, language) == PENDING_CHANGE_MESSAGES["pending." + key][language]


def test_metadata_typing_and_catalog_edits_participate_in_menu_undo(window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtTest import QTest

    w, _paths = window
    album, catalog = w.imagePianodirTitleEdit, w.imagePianodirCatalogEdit
    original_album, original_catalog = album.text(), catalog.text()
    QApplication.sendEvent(album, QFocusEvent(QEvent.FocusIn))
    QTest.keyClicks(album, "New album")
    assert w.editUndoAction.isEnabled()  # Current field need not lose focus first.
    album.editingFinished.emit()
    QApplication.sendEvent(catalog, QFocusEvent(QEvent.FocusIn))
    QTest.keyClicks(catalog, "1234")
    catalog.editingFinished.emit()

    w.editUndoAction.trigger()
    assert catalog.text() == original_catalog
    assert album.text() == original_album + "New album"
    w.editUndoAllAction.trigger()
    assert album.text() == original_album
    assert catalog.text() == original_catalog
    assert not w._can_undo_staged_changes()


def test_undo_menu_commits_and_reverses_current_metadata_typing(window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtTest import QTest

    w, _paths = window
    editor = w.imagePianodirTitleEdit
    original = editor.text()
    QApplication.sendEvent(editor, QFocusEvent(QEvent.FocusIn))
    QTest.keyClicks(editor, "In progress")

    w.editUndoAction.trigger()

    assert editor.text() == original
    assert not w._can_undo_staged_changes()


def test_undo_actions_are_disabled_during_background_work_and_history_resets_on_reload(window):
    w, paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    snapshot_directories = [Path(snapshot["assets"].name) for snapshot in w._staged_undo_stack]
    w.choose_button.setEnabled(False)
    w._refresh_pending_changes_ui()
    assert not w.editUndoAction.isEnabled()
    assert not w.editUndoAllAction.isEnabled()
    assert not w.editReviewChangesAction.isEnabled()
    w.choose_button.setEnabled(True)

    w._load_regular_files([str(path) for path in paths], "Reloaded")

    assert not w._can_undo_staged_changes()
    assert all(not directory.exists() for directory in snapshot_directories)


def _load_catalog_image(w, tmp_path):
    from aps_midi_prep_tool_app import floppy_image
    from aps_midi_prep_tool_app.smart_pianosoft import build_smart_pianosoft_song_record

    image = tmp_path / "catalog.img"
    disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    floppy_image.create_blank_floppy_image(image, disk_format)
    names = ("FIRST.MID", "SECOND.MID", "THIRD.MID")
    records = []
    for index, name in enumerate(names, start=1):
        path = tmp_path / name
        path.write_bytes(midi_bytes(f"Embedded {index}"))
        floppy_image._copy_host_file_into_image(image, path, name)
        records.append(build_smart_pianosoft_song_record(name, f"Catalog {index}"))
    header = bytearray(b" " * 0x80)
    header[:16] = b"PSONG   MNG   \r\n"
    header[16:32] = b"MAX003        \r\n"
    header[32:48] = b"FILE003       \r\n"
    catalog = tmp_path / "PSONG.MNG"
    catalog.write_bytes(bytes(header) + b"".join(records))
    floppy_image._copy_host_file_into_image(image, catalog, catalog.name)
    session = floppy_image.FloppyImageSession.load(image)
    w._activate_disk_session(session, session.list_entries())
    return names


def test_discard_catalog_title_keeps_catalog_routing_and_other_song_edits(window, tmp_path):
    from aps_midi_prep_tool_app.smart_pianosoft import parse_smart_pianosoft_song_catalog

    w, paths = window
    first, second, _third = _load_catalog_image(w, tmp_path)
    w._stage_image_title_edit(first, "Changed first")
    w._stage_image_title_edit(second, "Changed second")

    w.discard_staged_song_changes([first])

    assert w.imageFileInfo[first]["title"] == "Catalog 1"
    assert w._image_title_is_smart_pianosoft_catalog_backed(first)
    assert w.pendingSmartPianoSoftTitleEdits == {second: "Changed second"}
    catalog = Path(w.pendingImageReplacements["PSONG.MNG"]).read_bytes()
    assert [song.title for song in parse_smart_pianosoft_song_catalog(catalog)] == ["Catalog 1", "Changed second", "Catalog 3"]
    assert w._stage_image_title_edit(first, "Edited again") == "smart_pianosoft_catalog"
    assert first not in w.pendingImageTitleEdits


def test_discard_deleted_image_song_restores_original_slot_and_catalog_title(window, tmp_path, monkeypatch):
    w, paths = window
    names = _load_catalog_image(w, tmp_path)
    monkeypatch.setattr(w, "_confirm_with_optional_skip", lambda **kwargs: True)
    row = next(row for row in range(w.table.rowCount()) if w.table.item(row, 1).text() == names[0])
    w.remove_image_row(row)
    assert names[0] in w.pendingImageDeletes

    w.discard_staged_song_changes([names[0]])

    visible = [w.table.item(row, 1).text() for row in range(w.table.rowCount()) if w.table.item(row, 1).text() in names]
    assert visible == list(names)
    assert names[0] not in w.pendingImageDeletes
    assert w.imageFileInfo[names[0]]["title"] == "Catalog 1"
    assert w._image_title_is_smart_pianosoft_catalog_backed(names[0])
    w.undo_last_staged_batch()
    assert names[0] in w.pendingImageDeletes
