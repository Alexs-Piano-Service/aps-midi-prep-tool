import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mido
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profiles import (
    PIANO_PROFILES, get_preparation_medium, get_preparation_profile, proposed_settings,
)


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


def _apply(window, key):
    profile = get_preparation_profile(key)
    window._apply_preparation_profile(profile, get_preparation_medium(profile, ""))


def _write_song(path, title):
    song = mido.MidiFile(type=0)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=480),
    ]))
    song.save(path)


def _assert_filename_dialog_defaults(window, monkeypatch, *, enabled, checked):
    monkeypatch.setattr(window, "_discover_floppy_devices", lambda: ([], []))
    seen = []

    def inspect_dialog(dialog):
        read_dialog = dialog.windowTitle() == "Read Floppy"
        controls = {checkbox.text(): checkbox for checkbox in dialog.findChildren(QCheckBox)}
        names = controls[window._lt("Name MIDI files by track number and song title") if read_dialog
                         else window._t("bulk.long_filenames")]
        convert = controls["Convert E-SEQ files to MIDI after reading" if read_dialog
                           else window._t("bulk.convert")]
        assert not convert.isChecked()
        assert names.isEnabled() is enabled
        assert names.isChecked() is checked
        seen.append(dialog.windowTitle())
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_dialog)
    window._choose_floppy_read_options()
    window.show_bulk_extraction_utility()
    assert seen == ["Read Floppy", window._t("bulk.title")]


def _store_old_filename_preferences(settings, profile_key, medium_key, *, dos83, long_names):
    settings.clear()
    settings.setValue("preparation_profile", profile_key)
    settings.setValue("preparation_medium", medium_key)
    settings.setValue("use_dos83_filenames", dos83)
    for key in ("long_midi_filenames", "eseq_to_midi_long_filenames",
                "read_floppy_long_filenames", "bulk_extraction_long_midi_filenames"):
        settings.setValue(key, long_names)
    settings.setValue("read_floppy_convert_to_midi", False)
    settings.setValue("bulk_extraction_convert_eseq", False)


def test_fresh_window_shows_preparation_and_uses_long_names(window):
    window.show()
    QApplication.processEvents()
    assert window.preparationBar.isVisible()
    assert window.viewShowPreparationRowAction.isChecked()
    assert window._long_midi_filenames_enabled()
    assert not window.format_disklavier_checkbox.isChecked()


@pytest.mark.parametrize("action_name,widget_name,setting,inverted", (
    ("viewShowStatusAction", "statusWidget", "hide_status", True),
    ("viewShowQuickPanelAction", "quickPanelWidget", "hide_quick_panel", True),
    ("viewShowAlbumMetadataAction", "imagePianodirMetadataWidget", "hide_album_metadata", True),
    ("viewShowPreparationRowAction", "preparationBar", "show_preparation_row", False),
    ("viewShowSaveDestinationAction", "saveDestinationLabel", "show_save_destination", False),
))
def test_view_checks_mean_visible_and_preserve_preference_keys(
    window, action_name, widget_name, setting, inverted,
):
    window.show()
    action = getattr(window, action_name)
    widget = getattr(window, widget_name)
    assert action.text().replace("&", "").startswith("Show ")
    for visible in (False, True, False):
        action.setChecked(visible)
        QApplication.processEvents()
        assert widget.isVisible() is visible
        assert window.settings.value(setting, not visible if inverted else visible, type=bool) is (
            not visible if inverted else visible
        )


@pytest.mark.parametrize("profile", PIANO_PROFILES, ids=lambda profile: profile.key)
def test_profile_defaults_match_controller_and_medium(profile):
    for medium_key in profile.media:
        changes = proposed_settings(profile, get_preparation_medium(profile, medium_key))
        assert changes["format_disklavier_screen"] is (
            profile.key in {"mark_i", "mark_ii", "mark_ii_xg", "mark_iii"}
        )
        for key in ("long_midi_filenames", "read_floppy_long_filenames", "bulk_extraction_long_midi_filenames"):
            assert changes[key] is (not changes["use_dos83_filenames"])


