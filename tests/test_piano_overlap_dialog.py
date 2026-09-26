"""The overlap prompt is conditional, persistent only by opt-in, and resettable."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QLabel

from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_inspection_staging import window, _item, _open_image
from test_piano_overlap import song, intervals


def _source(tmp_path, overlapping=True, native=False, name="SONG"):
    data = song([(1, 0, 120, 60, 80), (2, 20, 40, 60 if overlapping else 64, 90)])
    if native:
        data = convert_midi_bytes_to_eseq_bytes(data, timing_policy="preserve", pedal_policy="preserve")
    path = tmp_path / (name + (".FIL" if native else ".MID"))
    path.write_bytes(data)
    return path


def _dialog_choice(monkeypatch, window, mode="retrigger", remember=False, accepted=True):
    seen = []

    def choose(dialog):
        assert dialog.windowTitle() == window._lt("Overlapping Piano Notes")
        combo = dialog.findChild(QComboBox, "pianoOverlapBehavior")
        checkbox = dialog.findChild(QCheckBox, "rememberPianoOverlapBehavior")
        assert not checkbox.isChecked()
        seen.append(dialog)
        combo.setCurrentIndex(combo.findData(mode))
        checkbox.setChecked(remember)
        return QDialog.Accepted if accepted else QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", choose)
    return seen


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("image_mode", [False, True])
@pytest.mark.parametrize("inspector", [False, True])
def test_overlap_dialog_only_for_affected_song_in_every_entrypoint(
    window, tmp_path, monkeypatch, native, image_mode, inspector,
):
    affected = _source(tmp_path, native=native)
    clean = _source(tmp_path, overlapping=False, native=native, name="CLEAN")
    original = affected.read_bytes()
    if image_mode:
        _open_image(window, tmp_path, [(affected, affected.name), (clean, clean.name)])
        affected_key, clean_key = affected.name, clean.name
    else:
        window._load_regular_files([str(affected), str(clean)], "Overlap test", prepare_destination=False)
        affected_key, clean_key = str(affected), str(clean)
    seen = _dialog_choice(monkeypatch, window)
    if inspector:
        assert window._stage_inspected_midi_action(_item(window, clean_key), "piano")["changed"]
        assert not seen
        assert window._stage_inspected_midi_action(_item(window, affected_key), "piano")["changed"]
    else:
        monkeypatch.setattr(window, "_channel_merging_options_dialog", lambda _: -1)
        window.show_channel_merging_utility()
    assert len(seen) == 1
    assert affected.name in " ".join(label.text() for label in seen[0].findChildren(QLabel))
    staged = Path(_item(window, affected_key)["path"]).read_bytes()
    assert staged != original and affected.read_bytes() == original
    if not native:
        assert intervals(staged) == [(0, 20, 60, 80), (20, 40, 60, 90)]
    assert not window.settings.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, False, type=bool)
    # An already merged staged song is a no-op, including if merge-only was chosen.
    window._stage_inspected_midi_action(_item(window, affected_key), "piano")
    assert len(seen) == 1


@pytest.mark.parametrize("mode", ["smart", "retrigger", "off"])
def test_remembered_choice_applies_to_later_files_persists_and_reset_shows_again(window, tmp_path, monkeypatch, mode):
    sources = [_source(tmp_path, name=f"SONG{index}") for index in range(3)]
    window._load_regular_files([str(path) for path in sources], "Overlap test", prepare_destination=False)
    seen = _dialog_choice(monkeypatch, window, mode, remember=True)
    monkeypatch.setattr(window, "_channel_merging_options_dialog", lambda _: -1)
    window.show_channel_merging_utility()
    assert len(seen) == 1
    assert len(window.pendingRegularConversions) == 3
    # Read from a fresh settings instance, as on application restart.
    restored = QSettings(window.settings.fileName(), QSettings.IniFormat)
    assert restored.value(window.SETTING_PIANO_OVERLAP_MODE) == mode
    assert restored.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, type=bool)
    window.settings = restored
    assert window._piano_overlap_behavior("Later song", 1) == mode
    assert len(seen) == 1
    window.reset_hidden_dialog_settings()
    window.undo_last_staged_batch()
    seen = _dialog_choice(monkeypatch, window, mode, remember=False)
    window.show_channel_merging_utility()
    assert len(seen) == 3


@pytest.mark.parametrize("inspector", [False, True])
@pytest.mark.parametrize("image_mode", [False, True])
def test_cancel_does_not_stage_or_remember(window, tmp_path, monkeypatch, inspector, image_mode):
    source = _source(tmp_path)
    original = source.read_bytes()
    if image_mode:
        _open_image(window, tmp_path, [(source, source.name)])
        key = source.name
    else:
        window._load_regular_files([str(source)], "Overlap test", prepare_destination=False)
        key = str(source)
    before = window._staged_signature()
    seen = _dialog_choice(monkeypatch, window, remember=True, accepted=False)
    if inspector:
        result = window._stage_inspected_midi_action(_item(window, key), "piano")
        assert not result["changed"]
        assert result["message"] == "Channel merge canceled."
    else:
        monkeypatch.setattr(window, "_channel_merging_options_dialog", lambda _: -1)
        window.show_channel_merging_utility()
    assert len(seen) == 1
    assert before == window._staged_signature()
    assert not window._staged_undo_stack
    assert not window.settings.contains(window.SETTING_PIANO_OVERLAP_MODE)
    assert not window.settings.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, False, type=bool)
    assert source.read_bytes() == original
    assert "Channel merge canceled." in window.status_label.text()


def test_invalid_remembered_behavior_prompts_again(window, monkeypatch):
    window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, "obsolete")
    window.settings.setValue(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, True)
    seen = _dialog_choice(monkeypatch, window)
    assert window._piano_overlap_behavior("SONG.MID", 1) == "retrigger"
    assert len(seen) == 1


def test_unchecked_choice_does_not_persist_between_songs(window, monkeypatch):
    seen = _dialog_choice(monkeypatch, window, "off")
    for filename in ("FIRST.MID", "SECOND.MID"):
        assert window._piano_overlap_behavior(filename, 1) == "off"
    assert len(seen) == 2
    assert not window.settings.contains(window.SETTING_PIANO_OVERLAP_MODE)
    assert not window.settings.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, False, type=bool)


@pytest.mark.parametrize("mode", ["smart", "retrigger", "off"])
@pytest.mark.parametrize("automatic", [False, True])
def test_settings_changes_saved_behavior_and_can_restore_prompting(window, monkeypatch, mode, automatic):
    original_mode = "off" if mode == "smart" else "smart"
    window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, original_mode)
    window.settings.setValue(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, True)
    window.settings.setValue(window.SETTING_SKIP_TYPE0_WARNING, True)
    before = window._staged_signature()
    seen = []

    def edit(dialog):
        seen.append(dialog)
        combo = dialog.findChild(QComboBox, "pianoOverlapBehavior")
        checkbox = dialog.findChild(QCheckBox, "rememberPianoOverlapBehavior")
        assert combo.currentData() == original_mode
        assert checkbox.isChecked()
        assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save) is not None
        combo.setCurrentIndex(combo.findData(mode))
        checkbox.setChecked(automatic)
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", edit)
    action = window.settingsPianoOverlapAction
    assert action in window.settingsMenu.actions()
    assert action.isEnabled()  # Settings are available without loading a song.
    action.trigger()
    assert len(seen) == 1
    assert before == window._staged_signature()
    restored = QSettings(window.settings.fileName(), QSettings.IniFormat)
    assert restored.value(window.SETTING_PIANO_OVERLAP_MODE) == mode
    assert restored.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, type=bool) is automatic
    assert restored.value(window.SETTING_SKIP_TYPE0_WARNING, type=bool)
    window.settings = restored
    prompts = _dialog_choice(monkeypatch, window, mode)
    for filename in ("FIRST.MID", "SECOND.MID"):
        assert window._piano_overlap_behavior(filename, 1) == mode
    assert len(prompts) == (0 if automatic else 2)


@pytest.mark.parametrize("mode,automatic", [(None, False), ("obsolete", True)])
def test_settings_defaults_require_opt_in(window, monkeypatch, mode, automatic):
    if mode is not None:
        window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, mode)
        window.settings.setValue(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, automatic)

    def save_defaults(dialog):
        assert dialog.findChild(QComboBox, "pianoOverlapBehavior").currentData() == "smart"
        assert not dialog.findChild(QCheckBox, "rememberPianoOverlapBehavior").isChecked()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", save_defaults)
    window.settingsPianoOverlapAction.trigger()
    prompts = _dialog_choice(monkeypatch, window)
    assert window._piano_overlap_behavior("SONG.MID", 1) == "retrigger"
    assert len(prompts) == 1
    assert not window.settings.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, type=bool)


@pytest.mark.parametrize("automatic", [False, True])
def test_cancelling_settings_preserves_preferences(window, monkeypatch, automatic):
    window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, "off")
    window.settings.setValue(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, automatic)

    def cancel(dialog):
        combo = dialog.findChild(QComboBox, "pianoOverlapBehavior")
        combo.setCurrentIndex(combo.findData("retrigger"))
        dialog.findChild(QCheckBox, "rememberPianoOverlapBehavior").setChecked(not automatic)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", cancel)
    window.settingsPianoOverlapAction.trigger()
    assert window.settings.value(window.SETTING_PIANO_OVERLAP_MODE) == "off"
    assert window.settings.value(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, type=bool) is automatic


@pytest.mark.parametrize("remap, overlapping, accepted, prompts", [
    (True, True, True, 1), (True, True, False, 1),
    (True, False, True, 0), (False, True, True, 0),
])
def test_type0_optional_piano_merge_uses_same_overlap_choice(
    window, tmp_path, monkeypatch, remap, overlapping, accepted, prompts,
):
    source = _source(tmp_path, overlapping=overlapping)
    original = source.read_bytes()
    window._load_regular_files([str(source)], "Overlap test", prepare_destination=False)
    monkeypatch.setattr(window, "_confirm_type0_conversion", lambda *a, **k: remap)
    seen = _dialog_choice(monkeypatch, window, accepted=accepted)
    window.convert_all_to_type0()
    assert len(seen) == prompts
    assert source.read_bytes() == original
    if not accepted:
        assert not window.pendingRegularConversions
        assert not window._staged_undo_stack
        assert "Channel merge canceled." in window.status_label.text()
    else:
        staged = Path(_item(window, source)["path"]).read_bytes()
        assert int.from_bytes(staged[8:10], "big") == 0
        if remap and overlapping:
            assert intervals(staged) == [(0, 20, 60, 80), (20, 40, 60, 90)]


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
@pytest.mark.parametrize("editing_settings", [False, True])
def test_overlap_dialog_is_localized(window, monkeypatch, language, editing_settings):
    window._set_language(language)
    assert window.settingsPianoOverlapAction.text().replace("&", "") == translate_text(
        "Overlapping Piano Notes...", language,
    )
    seen = []

    def inspect(dialog):
        seen.append(dialog)
        expected = lambda source: translate_text(source, language)
        assert dialog.windowTitle() == expected("Overlapping Piano Notes")
        combo = dialog.findChild(QComboBox, "pianoOverlapBehavior")
        assert combo.itemText(0) == expected("Smart repair")
        assert combo.itemText(1) == expected("Keep attacks — trim overlaps")
        assert combo.itemText(2) == expected("Merge only — keep overlaps")
        assert dialog.findChild(QCheckBox).text() == expected("Use this behavior for all future channel merges")
        note = (
            "Choose how to handle overlapping notes when merging channels. "
            "Leave the box unchecked to be asked for each affected song."
            if editing_settings else
            "You can change this any time in Settings → Overlapping Piano Notes..."
        )
        assert expected(note) in [label.text() for label in dialog.findChildren(QLabel)]
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    if editing_settings:
        window.settingsPianoOverlapAction.trigger()
    else:
        assert window._piano_overlap_behavior("SONG.MID", 2) is None
    assert len(seen) == 1
