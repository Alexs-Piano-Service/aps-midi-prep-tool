import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    instance = main_window.MidiTitleWindow()
    title = b"  Song  "
    track = b"\x00\xff\x03" + bytes([len(title)]) + title + b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    song = tmp_path / "SONG.MID"
    original = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track
    song.write_bytes(original)
    instance._load_regular_files([str(song)], "Loaded")
    yield instance, app, song
    assert song.read_bytes() == original
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


def _prepare(window, key="mark_i"):
    profile = get_preparation_profile(key)
    window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))


def test_edit_menu_replaces_buttons_and_ctrl_z_undoes_last_change(window):
    w, app, _song = window
    menu_names = [action.text().replace("&", "") for action in w.menuBar().actions()]
    assert menu_names[:3] == ["File", "Edit", "Disk"]
    assert not hasattr(w, "pendingChangesButton")
    assert not hasattr(w, "undoBatchButton")
    assert not any(button.text() in {"No unsaved song changes", "Undo Last Batch"}
                   for button in w.findChildren(QPushButton))
    assert w.editUndoAction.text() == "Undo\tCtrl+Z"
    assert not w.editUndoAction.isEnabled()
    w.trim_title_spaces_for_all(show_summary=False)
    assert w.pendingEdits
    assert w.editUndoAction.isEnabled()
    assert w.editUndoAllAction.isEnabled()
    assert w.editReviewChangesAction.isEnabled()
    w.show()
    w.activateWindow()
    w.table.setFocus()
    app.processEvents()
    QTest.keyClick(w.table, Qt.Key_Z, Qt.ControlModifier)
    app.processEvents()
    assert not w.pendingEdits
    assert not w.editUndoAction.isEnabled()


def test_undo_all_restores_titles_and_preparation_in_one_action(window):
    w, _app, song = window
    w.trim_title_spaces_for_all(show_summary=False)
    _prepare(w)
    assert w.pendingRegularConversions
    assert w._preparation_profile().key == "mark_i"
    w.editUndoAllAction.trigger()
    assert not w.pendingEdits
    assert not w.pendingRegularConversions
    assert not w.pendingGeneratePianodir
    assert w._preparation_profile().key == "unsure"
    assert w._row_raw_title(w._find_regular_row_for_path(str(song))) == "  Song  "
    assert not w.editUndoAction.isEnabled()
    assert not w.editUndoAllAction.isEnabled()


@pytest.mark.parametrize("appearance", ("light", "dark"))
def test_preparation_stays_one_row_and_custom_disengages_with_one_click(window, appearance):
    w, app, song = window
    w._apply_appearance_mode(appearance)
    _prepare(w, "mark_ii")
    w.resize(900, 720)
    w.show()
    app.processEvents()
    assert w.preparationBar.property("preparationActive") is True
    assert "background-color" in w.preparationBar.styleSheet()
    assert ("#193a32" if appearance == "dark" else "#e0f2ec") in w.preparationBar.styleSheet()
    assert not w.preparationLabel.wordWrap()
    assert w.preparationLabel.text() == "Mark II · Original floppy drive · E-SEQ · 720 KB"
    assert "\n" not in w.preparationLabel.text()
    assert w.preparationBar.height() <= w.preparationButton.height() + 10
    assert not w.preparationCustomButton.isHidden()
    pending_path = w.pendingRegularConversions[str(song)]["temp_path"]
    QTest.mouseClick(w.preparationCustomButton, Qt.LeftButton)
    assert w._preparation_profile().key == "custom"
    assert w.preparationBar.property("preparationActive") is False
    assert not w.preparationBar.styleSheet()
    assert w.preparationCustomButton.isHidden()
    assert w.preparationLabel.text() == "Custom"
    assert w.convertEseqToMidiButton.isEnabled()
    assert w.pendingRegularConversions[str(song)]["temp_path"] == pending_path


def test_busy_operation_disables_undo_and_custom(window):
    w, _app, _song = window
    _prepare(w)
    w._set_disk_load_busy(True)
    assert not w.editUndoAction.isEnabled()
    assert not w.editUndoAllAction.isEnabled()
    assert not w.preparationCustomButton.isEnabled()
    w._set_disk_load_busy(False)
    assert w.editUndoAction.isEnabled()
    assert w.preparationCustomButton.isEnabled()


def test_preparation_bar_refreshes_profile_delivery_controls_and_tooltips_in_every_language(window):
    w, app, _song = window
    profile = get_preparation_profile("pianodisc_prodigy")
    medium = get_preparation_medium(profile, "pianodisc_app")
    w.settings.setValue("preparation_profile", profile.key)
    w.settings.setValue("preparation_medium", medium.key)
    w.show()
    for language in SUPPORTED_LANGUAGES:
        w.currentLanguage = language.code
        w._refresh_translated_ui()
        app.processEvents()
        assert w.preparationButton.text() == translate_text("Preparing for...", language.code)
        assert w.preparationAction.text() == translate_text("Preparing for...", language.code)
        assert w.preparationCustomButton.text() == translate_text("Custom", language.code)
        assert w.preparationCustomButton.toolTip() == translate_text("Switch to Custom", language.code)
        assert w.preparationLabel.text() == " · ".join((
            translate_text(profile.label, language.code), translate_text(medium.label, language.code), "MIDI",
        ))
        assert w.preparationLabel.toolTip() == translate_text(
            "Preparing for {profile} uses {format}. Conversion to {other} is disabled. "
            "Change the destination or choose Custom to enable it.",
            language.code, profile=translate_text(profile.label, language.code), format="MIDI", other="E-SEQ",
        )
        assert w.preparationCustomButton.isVisible()


@pytest.mark.parametrize("profile_key,medium,expected", (
    ("mark_ii", "nalbantov_slim", 0),
    ("mark_ii_xg", "flashfloppy_img", 0),
    ("custom", "custom", 1),
))
def test_startup_corrects_preset_numbering_and_preserves_manual_settings(
    tmp_path, monkeypatch, profile_key, medium, expected,
):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "numbering.ini"), QSettings.IniFormat)
    settings.setValue("preparation_profile", profile_key)
    settings.setValue("preparation_medium", medium)
    settings.setValue("emulator_image_starting_number", 1)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    w = main_window.MidiTitleWindow()
    try:
        assert settings.value("emulator_image_starting_number", type=int) == expected
    finally:
        w.close()
        app.processEvents()
