"""Read Floppy exposes destination requirements without replacing manual choices."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import FloppyDriveInfo
from aps_midi_prep_tool_app.preparation_profiles import (
    PIANO_PROFILES, get_preparation_medium, get_preparation_profile,
)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "read-floppy.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    drive = FloppyDriveInfo("/dev/fd0", 737280)
    monkeypatch.setattr(instance, "_discover_floppy_devices", lambda: ([drive], []))
    yield instance
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


def _apply(window, profile_key):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, profile.default_medium),
    )
    return profile


def _controls(window, dialog):
    checkboxes = {item.text(): item for item in dialog.findChildren(QCheckBox)}
    return (
        checkboxes[window._lt("Convert E-SEQ files to MIDI after reading")],
        checkboxes[window._lt("Name MIDI files by track number and song title")],
        checkboxes[window._lt("Trim title spaces after reading")],
    )


@pytest.mark.parametrize("profile_key", [profile.key for profile in PIANO_PROFILES if profile.song_format])
def test_destination_locks_effective_read_options_and_preserves_manual_choices(
    window, monkeypatch, profile_key,
):
    profile = _apply(window, profile_key)
    # Opposing saved preferences make it clear that the displayed/returned
    # choices come from the destination, and accepting preserves manual values.
    manual_conversion = profile.song_format != "midi"
    window.settings.setValue(window.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, manual_conversion)
    window.settings.setValue(window.SETTING_READ_FLOPPY_TRIM_TITLES, False)
    window.settings.setValue(window.SETTING_LONG_MIDI_FILENAMES, True)
    dos83 = window._preparation_requires_dos83_filenames()

    def inspect(dialog):
        convert, names, trim = _controls(window, dialog)
        assert not convert.isEnabled()
        assert convert.isChecked() is (profile.song_format == "midi")
        assert names.isEnabled() is (not dos83)
        assert names.isChecked() is (not dos83)
        assert trim.isEnabled() is (not profile.trim_title_spaces)
        assert trim.isChecked() is profile.trim_title_spaces
        hints = [label for label in dialog.findChildren(QLabel) if not label.isHidden()]
        preparation_hints = [label.text() for label in hints if "Preparing for" in label.text()]
        assert preparation_hints == [window._preparation_options_hint()]
        for control in (convert, names, trim):
            if control.isEnabled():
                continue
            assert "Preparing for" in control.toolTip()
            assert profile.label in control.toolTip()
            assert "Custom" in control.toolTip()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = window._choose_floppy_read_options()

    assert result["convert_to_midi"] is (profile.song_format == "midi")
    assert result["long_filenames"] is (not dos83)
    assert result["trim_titles"] is profile.trim_title_spaces
    assert window.settings.value(window.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, type=bool) is manual_conversion
    assert not window.settings.value(window.SETTING_READ_FLOPPY_TRIM_TITLES, type=bool)
    assert window.settings.value(window.SETTING_LONG_MIDI_FILENAMES, type=bool)


@pytest.mark.parametrize("profile_key", ("custom", "unsure"))
def test_manual_destination_keeps_read_options_editable_and_saves_choices(
    window, monkeypatch, profile_key,
):
    _apply(window, profile_key)
    window.settings.setValue(window.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, False)
    window.settings.setValue(window.SETTING_READ_FLOPPY_TRIM_TITLES, False)
    window._set_long_midi_filenames_enabled(True)

    def inspect(dialog):
        convert, names, trim = _controls(window, dialog)
        assert not any("Preparing for" in label.text() for label in dialog.findChildren(QLabel))
        for control in (convert, names, trim):
            assert control.isEnabled()
            assert "Preparing for" not in control.toolTip()
            control.setChecked(not control.isChecked())
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = window._choose_floppy_read_options()

    assert result["convert_to_midi"]
    assert result["trim_titles"]
    assert not result["long_filenames"]
    assert window.settings.value(window.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, type=bool)
    assert window.settings.value(window.SETTING_READ_FLOPPY_TRIM_TITLES, type=bool)
    assert not window.settings.value(window.SETTING_LONG_MIDI_FILENAMES, type=bool)


def test_custom_restores_manual_read_preferences_after_accepting_forced_options(window, monkeypatch):
    _apply(window, "midi_export")
    window.settings.setValue(window.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, False)
    window.settings.setValue(window.SETTING_READ_FLOPPY_TRIM_TITLES, False)
    monkeypatch.setattr(window, "_exec_child_dialog", lambda _dialog: QDialog.Accepted)
    forced = window._choose_floppy_read_options()
    assert forced["convert_to_midi"] and forced["trim_titles"]

    _apply(window, "custom")

    def inspect(dialog):
        convert, _names, trim = _controls(window, dialog)
        for control in (convert, trim):
            assert control.isEnabled()
            assert not control.isChecked()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    manual = window._choose_floppy_read_options()
    assert not manual["convert_to_midi"]
    assert not manual["trim_titles"]
