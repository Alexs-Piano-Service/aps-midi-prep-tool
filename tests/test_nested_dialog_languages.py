"""Exercise nested warnings, backup reports, and toolkit labels in every language."""

import os
from pathlib import Path
from string import Formatter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QPushButton, QWidget

from aps_midi_prep_tool_app import rename_recovery_dialog
from aps_midi_prep_tool_app.emulator_image_builder import EmulatorAlbumPreview, EmulatorBuildPreview
from aps_midi_prep_tool_app.emulator_preview_dialog import EmulatorPreviewDialog
from aps_midi_prep_tool_app.localized_dialogs import QMessageBox, install_qt_translations
from aps_midi_prep_tool_app.markiv_backup.devices import MountedDevice
from aps_midi_prep_tool_app.markiv_backup_dialog import MarkIVBackupDialog
from aps_midi_prep_tool_app.markiv_backup_diagnostics import localize_backup_diagnostic
from aps_midi_prep_tool_app.markiv_backup_diagnostics_translations import MARKIV_BACKUP_DIAGNOSTICS_TRANSLATIONS
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr, translate_text


LANGUAGES = [language.code for language in SUPPORTED_LANGUAGES]


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("language", LANGUAGES)
def test_backup_diagnostic_translations_preserve_fields_and_paths(language):
    for source, translations in MARKIV_BACKUP_DIAGNOSTICS_TRANSLATIONS.items():
        assert set(translations) == set(LANGUAGES) - {"en"}
        fields = {field for _literal, field, _spec, _conversion in Formatter().parse(source) if field}
        translated = translate_text(source, language)
        assert {field for _literal, field, _spec, _conversion in Formatter().parse(translated) if field} == fields
        if language != "en":
            assert translated != source
        values = {field: "<data> {literal}" for field in fields}
        message = source.format(**values)
        assert localize_backup_diagnostic(message, language) == translated.format(**values)
        prefix = "C:/Music/Album: Save {original}.FIL: "
        assert localize_backup_diagnostic(prefix + message, language) == prefix + translated.format(**values)
    timeout = "Drive detection did not finish within {seconds} seconds."
    rendered = localize_backup_diagnostic(timeout.format(seconds="0.5"), language)
    assert rendered == translate_text(timeout, language, seconds="0.5")
    if language != "en":
        assert rendered != timeout.format(seconds="0.5")


