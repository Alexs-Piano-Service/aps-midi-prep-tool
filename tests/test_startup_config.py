"""Deployment settings, USB relocation, and startup ordering with real QSettings."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from aps_midi_prep_tool_app import main_window, onboarding_dialog, startup_config
from aps_midi_prep_tool_app.startup_config import (
    CONFIG_FILENAME, StartupConfigError, apply_startup_config, load_startup_config,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    result = QSettings(str(tmp_path / "saved.ini"), QSettings.IniFormat)
    result.setFallbacksEnabled(False)
    return result


def write_config(directory, values):
    path = directory / CONFIG_FILENAME
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


@pytest.mark.parametrize("appimage", [False, True])
def test_packaged_location_ignores_cwd_and_bundle_extraction(monkeypatch, tmp_path, appimage):
    usb = tmp_path / "USB stick"
    usb.mkdir()
    elsewhere = tmp_path / "launcher"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(startup_config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup_config.sys, "_MEIPASS", str(elsewhere), raising=False)
    monkeypatch.setattr(startup_config.sys, "executable", str(usb / "APSMidiPrepTool.exe"))
    monkeypatch.delenv("APPIMAGE", raising=False)
    if appimage:
        monkeypatch.setattr(startup_config.sys, "platform", "linux")
        monkeypatch.setattr(startup_config.sys, "executable", str(elsewhere / "usr/bin/APS"))
        monkeypatch.setenv("APPIMAGE", str(usb / "APSMidiPrepTool.AppImage"))
    write_config(elsewhere, {"language": "de"})
    assert load_startup_config() == {}
    write_config(usb, {"language": "fr"})
    assert startup_config.startup_config_path() == usb / CONFIG_FILENAME
    assert load_startup_config() == {"language": "fr"}


def test_source_location_is_beside_entry_script(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(startup_config.sys, "frozen", False, raising=False)
    assert startup_config.startup_config_path() == Path(__file__).resolve().parents[1] / CONFIG_FILENAME


def test_relative_paths_follow_relocated_usb_and_preserve_empty_and_device_paths(tmp_path):
    values = {
        key: "./Música/Nalbantov" for key in startup_config.PATH_SETTINGS
    }
    values.update({"bulk_extraction_output": "", "greaseweazle_device_path": "COM3"})
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    original.mkdir()
    relocated.mkdir()
    for directory in (original, relocated):
        path = write_config(directory, values)
        before = path.read_bytes()
        loaded = load_startup_config(path)
        for key in startup_config.PATH_SETTINGS - {"bulk_extraction_output"}:
            assert loaded[key] == str(directory / "Música/Nalbantov")
        assert loaded["bulk_extraction_output"] == ""
        assert loaded["greaseweazle_device_path"] == "COM3"
        assert path.read_bytes() == before


def test_utf8_bom_and_absolute_parent_and_backslash_paths(tmp_path):
    path = tmp_path / CONFIG_FILENAME
    values = {
        "save_as_location": str(tmp_path / "absolute"),
        "open_folder_location": "../Music",
        "open_image_location": ".\\Nalbantov",
    }
    path.write_text(json.dumps(values), encoding="utf-8-sig")
    loaded = load_startup_config(path)
    assert loaded["save_as_location"] == values["save_as_location"]
    assert loaded["open_folder_location"] == str(tmp_path.parent / "Music")
    assert loaded["open_image_location"] == str(tmp_path / "Nalbantov")


@pytest.mark.parametrize("contents", [
    '{', '[]', 'null', '{"language": "en", "language": "de"}',
    '{"store_backups": "false"}', '{"store_backups": 1}',
    '{"emulator_image_starting_number": true}',
    '{"emulator_image_starting_number": "one"}',
    '{"emulator_image_starting_number": -1}',
    '{"emulator_image_starting_number": 1e100}',
    '{"emulator_image_starting_number": 9999999999999999999999999999}',
    '{"language": ["en"]}', '{"language": null}',
    '{"language": "en\\u0000"}', '{"langauge": "de"}',
    '{"preparation_profile": "invalid"}',
    '{"preparation_medium": "invalid"}',
    '{"preparation_profile": "enspire", "preparation_medium": "nalbantov"}',
    '{"keyboard_shortcuts/": "Ctrl+O"}',
])
def test_invalid_file_is_rejected_before_applying_any_entries(tmp_path, settings, contents):
    settings.setValue("language", "fr")
    path = tmp_path / CONFIG_FILENAME
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(StartupConfigError) as error:
        apply_startup_config(settings, load_startup_config(path))
    assert str(path) in str(error.value)
    assert settings.allKeys() == ["language"]
    assert settings.value("language") == "fr"


def test_missing_empty_and_unreadable_configuration(tmp_path, settings):
    settings.setValue("language", "fr")
    assert load_startup_config(tmp_path / CONFIG_FILENAME) == {}
    apply_startup_config(settings, load_startup_config(write_config(tmp_path, {})))
    assert settings.value("language") == "fr"
    with pytest.raises(StartupConfigError):
        load_startup_config(tmp_path)  # A directory cannot be read as a config file.
    (tmp_path / CONFIG_FILENAME).write_bytes(b"\xff\xfe")
    with pytest.raises(StartupConfigError):
        load_startup_config(tmp_path / CONFIG_FILENAME)


def test_config_overrides_saved_preferences_each_launch_and_allows_session_changes(tmp_path, settings):
    settings.setValue("language", "fr")
    settings.setValue("store_backups", False)
    path = write_config(tmp_path, {"language": "de", "keyboard_shortcuts/file.save": "Alt+S"})
    apply_startup_config(settings, load_startup_config(path))
    assert settings.value("language") == "de"
    assert settings.value("store_backups", type=bool) is False
    assert settings.value("keyboard_shortcuts/file.save") == "Alt+S"
    settings.setValue("language", "it")
    assert settings.value("language") == "it"
    apply_startup_config(settings, load_startup_config(path))
    assert settings.value("language") == "de"


def test_instrument_uses_its_default_medium_and_clears_old_image_overrides(settings):
    settings.setValue("preparation_medium", "nalbantov")
    settings.setValue("preparation_image_format", "hfe")
    settings.setValue("preparation_disk_format", "ibm.720")
    apply_startup_config(settings, {"preparation_profile": "enspire"})
    assert settings.value("preparation_medium") == "usb"
    assert settings.value("emulator_image_content") == "midi"
    assert settings.value("long_midi_filenames", type=bool) is True
    assert not settings.contains("preparation_image_format")
    assert not settings.contains("preparation_disk_format")


def test_config_reaches_ui_and_onboarding_after_first_run_migrations(
    application, monkeypatch, tmp_path, settings,
):
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(onboarding_dialog, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(QDialog, "exec", lambda _self: pytest.fail("Welcome should be hidden"))
    path = write_config(tmp_path, {
        "preparation_profile": "mark_ii", "preparation_medium": "nalbantov",
        "language": "de", "appearance_mode": "dark", "font_scale": "small",
        "store_backups": False, "skip_first_time_dialog": True,
        "skip_type0_warning": True, "skip_eseq_to_midi_conversion_prompt": True,
        "eseq_to_midi_switch_mode": "switch", "hide_gw_sector_report_read_v1": True,
        "emulator_image_starting_number": 12, "keyboard_shortcuts/file.save": "Alt+S",
    })
    # Repeat to exercise both a new installation and an existing user's startup.
    for _ in range(2):
        window = main_window.MidiTitleWindow(initial_settings=load_startup_config(path))
        try:
            assert window.currentLanguage == "de"
            assert window.currentAppearanceMode == "dark"
            assert window.currentFontScale == "small"
            assert window.regularEseqMode
            assert window._preparation_profile().key == "mark_ii"
            assert window._preparation_medium().key == "nalbantov"
            assert settings.value("emulator_image_output_format") == "hfe"
            assert settings.value("emulator_image_disk_format") == "ibm.720"
            assert settings.value("emulator_image_prefix") == "DSKA"
            assert settings.value("emulator_image_starting_number", type=int) == 12
            for key in ("skip_type0_warning", "skip_eseq_to_midi_conversion_prompt", "hide_gw_sector_report_read_v1"):
                assert settings.value(key, type=bool) is True
            assert settings.value("store_backups", type=bool) is False
            assert settings.value("eseq_to_midi_switch_mode") == "switch"
            assert window.keyboardShortcutObjects["file.save"].key().toString() == "Alt+S"
            assert window._regular_file_count() == 0
            onboarding_dialog.show_first_time_dialog(parent=window)
        finally:
            window.close()


def test_open_dialog_paths_use_config_and_remember_selection(application, monkeypatch, tmp_path, settings):
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    window = main_window.MidiTitleWindow(initial_settings={
        "open_folder_location": str(tmp_path), "open_image_location": str(tmp_path),
    })
    selected = tmp_path / "Nalbantov"
    selected.mkdir()
    def open_file(_parent, _title, directory, _filters):
        assert directory == str(tmp_path)
        return str(selected / "DSKA0000.HFE"), ""
    def open_folder(_parent, _title, directory):
        assert directory == str(tmp_path)
        return str(selected)
    monkeypatch.setattr(main_window.QFileDialog, "getOpenFileName", open_file)
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", open_folder)
    loaded = []
    monkeypatch.setattr(window, "load_image_file", loaded.append)
    try:
        window.open_image_dialog()
        window.browse_directory()
        assert loaded == [str(selected / "DSKA0000.HFE")]
        assert settings.value("open_image_location") == str(selected)
        assert settings.value("open_folder_location") == str(selected)
    finally:
        window.close()


def test_all_saved_setting_constants_and_literal_keys_are_configurable():
    known = startup_config.BOOLEAN_SETTINGS | startup_config.INTEGER_SETTINGS | startup_config.STRING_SETTINGS
    root = Path(startup_config.__file__).parent
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("SETTING_"):
                        assert node.value.value in known | {"keyboard_shortcuts"}, (path.name, target.id)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"setValue", "value"} and "settings" in ast.unparse(node.func.value).lower():
                    if node.args and isinstance(node.args[0], ast.Constant):
                        assert node.args[0].value in known, (path.name, node.lineno)


def test_shipped_example_is_valid_and_all_settings_are_documented(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / CONFIG_FILENAME
    path.write_bytes((root / "docs/examples" / CONFIG_FILENAME).read_bytes())
    values = load_startup_config(path)
    assert values["preparation_profile"] == "mark_ii"
    assert values["emulator_image_output"] == str(tmp_path / "Nalbantov")
    guide = (root / "docs/startup-configuration.md").read_text(encoding="utf-8")
    for key in startup_config.BOOLEAN_SETTINGS | startup_config.INTEGER_SETTINGS | startup_config.STRING_SETTINGS:
        assert f"`{key}`" in guide, key


@pytest.mark.parametrize("valid", [True, False])
def test_entry_point_loads_config_before_ui_and_reports_errors(tmp_path, valid):
    path = write_config(tmp_path, {
        "language": "de", "skip_first_time_dialog": True,
        "preparation_profile": "mark_ii", "preparation_medium": "nalbantov",
    })
    if not valid:
        path.write_text('{"language": "de",', encoding="utf-8")
    # A separate process exercises the actual QApplication startup and queued
    # dialogs without sharing the test runner's QApplication or native settings.
    script = r'''
import sys
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from aps_midi_prep_tool_app import app, main_window, onboarding_dialog, startup_config
from aps_midi_prep_tool_app.app_info import SETTINGS_APP

path = Path(sys.argv[1])
valid = sys.argv[2] == "True"
native_settings = QtCore.QSettings
def isolated_settings(org, name):
    result = native_settings(str(path.parent / (name + ".ini")), native_settings.IniFormat)
    result.setFallbacksEnabled(False)
    return result
QtCore.QSettings = isolated_settings
main_window.QSettings = isolated_settings
onboarding_dialog.QSettings = isolated_settings
settings = isolated_settings("", SETTINGS_APP)
settings.setValue("language", "fr")
settings.setValue("store_backups", False)
startup_config.startup_config_path = lambda: path
warnings = []
def show_warning(message):
    warnings.append(message.informativeText())
    return 0
QtWidgets.QMessageBox.exec = show_warning
observed = []
def startup_dialog(app_icon, parent):
    observed.append((parent.currentLanguage, parent._preparation_profile().key,
                     parent.settings.value("skip_first_time_dialog", False, type=bool),
                     parent.settings.value("store_backups", type=bool)))
    QtWidgets.QApplication.instance().quit()
onboarding_dialog.show_first_time_dialog = startup_dialog
try:
    app.main()
except SystemExit as exc:
    assert exc.code == 0
assert observed == [("de", "mark_ii", True, False) if valid else ("fr", "unsure", False, False)], observed
assert len(warnings) == (0 if valid else 1), warnings
if warnings:
    assert str(path) in warnings[0]
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(valid)],
        capture_output=True, text=True, timeout=20,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stdout + result.stderr
