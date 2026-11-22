"""The output naming preference is global, persistent, and configurable."""

import json

import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.startup_config import load_startup_config
from test_ui_preference_defaults import window


def test_random_output_suffixes_default_on_under_save_options(window):
    action = window.settingsRandomOutputSuffixesAction
    assert action in window.fileSaveOptionsMenu.actions()
    assert window.fileSaveOptionsMenu.menuAction() in window.settingsMenu.actions()
    assert action.isCheckable() and action.isChecked()
    assert window._random_output_suffixes_enabled()
    assert action.text().replace("&", "") == "Add Random Suffixes to Output Names"


def test_random_output_suffixes_can_be_disabled_and_remembered(window):
    window.settingsRandomOutputSuffixesAction.trigger()
    assert not window._random_output_suffixes_enabled()
    assert not window.settings.value("random_output_suffixes", True, type=bool)
    reopened = main_window.MidiTitleWindow(settings=window.settings)
    try:
        assert not reopened.settingsRandomOutputSuffixesAction.isChecked()
        assert not reopened._random_output_suffixes_enabled()
        reopened.toggle_random_output_suffixes(True)
        assert reopened.settingsRandomOutputSuffixesAction.isChecked()
        assert reopened._random_output_suffixes_enabled()
    finally:
        reopened.close()


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
def test_random_output_suffixes_refresh_preserves_choice_and_localizes(window, language):
    window.toggle_random_output_suffixes(False)
    window.currentLanguage = language
    window._refresh_translated_ui()
    action = window.settingsRandomOutputSuffixesAction
    assert not action.isChecked()
    assert not window._random_output_suffixes_enabled()
    assert action.text().replace("&", "") == window._t("output_naming.action")
    assert action.toolTip() == window._t("output_naming.tooltip")
    if language != "en":
        assert action.text().replace("&", "") != "Add Random Suffixes to Output Names"
    spec = next(item for item in window._keyboard_shortcut_specs()
                if item["id"] == "settings.random_output_suffixes")
    assert spec["label"] == window._t("output_naming.action")
    assert spec["default"] == ""


@pytest.mark.parametrize("enabled", [False, True])
def test_random_output_suffixes_startup_override_precedes_widgets(window, tmp_path, enabled):
    window.toggle_random_output_suffixes(not enabled)
    path = tmp_path / "startup.json"
    path.write_text(json.dumps({"random_output_suffixes": enabled}), encoding="utf-8")
    reopened = main_window.MidiTitleWindow(
        settings=window.settings, initial_settings=load_startup_config(path),
    )
    try:
        assert reopened.settingsRandomOutputSuffixesAction.isChecked() is enabled
        assert reopened._random_output_suffixes_enabled() is enabled
    finally:
        reopened.close()
