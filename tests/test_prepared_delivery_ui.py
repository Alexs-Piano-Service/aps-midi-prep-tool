"""Preparation readiness and destination-aware delivery behavior."""

import mido
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA, convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile
from test_preparation_delivery import _midi, window


def _select(window, profile_key, medium_key):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(profile, get_preparation_medium(profile, medium_key))


def _load(window, tmp_path, kind=1):
    source = tmp_path / "SONG.MID"
    original = _midi(kind)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported")
    return source, original


def test_preparation_survives_hidden_optional_controls(window, tmp_path, monkeypatch):
    _select(window, "pianodisc_228cfx", "original")
    observed = []
    prepare_midi_type = window._prepare_destination_midi_type

    def prepare():
        observed.append(window._preparing_destination)
        prepare_midi_type()

    monkeypatch.setattr(window, "_prepare_destination_midi_type", prepare)
    window.toggle_show_status(False)
    window.toggle_show_preparation_row(False)
    window.toggle_show_quick_panel(False)
    source, original = _load(window, tmp_path)
    assert window._destination_preparation_queued
    QTest.qWait(150)
    window.show()
    QApplication.processEvents()

    assert observed == [True]
    assert not hasattr(window, "preparationStateLabel")
    assert not window.preparationRow.isVisible()
    assert not window.statusWidget.isVisible()
    assert not window.preparationBar.isVisible()
    assert not window.quickPanelWidget.isVisible()
    assert not window._preparation_incompatible_files()
    assert window._preparation_song_counts()["total"] == 1
    assert mido.MidiFile(window._regular_source_material_path(str(source))).type == 0
    assert source.read_bytes() == original


@pytest.mark.parametrize("profile,medium", [("custom", "custom"), ("unsure", "flashfloppy_hfe")])
def test_preparation_requirements_survive_status_clear_and_unset_target_disables_them(
    window, tmp_path, profile, medium,
):
    _select(window, "pianodisc_228cfx", "original")
    source, original = _load(window, tmp_path, kind=2)
    QTest.qWait(150)
    assert window.delivery_test_errors
    issues = window._preparation_incompatible_files()
    window.status_label.clear()
    assert window._preparation_incompatible_files() == issues
    assert len(issues) == 1
    assert "SONG.MID" in issues[0]
    _select(window, profile, medium)
    assert not window._preparation_incompatible_files()
    assert source.read_bytes() == original


def test_undo_required_conversion_updates_readiness(window, tmp_path, monkeypatch):
    _select(window, "pianodisc_228cfx", "original")
    _load(window, tmp_path)
    QTest.qWait(150)
    assert not window._preparation_incompatible_files()
    window.editUndoAction.trigger()
    assert window._preparation_incompatible_files()
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_a, **_k: main_window.QMessageBox.Yes)
    window.clear_list()
    assert window._preparation_song_counts()["total"] == 0
    assert not window._preparation_incompatible_files()


def test_attention_counts_only_failed_songs_after_expected_conversion(window, tmp_path):
    _select(window, "pianodisc_228cfx", "original")
    sources = [tmp_path / f"TYPE{kind}.MID" for kind in range(3)]
    for kind, source in enumerate(sources):
        source.write_bytes(_midi(kind))
    window._load_regular_files([str(source) for source in sources], "Imported")
    assert window._destination_preparation_queued
    QTest.qWait(150)
    assert window._preparation_song_counts()["total"] == 3
    issues = window._preparation_incompatible_files()
    assert len(issues) == 1
    assert "TYPE2.MID" in issues[0]
    assert "TYPE0.MID" not in issues[0]
    assert "TYPE1.MID" not in issues[0]
    assert mido.MidiFile(window._regular_source_material_path(str(sources[1]))).type == 0


@pytest.mark.parametrize("profile,medium,route", [
    ("midi_export", "usb", "files"),
    ("custom", "custom", "files"),
    ("mark_ii", "nalbantov", "images"),
    ("mark_ii_xg", "flashfloppy_img", "images"),
    ("mark_ii_xg", "original", "floppy"),
    ("unsure", "flashfloppy_hfe", "manual_image"),
])
def test_delivery_routes_current_prepared_songs(window, monkeypatch, tmp_path, profile, medium, route):
    from aps_midi_prep_tool_app import prepared_delivery

    _select(window, profile, medium)
    source, original = _load(window, tmp_path)
    calls = []
    monkeypatch.setattr(window, "save_as_changes", lambda: calls.append("files"))
    monkeypatch.setattr(window, "save_as_image", lambda: calls.append("manual_image"))
    monkeypatch.setattr(window, "_save_image_and_apply_to_floppy", lambda: calls.append("floppy"))
    monkeypatch.setattr(prepared_delivery, "create_prepared_disk_images", lambda owner: calls.append("images"))
    # Save immediately after import, before the queued preparation timer.
    window.save_prepared_delivery()

    assert calls == [route]
    assert not window._preparation_incompatible_files()
    assert source.read_bytes() == original
    assert window.fileSaveAction.text().replace("&", "") == "Save"
    assert window.fileSaveAsAction.isEnabled()
    assert window.fileSaveAsImageAction.isEnabled()


