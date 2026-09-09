import os
from string import Formatter
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.dynamic_dialog_translations import DYNAMIC_DIALOG_TRANSLATIONS
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.recovery_delivery_translations import RECOVERY_DELIVERY_TRANSLATIONS


LANGUAGES = [language.code for language in SUPPORTED_LANGUAGES]
DROP_ERRORS = (
    "Could not prepare dropped files: {error}",
    "Could not add dropped file: {error}",
)


class _RecoveryHarness(QWidget):
    _translate_dialog_tree = main_window.MidiTitleWindow._translate_dialog_tree
    _translate_dialog_button_box = main_window.MidiTitleWindow._translate_dialog_button_box

    def __init__(self, language):
        super().__init__()
        self.language = language
        self.status_label = QLabel(self)
        self.diskWriteTargetProgressDialog = None
        self.image_session = SimpleNamespace(last_write_verification={})

    def _language_code(self):
        return self.language

    def _lt(self, source, **fields):
        return translate_text(source, self.language, **fields)

    def _log_event(self, *_args, **_kwargs):
        pass

    def _log_warning_event(self, *_args, **_kwargs):
        pass

    def _show_greaseweazle_sector_reports(self, _reports):
        pass


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _fields(template):
    return {field for _literal, field, _format, _conversion in Formatter().parse(template) if field}


def test_recovery_and_drop_templates_cover_languages_and_preserve_inserted_data():
    templates = dict(RECOVERY_DELIVERY_TRANSLATIONS)
    templates.update({source: DYNAMIC_DIALOG_TRANSLATIONS[source] for source in DROP_ERRORS})
    for source, translations in templates.items():
        fields = _fields(source)
        supplied = {field: f"Save {{{field}}} <disk> & A:" for field in fields}
        for language in LANGUAGES:
            if language != "en":
                assert language in translations, (source, language)
                assert _fields(translations[language]) == fields
            rendered = translate_text(source, language, **supplied)
            for value in supplied.values():
                assert value in rendered
            if language != "en":
                assert rendered != source.format(**supplied), (source, language)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("file_level", (False, True))
@pytest.mark.parametrize("verified", (False, True))
def test_write_completion_dialog_and_status_are_localized(
    application, monkeypatch, language, file_level, verified,
):
    window = _RecoveryHarness(language)
    window.image_session.last_write_verification = {
        "confidence": "contents_verified" if verified else "written",
    }
    target = "Save {target} <disk> & A:"
    captured = []
    confidence = (
        "Contents verified by readback. Playback on your piano has not been tested."
        if verified else "Files written. Readback verification was not requested."
    )

    def inspect(box):
        captured.append(box.text())
        assert target in box.text()
        assert window._lt(confidence) in box.text()
        assert box.button(main_window.QMessageBox.Ok).text() == window._lt("OK")
        if language != "en":
            assert confidence not in box.text()
        return main_window.QMessageBox.Ok

    monkeypatch.setattr(main_window.QMessageBox, "exec", inspect)
    try:
        main_window.MidiTitleWindow._on_write_image_to_floppy_success(
            window, target, file_level=file_level,
        )
        assert len(captured) == 1
        status = "Saved current files to {target}." if file_level else "Wrote current image to {target}."
        assert window.status_label.text() == window._lt(status, target=target)
    finally:
        window.close()


@pytest.mark.parametrize("language", LANGUAGES)
def test_retained_capture_dialog_and_save_filter_are_localized(
    application, monkeypatch, tmp_path, language,
):
    window = _RecoveryHarness(language)
    capture = tmp_path / "Save {path}.img"
    capture.write_bytes(b"partial image")
    window.lastDiskRecoveryDiagnostics = {"partial_capture_path": str(capture)}
    inspected = []

    def inspect(message):
        window._translate_dialog_tree(message)
        assert message.windowTitle() == window._lt("Partial capture retained")
        assert message.text() == window._lt(
            "Recovered sectors are still available. Save the partial image and its sector coverage without reading the floppy again. Unread bytes are zero-filled."
        )
        assert message.button(main_window.QMessageBox.Close).text() == window._lt("Close")
        assert any(button.text() == window._lt("Save partial capture...") for button in message.buttons())
        inspected.append(True)
        return main_window.QMessageBox.Close

    window._exec_child_dialog = inspect
    picker_calls = []

    def choose_path(_parent, caption, _initial, file_filter):
        picker_calls.append((caption, file_filter))
        return "", ""

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", choose_path)
    try:
        main_window.MidiTitleWindow._offer_partial_recovery_capture(window)
        assert inspected == [True]
        main_window.MidiTitleWindow.save_partial_recovery_capture(window)
        assert picker_calls == [(window._lt("Save partial capture..."), window._lt("Raw floppy image (*.img)"))]
        assert "*.img" in picker_calls[0][1]
        assert capture.read_bytes() == b"partial image"
    finally:
        window.close()


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("already_written", (False, True))
def test_floppy_commit_cancel_status_distinguishes_written_data(
    application, monkeypatch, language, already_written,
):
    window = _RecoveryHarness(language)
    window.diskCommitProgressDialog = None
    window.image_session.last_write_verification = {"confidence": "written" if already_written else ""}
    captured = []
    monkeypatch.setattr(main_window.QMessageBox, "exec", lambda box: captured.append(box.text()) or main_window.QMessageBox.Ok)
    try:
        main_window.MidiTitleWindow._on_floppy_commit_cancelled(window, "raw diagnostic")
        source = (
            "Files were written. Readback verification was cancelled, so the contents have not been verified."
            if already_written else "Floppy write cancelled. Pending changes are still staged."
        )
        assert window.status_label.text() == window._lt(source)
        assert len(captured) == 1
        if already_written:
            assert captured == [window._lt(source)]
        elif language != "en":
            assert "Writing was cancelled" not in captured[0]
    finally:
        window.close()