@pytest.mark.parametrize("key", ("mark_i", "mark_ii", "mark_ii_xg", "mark_iii", "mark_iv", "dsr1", "custom"))
def test_restored_profile_supplies_missing_title_and_filename_defaults(window, monkeypatch, key):
    window.settings.clear()
    window.settings.setValue("preparation_profile", key)
    restored = main_window.MidiTitleWindow()
    try:
        profile = get_preparation_profile(key)
        changes = proposed_settings(profile, get_preparation_medium(profile, ""))
        assert restored.format_disklavier_checkbox.isChecked() is changes["format_disklavier_screen"]
        assert restored._long_midi_filenames_enabled() is changes["long_midi_filenames"]
    finally:
        restored.close()


@pytest.mark.parametrize("profile_key,medium_key", (
    ("custom", "custom"), ("unsure", "custom"), ("unsure", "flashfloppy_hfe"),
    ("mark_iv", "usb"), ("e3_850", "usb"), ("enspire", "usb"),
))
def test_old_dos83_preferences_do_not_disable_long_names_for_non83_target(
    window, monkeypatch, profile_key, medium_key,
):
    _store_old_filename_preferences(
        window.settings, profile_key, medium_key, dos83=True, long_names=False,
    )
    restored = main_window.MidiTitleWindow()
    try:
        assert restored._preparation_profile().key == profile_key
        assert restored._preparation_medium().key == medium_key
        _assert_filename_dialog_defaults(restored, monkeypatch, enabled=True, checked=True)
        assert restored.settings.value(restored.SETTING_FILENAME_DEFAULTS_VERSION, type=int) == (
            restored.FILENAME_DEFAULTS_VERSION
        )
    finally:
        restored.close()


@pytest.mark.parametrize("profile_key,medium_key", (
    ("mark_i", "original"), ("mark_ii", "original"), ("mark_ii_xg", "original"),
    ("mark_iii", "flashfloppy_img"), ("mark_iv", "original"),
    ("pianodisc_128plus", "original"),
))
def test_83_target_disables_long_names_even_when_old_preferences_enable_them(
    window, monkeypatch, profile_key, medium_key,
):
    _store_old_filename_preferences(
        window.settings, profile_key, medium_key, dos83=False, long_names=True,
    )
    restored = main_window.MidiTitleWindow()
    try:
        _assert_filename_dialog_defaults(restored, monkeypatch, enabled=False, checked=False)
        # Availability follows the destination even if an old/manual filename
        # preference is reintroduced after startup migration has already run.
        restored.settings.setValue("use_dos83_filenames", False)
        restored.settings.setValue("long_midi_filenames", True)
        restored.settings.setValue("bulk_extraction_long_midi_filenames", True)
        _assert_filename_dialog_defaults(restored, monkeypatch, enabled=False, checked=False)
    finally:
        restored.close()


def test_filename_default_migration_runs_once_and_keeps_later_unchecked_choice(window, monkeypatch):
    _store_old_filename_preferences(
        window.settings, "custom", "custom", dos83=True, long_names=False,
    )
    migrated = main_window.MidiTitleWindow()
    try:
        _assert_filename_dialog_defaults(migrated, monkeypatch, enabled=True, checked=True)
        migrated._set_long_midi_filenames_enabled(False)
        migrated.settings.setValue("bulk_extraction_long_midi_filenames", False)
        _assert_filename_dialog_defaults(migrated, monkeypatch, enabled=True, checked=False)
        migrated.settings.sync()
    finally:
        migrated.close()

    # Reopen the actual settings file to exercise persisted migration state,
    # rather than relying on the original QSettings object's in-memory values.
    saved_settings = QSettings(window.settings.fileName(), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: saved_settings)
    restarted = main_window.MidiTitleWindow()
    try:
        _assert_filename_dialog_defaults(restarted, monkeypatch, enabled=True, checked=False)
        assert saved_settings.value(restarted.SETTING_FILENAME_DEFAULTS_VERSION, type=int) == (
            restarted.FILENAME_DEFAULTS_VERSION
        )
    finally:
        restarted.close()


def test_profile_switch_and_undo_restore_screen_controls_and_manual_overrides(window):
    _apply(window, "mark_ii")
    assert window.format_disklavier_checkbox.isChecked()
    assert window.viewFormatDisklavierScreenAction.isChecked()
    assert not window._long_midi_filenames_enabled()
    _apply(window, "custom")
    assert not window.format_disklavier_checkbox.isChecked()
    assert window._long_midi_filenames_enabled()
    window.undo_last_staged_batch()
    assert window.format_disklavier_checkbox.isChecked()
    assert window.viewFormatDisklavierScreenAction.isChecked()
    assert not window._long_midi_filenames_enabled()
    window.format_disklavier_checkbox.setChecked(False)
    assert not window.settings.value("format_disklavier_screen", type=bool)


