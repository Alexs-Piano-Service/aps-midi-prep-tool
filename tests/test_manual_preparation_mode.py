"""Custom keeps current work; Reset preparation removes the automatic layer."""

import io

import mido
import pytest
from PySide6.QtWidgets import QDialog

from test_preparation_layers import (  # noqa: F401
    _apply, _assert_sources_unchanged, _edit_first_title, _export, _load,
    _materials, _rows, _settle_import, _song, _structure, destination_window,
)


def _preferences(window):
    return {key: window.settings.value(key) for key in window.settings.allKeys()
            if key not in {"preparation_profile", "preparation_medium"}}


def _choose_custom(window, monkeypatch, entry):
    if entry == "button":
        window.preparationCustomButton.click()
    else:
        def select_custom(dialog):
            dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("custom"))
            assert dialog.selection()[0].key == "custom"
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(window, "_exec_child_dialog", select_custom)
        window.choose_preparation_profile()
    assert window._preparation_profile().key == "custom"
    assert window._preparation_medium().key == "custom"


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("profile", ("mark_ii", "pianodisc_128plus"))
@pytest.mark.parametrize("entry", ("button", "dialog"))
def test_both_custom_entry_points_keep_every_preference_and_staged_change(
    destination_window, monkeypatch, tmp_path, image_mode, profile, entry,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, profile)
    _edit_first_title(window, monkeypatch, "My manual title")
    window.settings.setValue(window.SETTING_SAVE_AS_IMAGE_FORMAT, "hfe")
    window.settings.setValue("emulator_image_starting_number", 237)
    before_preferences = _preferences(window)
    before_work = window._staged_signature()[:-1]
    before_materials = _materials(window)
    history_count = len(window._staged_undo_stack)
    window._schedule_destination_preparation()
    assert window._destination_preparation_queued

    _choose_custom(window, monkeypatch, entry)
    _settle_import()

    assert _preferences(window) == before_preferences
    assert window._staged_signature()[:-1] == before_work
    assert _materials(window) == before_materials
    assert not window._destination_preparation_queued
    assert len(window._staged_undo_stack) == history_count + 1
    assert window.editResetPreparationAction.isEnabled()
    assert window._preparation_conversion_restriction("midi") == ""
    assert window._preparation_conversion_restriction("eseq") == ""

    window.undo_last_staged_batch()

    assert window._preparation_profile().key == profile
    assert _preferences(window) == before_preferences
    assert _materials(window) == before_materials
    assert window._row_raw_title(_rows(window)[0][0]) == "My manual title"
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("entry", ("button", "dialog"))
def test_custom_keeps_provenance_so_the_next_destination_prepares_from_originals(
    destination_window, monkeypatch, tmp_path, image_mode, entry,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, "mark_ii")
    _choose_custom(window, monkeypatch, entry)
    assert _rows(window)[0][2] == "eseq"

    _apply(window, "midi_export")

    assert _export(window, tmp_path / "export") == {"SONG1.MID": _song()}
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("enter_custom", (False, True), ids=("active-destination", "manual-mode"))
def test_reset_preparation_preserves_manual_title_preferences_and_can_be_undone(
    destination_window, monkeypatch, tmp_path, image_mode, enter_custom,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, "mark_ii")
    _edit_first_title(window, monkeypatch, "Deliberate title")
    window.settings.setValue(window.SETTING_SAVE_AS_IMAGE_FORMAT, "img")
    window.settings.setValue("emulator_image_starting_number", 42)
    if enter_custom:
        _choose_custom(window, monkeypatch, "dialog")
    previous_profile = window._preparation_profile().key
    before_preferences = _preferences(window)
    prepared = _materials(window)
    history_count = len(window._staged_undo_stack)
    assert window.editResetPreparationAction.isEnabled()

    window.editResetPreparationAction.trigger()

    assert window._preparation_profile().key == "custom"
    assert _preferences(window) == before_preferences
    assert _rows(window)[0][2] == "midi"
    assert not window.pendingGeneratePianodir
    assert not window.editResetPreparationAction.isEnabled()
    assert len(window._staged_undo_stack) == history_count + 1
    assert window._row_raw_title(_rows(window)[0][0]) == "Deliberate title"
    restored = next(iter(_export(window, tmp_path / "reset-export").values()))
    assert _structure(restored, ignore_titles=True) == _structure(_song(), ignore_titles=True)

    window.undo_last_staged_batch()

    assert window._preparation_profile().key == previous_profile
    assert _preferences(window) == before_preferences
    assert _rows(window)[0][2] == "eseq"
    assert _materials(window) == prepared
    assert window._row_raw_title(_rows(window)[0][0]) == "Deliberate title"
    assert window.editResetPreparationAction.isEnabled()
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_manual_mode_utility_edits_current_prepared_song_without_repreparing(
    destination_window, monkeypatch, tmp_path, image_mode,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, "pianodisc_128plus")
    _choose_custom(window, monkeypatch, "button")
    before = _materials(window)
    assert mido.MidiFile(file=io.BytesIO(before["SONG1.MID"])).type == 0
    utility = (window._apply_pedal_compatibility_to_image_rows if image_mode
               else window._apply_pedal_compatibility_to_regular_rows)

    utility([(row, source) for row, source, _kind, _name in _rows(window)], {"binary_pedal": True})

    assert window._preparation_profile().key == "custom"
    output = _export(window, tmp_path / "export")["SONG1.MID"]
    midi = mido.MidiFile(file=io.BytesIO(output))
    assert midi.type == 0
    assert [message.value for track in midi.tracks for message in track
            if message.type == "control_change" and message.control == 66] == [127, 0]
    window.undo_last_staged_batch()
    assert _materials(window) == before
    assert window._preparation_profile().key == "custom"
    _assert_sources_unchanged(originals)
