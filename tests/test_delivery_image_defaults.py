"""Delivery presets initialize dialogs; the user's next selection remains final."""

import io
import os
from pathlib import Path
from types import SimpleNamespace

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT,
    get_preparation_medium, get_preparation_profile, proposed_settings,
)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "delivery.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_a: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    monkeypatch.setattr(instance, "_show_operation_error", lambda *a, **k: pytest.fail(str((a, k))))
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()


def _choose(window, profile_key, medium_key):
    profile = get_preparation_profile(profile_key)
    medium = get_preparation_medium(profile, medium_key)
    assert medium.key == medium_key
    window._apply_preparation_profile(profile, medium)


@pytest.mark.parametrize("profile_key", ("mark_iii", "unsure"))
@pytest.mark.parametrize("medium_key,expected", (("flashfloppy_hfe", "hfe"), ("flashfloppy_img", "img")))
def test_active_medium_wins_stale_settings_source_and_new_image_defaults(
    window, monkeypatch, tmp_path, profile_key, medium_key, expected,
):
    _choose(window, profile_key, medium_key)
    stale = "img" if expected == "hfe" else "hfe"
    window.settings.setValue(SETTING_IMAGE_FORMAT, stale)
    window.settings.setValue(SETTING_DISK_FORMAT, "ibm.720")
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_OUTPUT_FORMAT, stale)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_DISK_FORMAT, "ibm.720")
    seen = []

    def inspect(dialog):
        image_type, disk = dialog.findChildren(QComboBox)
        assert image_type.currentData() == expected
        assert disk.currentData().key == ("ibm.1440" if profile_key == "mark_iii" else "ibm.720")
        seen.append(dialog.windowTitle())
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert window._prompt_for_new_image_options() is None
    assert window._prompt_for_save_image_options() is None
    assert window._prompt_for_save_image_options(
        default_ext=stale, default_disk_format=DISK_FORMAT_BY_KEY["ibm.720"],
    ) is None
    assert len(seen) == 3

    def inspect_builder(dialog, **_kwargs):
        assert dialog.findChild(QComboBox, "emulatorOutputFormatCombo").currentData() == expected
        disks = next(combo for combo in dialog.findChildren(QComboBox) if hasattr(combo.currentData(), "size_bytes"))
        assert disks.currentData().key == ("ibm.1440" if profile_key == "mark_iii" else "ibm.720")
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_builder)
    window.show_emulator_image_utility()


def test_unsure_applies_only_explicit_delivery_and_reviews_it(window):
    window.settings.setValue("emulator_image_content", "midi")
    window.settings.setValue("use_dos83_filenames", False)
    window.settings.setValue("emulator_image_disk_format", "ibm.1440")
    _choose(window, "unsure", "flashfloppy_hfe")
    assert window._preparation_export_defaults() == {"image_format": "hfe"}
    assert window.settings.value("emulator_image_content") == "midi"
    assert window.settings.value("use_dos83_filenames", type=bool) is False
    assert window.settings.value("emulator_image_disk_format") == "ibm.1440"
    assert "HFE" in window.preparationLabel.text()
    assert "MIDI" not in window.preparationLabel.text()
    profile = get_preparation_profile("unsure")
    changes = proposed_settings(profile, get_preparation_medium(profile, "flashfloppy_hfe"))
    assert set(changes) == {
        SETTING_IMAGE_FORMAT, "emulator_image_output_format", "emulator_image_prefix", "emulator_image_starting_number",
    }
    dialog = PreparationProfileDialog(window.settings, "unsure", "flashfloppy_hfe")
    try:
        assert dialog.selection()[1].key == "flashfloppy_hfe"
        rows = [[dialog.changes_table.item(row, column).text() for column in range(3)]
                for row in range(dialog.changes_table.rowCount())]
        assert rows == [["Image type", "HFE", "HFE"]]
    finally:
        dialog.deleteLater()


def test_dialog_override_is_the_export_choice(window, monkeypatch, tmp_path):
    _choose(window, "mark_iii", "flashfloppy_hfe")

    def select_img(dialog):
        image_type, disk = dialog.findChildren(QComboBox)
        assert image_type.currentData() == "hfe"
        image_type.setCurrentIndex(image_type.findData("img"))
        disk.setCurrentIndex(next(index for index in range(disk.count()) if disk.itemData(index).key == "ibm.720"))
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", select_img)
    options = window._prompt_for_new_image_options()
    assert options["output_ext"] == "img" and options["disk_format"].key == "ibm.720"
    calls = []

    def save_name(_parent, _title, initial, file_filter):
        calls.append((initial, file_filter))
        return str(tmp_path / "chosen.img"), file_filter

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", save_name)
    options = window._prompt_for_save_image_options(default_ext="img")
    assert options == (str(tmp_path / "chosen.img"), "img", DISK_FORMAT_BY_KEY["ibm.720"])
    assert calls[0][0].endswith(".img") and "*.img" in calls[0][1]
    assert window._preparation_medium().key == "flashfloppy_hfe"


def test_emulator_dialog_override_reaches_builder_without_changing_delivery(window, monkeypatch, tmp_path):
    _choose(window, "unsure", "flashfloppy_hfe")
    monkeypatch.setattr(window, "_emulator_image_default_source_directory", lambda: str(tmp_path))
    calls = []

    def select_img(dialog, **_kwargs):
        image_type = dialog.findChild(QComboBox, "emulatorOutputFormatCombo")
        assert image_type.currentData() == "hfe"
        image_type.setCurrentIndex(image_type.findData("img"))
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", select_img)
    monkeypatch.setattr(window, "_start_emulator_image_build", lambda *a, **k: calls.append((a, k)))
    window.show_emulator_image_utility()
    assert calls[0][1]["output_ext"] == "img"
    assert window.settings.value(window.SETTING_EMULATOR_IMAGE_OUTPUT_FORMAT) == "img"
    assert window._preparation_export_defaults()["image_format"] == "hfe"