@pytest.mark.parametrize("accepted", (False, True))
def test_disklavier_title_dialog_truncates_initial_title_and_stages_only_on_accept(
    window, tmp_path, monkeypatch, accepted,
):
    title = "12345678901234567890123456789012 extra text"
    source = tmp_path / "SONG.MID"
    _write_song(source, title)
    original = source.read_bytes()
    window._load_regular_files([str(source)], "Loaded")
    window.format_disklavier_checkbox.setChecked(True)

    def inspect_dialog(dialog):
        fields = dialog.findChildren(main_window.DisklavierScreenLineEdit)
        assert len(fields) == 2
        assert "".join(field.text() for field in fields) == title[:32]
        assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled()
        return QDialog.Accepted if accepted else QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_dialog)
    window.edit_via_dialog(window._find_regular_row_for_path(str(source)))
    assert window.pendingEdits == ({str(source): title[:32]} if accepted else {})
    assert source.read_bytes() == original


def test_long_titles_use_native_item_rendering_and_keep_long_indicator(window, tmp_path):
    source = tmp_path / "SONG.MID"
    title = "A title that exceeds the thirty two character limit"
    _write_song(source, title)
    window._load_regular_files([str(source)], "Loaded")
    row = window._find_regular_row_for_path(str(source))
    assert window.table.itemDelegateForColumn(4) is None
    assert window.table.item(row, 4).font() == window.title_monospace_font
    assert window.table.item(row, 4).text() == title
    assert not window.table.isColumnHidden(5)
    assert window.table.item(row, 5).text() == "LONG"


@pytest.mark.parametrize("dialog_kind", ("read", "bulk"))
@pytest.mark.parametrize("dos83", (False, True))
def test_long_filename_controls_do_not_require_eseq_conversion(window, monkeypatch, dialog_kind, dos83):
    if dos83:
        _apply(window, "mark_ii_xg")
    monkeypatch.setattr(window, "_discover_floppy_devices", lambda: ([], []))
    seen = []

    def inspect_dialog(dialog):
        controls = {checkbox.text(): checkbox for checkbox in dialog.findChildren(QCheckBox)}
        names = controls[window._lt("Name MIDI files by track number and song title") if dialog_kind == "read"
                         else window._t("bulk.long_filenames")]
        convert = controls["Convert E-SEQ files to MIDI after reading" if dialog_kind == "read"
                           else window._t("bulk.convert")]
        assert not convert.isChecked()
        for enabled in (False, True, False):
            convert.setChecked(enabled)
            assert names.isEnabled() is (not dos83)
            assert names.isChecked() is (not dos83)
        seen.append(True)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_dialog)
    if dialog_kind == "read":
        window._choose_floppy_read_options()
    else:
        window.show_bulk_extraction_utility()
    assert seen


