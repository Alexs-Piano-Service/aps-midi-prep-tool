import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    MEDIA_BY_KEY, PIANO_PROFILES,
    SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT, SETTING_MEDIUM, SETTING_PROFILE,
    get_preparation_medium, get_preparation_profile, proposed_settings,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_nalbantov_is_one_delivery_option_across_floppy_disklaviers():
    assert "nalbantov_slim" not in MEDIA_BY_KEY
    assert all("nalbantov_slim" not in profile.media for profile in PIANO_PROFILES)
    assert {profile.key for profile in PIANO_PROFILES if "nalbantov" in profile.media} == {
        "mark_i", "mark_ii", "dsr1", "mark_ii_xg", "mark_iii", "mark_iv",
    }


@pytest.mark.parametrize("profile_key", ("mark_i", "mark_ii", "dsr1", "mark_ii_xg", "mark_iii", "mark_iv"))
def test_retired_medium_key_resolves_to_same_nalbantov_defaults(profile_key):
    profile = get_preparation_profile(profile_key)
    assert get_preparation_medium(profile, "emulator_custom").key == "emulator_custom"
    migrated = get_preparation_medium(profile, "nalbantov_slim")
    assert migrated.key == "nalbantov"
    assert proposed_settings(profile, migrated) == proposed_settings(profile, MEDIA_BY_KEY["nalbantov"])


@pytest.mark.parametrize(
    ("profile_key", "song_format", "disk_format"),
    (("mark_ii", "eseq", "ibm.720"), ("mark_ii_xg", "midi", "ibm.1440")),
)
def test_nalbantov_keeps_each_controllers_song_and_capacity_requirements(profile_key, song_format, disk_format):
    profile = get_preparation_profile(profile_key)
    settings = proposed_settings(profile, get_preparation_medium(profile, "nalbantov"))
    assert settings["emulator_image_content"] == song_format
    assert settings[SETTING_DISK_FORMAT] == disk_format
    assert settings[SETTING_IMAGE_FORMAT] == "hfe"
    assert settings["emulator_image_prefix"] == "DSKA"
    assert settings["emulator_image_starting_number"] == 0
    assert settings["use_dos83_filenames"] is True


@pytest.mark.parametrize("medium_key", ("nalbantov", "flashfloppy_img", "flashfloppy_hfe"))
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


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_dialog_displays_one_nalbantov_for_retired_selection_without_writing_settings(application, tmp_path, language):
    profile_key = "mark_ii"
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue(SETTING_PROFILE, profile_key)
    settings.setValue(SETTING_MEDIUM, "nalbantov_slim")
    settings.setValue("language", language)
    dialog = PreparationProfileDialog(settings, profile_key, "nalbantov_slim")
    try:
        assert dialog.medium_combo.findData("nalbantov_slim") == -1
        assert dialog.selection()[1].key == "nalbantov"
        assert dialog.medium_combo.currentText() == translate_text("Nalbantov", language)
        assert sum(dialog.medium_combo.itemData(index) == "nalbantov"
                   for index in range(dialog.medium_combo.count())) == 1
        assert dialog.medium_combo.findData("emulator_custom") >= 0
        assert settings.value(SETTING_MEDIUM) == "nalbantov_slim"
    finally:
        dialog.close()


def test_dialog_preserves_nalbantov_between_floppy_controllers(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "mark_ii", "nalbantov_slim")
    try:
        assert dialog.selection()[1].key == "nalbantov"
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii_xg"))
        assert dialog.selection()[1].key == "nalbantov"
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_iii"))
        assert dialog.selection()[1].key == "nalbantov"
        assert dialog.medium_combo.findData("nalbantov_slim") == -1
    finally:
        dialog.close()


@pytest.mark.parametrize("profile_key", ("mark_i", "mark_ii", "dsr1", "mark_ii_xg", "mark_iii", "mark_iv"))
def test_startup_migrates_retired_selection_to_nalbantov(application, monkeypatch, tmp_path, profile_key):
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
        assert medium.key == "nalbantov"
        assert window._preparation_export_defaults()["image_format"] == "hfe"
        assert window._preparation_export_defaults()["disk_format"] == profile.disk_format_key
        assert settings.value("emulator_image_output_format") == "hfe"
        assert settings.value("emulator_image_prefix") == "DSKA"
        assert settings.value("emulator_image_starting_number", type=int) == 0
    finally:
        window.close()