def test_unsure_hfe_survives_settings_reload(window):
    _choose(window, "unsure", "flashfloppy_hfe")
    restored = main_window.MidiTitleWindow()
    try:
        assert restored._preparation_medium().key == "flashfloppy_hfe"
        assert restored._preparation_export_defaults() == {"image_format": "hfe"}
        assert "HFE" in restored.preparationLabel.text()
    finally:
        restored._clear_staging_history()
        restored.deleteLater()


@pytest.mark.parametrize("profile_key,medium_key", (("custom", "custom"), ("unsure", "custom"), ("mark_iv", "usb")))
def test_manual_and_folder_delivery_keep_source_fallback(window, monkeypatch, profile_key, medium_key):
    _choose(window, profile_key, medium_key)
    window.settings.setValue(SETTING_IMAGE_FORMAT, "hfe")
    window.settings.setValue(SETTING_DISK_FORMAT, "ibm.1440")

    def inspect(dialog):
        image_type, disk = dialog.findChildren(QComboBox)
        assert image_type.currentData() == "img"
        assert disk.currentData().key == "ibm.720"
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window._prompt_for_save_image_options(default_ext="img", default_disk_format=DISK_FORMAT_BY_KEY["ibm.720"])


def _open_image(window, tmp_path):
    song = mido.MidiFile(type=0)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Source song"),
        mido.Message("note_on", channel=4, note=67, velocity=81, time=120),
        mido.Message("note_off", channel=4, note=67, velocity=0, time=480),
    ]))
    source = tmp_path / "SONG.MID"
    song.save(source)
    image_path = tmp_path / "source.img"
    create_floppy_images_from_files(
        [{"host_path": str(source), "image_path": source.name}], image_path, "img",
        DISK_FORMAT_BY_KEY["ibm.720"],
    )
    session = FloppyImageSession.load(image_path)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    return image_path, source


def test_open_img_exports_real_hfe_with_selected_default_and_keeps_source(window, monkeypatch, tmp_path):
    _choose(window, "unsure", "flashfloppy_hfe")
    image_path, source = _open_image(window, tmp_path)
    original = image_path.read_bytes()
    output = tmp_path / "delivery.hfe"
    option_dialogs = []

    def inspect(dialog):
        combos = dialog.findChildren(QComboBox)
        if not combos:
            return QDialog.Accepted
        image_type, disk = combos
        assert image_type.currentData() == "hfe" and disk.currentData().key == "ibm.720"
        option_dialogs.append(True)
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *_a: (str(output), ""))
    monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *_a, **_k: None)
    window.save_image_as()
    assert option_dialogs == [True]
    assert output.read_bytes().startswith(b"HXCPICFE")
    saved = FloppyImageSession.load(output)
    try:
        extracted = Path(saved.extract_file("SONG.MID")).read_bytes()
        song, original_song = mido.MidiFile(file=io.BytesIO(extracted)), mido.MidiFile(source)
        assert song.type == original_song.type and song.ticks_per_beat == original_song.ticks_per_beat
        assert song.tracks == original_song.tracks
    finally:
        saved.cleanup()
    assert image_path.read_bytes() == original
    assert window.image_session.source_ext == "hfe"


@pytest.mark.parametrize("source_kind", ("floppy_usb", "floppy_gw"))
def test_generic_save_as_image_honors_hfe_for_open_floppy_session(window, monkeypatch, tmp_path, source_kind):
    _choose(window, "unsure", "flashfloppy_hfe")
    image_path, _source = _open_image(window, tmp_path)
    original = image_path.read_bytes()
    window.image_session.source_kind = source_kind
    window.image_session.gw_source = SimpleNamespace(archival_quality=False, capture_output_ext="img", drive="A")
    seen = []

    def inspect(dialog):
        assert dialog.findChildren(QComboBox)[0].currentData() == "hfe"
        seen.append(True)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window.save_image_as()
    assert seen and image_path.read_bytes() == original


def test_capture_specific_export_retains_requested_archive_default(window, monkeypatch):
    _choose(window, "unsure", "flashfloppy_hfe")
    window.image_session = SimpleNamespace(source_kind="floppy_gw", gw_source=SimpleNamespace(drive="A"), cleanup=lambda: None)
    monkeypatch.setattr(window, "_catalog_filename_stem", lambda: "")
    seen = []

    def cancel_name(_parent, _title, initial, _filters):
        seen.append(initial)
        return "", ""

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", cancel_name)
    window._save_greaseweazle_read_image_now("scp")
    assert seen[0].endswith(".scp")


def test_normal_save_keeps_original_img_container(window, monkeypatch, tmp_path):
    _choose(window, "unsure", "flashfloppy_hfe")
    image_path, _source = _open_image(window, tmp_path)
    window.pendingImageTitleEdits["SONG.MID"] = "Saved title"
    window.fileWriteProtectOriginalAction.setChecked(False)
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_k: pytest.fail("Normal Save must not become Save As Image"))
    window.save_image_changes()
    assert not window.pendingImageTitleEdits
    assert window.image_session.source_ext == "img"
    assert image_path.stat().st_size == 737280
    assert not list(tmp_path.glob("*.hfe"))
    saved = FloppyImageSession.load(image_path)
    try:
        song = mido.MidiFile(saved.extract_file("SONG.MID"))
        assert next(message.name for message in song.tracks[0] if message.type == "track_name") == "Saved title"
    finally:
        saved.cleanup()
