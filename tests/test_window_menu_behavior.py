"""Main-menu availability and keyboard behavior follow the current session."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mido
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "menus.ini"), QSettings.IniFormat)
    settings.setValue("preparation_profile", "custom")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


def _load_song(window, tmp_path):
    song = mido.MidiFile(type=1)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="  Song  "),
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=96),
    ]))
    path = tmp_path / "Descriptive song name.mid"
    song.save(path)
    window._load_regular_files([str(path)], "", prepare_destination=False)


FILE_OUTPUT_ACTIONS = (
    "fileSaveAction", "fileSaveAsAction", "fileSaveAsZipAction",
    "fileSaveAsImageAction", "fileClearListAction",
)


def test_empty_main_menu_disables_commands_that_need_content(window):
    for name in FILE_OUTPUT_ACTIONS:
        assert not getattr(window, name).isEnabled(), name
    assert not window.savePartialCaptureAction.isEnabled()
    assert window.fileOpenFolderAction.isEnabled()
    assert window.fileOpenImageAction.isEnabled()
    assert window.utilitiesRepairBootSectorAction.isEnabled()


def test_deferred_smart_pianosoft_cannot_be_opened_by_menu_or_saved_shortcut(window, monkeypatch):
    assert not window.ENABLE_SMART_PIANOSOFT_UTILITY
    assert not hasattr(window, "utilitiesSmartPianoSoftAction")
    assert "utilities.smart_pianosoft" not in {
        spec["id"] for spec in window._keyboard_shortcut_specs()
    }
    window.settings.setValue(
        window._shortcut_settings_key("utilities.smart_pianosoft"), "Ctrl+Alt+8",
    )
    window._setup_keyboard_shortcuts()
    assert "utilities.smart_pianosoft" not in window.keyboardShortcutObjects

    def unexpected_snapshot():
        pytest.fail("The deferred utility attempted to open the current list")

    monkeypatch.setattr(window, "_smart_pianosoft_loaded_source", unexpected_snapshot)
    window.show_smart_pianosoft_utility()
    assert getattr(window, "smartPianoSoftDialog", None) is None


def test_busy_main_menu_disables_session_commands_without_changing_panels(window, tmp_path):
    _load_song(window, tmp_path)
    names = FILE_OUTPUT_ACTIONS + (
        "utilitiesRenameAction", "utilitiesSmfAction", "verifyFloppyWriteAction",
    )
    for name in names:
        assert getattr(window, name).isEnabled(), name
    panel_buttons = (
        window.saveButton, window.saveAsButton, window.saveAsImageButton,
        window.clearButton, window.renameAllButton, window.convertType0Button,
    )
    before = [button.isEnabled() for button in panel_buttons]
    window._set_disk_load_busy(True)
    for name in names:
        assert not getattr(window, name).isEnabled(), name
    assert [button.isEnabled() for button in panel_buttons] == before
    assert window.viewLogsAction.isEnabled()
    window._set_disk_load_busy(False)
    for name in names:
        assert getattr(window, name).isEnabled(), name


def test_blank_image_keeps_image_export_and_clear_available(window):
    session = FloppyImageSession.create_blank_session(DISK_FORMAT_BY_KEY["ibm.720"])
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    assert window.is_image_mode()
    assert window.fileSaveAsImageAction.isEnabled()
    assert window.fileClearListAction.isEnabled()
    assert not window.fileSaveAsAction.isEnabled()
    assert not window.fileSaveAsZipAction.isEnabled()
    window.toggle_original_write_protection(False)
    assert window.fileSaveAction.isEnabled()
    window._set_disk_load_busy(True)
    assert not window.fileSaveAction.isEnabled()
    assert not window.fileSaveAsImageAction.isEnabled()


def test_partial_capture_requires_retained_file_and_idle_window(window, tmp_path):
    capture = tmp_path / "partial.img"
    window.lastPartialRecoveryDiagnostics = {"partial_capture_path": str(capture)}
    window._update_menu_actions()
    assert not window.savePartialCaptureAction.isEnabled()
    capture.write_bytes(b"retained sectors")
    window._update_menu_actions()
    assert window.savePartialCaptureAction.isEnabled()
    window._set_disk_load_busy(True)
    assert not window.savePartialCaptureAction.isEnabled()
    window._set_disk_load_busy(False)
    assert window.savePartialCaptureAction.isEnabled()
    capture.unlink()
    window._update_menu_actions()
    assert not window.savePartialCaptureAction.isEnabled()


def test_shortcuts_stay_off_menu_labels_and_are_not_registered_twice(window):
    for spec in window._keyboard_shortcut_specs():
        action = getattr(window, spec["action"])
        shortcut = window._shortcut_text_for_spec(spec)
        assert "\t" not in action.text(), spec["id"]
        assert action.text()
        assert action.shortcut().isEmpty(), spec["id"]
        if shortcut:
            assert window.keyboardShortcutObjects[spec["id"]].key().toString() == shortcut


def test_rebinding_or_clearing_shortcut_keeps_menu_labels_compact(window):
    specs = window._keyboard_shortcut_specs()
    assignments = {spec["id"]: window._shortcut_text_for_spec(spec) for spec in specs}
    assignments["file.save"] = "Ctrl+Alt+9"
    assignments["utilities.long_filenames"] = "Ctrl+Alt+8"
    before = [window.fileSaveAction.text(), window.utilitiesLongFilenamesAction.text()]
    window._save_keyboard_shortcuts(assignments)
    for _ in range(3):
        window._update_menu_actions()
    assert window.keyboardShortcutObjects["file.save"].key().toString() == "Ctrl+Alt+9"
    assert window.keyboardShortcutObjects["utilities.long_filenames"].key().toString() == "Ctrl+Alt+8"
    assert [window.fileSaveAction.text(), window.utilitiesLongFilenamesAction.text()] == before
    assert "&" in window.utilitiesLongFilenamesAction.text()
    for spec in specs:
        assert "\t" not in getattr(window, spec["action"]).text()
    assignments["file.save"] = ""
    window._save_keyboard_shortcuts(assignments)
    assert "\t" not in window.fileSaveAction.text()
    assert "file.save" not in window.keyboardShortcutObjects


def test_shortcut_triggers_once_and_respects_disabled_menu(window, tmp_path):
    _load_song(window, tmp_path)
    triggered = []
    window.fileSaveAction.triggered.disconnect()
    window.fileSaveAction.triggered.connect(lambda: triggered.append("save"))
    window.show()
    window.activateWindow()
    window.table.setFocus()
    QApplication.processEvents()
    QTest.keyClick(window.table, Qt.Key_S, Qt.ControlModifier)
    QApplication.processEvents()
    assert triggered == ["save"]
    window._set_disk_load_busy(True)
    QTest.keyClick(window.table, Qt.Key_S, Qt.ControlModifier)
    QApplication.processEvents()
    assert triggered == ["save"]


def test_new_quit_shortcut_respects_existing_custom_binding(window):
    window.settings.setValue("keyboard_shortcuts/file.clear_list", "Ctrl+Q")
    window._setup_keyboard_shortcuts()
    window._update_menu_actions()
    assert window.keyboardShortcutObjects["file.clear_list"].key().toString() == "Ctrl+Q"
    assert "\t" not in window.fileClearListAction.text()
    assert "file.quit" not in window.keyboardShortcutObjects
    assert "\t" not in window.fileQuitAction.text()

    window.settings.setValue("keyboard_shortcuts/file.quit", "Ctrl+Alt+9")
    window._setup_keyboard_shortcuts()
    window._update_menu_actions()
    assert window.keyboardShortcutObjects["file.clear_list"].key().toString() == "Ctrl+Q"
    assert window.keyboardShortcutObjects["file.quit"].key().toString() == "Ctrl+Alt+9"
    assert "\t" not in window.fileQuitAction.text()