def test_delivery_blocks_incomplete_preparation_and_busy_work(window, monkeypatch, tmp_path):
    _select(window, "pianodisc_228cfx", "original")
    source, original = _load(window, tmp_path, kind=2)
    QTest.qWait(150)
    calls = []
    monkeypatch.setattr(window, "_save_image_and_apply_to_floppy", lambda: calls.append("write"))
    window.delivery_test_errors.clear()
    window.save_prepared_delivery()
    assert not calls
    assert window.delivery_test_errors[0][0][0] == "Preparation incomplete"
    window._set_disk_load_busy(True)
    window.save_prepared_delivery()
    assert not calls
    window._set_disk_load_busy(False)
    assert window._preparation_incompatible_files()
    assert source.read_bytes() == original


def test_folder_export_writes_staged_music_and_leaves_original_unchanged(window, monkeypatch, tmp_path):
    _select(window, "pianodisc_prodigy", "pianodisc_app")
    source, original = _load(window, tmp_path)
    QTest.qWait(150)
    row = window._find_regular_row_for_path(str(source))
    window.pendingEdits[str(source)] = "Prepared title"
    window.table.item(row, 4).setData(window.TITLE_RAW_ROLE, "Prepared title")
    window.table.item(row, 4).setText("Prepared title")
    destination = tmp_path / "prepared"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(destination))
    window.save_prepared_delivery()
    delivered = mido.MidiFile(destination / "SONG.MID")
    assert any(message.type == "track_name" and message.name == "Prepared title"
               for track in delivered.tracks for message in track)
    assert source.read_bytes() == original


def test_undo_required_filename_preparation_reports_attention(window, tmp_path):
    _select(window, "mark_iii", "original")
    source = tmp_path / "A VERY LONG SONG NAME.MID"
    original = _midi(1)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported")
    QTest.qWait(150)
    assert not window._preparation_incompatible_files()
    window.editUndoAction.trigger()
    assert any("8.3" in issue for issue in window._preparation_incompatible_files())
    assert not window._ensure_preparation_ready()
    assert source.read_bytes() == original


def test_saved_hidden_preparation_controls_stay_hidden_after_loading(window, tmp_path):
    window.settings.setValue(window.SETTING_SHOW_PREPARATION_ROW, False)
    instance = main_window.MidiTitleWindow()
    try:
        instance.show()
        QApplication.processEvents()
        assert not hasattr(instance, "preparationStateLabel")
        assert not instance.preparationRow.isVisible()
        _select(instance, "midi_export", "usb")
        _load(instance, tmp_path)
        QTest.qWait(150)
        assert not instance.preparationRow.isVisible()
        assert not instance.preparationBar.isVisible()
        assert not instance.viewShowPreparationRowAction.isChecked()
    finally:
        instance._clear_staging_history()
        instance._cleanup_midi_scratch_dir()
        instance.deleteLater()
        QApplication.processEvents()


@pytest.mark.parametrize("language", ["de", "ja"])
@pytest.mark.parametrize("kind", [1, 2])
def test_language_changes_do_not_reintroduce_preparation_indicator(
    window, tmp_path, language, kind,
):
    _select(window, "pianodisc_228cfx", "original")
    window._set_language(language)
    sources = [tmp_path / "FIRST.MID", tmp_path / "SECOND.MID"]
    for source in sources:
        source.write_bytes(_midi(kind))
    window._load_regular_files([str(source) for source in sources], "Imported")
    assert not hasattr(window, "preparationStateLabel")
    QTest.qWait(150)
    assert len(window._preparation_incompatible_files()) == (2 if kind == 2 else 0)
    window._set_language("en")
    assert not hasattr(window, "preparationStateLabel")
    _select(window, "custom", "custom")
    assert not window._preparation_incompatible_files()


def test_image_state_omits_removed_songs_without_extracting_eseq_again(window, tmp_path, monkeypatch):
    sources = [tmp_path / "FIRST.FIL", tmp_path / "SECOND.FIL"]
    for source in sources:
        source.write_bytes(convert_midi_bytes_to_eseq_bytes(_midi(0)))
    image_path = tmp_path / "source.img"
    create_floppy_images_from_files(
        [(str(source), source.name) for source in sources], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original = image_path.read_bytes()
    session = FloppyImageSession.load(image_path)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    _select(window, "mark_ii", "nalbantov")
    assert window._preparation_song_counts()["total"] == 2
    assert not window._preparation_incompatible_files()
    row, path, *_rest = next(window._preparation_song_rows())
    monkeypatch.setattr(window, "_confirm_with_optional_skip", lambda **_kwargs: True)
    window.remove_image_row(row)
    assert path in window.pendingImageDeletes
    assert window._preparation_song_counts()["total"] == 1

    def unexpected_extraction(*_args, **_kwargs):
        pytest.fail("Refreshing preparation status must not extract songs again")

    monkeypatch.setattr(window, "_pending_or_extracted_image_path", unexpected_extraction)
    window._refresh_preparation_state()
    assert not hasattr(window, "preparationStateLabel")
    assert image_path.read_bytes() == original


def test_undo_mda_conversion_reports_attention_despite_fil_extension(window, tmp_path):
    _select(window, "mark_ii", "nalbantov")
    source = tmp_path / "SONG.FIL"
    original = convert_midi_bytes_to_eseq_bytes(_midi(0), container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported")
    QTest.qWait(150)
    assert not window._preparation_incompatible_files()
    window.editUndoAction.trigger()
    issues = window._preparation_incompatible_files()
    assert len(issues) == 1
    assert "SONG.FIL" in issues[0]
    assert source.read_bytes() == original