@pytest.mark.parametrize("long_names", (False, True))
def test_native_midi_read_includes_all_songs_with_optional_export_names(
    window, tmp_path, monkeypatch, long_names,
):
    songs = [("FIRST.MID", "Moon River"), ("SECOND.MID", "Summer Wind"), ("THIRD.MID", "Misty")]
    originals = {}
    for filename, title in songs:
        source = tmp_path / filename
        _write_song(source, title)
        originals[filename] = source.read_bytes()
    image_path = tmp_path / "source.img"
    create_floppy_images_from_files(
        [(str(tmp_path / filename), filename) for filename, _title in songs],
        str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original = image_path.read_bytes()
    session = FloppyImageSession.load(image_path)
    monkeypatch.setattr(window, "_offer_post_load_sequence_conversions", lambda: None)
    window.pendingFloppyReadConvertToMidi = False
    window.pendingFloppyReadLongFilenames = long_names
    window._on_disk_load_success(session, session.list_entries())
    expected_names = {
        filename: f"{number:02d} - {title}.mid" if long_names else filename
        for number, (filename, title) in enumerate(songs, start=1)
    }
    assert window.table.rowCount() == len(songs)
    assert {window.table.item(row, 1).text() for row in range(window.table.rowCount())} == set(originals)
    assert {window.table.item(row, 3).text() for row in range(window.table.rowCount())} == set(expected_names.values())
    assert window.pendingImageExportFilenames == (expected_names if long_names else {})
    assert not window.pendingImageRenames
    assert not window.pendingImageReplacements
    assert image_path.read_bytes() == original
    for filename, expected_name in expected_names.items():
        assert window._image_folder_export_path(filename, filename) == expected_name
        assert Path(session.extract_file(filename)).read_bytes() == originals[filename]

    exported = window._export_image_session_files_to_folder(str(tmp_path / "export"))
    assert {Path(path).name for path in exported} == set(expected_names.values())
    for filename, expected_name in expected_names.items():
        assert (tmp_path / "export" / expected_name).read_bytes() == originals[filename]
        assert (tmp_path / filename).read_bytes() == originals[filename]
    assert image_path.read_bytes() == original
    if long_names:
        window.undo_last_staged_batch()
        assert not window.pendingImageExportFilenames
        assert image_path.read_bytes() == original


@pytest.mark.parametrize("ending", ("cancelled", "recovery_declined", "recovery_failed", "recovery_cancelled", "new_image"))
def test_abandoned_read_naming_does_not_leak_into_unrelated_image(window, tmp_path, monkeypatch, ending):
    source = tmp_path / "SONG.MID"
    _write_song(source, "Moon River")
    image_path = tmp_path / "unrelated.img"
    create_floppy_images_from_files(
        [(str(source), source.name)], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original = image_path.read_bytes()
    window.pendingFloppyReadConvertToMidi = True
    window.pendingFloppyReadLongFilenames = True
    window.pendingFloppyReadTrimTitles = True
    monkeypatch.setattr(window, "_show_operation_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_offer_partial_recovery_capture", lambda: None)
    monkeypatch.setattr(window, "_offer_post_load_sequence_conversions", lambda: None)
    if ending == "cancelled":
        window._on_disk_load_cancelled("Cancelled")
    elif ending == "recovery_declined":
        window._on_disk_load_failure("Unreadable disk")
        assert window.pendingFloppyReadLongFilenames
        monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args: main_window.QMessageBox.No)
        window._offer_disk_recovery(window.pendingDiskRecoveryRequest)
    elif ending == "recovery_failed":
        window._on_disk_recovery_failure("Unreadable disk")
    elif ending == "recovery_cancelled":
        window._on_disk_recovery_cancelled("Cancelled")
    if ending != "new_image":
        assert not window.pendingFloppyReadLongFilenames

    def load_image(**options):
        assert options["load_kind"] == "image"
        assert not window.pendingFloppyReadConvertToMidi
        assert not window.pendingFloppyReadLongFilenames
        assert not window.pendingFloppyReadTrimTitles
        session = FloppyImageSession.load(options["source"])
        window._on_disk_load_success(session, session.list_entries())

    monkeypatch.setattr(window, "_start_disk_load_worker", load_image)
    window.load_image_file(str(image_path), prevalidated=True)
    assert not window.pendingImageExportFilenames
    assert window.table.item(0, 3).text() == "SONG.MID"
    assert image_path.read_bytes() == original


def test_recovery_continuation_keeps_requested_long_names(window, tmp_path, monkeypatch):
    source = tmp_path / "SONG.MID"
    _write_song(source, "Moon River")
    image_path = tmp_path / "recovered.img"
    create_floppy_images_from_files(
        [(str(source), source.name)], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original = image_path.read_bytes()
    window.pendingFloppyReadConvertToMidi = False
    window.pendingFloppyReadLongFilenames = True
    window._on_disk_load_failure("Unreadable disk")
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args: main_window.QMessageBox.Yes)
    monkeypatch.setattr(window, "_information_with_optional_hide", lambda **_kwargs: None)
    monkeypatch.setattr(window, "_offer_post_load_sequence_conversions", lambda: None)
    recovered = []

    def recover(_request):
        assert window.pendingFloppyReadLongFilenames
        session = FloppyImageSession.load(image_path)
        window._on_disk_recovery_success(session, session.list_entries())
        recovered.append(True)

    monkeypatch.setattr(window, "_start_disk_recovery_worker", recover)
    window._offer_disk_recovery(window.pendingDiskRecoveryRequest)
    assert recovered
    assert window.pendingImageExportFilenames == {"SONG.MID": "01 - Moon River.mid"}
    assert not window.pendingFloppyReadLongFilenames
    assert image_path.read_bytes() == original