@pytest.mark.parametrize("language", LANGUAGES)
def test_backup_report_localizes_nested_errors_without_changing_logs(application, tmp_path, monkeypatch, language):
    settings = QSettings(str(tmp_path / "backup.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    dialog = MarkIVBackupDialog(settings, refresh_on_open=False)
    prefix = "songs/user/<Album>/{title}.FIL: "
    inner = "SHA-256 checksum does not match"
    wrapper = "E-SEQ conversion failed: {error}"
    warning = prefix + wrapper.format(error=inner)
    dialog._failed(warning)
    captured = []

    def inspect(child):
        child.show()
        application.processEvents()
        assert child.windowTitle() == tr("markiv.report_title", language)
        assert child.findChild(QPlainTextEdit).toPlainText() == prefix + translate_text(
            wrapper, language, error=translate_text(inner, language)
        )
        assert child.findChild(QPushButton).text() == translate_text("Close", language)
        captured.append(True)
        child.close()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", inspect)
    try:
        dialog._open_report()
        assert captured == [True]
        assert dialog.status_label.text() == tr("markiv.failed", language)
        assert dialog._report_messages == [warning]
        assert dialog._metadata_lines({"artist": "Close", "metadata_source": "Original filename"}, expanded=True) == [
            f"{tr('markiv.artist', language)}: Close",
            f"{tr('markiv.metadata_source', language)}: {translate_text('Original filename', language)}",
        ]
        if language != "en":
            assert translate_text("Original filename", language) != "Original filename"
        dialog._operation = "discover"
        device = MountedDevice("/dev/example", Path("/Music <Original>"), is_mark_iv=True)
        dialog._succeeded([device])
        assert dialog.source_combo.itemData(0, Qt.ToolTipRole) == (
            f"{translate_text('Disklavier music', language)}  —  /dev/example  (/Music <Original>)"
        )
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
def test_emulator_validation_warning_uses_dialog_language_without_main_window(application, monkeypatch, language):
    host = QWidget()
    host._language_code = lambda: language
    preview = EmulatorBuildPreview(
        "/source", "/output", (),
        (EmulatorAlbumPreview("/source/album", "Album", 0, False, "Folder name"),),
        (), {}, {}, "midi", "folders",
    )
    dialog = EmulatorPreviewDialog(preview, host)
    captured = []

    def inspect(box):
        assert box.windowTitle() == translate_text("Review emulator disk set", language)
        assert box.text() == translate_text(
            "Include at least one album and use nonempty titles without NUL characters.", language,
        )
        assert box.button(QMessageBox.Ok).text() == translate_text("OK", language)
        captured.append(True)
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    try:
        dialog._revise()
        assert captured == [True]
        assert dialog.decision is None
    finally:
        host.close()
        host.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
def test_recovery_details_and_completion_follow_selected_language(application, monkeypatch, language):
    host = QWidget()
    host._language_code = lambda: language
    host._lt = lambda source: translate_text(source, language)
    path = "/recovery/<album>/{source}"
    monkeypatch.setattr(rename_recovery_dialog, "find_pending_midi_renames", lambda: [path])
    recovered = []
    monkeypatch.setattr(rename_recovery_dialog, "recover_midi_dos83_transaction",
                        lambda directory, action: recovered.append((directory, action)))
    observed = []

    def inspect(box):
        if box.detailedText():
            assert box.windowTitle() == translate_text("Recover interrupted rename", language)
            assert box.detailedText() == path
            details = next(button for button in box.buttons() if button.property("_aps_details_translation"))
            assert details.text() == translate_text("Show Details...", language)
            details.click()
            assert details.text() == translate_text("Hide Details...", language)
            button = next(button for button in box.buttons() if button.text() == translate_text("Restore originals", language))
            button.click()
        else:
            assert box.windowTitle() == translate_text("Rename recovery complete", language)
            assert box.button(QMessageBox.Ok).text() == translate_text("OK", language)
        observed.append(True)
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    try:
        rename_recovery_dialog.show_rename_recovery_dialogs(host)
        assert observed == [True, True]
        assert recovered == [(path, "restore")]
    finally:
        host.close()
        host.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("fail_during_recovery", [False, True])
def test_recovery_failures_show_translated_guidance_and_original_diagnostics(
    application, monkeypatch, language, fail_during_recovery,
):
    host = QWidget()
    host._language_code = lambda: language
    host._lt = lambda source: translate_text(source, language)
    diagnostic = "External failure at /recovery/<original>/{file}"

    def fail(*_args):
        raise OSError(diagnostic)

    monkeypatch.setattr(rename_recovery_dialog, "find_pending_midi_renames",
                        (lambda: ["/recovery"]) if fail_during_recovery else fail)
    monkeypatch.setattr(rename_recovery_dialog, "recover_midi_dos83_transaction", fail)
    observed = []

    def inspect(box):
        if box.windowTitle() == translate_text("Recover interrupted rename", language):
            box.defaultButton().click()
            return QMessageBox.Ok
        source = ("Recovery could not be completed. Review the details before trying again."
                  if fail_during_recovery else
                  "Interrupted renames could not be checked. Review the details before trying again.")
        assert box.text() == translate_text(source, language)
        if language != "en":
            assert box.text() != source
        assert box.detailedText() == diagnostic
        assert box.button(QMessageBox.Ok).text() == translate_text("OK", language)
        observed.append(True)
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    try:
        rename_recovery_dialog.show_rename_recovery_dialogs(host)
        assert observed == [True]
    finally:
        host.close()
        host.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("labels", [("Convert to MIDI", "Not Now"), ("Convert and Exit", "Cancel")])
def test_shown_message_boxes_keep_specific_action_captions(application, language, labels):
    host = QWidget()
    host.language_code = language
    try:
        assert install_qt_translations(language, application)
        box = QMessageBox(host)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Yes)
        for button_id, source in zip((QMessageBox.Yes, QMessageBox.No), labels):
            box.button(button_id).setText(translate_text(source, language))
        box.show()
        application.processEvents()
        for button_id, source in zip((QMessageBox.Yes, QMessageBox.No), labels):
            assert box.button(button_id).text() == translate_text(source, language)
        application.sendEvent(box, QEvent(QEvent.LanguageChange))
        application.processEvents()
        for button_id, source in zip((QMessageBox.Yes, QMessageBox.No), labels):
            assert box.button(button_id).text() == translate_text(source, language)
        assert box.button(QMessageBox.Cancel).text() == translate_text("Cancel", language)
        assert box.defaultButton() is box.button(QMessageBox.Yes)
    finally:
        host.close()
        host.deleteLater()
        install_qt_translations("en", application)
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
def test_toolkit_translation_switch_is_idempotent_and_keeps_details_localized(application, language):
    host = QWidget()
    host.language_code = language
    try:
        assert install_qt_translations(language, application)
        translator = getattr(application, "_aps_qt_translator", None)
        assert install_qt_translations(language, application)
        assert getattr(application, "_aps_qt_translator", None) is translator
        box = QMessageBox(host)
        box.setStandardButtons(QMessageBox.Ok)
        box.setDetailedText("<external diagnostic>")
        box.show()
        application.processEvents()
        assert box.button(QMessageBox.Ok).text() == translate_text("OK", language)
        details = next(button for button in box.buttons() if button.property("_aps_details_translation"))
        assert details.text() == translate_text("Show Details...", language)
        details.click()
        assert details.text() == translate_text("Hide Details...", language)
        details.click()
        assert details.text() == translate_text("Show Details...", language)
    finally:
        host.close()
        host.deleteLater()
        install_qt_translations("en", application)
        application.processEvents()
