"""Exercise the drag/drop dialog path, which bypasses main-window dialog setup."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from aps_midi_prep_tool_app import drop_table_widget
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_drop_progress_and_failures_use_selected_language(monkeypatch, tmp_path, code):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._lt = lambda source: translate_text(source, code)
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    detail = "Song {untouched} & title.mid"
    results = []
    progress = []

    def fail(_paths):
        raise OSError(detail)

    parent.prepare_regular_file_drop = fail
    parent.add_regular_file_from_drop = fail
    parent.finish_regular_file_drop = results.extend
    real_progress = drop_table_widget.QProgressDialog

    def capture_progress(*args):
        dialog = real_progress(*args)
        progress.append(dialog)
        return dialog

    monkeypatch.setattr(drop_table_widget, "QProgressDialog", capture_progress)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / f"song-{n}.mid")) for n in range(2)])
    event = SimpleNamespace(mimeData=lambda: mime, acceptProposedAction=lambda: None)
    table.dropEvent(event)

    assert len(progress) == 1
    dialog = progress[0]
    assert dialog.windowTitle() == translate_text("Adding Files", code)
    assert dialog.labelText() == translate_text("Adding files...", code)
    assert any(button.text() == translate_text("Cancel", code)
               for button in dialog.findChildren(QPushButton))
    assert len(results) == 3
    for result, source in zip(results, ["Could not prepare dropped files: {error}"] +
                              ["Could not add dropped file: {error}"] * 2):
        assert result["message"] == translate_text(source, code, error=detail)
        assert detail in result["message"]
        if code != "en":
            assert not result["message"].startswith("Could not")
    parent.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_drop_fallback_error_localizes_standard_button(monkeypatch, code):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._lt = lambda source: translate_text(source, code)
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    seen = []

    def inspect(dialog):
        assert dialog.windowTitle() == translate_text("Drop Failed", code)
        assert dialog.text() == translate_text("The dropped files could not be added.", code) + "\n\nraw diagnostic"
        assert dialog.button(drop_table_widget.QMessageBox.Ok).text() == translate_text("OK", code)
        seen.append(True)
        return drop_table_widget.QMessageBox.Ok

    monkeypatch.setattr(drop_table_widget.QMessageBox, "exec", inspect)
    table._show_drop_exception(parent, OSError("raw diagnostic"))
    assert seen == [True]
    parent.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_ambiguous_image_drop_warning_uses_selected_language(monkeypatch, tmp_path, code):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.currentLanguage = code
    parent._lt = lambda source: translate_text(source, code)
    opened, seen = [], []
    parent.load_image_file = opened.append
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    source = (
        "Only one disk image can be opened at a time. Drop it separately from other files.\n\n"
        "Nothing from this drop was imported. Extract ZIP files first, then "
        "drop one image or select the song files separately."
    )

    def inspect(dialog):
        assert dialog.icon() == drop_table_widget.QMessageBox.Warning
        assert dialog.windowTitle() == translate_text("Drop Failed", code)
        assert dialog.text() == translate_text(source, code)
        assert dialog.button(drop_table_widget.QMessageBox.Ok).text() == translate_text("OK", code)
        if code != "en":
            assert dialog.text() != source
        seen.append(True)
        return drop_table_widget.QMessageBox.Ok

    monkeypatch.setattr(drop_table_widget.QMessageBox, "exec", inspect)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / f"disk-{n}.img")) for n in range(2)])
    try:
        table.dropEvent(SimpleNamespace(mimeData=lambda: mime, acceptProposedAction=lambda: None))
        assert seen == [True]
        assert opened == []
    finally:
        parent.deleteLater()
        app.processEvents()
