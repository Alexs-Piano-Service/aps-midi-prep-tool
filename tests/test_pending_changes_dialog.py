"""User-visible review, undo, and stable resizing of the changes dialog."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.pending_changes import staged_batch
from aps_midi_prep_tool_app.pending_changes_dialog import PendingChangesDialog


def _midi(title):
    title = title.encode("ascii")
    track = (b"\x00\xff\x03" + bytes([len(title)]) + title
             + b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00")
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk"
            + len(track).to_bytes(4, "big") + track)


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "review.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    w = main_window.MidiTitleWindow()
    paths = [tmp_path / name for name in ("first.mid", "second.mid", "third.mid")]
    for index, path in enumerate(paths):
        path.write_bytes(_midi(f"  Song <b>{index}</b>  "))
    w._load_regular_files([str(path) for path in paths], "Review test", prepare_destination=False)
    yield w, paths
    w._clear_staging_history()
    w._cleanup_midi_scratch_dir()
    w.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("change", ["title", "filename", "both"])
def test_review_identifies_simple_edits_and_shows_first_comparison_immediately(window, change):
    w, paths = window
    before = [path.read_bytes() for path in paths]
    if change in ("title", "both"):
        w.pendingEdits[str(paths[0])] = "Edited title"
    if change in ("filename", "both"):
        w._stage_regular_row_pending_rename(0, str(paths[0]), "renamed.mid")

    dialog = PendingChangesDialog(w)
    try:
        assert dialog.table.rowCount() == 1
        assert dialog.selected_rows() == [0]
        assert dialog.discard_button.isEnabled()
        summary = dialog.table.item(0, 2).text()
        if change in ("title", "both"):
            assert w._pending_text("title_changed") in summary
            assert "Edited title" in dialog.details.toPlainText()
        if change in ("filename", "both"):
            assert w._pending_text("filename_changed") in summary
            assert dialog.table.item(0, 1).text() == "renamed.mid"
            assert "renamed.mid" in dialog.details.toPlainText()
        assert "first.mid" in dialog.details.toPlainText()
        assert "Song <b>0</b>" in dialog.details.toPlainText()
        assert [path.read_bytes() for path in paths] == before
        assert not (paths[0].parent / "renamed.mid").exists()
    finally:
        dialog.close()


def test_comparison_keeps_literal_markup_and_exact_title_spacing(window):
    w, paths = window
    title = "  New <img src='missing'> & <b>literal</b>  "
    w.pendingEdits[str(paths[0])] = title
    filename = "<img src='missing'> & renamed.mid"
    w._stage_regular_row_pending_rename(0, str(paths[0]), filename)

    dialog = PendingChangesDialog(w)
    try:
        plain = dialog.details.toPlainText()
        assert "  Song <b>0</b>  " in plain
        assert title in plain
        assert filename in plain
        assert dialog.details.document().find("<img src='missing'>").hasSelection()
    finally:
        dialog.close()


def test_discard_and_undo_refresh_selection_without_writing_sources(window):
    w, paths = window
    before = [path.read_bytes() for path in paths]
    w.trim_title_spaces_for_all(show_summary=False)
    dialog = PendingChangesDialog(w)
    try:
        dialog.table.selectRow(1)
        dialog.discard_button.click()

        assert str(paths[1]) not in w.pendingEdits
        assert dialog.table.rowCount() == 2
        assert dialog.selected_rows() == [1]
        assert "third.mid" in dialog.details.toPlainText()
        assert "second.mid" not in dialog.details.toPlainText()

        dialog.undo_button.click()

        assert set(w.pendingEdits) == {str(path) for path in paths}
        assert dialog.table.rowCount() == 3
        assert dialog.selected_rows() == [2]
        assert "third.mid" in dialog.details.toPlainText()
        assert [path.read_bytes() for path in paths] == before
    finally:
        dialog.close()


def test_multiple_selected_songs_show_their_own_comparisons(window):
    w, _paths = window
    w.trim_title_spaces_for_all(show_summary=False)
    dialog = PendingChangesDialog(w)
    try:
        dialog.table.selectAll()
        plain = dialog.details.toPlainText()
        for index, name in enumerate(("first.mid", "second.mid", "third.mid")):
            assert name in plain
            assert f"  Song <b>{index}</b>  " in plain
        dialog.table.clearSelection()
        assert not dialog.discard_button.isEnabled()
        assert dialog.details.toPlainText() == w._pending_text("select_song")
    finally:
        dialog.close()


@pytest.mark.parametrize("catalog_only", [False, True])
def test_empty_review_explains_song_or_catalog_state_and_keeps_undo_available(window, catalog_only):
    w, _paths = window
    if catalog_only:
        @staged_batch
        def stage_catalog(window):
            window.pendingGeneratePianodir = True

        stage_catalog(w)
    dialog = PendingChangesDialog(w)
    try:
        assert dialog.table.rowCount() == 0
        assert not dialog.discard_button.isEnabled()
        assert dialog.summary.text() == w._pending_text("catalog" if catalog_only else "none")
        assert dialog.details.toPlainText() == dialog.summary.text()
        assert dialog.undo_button.isEnabled() is catalog_only
        if catalog_only:
            dialog.undo_button.click()
            assert dialog.summary.text() == w._pending_text("none")
            assert not dialog.undo_button.isEnabled()
    finally:
        dialog.close()


def test_long_filenames_and_report_text_remain_accessible_in_small_review(window, monkeypatch):
    w, _paths = window
    filename = "LongSongName" * 20 + ".mid"
    title = "Long title " * 40
    report = "E-SEQ → MIDI Type 0\n" + "Complete musical comparison " * 40
    monkeypatch.setattr(w, "_pending_review_rows", lambda: [
        ("/source/" + filename, "original.fil\n" + title,
         filename + "\n" + title, "FIL → Type 0", report),
    ])
    dialog = PendingChangesDialog(w)
    try:
        dialog.resize(700, 520)
        dialog.show()
        QApplication.processEvents()
        assert filename in dialog.table.item(0, 1).toolTip()
        assert filename in dialog.details.toPlainText()
        assert title in dialog.details.toPlainText()
        assert report in dialog.details.toPlainText()
        assert dialog.rect().contains(dialog.buttons.geometry())
        assert dialog.details.horizontalScrollBar().maximum() == 0
    finally:
        dialog.close()


@pytest.mark.parametrize("font_size", [9, 14])
def test_live_review_keeps_user_geometry_through_resizing_and_selection(window, monkeypatch, font_size):
    w, _paths = window
    app = QApplication.instance()
    original_font = QFont(app.font())
    app.setFont(QFont(original_font.family(), font_size))
    w.setFont(QFont(original_font.family(), font_size))
    w.trim_title_spaces_for_all(show_summary=False)
    execute = w._exec_child_dialog
    failures = []

    def inspect(dialog, **kwargs):
        assert kwargs.get("resize_to_contents") is False
        assert dialog.font().pointSize() == font_size

        def resize_and_select():
            try:
                assert len(dialog.findChildren(QDialogButtonBox)) == 1
                minimum = dialog.minimumSizeHint()
                for width, height in ((1180, 800), (700, 520), (960, 680)):
                    size = max(width, minimum.width()), max(height, minimum.height())
                    dialog.resize(*size)
                    dialog.move(20, 30)
                    QTest.qWait(130)
                    assert dialog.size().toTuple() == size
                    assert dialog.pos().toTuple() == (20, 30)
                    geometry = dialog.geometry()
                    for row in (2, 0):
                        dialog.table.selectRow(row)
                        QTest.qWait(130)
                        assert dialog.geometry() == geometry
                        assert dialog.rect().contains(dialog.buttons.geometry())
                        assert dialog.splitter.geometry().bottom() < dialog.buttons.geometry().top()
                        assert dialog.table.geometry().bottom() < dialog.details.geometry().top()
                        assert dialog.details.height() >= dialog.details.minimumHeight()
                        assert dialog.discard_button.isVisible()
                        assert dialog.undo_button.isVisible()
                        assert dialog.buttons.button(QDialogButtonBox.Close).isVisible()
            except BaseException as exc:
                failures.append(exc)
            finally:
                dialog.reject()

        QTimer.singleShot(150, resize_and_select)
        return execute(dialog, **kwargs)

    monkeypatch.setattr(w, "_exec_child_dialog", inspect)
    try:
        w.show_pending_changes()
    finally:
        app.setFont(original_font)
    if failures:
        raise failures[0]
    app.processEvents()


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_translated_buttons_fit_large_font_and_follow_user_resizing(window, monkeypatch, language):
    w, _paths = window
    app = QApplication.instance()
    original_font = QFont(app.font())
    app.setFont(QFont(original_font.family(), 14))
    w.currentLanguage = language
    w.trim_title_spaces_for_all(show_summary=False)
    w.setFont(QFont(w.font().family(), 14))
    execute = w._exec_child_dialog
    failures = []

    def inspect(dialog, **kwargs):
        assert dialog.font().pointSize() == 14

        def assert_buttons_fit():
            assert dialog.rect().contains(dialog.buttons.geometry())
            buttons = dialog.buttons.buttons()
            for index, button in enumerate(buttons):
                assert button.isVisible()
                assert dialog.buttons.rect().contains(button.geometry())
                assert button.width() >= button.minimumSizeHint().width()
                assert button.width() >= button.sizeHint().width()
                for other in buttons[index + 1:]:
                    assert not button.geometry().intersects(other.geometry())

        def resize_and_inspect():
            try:
                available = dialog.screen().availableGeometry()
                assert dialog.width() <= available.width()
                assert dialog.height() <= available.height()
                assert_buttons_fit()
                for width, height in ((1180, 800), (700, 520), (960, 680)):
                    dialog.resize(width, height)
                    dialog.move(20, 30)
                    QTest.qWait(130)
                    assert dialog.size().toTuple() == (width, height)
                    assert dialog.pos().toTuple() == (20, 30)
                    assert_buttons_fit()
            except BaseException as exc:
                failures.append(exc)
            finally:
                dialog.reject()

        QTimer.singleShot(150, resize_and_inspect)
        return execute(dialog, **kwargs)

    monkeypatch.setattr(w, "_exec_child_dialog", inspect)
    try:
        w.show_pending_changes()
    finally:
        app.setFont(original_font)
    if failures:
        raise failures[0]
    app.processEvents()
