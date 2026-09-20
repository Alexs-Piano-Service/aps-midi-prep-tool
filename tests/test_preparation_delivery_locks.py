"""Delivery dialogs distinguish preparation requirements from editable defaults."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QLabel

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.preparation_profiles import (
    PIANO_PROFILES, get_preparation_medium, get_preparation_profile,
)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "delivery-locks.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_a: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    instance.deleteLater()
    app.processEvents()


def _apply(window, profile_key, medium_key=None):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, medium_key or profile.default_medium),
    )
    return profile


@pytest.mark.parametrize("profile_key", [profile.key for profile in PIANO_PROFILES if profile.song_format])
def test_new_image_locks_the_prepared_song_format_with_visible_reason(window, monkeypatch, profile_key):
    profile = _apply(window, profile_key)

    def inspect(dialog):
        checkbox = dialog.findChild(QCheckBox, "newImageEseqCheckbox")
        assert not checkbox.isEnabled()
        assert checkbox.isChecked() == (profile.song_format == "eseq")
        checkbox.click()
        assert checkbox.isChecked() == (profile.song_format == "eseq")
        reason = checkbox.toolTip()
        assert profile.label in reason
        assert "Preparing for" in reason and "Custom" in reason
        assert dialog.findChild(QLabel, "newImagePreparationHint").text() == reason
        assert all(combo.isEnabled() for combo in dialog.findChildren(QComboBox))
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert window._prompt_for_new_image_options()["eseq_disk"] == (profile.song_format == "eseq")


@pytest.mark.parametrize("manual_profile", ("custom", "unsure"))
def test_new_image_manual_destination_restores_both_song_format_choices(window, monkeypatch, manual_profile):
    _apply(window, "mark_ii")
    _apply(window, manual_profile)

    def inspect(dialog):
        checkbox = dialog.findChild(QCheckBox, "newImageEseqCheckbox")
        assert checkbox.isEnabled()
        assert checkbox.toolTip() == ""
        assert dialog.findChild(QLabel, "newImagePreparationHint") is None
        checkbox.setChecked(True)
        checkbox.click()
        assert not checkbox.isChecked()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert window._prompt_for_new_image_options()["eseq_disk"] is False


@pytest.mark.parametrize("profile_key", ("pianodisc_128plus", "pianodisc_228cfx"))
def test_emulator_builder_explains_forced_midi_type0(window, monkeypatch, profile_key):
    profile = _apply(window, profile_key)

    def inspect(dialog, **_kwargs):
        content = dialog.findChild(QComboBox, "emulatorContentCombo")
        assert content.currentData() == "midi"
        assert not content.isEnabled()
        reason = content.toolTip()
        assert profile.label in reason
        assert "MIDI Type 0" in reason and "Custom" in reason
        assert dialog.findChild(QLabel, "emulatorContentRestrictionHint").text() == reason
        assert all(combo.isEnabled() for combo in dialog.findChildren(QComboBox) if combo is not content)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window.show_emulator_image_utility()


@pytest.mark.parametrize("profile_key,medium_key", (
    ("mark_ii", "nalbantov"), ("mark_iii", "flashfloppy_hfe"),
    ("pianodisc_128plus", "original"),
))
def test_save_image_keeps_image_type_and_capacity_defaults_editable(window, monkeypatch, profile_key, medium_key):
    _apply(window, profile_key, medium_key)

    def inspect(dialog):
        assert all(combo.isEnabled() for combo in dialog.findChildren(QComboBox))
        assert all(checkbox.isEnabled() for checkbox in dialog.findChildren(QCheckBox))
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert window._prompt_for_save_image_options() is None
