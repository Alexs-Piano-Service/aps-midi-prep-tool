"""Rendered localization contracts for populated preparation/review dialogs."""

import os
import struct
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication, QDialogButtonBox, QLabel, QMessageBox, QTableWidget, QTabWidget, QTextEdit, QWidget,
)

from aps_midi_prep_tool_app import bulk_extraction_job, main_window
from aps_midi_prep_tool_app.conversion_review import compare_music_bytes
from aps_midi_prep_tool_app.emulator_image_builder import (
    EmulatorAlbumPreview, EmulatorBuildPreview, EmulatorDiskPreview, _PreparedSong,
)
from aps_midi_prep_tool_app.emulator_preview_dialog import EmulatorPreviewDialog, localize_emulator_warning
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr, translate_text
from aps_midi_prep_tool_app.pending_changes import PendingChangesMixin


LANGUAGES = [language.code for language in SUPPORTED_LANGUAGES]
RECOVERY_WARNING = (
    "Source contains [BAD SECTOR] recovery filler. MIDI bytes were preserved unchanged; "
    "missing data was not repaired and playback may fail."
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _midi(file_type):
    track = b"\x00\x90\x3C\x40\x60\x80\x3C\x00\x00\xFF\x2F\x00"
    tracks = 2 if file_type == 2 else 1
    return struct.pack(">4sIHHH", b"MThd", 6, file_type, tracks, 96) + (
        b"MTrk" + len(track).to_bytes(4, "big") + track
    ) * tracks


def _report_heading(language):
    before = translate_text(
        "{format} (independent sequences, total)", language,
        format=translate_text("MIDI Type {type}", language, type=2),
    )
    return before + " → " + translate_text("MIDI Type {type}", language, type=0)


@pytest.mark.parametrize("language", LANGUAGES)
def test_populated_emulator_preview_localizes_recovery_warning_and_type_two_report(application, tmp_path, language):
    source = tmp_path / "source"
    album = source / "album"
    output = tmp_path / "output"
    report = compare_music_bytes(_midi(2), _midi(0))
    songs = (
        _PreparedSong(str(album / "source.mid"), "SONG.MID", "unused", "Original title", warning=RECOVERY_WARNING),
        _PreparedSong(str(album / "sequences.mid"), "SEQUENCE.MID", "unused", "Sequence title", conversion_report=report),
    )
    prefix = "DISK001.IMG / SONG.MID (album/source.mid): "
    preview = EmulatorBuildPreview(
        str(source), str(output),
        (EmulatorDiskPreview(str(output / "DISK001.IMG"), 600000, songs),),
        (EmulatorAlbumPreview(str(album), "Album title", 2, True, "Folder name"),),
        (prefix + RECOVERY_WARNING,), {}, {}, "midi", "folders",
    )
    parent = QWidget()
    parent._language_code = lambda: language
    dialog = EmulatorPreviewDialog(preview, parent)
    try:
        dialog.show()
        dialog.findChild(QTabWidget).setCurrentIndex(2)
        dialog.songs_table.setCurrentCell(0, 0)
        application.processEvents()
        expected_warning = translate_text(RECOVERY_WARNING, language)
        assert dialog.report_text.toPlainText() == expected_warning
        assert prefix + expected_warning in {label.text() for label in dialog.findChildren(QLabel)}
        assert dialog.songs_table.item(0, 3).text() == "Original title"
        assert dialog.songs_table.item(0, 5).text() == translate_text("Unverified", language)
        dialog.songs_table.setCurrentCell(1, 0)
        application.processEvents()
        assert dialog.report_text.toPlainText().splitlines()[0] == _report_heading(language)
        assert dialog.buttons.button(QDialogButtonBox.Cancel).text() == translate_text("Cancel", language)
        if language != "en":
            assert expected_warning != RECOVERY_WARNING
            assert "independent sequences, total" not in dialog.report_text.toPlainText()
        assert report.before.format == "MIDI Type 2 (independent sequences, total)"
        assert preview.warnings == (prefix + RECOVERY_WARNING,)
    finally:
        dialog.close()
        parent.deleteLater()
        application.processEvents()


class _PendingReviewHost(QWidget, PendingChangesMixin):
    """Supply unchanged staged data while exercising the production review UI."""

    def __init__(self, language, source, report):
        super().__init__()
        self.language = language
        self.source = source
        self.table = QTableWidget(0, 2, self)
        self.listedFileInfo = {source: {"title": "Prepared title", "midi_type": "Type 0", "title_mode": "midi"}}
        self.pendingEdits = {}
        self.pendingRegularConversions = {source: {"change_report": report.as_dict()}}

    def _language_code(self):
        return self.language

    def _lt(self, source, **fields):
        return translate_text(source, self.language, **fields)

    def is_image_mode(self):
        return False

    def _regular_eseq_rows(self):
        return ()

    def _pending_song_paths(self):
        return {self.source}

    def _probe_regular_file(self, _path):
        return "Original title", "Type 2", "midi", True, b""

    def _regular_output_filename_for_path(self, _path):
        return "Prepared.mid"


@pytest.mark.parametrize("language", LANGUAGES)
def test_pending_review_localizes_type_transition_details_and_close_button(application, tmp_path, language):
    report = compare_music_bytes(_midi(2), _midi(0))
    host = _PendingReviewHost(language, str(tmp_path / "Original.mid"), report)
    inspected = []

    def inspect_dialog(dialog):
        dialog.show()
        table = dialog.findChild(QTableWidget)
        table.selectRow(0)
        application.processEvents()
        assert table.rowCount() == 1
        assert table.horizontalHeaderItem(2).text() == translate_text("Type", language)
        assert table.item(0, 2).text() == " → ".join(
            translate_text("Type {type}", language, type=kind) for kind in (2, 0)
        )
        assert table.item(0, 0).text() == "Original.mid\nOriginal title"
        assert table.item(0, 1).text() == "Prepared.mid\nPrepared title"
        assert dialog.findChild(QTextEdit).toPlainText().splitlines()[0] == _report_heading(language)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons.button(QDialogButtonBox.Close).text() == translate_text("Close", language)
        assert dialog.windowTitle() == tr("pending.review", language)
        inspected.append(True)
        dialog.close()

    host._exec_child_dialog = inspect_dialog
    try:
        host.show_pending_changes()
        assert inspected == [True]
        assert host.listedFileInfo[host.source]["midi_type"] == "Type 0"
    finally:
        host.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
def test_saved_bulk_job_error_translates_the_message_and_preserves_the_filename(language):
    filename = "<album>.img"
    source = f"Source image changed since this job was saved: {filename}"
    error = ValueError(source)

    localized = bulk_extraction_job.localize_extraction_job_error(error, language)

    assert localized == translate_text("Source image changed since this job was saved: {name}", language, name=filename)
    assert filename in localized
    assert str(error) == source
    if language != "en":
        assert localized != source


@pytest.mark.parametrize("language", LANGUAGES)
def test_invalid_bulk_job_json_uses_localized_location_without_exposing_parser_english(language):
    try:
        json.loads('{\n "images": ]')
    except json.JSONDecodeError as error:
        localized = bulk_extraction_job.localize_extraction_job_error(error, language)
        assert localized == translate_text(
            "The extraction job is not valid JSON (line {line}, column {column}).",
            language, line=error.lineno, column=error.colno,
        )
        assert "Expecting value" not in localized
    else:
        pytest.fail("The deliberately malformed JSON was accepted")


@pytest.mark.parametrize("language", LANGUAGES)
def test_combined_emulator_warnings_and_nested_type_zero_errors_keep_path_context(language):
    prefix = "DISK001.IMG / SONG.MID (album/source.mid): "
    title_limit = "The PSONG.MNG title is limited to 32 bytes."
    combined = prefix + RECOVERY_WARNING + " " + title_limit
    assert localize_emulator_warning(combined, language) == (
        prefix + translate_text(RECOVERY_WARNING, language) + " " + translate_text(title_limit, language)
    )
    conversion_error = "MIDI format 2 files are not supported for Type 0 conversion."
    wrapper = "Could not prepare '{name}' as {format}: {error}"
    error = wrapper.format(name="song.mid", format="MIDI", error=conversion_error)
    assert localize_emulator_warning(prefix + error, language) == prefix + translate_text(
        wrapper, language, name="song.mid", format="MIDI", error=translate_text(conversion_error, language),
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_bulk_failure_callback_renders_localized_checkpoint_error(application, tmp_path, monkeypatch, language):
    settings = QSettings(str(tmp_path / "failure-ui.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_error_event", lambda *_args, **_kwargs: None)
    window = main_window.MidiTitleWindow()
    output = str(tmp_path / "output")
    window.bulkExtractionContext = {"output_directory": output}
    filename = "<album>.img"
    source = f"Source image changed since this job was saved: {filename}"
    inspected = []

    def inspect_box(box):
        box.show()
        application.processEvents()
        expected_detail = translate_text("Source image changed since this job was saved: {name}", language, name=filename)
        assert box.windowTitle() == tr("bulk.failure.title", language)
        assert expected_detail in box.text()
        assert filename in box.text()
        assert tr("bulk.failure.summary", language, path=output).rstrip(".") in box.text()
        assert tr("bulk.failure.guidance", language).rstrip(".") in box.text()
        assert box.button(QMessageBox.Ok).text() == translate_text("OK", language)
        if language != "en":
            assert source not in box.text()
        inspected.append(True)
        box.close()
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec", inspect_box)
    try:
        window._on_bulk_extraction_failure(source)
        assert inspected == [True]
        assert window.status_label.text() == tr("bulk.failure.status", language)
    finally:
        window.close()
        application.processEvents()
