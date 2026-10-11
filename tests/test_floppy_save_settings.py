"""Floppy save costs can be selected independently and survive app restarts."""

from itertools import product
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_save_as_overwrite import window


OPTIONS = (
    ("backup_originals", "floppyBackupOriginalsAction", "floppy_backup_originals",
     "Back Up Floppy Files Before Saving",
     "Back up original floppy files on this computer before saving, without using floppy space. This adds time. When off, APS cannot restore overwritten or deleted files after a failed save."),
    ("check_originals", "floppyCheckOriginalsAction", "floppy_check_originals",
     "Check Original Floppy Files Before Saving",
     "Read original floppy files again before replacing them to detect changes during saving. This adds time."),
    ("verify_copies", "floppyVerifyCopiesAction", "floppy_verify_copies",
     "Verify Files Copied to Floppy",
     "Read copied files back from the floppy and compare them with the prepared files. This adds time."),
)


def _plain_label(text):
    # Non-Latin translations may append a Latin menu shortcut in parentheses.
    import re
    return re.sub(r" \(&.\)$", "", text).replace("&", "")


def test_floppy_settings_menu_groups_options_and_defaults(window):
    assert _plain_label(window.settingsDiskOptionsMenu.title()) == "Floppy Settings"
    assert window.settingsDiskOptionsMenu.menuAction() in window.settingsMenu.actions()
    actions = window.settingsDiskOptionsMenu.actions()
    assert window.fileAutoWriteProtectAction in actions
    assert window.verifyFloppyWriteAction in actions
    for _option, action_name, _key, label, tooltip in OPTIONS:
        action = getattr(window, action_name)
        assert action in actions
        assert action.isCheckable()
        assert _plain_label(action.text()) == label
        assert action.toolTip() == tooltip
    assert window._floppy_file_save_options() == {
        "backup_originals": True, "check_originals": False, "verify_copies": False,
    }
    assert not window.verifyFloppyWriteAction.isChecked()
    assert "full disk image" in window.verifyFloppyWriteAction.toolTip()


@pytest.mark.parametrize("values", list(product((False, True), repeat=3)))
def test_floppy_options_persist_independently_of_local_backups_and_raw_image_verify(window, values):
    local_backup = window.backup_checkbox.isChecked()
    raw_verify = window.verifyFloppyWriteAction.isChecked()
    expected = {}
    for (option, action_name, _key, _label, _tooltip), enabled in zip(OPTIONS, values):
        getattr(window, action_name).setChecked(enabled)
        expected[option] = enabled
    window.settings.sync()

    saved = QSettings(window.settings.fileName(), QSettings.IniFormat)
    for (_option, _action, key, _label, _tooltip), enabled in zip(OPTIONS, values):
        # Default-valued options need not be physically written before toggling.
        assert saved.value(key, key == "floppy_backup_originals", type=bool) is enabled
    assert main_window.MidiTitleWindow._floppy_file_save_options(
        SimpleNamespace(settings=saved)
    ) == expected
    assert window._floppy_file_save_options() == expected
    assert window.backup_checkbox.isChecked() is local_backup
    assert window.verifyFloppyWriteAction.isChecked() is raw_verify


def test_floppy_options_restore_checkmarks_when_window_reopens(window):
    window.floppyBackupOriginalsAction.setChecked(False)
    window.floppyCheckOriginalsAction.setChecked(True)
    window.floppyVerifyCopiesAction.setChecked(True)
    window.settings.sync()
    reopened = main_window.MidiTitleWindow()
    try:
        assert not reopened.floppyBackupOriginalsAction.isChecked()
        assert reopened.floppyCheckOriginalsAction.isChecked()
        assert reopened.floppyVerifyCopiesAction.isChecked()
    finally:
        reopened._clear_staging_history()
        reopened._cleanup_midi_scratch_dir()
        reopened.deleteLater()
        QApplication.instance().processEvents()


def test_floppy_settings_cannot_change_during_disk_work(window):
    window.choose_button.setEnabled(False)
    window._update_menu_actions()
    for _option, action_name, _key, _label, _tooltip in OPTIONS:
        assert not getattr(window, action_name).isEnabled()
    window.choose_button.setEnabled(True)
    window._update_menu_actions()
    for _option, action_name, _key, _label, _tooltip in OPTIONS:
        assert getattr(window, action_name).isEnabled()


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_floppy_settings_retranslate_without_resetting_options(window, language):
    window.floppyBackupOriginalsAction.setChecked(False)
    window.floppyVerifyCopiesAction.setChecked(True)
    before = window._floppy_file_save_options()
    window._set_language("de" if language == "en" else "en")
    window._set_language(language)
    assert _plain_label(window.settingsDiskOptionsMenu.title()) == translate_text("Floppy Settings", language)
    for _option, action_name, _key, label, tooltip in OPTIONS:
        action = getattr(window, action_name)
        assert _plain_label(action.text()) == translate_text(label, language)
        assert action.toolTip() == translate_text(tooltip, language)
    assert window._floppy_file_save_options() == before
