"""Preparation constraints are explained wherever users can change the options."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium, get_preparation_profile,
)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "option-locks.ini"), QSettings.IniFormat)
    monkeypatch.setenv("APS_MIDI_RENAME_RECOVERY_DIR", str(tmp_path / "rename-recovery"))
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    instance.deleteLater()
    app.processEvents()


def _apply(window, profile_key, medium_key=None):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, medium_key or profile.default_medium),
    )


def _checkbox(dialog, text):
    return next(checkbox for checkbox in dialog.findChildren(QCheckBox)
                if checkbox.text() == text)


def _assert_visible_explanation(dialog, checkbox, profile):
    dialog.show()
    QApplication.processEvents()
    reason = checkbox.toolTip()
    assert "Preparing for" in reason
    assert profile.label in reason
    assert "Custom" in reason
    visible_hints = [
        label for label in dialog.findChildren(QLabel)
        if label.isVisible() and "Preparing for" in label.text()
        and profile.label in label.text() and "Custom" in label.text()
    ]
    assert len(visible_hints) == 1


@pytest.mark.parametrize("profile_key", ("mark_i", "mark_iii"))
def test_preparation_locks_filename_setting_and_direct_changes(window, profile_key):
    _apply(window, profile_key, "original")
    action = window.settingsUseDos83FilenamesAction
    assert action.isChecked()
    assert not action.isEnabled()
    assert action.text().replace("&", "") == "Use 8.3 filenames"
    assert window._preparation_profile().label in action.toolTip()
    assert "Custom" in action.toolTip()
    assert action.statusTip() == action.toolTip()

    window.toggle_dos83_filenames(False)
    window._set_long_midi_filenames_enabled(True)
    assert action.isChecked()
    assert not action.isEnabled()
    assert window._dos83_filenames_enabled()
    assert not window._long_midi_filenames_enabled()
    assert window.settings.value(window.SETTING_USE_DOS83_FILENAMES, type=bool)
    assert not window.settings.value(window.SETTING_LONG_MIDI_FILENAMES, type=bool)

    _apply(window, "custom")
    assert action.isEnabled()
    assert action.text().replace("&", "") == "Use 8.3 filenames"
    window.toggle_dos83_filenames(False)
    window._set_long_midi_filenames_enabled(True)
    assert not action.isChecked()
    assert not window._dos83_filenames_enabled()
    assert window._long_midi_filenames_enabled()


def test_filename_editor_explains_profile_lock_and_custom_unlocks(window, monkeypatch):
    _apply(window, "mark_iii", "original")

    def inspect_locked(dialog):
        checkbox = _checkbox(dialog, "Use 8.3 filenames")
        assert checkbox.isChecked()
        assert not checkbox.isEnabled()
        _assert_visible_explanation(dialog, checkbox, window._preparation_profile())
        dialog.close()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_locked)
    assert window._prompt_for_image_filename("song.mid") == ("SONG.MID", True)
    _apply(window, "custom")

    def inspect_custom(dialog):
        checkbox = _checkbox(dialog, "Use 8.3 filenames")
        assert checkbox.isEnabled()
        assert not checkbox.isChecked()
        assert "Preparing for" not in checkbox.toolTip()
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_custom)
    assert window._prompt_for_image_filename("Song Title.mid") == ("", False)


def test_descriptive_rename_cannot_override_prepared_regular_filenames(window, monkeypatch, tmp_path):
    source = tmp_path / "SONG.MID"
    track = b"\x00\xff\x03\x04Song\x00\xff\x2f\x00"
    original = (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk"
                + len(track).to_bytes(4, "big") + track)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Loaded", prepare_destination=False)
    _apply(window, "mark_iii", "original")
    action = window.utilitiesLongFilenamesAction
    assert not action.isEnabled()
    assert action.text().replace("&", "") == "Name MIDI Files from Song Titles"
    assert "Preparing for" in action.toolTip()
    notices = []
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *args: notices.append(args[2]))

    window.create_long_midi_filenames()

    assert notices == [action.toolTip()]
    assert not window.pendingRegularRenames
    assert source.read_bytes() == original
    _apply(window, "custom")
    assert action.isEnabled()
    assert action.text().replace("&", "") == "Name MIDI Files from Song Titles"


@pytest.mark.parametrize("profile_key", ("mark_iii", "midi_export", "e3_850", "enspire"))
@pytest.mark.parametrize("skip_dialog", (False, True))
def test_conversion_prompt_uses_forced_options_without_overwriting_preferences(
    window, monkeypatch, profile_key, skip_dialog,
):
    _apply(window, profile_key)
    force_short_names = profile_key == "mark_iii"
    force_trim = not force_short_names
    # These preferences remain available when preparation is disengaged.
    for key in (window.SETTING_LONG_MIDI_FILENAMES,
                window.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES,
                window.SETTING_READ_FLOPPY_LONG_FILENAMES):
        window.settings.setValue(key, True)
    window.settings.setValue(window.SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES, False)
    window.settings.setValue(window.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT, skip_dialog)

    def inspect(dialog):
        assert not skip_dialog, "The saved skip preference should skip this dialog."
        names = _checkbox(dialog, "Name MIDI files by track number and song title")
        trim = _checkbox(dialog, "Remove extra spaces from song titles")
        assert names.isChecked() is not force_short_names
        assert names.isEnabled() is not force_short_names
        assert trim.isChecked() is force_trim
        assert trim.isEnabled() is not force_trim
        locked = names if force_short_names else trim
        _assert_visible_explanation(dialog, locked, window._preparation_profile())
        dialog.close()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = window._confirm_eseq_to_midi_conversion(title="Convert", message="Convert songs")
    assert result == (True, not force_short_names, force_trim)
    for key in (window.SETTING_LONG_MIDI_FILENAMES,
                window.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES,
                window.SETTING_READ_FLOPPY_LONG_FILENAMES):
        assert window.settings.value(key, type=bool)
    assert not window.settings.value(window.SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES, type=bool)


def test_custom_conversion_options_are_editable_again(window, monkeypatch):
    _apply(window, "enspire")
    _apply(window, "custom")
    window.settings.setValue(window.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT, False)

    def inspect(dialog):
        for text in ("Name MIDI files by track number and song title",
                     "Remove extra spaces from song titles"):
            checkbox = _checkbox(dialog, text)
            assert checkbox.isEnabled()
            assert "Preparing for" not in checkbox.toolTip()
            checkbox.setChecked(False)
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert window._confirm_eseq_to_midi_conversion(
        title="Convert", message="Convert songs",
    ) == (True, False, False)


@pytest.mark.parametrize("profile_key", ("mark_iii", "midi_export", "e3_850", "enspire"))
def test_midi_preparation_owns_post_read_conversion_without_no_eseq_notice(
    window, monkeypatch, profile_key,
):
    _apply(window, profile_key)
    scheduled = []
    deferred = []
    monkeypatch.setattr(window, "_schedule_destination_preparation", lambda: scheduled.append(True))
    monkeypatch.setattr(main_window.QTimer, "singleShot", lambda delay, callback: deferred.append((delay, callback)))
    monkeypatch.setattr(main_window.QMessageBox, "information",
                        lambda *_a, **_k: pytest.fail("Prepared MIDI files must not trigger a No E-SEQ notice."))
    monkeypatch.setattr(window, "_image_eseq_conversion_rows",
                        lambda: pytest.fail("Destination preparation owns conversion."))
    window.pendingFloppyReadConvertToMidi = True
    window.pendingFloppyReadLongFilenames = True

    window._convert_loaded_floppy_to_midi_after_read()

    assert not scheduled
    assert deferred == [(0, window._offer_post_load_sequence_conversions)]
    assert not window.pendingFloppyReadConvertToMidi
    assert not window.pendingFloppyReadLongFilenames


@pytest.mark.parametrize("profile_key", ("midi_export", "e3_850", "enspire"))
def test_modern_preparation_owns_post_read_title_cleanup(window, monkeypatch, profile_key):
    _apply(window, profile_key)
    monkeypatch.setattr(
        window, "_stage_trim_title_spaces_for_all",
        lambda **_kwargs: pytest.fail("The queued preparation owns title cleanup and error reporting."),
    )
    window.pendingFloppyReadTrimTitles = True

    assert window._apply_pending_floppy_read_title_trim() == 0
    assert not window.pendingFloppyReadTrimTitles


def test_new_bulk_job_explains_filename_lock_and_keeps_conversion_manual(window, monkeypatch):
    _apply(window, "mark_iii", "original")
    window.settings.setValue(window.SETTING_BULK_EXTRACTION_LONG_MIDI_FILENAMES, True)
    window.settings.setValue(window.SETTING_BULK_EXTRACTION_CONVERT_ESEQ, False)

    def inspect(dialog):
        names = _checkbox(dialog, window._t("bulk.long_filenames"))
        conversion = _checkbox(dialog, window._t("bulk.convert"))
        assert not names.isChecked()
        assert not names.isEnabled()
        _assert_visible_explanation(dialog, names, window._preparation_profile())
        assert conversion.isEnabled()
        assert not conversion.isChecked()
        conversion.setChecked(True)
        assert conversion.isChecked()
        assert not names.isEnabled()
        dialog.close()
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window.show_bulk_extraction_utility()
    assert window.settings.value(window.SETTING_BULK_EXTRACTION_LONG_MIDI_FILENAMES, type=bool)
