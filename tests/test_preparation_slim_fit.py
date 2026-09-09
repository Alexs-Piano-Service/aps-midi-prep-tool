import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    MEDIA_BY_KEY, PIANO_PROFILES,
    SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT, SETTING_MEDIUM, SETTING_PROFILE,
    get_preparation_medium, get_preparation_profile, proposed_settings,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_slim_fit_is_limited_to_mark_ii_and_mark_ii_xg():
    assert {profile.key for profile in PIANO_PROFILES if "nalbantov_slim" in profile.media} == {
        "mark_ii", "mark_ii_xg",
    }


@pytest.mark.parametrize("profile_key", ("mark_i", "dsr1", "mark_iii", "mark_iv"))
def test_other_controllers_keep_manual_emulator_choice_and_reject_slim(profile_key):
    profile = get_preparation_profile(profile_key)
    assert get_preparation_medium(profile, "emulator_custom").key == "emulator_custom"
    fallback = get_preparation_medium(profile, "nalbantov_slim")
    assert fallback.key == profile.default_medium
    assert proposed_settings(profile, MEDIA_BY_KEY["nalbantov_slim"]) == proposed_settings(profile, fallback)


@pytest.mark.parametrize(
    ("profile_key", "song_format", "disk_format"),
    (("mark_ii", "eseq", "ibm.720"), ("mark_ii_xg", "midi", "ibm.1440")),
)
def test_slim_keeps_each_supported_controllers_song_and_capacity_requirements(profile_key, song_format, disk_format):
    profile = get_preparation_profile(profile_key)
    settings = proposed_settings(profile, get_preparation_medium(profile, "nalbantov_slim"))
    assert settings["emulator_image_content"] == song_format
    assert settings[SETTING_DISK_FORMAT] == disk_format
    assert settings[SETTING_IMAGE_FORMAT] == "hfe"
    assert settings["emulator_image_prefix"] == "DSKA"
    assert settings["emulator_image_starting_number"] == 0
    assert settings["use_dos83_filenames"] is True


@pytest.mark.parametrize("medium_key", ("nalbantov_slim", "flashfloppy_img", "flashfloppy_hfe"))
def test_numbered_emulator_presets_all_start_at_zero(medium_key):
    profile = get_preparation_profile("mark_ii")
    medium = get_preparation_medium(profile, medium_key)
    assert medium.starting_number == 0
    assert proposed_settings(profile, medium)["emulator_image_starting_number"] == 0


@pytest.mark.parametrize("profile_key", ("custom", "unsure", "mark_ii"))
def test_manual_preparation_does_not_override_numbering(profile_key):
    profile = get_preparation_profile(profile_key)
    medium = get_preparation_medium(profile, "emulator_custom")
    assert "emulator_image_starting_number" not in proposed_settings(profile, medium)


@pytest.mark.parametrize("profile_key", ("mark_i", "mark_iii", "mark_iv"))
def test_dialog_normalizes_incompatible_saved_slim_selection_without_writing_settings(application, tmp_path, profile_key):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue(SETTING_PROFILE, profile_key)
    settings.setValue(SETTING_MEDIUM, "nalbantov_slim")
    dialog = PreparationProfileDialog(settings, profile_key, "nalbantov_slim")
    try:
        assert dialog.medium_combo.findData("nalbantov_slim") == -1
        assert dialog.selection()[1].key == get_preparation_profile(profile_key).default_medium
        assert dialog.medium_combo.findData("emulator_custom") >= 0
        assert settings.value(SETTING_MEDIUM) == "nalbantov_slim"
    finally:
        dialog.close()


def test_dialog_preserves_slim_between_supported_models_and_falls_back_for_mark_iii(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "mark_ii", "nalbantov_slim")
    try:
        assert dialog.selection()[1].key == "nalbantov_slim"
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii_xg"))
        assert dialog.selection()[1].key == "nalbantov_slim"
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_iii"))
        assert dialog.selection()[1].key == "original"
        assert dialog.medium_combo.findData("nalbantov_slim") == -1
    finally:
        dialog.close()


@pytest.mark.parametrize("profile_key", ("mark_i", "mark_iii", "mark_iv"))
def test_startup_replaces_invalid_slim_defaults(application, monkeypatch, tmp_path, profile_key):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue(SETTING_PROFILE, profile_key)
    settings.setValue(SETTING_MEDIUM, "nalbantov_slim")
    settings.setValue(SETTING_IMAGE_FORMAT, "hfe")
    settings.setValue(SETTING_DISK_FORMAT, "ibm.720")
    settings.setValue("emulator_image_output_format", "hfe")
    settings.setValue("emulator_image_prefix", "DSKA")
    settings.setValue("emulator_image_starting_number", 1)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    window = MidiTitleWindow()
    try:
        profile = get_preparation_profile(profile_key)
        medium = get_preparation_medium(profile, "nalbantov_slim")
        assert settings.value(SETTING_MEDIUM) == medium.key
        assert window._preparation_medium().key == medium.key
        if medium.key == "original":
            assert window._preparation_export_defaults()["image_format"] == "img"
            assert window._preparation_export_defaults()["disk_format"] == profile.disk_format_key
            assert settings.value("emulator_image_output_format") == "img"
        else:
            assert window._preparation_export_defaults() == {}
            assert not settings.contains(SETTING_IMAGE_FORMAT)
            assert not settings.contains(SETTING_DISK_FORMAT)
    finally:
        window.close()
