"""Manual catalog creation overrides profile defaults in actual saved output."""

import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.preparation_profiles import SETTING_GENERATE_YAMAHA_CATALOGS
from aps_midi_prep_tool_app.startup_config import load_startup_config
from aps_midi_prep_tool_app.yamaha_catalog_delivery import catalog_generation_enabled
from test_prepared_image_delivery import _apply, _song, window
from test_yamaha_catalog_delivery import _read


@pytest.mark.parametrize('profile, default', [('mark_iii', True), ('enspire', False), ('midi_export', False)])
def test_manual_catalog_creation_overrides_profile_and_survives_restart(window, profile, default):
    _apply(window, profile)
    action = window.settingsGenerateYamahaCatalogsAction
    assert action in window.fileSaveOptionsMenu.actions()
    assert action.isCheckable() and action.isEnabled()
    assert action.isChecked() is default
    action.trigger()
    assert catalog_generation_enabled(window) is not default
    reopened = main_window.MidiTitleWindow(settings=window.settings)
    try:
        assert reopened.settingsGenerateYamahaCatalogsAction.isChecked() is not default
        assert catalog_generation_enabled(reopened) is not default
    finally:
        reopened.close()


def test_profile_changes_restore_default_and_custom_keeps_manual_choice(window):
    _apply(window, 'enspire')
    window.settingsGenerateYamahaCatalogsAction.trigger()
    _apply(window, 'custom')
    assert window.settingsGenerateYamahaCatalogsAction.isChecked()
    _apply(window, 'enspire')
    assert not window.settingsGenerateYamahaCatalogsAction.isChecked()
    _apply(window, 'mark_iii')
    assert window.settingsGenerateYamahaCatalogsAction.isChecked()


@pytest.mark.parametrize('profile', ['enspire', 'mark_iii', 'custom'])
@pytest.mark.parametrize('enabled', [False, True])
def test_manual_choice_controls_new_catalogs_and_actual_output_names(window, tmp_path, profile, enabled):
    source = tmp_path / 'source'
    source.mkdir()
    song = source / 'A descriptive MIDI filename.mid'
    song.write_bytes(_song('Displayed song title'))
    original = song.read_bytes()
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window, profile)
    window.toggle_yamaha_catalog_generation(enabled)
    output = tmp_path / 'export'
    errors, *_ = window._export_regular_files_to_folder(str(output))
    assert not errors
    assert (output / 'PSONG.MNG').exists() is enabled
    assert (output / 'PDISK.MNG').exists() is enabled
    if enabled:
        songs, _album = _read(output)
        assert len(songs) == 1
        assert (output / songs[0].filename).is_file()
        assert songs[0].title == 'Displayed song title'
    elif profile in {'enspire', 'custom'}:
        assert (output / song.name).is_file()
    assert song.read_bytes() == original


def test_undo_catalog_toggle_restores_preference_and_staged_names(window, tmp_path):
    song = tmp_path / 'Long descriptive title.mid'
    song.write_bytes(_song('Song'))
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window, 'custom')
    window.toggle_yamaha_catalog_generation(True)
    assert window.pendingRegularRenames
    assert catalog_generation_enabled(window)
    window.undo_last_staged_batch()
    assert not window.pendingRegularRenames
    assert not catalog_generation_enabled(window)
    assert not window.settingsGenerateYamahaCatalogsAction.isChecked()


@pytest.mark.parametrize('language', [item.code for item in SUPPORTED_LANGUAGES])
def test_catalog_setting_retranslation_keeps_manual_choice(window, language):
    _apply(window, 'enspire')
    window.toggle_yamaha_catalog_generation(True)
    window.currentLanguage = language
    window._refresh_translated_ui()
    action = window.settingsGenerateYamahaCatalogsAction
    assert action.isChecked() and action.isEnabled()
    assert action.text().replace('&', '') == window._lt('Create Missing Yamaha Catalogs')
    assert all(name in action.toolTip() for name in ('PSONG.MNG', 'PDISK.MNG', 'PIANODIR.FIL'))
    assert window.settings.value(SETTING_GENERATE_YAMAHA_CATALOGS, type=bool)


@pytest.mark.parametrize('enabled', [False, True])
def test_catalog_setting_startup_override_precedes_widgets(window, tmp_path, enabled):
    path = tmp_path / 'startup.json'
    path.write_text(json.dumps({'preparation_profile': 'mark_iii', SETTING_GENERATE_YAMAHA_CATALOGS: enabled}))
    reopened = main_window.MidiTitleWindow(settings=window.settings, initial_settings=load_startup_config(path))
    try:
        assert reopened.settingsGenerateYamahaCatalogsAction.isChecked() is enabled
    finally:
        reopened.close()


@pytest.mark.parametrize('profile, enabled', [('enspire', True), ('mark_iii', False)])
def test_bulk_dialog_passes_manual_catalog_choice_to_worker(window, tmp_path, monkeypatch, profile, enabled):
    from PySide6.QtWidgets import QDialogButtonBox
    from aps_midi_prep_tool_app.disk_session_worker import BulkExtractionWorker
    from test_bulk_extraction_dialog import _controls, _run_dialog

    source = tmp_path / 'images'
    source.mkdir()
    output = tmp_path / 'extracted'
    _apply(window, profile)
    window.toggle_yamaha_catalog_generation(enabled)
    captured = []

    def capture_worker(worker):
        captured.append((worker.generate_yamaha_catalogs, worker.preserve_yamaha_catalogs,
                         window.bulkExtractionContext['generate_yamaha_catalogs']))
        worker.finished.emit()

    def accept_dialog(dialog):
        controls = _controls(window, dialog)
        controls['source'].setText(str(source))
        controls['output'].setText(str(output))
        controls['save'].setChecked(False)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    monkeypatch.setattr(BulkExtractionWorker, 'start', capture_worker)
    monkeypatch.setattr(window, '_show_centered_progress_dialog', lambda *_args: None)
    try:
        _run_dialog(window, monkeypatch, accept_dialog)
        assert captured == [(enabled, True, enabled)]
    finally:
        window._close_bulk_extraction_progress()


@pytest.mark.parametrize('profile, enabled', [('enspire', True), ('mark_iii', False)])
def test_emulator_launch_passes_manual_catalog_choice_to_worker(window, tmp_path, monkeypatch, profile, enabled):
    from aps_midi_prep_tool_app.disk_session_worker import EmulatorImageBuildWorker
    from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY

    _apply(window, profile)
    window.toggle_yamaha_catalog_generation(enabled)
    captured = []

    def capture_worker(worker):
        captured.append((worker.generate_yamaha_catalogs, worker.preserve_yamaha_catalogs))
        worker.finished.emit()

    monkeypatch.setattr(EmulatorImageBuildWorker, 'start', capture_worker)
    monkeypatch.setattr(window, '_show_centered_progress_dialog', lambda *_args: None)
    try:
        window._start_emulator_image_build(
            str(tmp_path / 'songs'), str(tmp_path / 'images'), prefix='DSKA', starting_number=0,
            safety_margin_bytes=0, album_title='', output_content='midi',
            disk_format=DISK_FORMAT_BY_KEY['ibm.1440'], output_ext='img',
            include_subfolders=False, shuffle=False, include_song_lists=False,
        )
        assert captured == [(enabled, True)]
    finally:
        window._close_emulator_image_progress()
