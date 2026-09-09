import os
import io
from types import SimpleNamespace

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession
from aps_midi_prep_tool_app.floppy_image import create_floppy_images_from_files
from aps_midi_prep_tool_app.eseq_converter import ESEQ_CONTAINER_CLAVINOVA_MDA, convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import ESEQ_VARIANT_CLAVINOVA, ESEQ_VARIANT_DISKLAVIER, build_music_dir_bytes, is_clavinova_mda_file
from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    PIANO_PROFILES, SETTING_PROFILE, SETTING_MEDIUM, SETTING_DISK_FORMAT,
    SETTING_IMAGE_FORMAT, get_preparation_profile, get_preparation_medium,
    proposed_settings,
)


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, *, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self.values[key] = value


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("key", ("mark_i", "mark_ii"))
def test_early_controllers_keep_conservative_eseq_2dd_defaults(key):
    profile = get_preparation_profile(key)
    settings = proposed_settings(profile, get_preparation_medium(profile, "original"))
    assert settings["emulator_image_content"] == "eseq"
    assert settings[SETTING_DISK_FORMAT] == "ibm.720"
    assert settings["use_dos83_filenames"] is True
    assert profile.midi_types == ()


def test_emulator_choice_changes_container_and_numbering_not_piano_requirements():
    profile = get_preparation_profile("mark_i")
    original = proposed_settings(profile, get_preparation_medium(profile, "original"))
    emulator = proposed_settings(profile, get_preparation_medium(profile, "flashfloppy_hfe"))
    assert original["emulator_image_content"] == emulator["emulator_image_content"] == "eseq"
    assert original[SETTING_DISK_FORMAT] == emulator[SETTING_DISK_FORMAT] == "ibm.720"
    assert original[SETTING_IMAGE_FORMAT] == "img"
    assert emulator[SETTING_IMAGE_FORMAT] == "hfe"
    assert emulator["emulator_image_prefix"] == "DSKA"
    assert emulator["emulator_image_starting_number"] == 0


def test_enspire_profile_excludes_eseq_and_floppy_defaults():
    profile = get_preparation_profile("enspire")
    medium = get_preparation_medium(profile, "original")
    settings = proposed_settings(profile, medium)
    assert profile.supported_song_formats == ("midi",)
    assert medium.key == "usb"
    assert settings["emulator_image_content"] == "midi"
    assert settings["use_dos83_filenames"] is False
    assert SETTING_DISK_FORMAT not in settings
    assert SETTING_IMAGE_FORMAT not in settings


@pytest.mark.parametrize("key", ("pianodisc_128plus", "pianodisc_228cfx"))
def test_pianodisc_floppy_defaults_require_type0_without_assuming_emulator_fit(key):
    profile = get_preparation_profile(key)
    medium = get_preparation_medium(profile, "nalbantov_slim")
    settings = proposed_settings(profile, medium)
    assert profile.category == "pianodisc"
    assert profile.midi_types == (0,)
    assert medium.key == "original"
    assert settings[SETTING_DISK_FORMAT] == "ibm.720"
    assert settings["emulator_image_content"] == "midi"
    assert settings["use_dos83_filenames"]


@pytest.mark.parametrize("key, expected_medium", (
    ("pianodisc_prodigy", "pianodisc_app"),
    ("qrs_pmii", "usb"), ("qrs_pno3", "usb"), ("qrs_pno4", "usb"),
))
def test_manufacturer_folder_profiles_ignore_old_floppy_export_defaults(key, expected_medium):
    profile = get_preparation_profile(key)
    medium = get_preparation_medium(profile, "nalbantov")
    assert medium.key == expected_medium
    changes = proposed_settings(profile, medium)
    assert changes["emulator_image_content"] == "midi"
    assert changes["use_dos83_filenames"] is False
    assert SETTING_DISK_FORMAT not in changes
    assert SETTING_IMAGE_FORMAT not in changes
    settings = _Settings({SETTING_PROFILE: key, SETTING_MEDIUM: expected_medium,
                          SETTING_DISK_FORMAT: "ibm.720", SETTING_IMAGE_FORMAT: "hfe"})
    assert MidiTitleWindow._preparation_export_defaults(SimpleNamespace(settings=settings)) == {}


def test_chili_retains_both_midi_types_and_uses_hd_dos_media():
    profile = get_preparation_profile("qrs_chili")
    medium = get_preparation_medium(profile, "original")
    assert profile.category == "qrs"
    assert profile.midi_types == (0, 1)
    assert proposed_settings(profile, medium)[SETTING_DISK_FORMAT] == "ibm.1440"


@pytest.mark.parametrize("key", ("custom", "unsure", "unrecognized-old-profile"))
def test_custom_and_unknown_profiles_do_not_propose_setting_changes(key):
    profile = get_preparation_profile(key)
    assert proposed_settings(profile, get_preparation_medium(profile, "")) == {}


def test_no_profile_implies_hardware_testing_or_changes_musical_data():
    allowed = {
        "emulator_image_content", "emulator_image_disk_format", "emulator_image_output_format",
        "emulator_image_prefix", "emulator_image_starting_number", "use_dos83_filenames",
        "long_midi_filenames", "eseq_to_midi_long_filenames", "read_floppy_long_filenames", SETTING_IMAGE_FORMAT, SETTING_DISK_FORMAT,
    }
    for profile in PIANO_PROFILES:
        assert profile.evidence_level in {"documented", "unverified"}
        for key in profile.media:
            assert set(proposed_settings(profile, get_preparation_medium(profile, key))) <= allowed


def _multitrack_song():
    song = mido.MidiFile(type=1, ticks_per_beat=480)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=600000),
        mido.MetaMessage("set_tempo", tempo=500000, time=240),
    ]))
    for channel, note, program in ((2, 60, 0), (5, 67, 40)):
        song.tracks.append(mido.MidiTrack([
            mido.Message("program_change", channel=channel, program=program),
            mido.Message("note_on", channel=channel, note=note, velocity=73, time=120),
            mido.Message("control_change", channel=channel, control=64, value=77, time=100),
            mido.Message("note_off", channel=channel, note=note, velocity=42, time=360),
            mido.Message("control_change", channel=channel, control=64, value=0, time=30),
        ]))
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _performance_events(data):
    song = mido.MidiFile(file=io.BytesIO(data))
    tick = 0
    events = []
    for message in mido.merge_tracks(song.tracks):
        tick += message.time
        if not message.is_meta or message.type == "set_tempo":
            events.append((tick, message.copy(time=0).dict()))
    return events


@pytest.mark.parametrize("image_mode", (False, True))
def test_pianodisc_preparation_stages_type0_preserves_performance_and_undoes(
    application, monkeypatch, tmp_path, image_mode,
):
    settings = QSettings(str(tmp_path / "pianodisc.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    source = tmp_path / "SONG.MID"
    original = _multitrack_song()
    source.write_bytes(original)
    window = MidiTitleWindow()
    try:
        if image_mode:
            image_path = tmp_path / "source.img"
            create_floppy_images_from_files(
                [(str(source), source.name)], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
            )
            session = FloppyImageSession.load(image_path)
            window._activate_disk_session(session, session.list_entries())
            window.pendingImageTitleEdits["SONG.MID"] = "Edited title"
        else:
            window._load_regular_files([str(source)], "Loaded")
            window.pendingEdits[str(source)] = "Edited title"
        assert window._preparation_song_counts()["midi_non_type0"] == 1
        profile = get_preparation_profile("pianodisc_228cfx")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        assert window._preparation_profile().key == "pianodisc_228cfx"
        if image_mode:
            prepared = window.pendingImageReplacements["SONG.MID"]
        else:
            prepared = window.pendingRegularConversions[str(source)]["temp_path"]
        song = mido.MidiFile(prepared)
        assert song.type == 0
        assert any(msg.type == "track_name" and msg.name == "Edited title" for msg in song.tracks[0])
        with open(prepared, "rb") as handle:
            assert _performance_events(handle.read()) == _performance_events(original)
        assert window._preparation_song_counts().get("midi_non_type0", 0) == 0
        assert source.read_bytes() == original
        window.undo_last_staged_batch()
        assert window._preparation_profile().key == "unsure"
        assert window._preparation_song_counts()["midi_non_type0"] == 1
        assert not window.pendingImageReplacements
        assert not window.pendingRegularConversions
        edits = window.pendingImageTitleEdits if image_mode else window.pendingEdits
        assert list(edits.values()) == ["Edited title"]
    finally:
        window.pendingRegularConversions.clear()
        window.pendingImageReplacements.clear()
        window.pendingImageTitleEdits.clear()
        window.pendingEdits.clear()
        window.close()


def test_failed_required_type0_conversion_stays_unprepared(application, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "failed-pianodisc.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    source = tmp_path / "SONG.MID"
    original = _multitrack_song()
    source.write_bytes(original)
    window = MidiTitleWindow()
    errors = []
    window._show_error_list = lambda _title, _summary, details, **_kwargs: errors.extend(details)
    try:
        window._load_regular_files([str(source)], "Loaded")
        monkeypatch.setattr(main_window, "convert_midi_file_to_type0_path", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("Invalid MIDI")))
        profile = get_preparation_profile("pianodisc_228cfx")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        assert errors == ["SONG.MID: Invalid MIDI"]
        assert window.pendingRegularConversions == {}
        assert "1 file(s) still need conversion" in window.status_label.text()
        assert source.read_bytes() == original
    finally:
        window.close()


def test_dialog_shows_proposals_without_mutating_settings(application):
    settings = _Settings({"emulator_image_content": "midi", "emulator_image_prefix": "MINE"})
    original = dict(settings.values)
    dialog = PreparationProfileDialog(settings, "mark_i", "original")
    try:
        assert dialog.changes_table.rowCount() > 0
        dialog.medium_combo.setCurrentIndex(dialog.medium_combo.findData("flashfloppy_hfe"))
        proposed_values = [
            dialog.changes_table.item(row, 2).text()
            for row in range(dialog.changes_table.rowCount())
        ]
        assert "HFE" in proposed_values
        assert dialog.changes_table.rowCount() == 4
        assert settings.values == original
        dialog.buttons.button(QDialogButtonBox.Apply).click()
        assert dialog.result() == QDialog.Accepted
        assert settings.values == original
        assert dialog.selection()[1].key == "flashfloppy_hfe"
    finally:
        dialog.close()


def test_dialog_restricts_media_when_switching_to_modern_usb_controller(application):
    dialog = PreparationProfileDialog(_Settings(), "mark_i", "flashfloppy_img")
    try:
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("enspire"))
        assert dialog.medium_combo.count() == 1
        assert dialog.medium_combo.currentData() == "usb"
        assert dialog.changes_table.item(0, 2).text() == "MIDI"
        assert dialog.changes_table.rowCount() == 2
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("custom"))
        assert dialog.changes_table.rowCount() == 0
    finally:
        dialog.close()


def test_apply_sets_export_defaults_and_keeps_staged_work():
    settings = _Settings({"pedal_strength": 20, "strip_metadata": False})
    pending = {"song.mid": "Edited title"}
    window = SimpleNamespace(
        settings=settings,
        pendingEdits=pending,
        pendingRegularConversions={"song.mid": "staged.mid"},
        status_label=SimpleNamespace(setText=lambda _text: None),
        toggle_dos83_filenames=lambda _value: None,
        _set_long_midi_filenames_enabled=lambda _value: None,
        _refresh_preparation_ui=lambda: None,
    )
    profile = get_preparation_profile("mark_i")
    medium = get_preparation_medium(profile, "flashfloppy_hfe")
    MidiTitleWindow._apply_preparation_profile(window, profile, medium)
    assert settings.values[SETTING_PROFILE] == "mark_i"
    assert settings.values[SETTING_MEDIUM] == "flashfloppy_hfe"
    assert settings.values["emulator_image_content"] == "eseq"
    assert MidiTitleWindow._preparation_export_defaults(window) == {
        "image_format": "hfe", "disk_format": "ibm.720", "eseq": True,
    }
    assert window.pendingEdits is pending
    assert window.pendingEdits == {"song.mid": "Edited title"}
    assert window.pendingRegularConversions == {"song.mid": "staged.mid"}
    assert settings.values["pedal_strength"] == 20
    assert settings.values["strip_metadata"] is False

    manual_settings = dict(settings.values)
    custom = get_preparation_profile("custom")
    MidiTitleWindow._apply_preparation_profile(window, custom, get_preparation_medium(custom, ""))
    for key, value in manual_settings.items():
        if key not in {SETTING_PROFILE, SETTING_MEDIUM}:
            assert settings.values[key] == value
    assert MidiTitleWindow._preparation_export_defaults(window) == {}


def test_real_window_applies_profile_to_image_defaults_ahead_of_source_defaults(
    application, monkeypatch, tmp_path,
):
    settings = QSettings(str(tmp_path / "profile-settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    window = MidiTitleWindow()
    try:
        profile = get_preparation_profile("mark_iii")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "flashfloppy_hfe"))
        assert "Mark III" in window.preparationLabel.text()
        assert window.settingsUseDos83FilenamesAction.isChecked()

        def inspect_new_image(dialog):
            image_type, disk_format = dialog.findChildren(QComboBox)
            assert image_type.currentData() == "hfe"
            assert disk_format.currentData().key == "ibm.1440"
            eseq_option = next(checkbox for checkbox in dialog.findChildren(QCheckBox) if "PIANODIR" in checkbox.text())
            assert not eseq_option.isChecked()
            return QDialog.Rejected

        window._exec_child_dialog = inspect_new_image
        assert window._prompt_for_new_image_options() is None

        def inspect_explicit_image(dialog):
            image_type, disk_format = dialog.findChildren(QComboBox)
            assert image_type.currentData() == "hfe"
            assert disk_format.currentData().key == "ibm.1440"
            return QDialog.Rejected

        window._exec_child_dialog = inspect_explicit_image
        assert window._prompt_for_save_image_options(
            default_ext="img", default_disk_format=DISK_FORMAT_BY_KEY["ibm.720"],
        ) is None
    finally:
        window.close()


def _midi_song():
    track = b"\x00\xff\x03\x04Song\x00\xb0\x07\x00\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


def test_mark_ii_apply_prepares_loaded_songs_and_undo_restores_previous_profile(
    application, monkeypatch, tmp_path,
):
    settings = QSettings(str(tmp_path / "prepare-settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args: pytest.fail("Apply must not ask for another conversion confirmation"))
    source = tmp_path / "song.mid"
    source.write_bytes(_midi_song())
    window = MidiTitleWindow()
    try:
        window._load_regular_files([str(source)], "Loaded")
        assert not window.regularEseqMode
        profile = get_preparation_profile("mark_ii")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        assert window.regularEseqMode
        assert window.pendingGeneratePianodir
        staged = window.pendingRegularConversions[str(source)]
        assert staged["target_kind"] == "eseq"
        assert staged["target_filename"].upper().endswith(".FIL")
        assert window._preparation_export_defaults()["disk_format"] == "ibm.720"
        assert source.read_bytes() == _midi_song()
        assert not list(tmp_path.glob("*.FIL"))
        window.undo_last_staged_batch()
        assert window.pendingRegularConversions == {}
        assert window._preparation_profile().key == "unsure"
        assert not window.regularEseqMode
        assert not window.pendingGeneratePianodir
    finally:
        window.pendingRegularConversions.clear()
        window.pendingGeneratePianodir = False
        window.close()


def test_mark_ii_prepares_later_folder_import_and_keeps_empty_list_in_eseq_mode(
    application, monkeypatch, tmp_path,
):
    settings = QSettings(str(tmp_path / "import-settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    source = tmp_path / "later.mid"
    source.write_bytes(_midi_song())
    window = MidiTitleWindow()
    try:
        profile = get_preparation_profile("mark_ii")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        assert window.regularEseqMode
        assert window._regular_file_count() == 0
        window._load_regular_files([str(source)], "Loaded later")
        application.processEvents()
        assert window.pendingRegularConversions[str(source)]["target_kind"] == "eseq"
        assert window.regularEseqMode
        assert window.pendingGeneratePianodir
        assert source.read_bytes() == _midi_song()
    finally:
        window.pendingRegularConversions.clear()
        window.pendingGeneratePianodir = False
        window.close()


@pytest.mark.parametrize("profile_key, medium_key, shorten", (
    ("mark_iii", "flashfloppy_img", True), ("enspire", "usb", False),
))
def test_destination_stages_only_incompatible_floppy_filenames_without_collisions(
    application, monkeypatch, tmp_path, profile_key, medium_key, shorten,
):
    settings = QSettings(str(tmp_path / "filenames.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    paths = [tmp_path / name for name in ("LONG_SON.mid", "long song one.mid", "long song two.mid", "valid.mid")]
    for path in paths:
        path.write_bytes(_midi_song())
    # An unlisted sibling also owns its filename during in-place Save.
    (tmp_path / "LONG_SO1.MID").write_bytes(_midi_song())
    window = MidiTitleWindow()
    try:
        window._load_regular_files([str(path) for path in paths], "Loaded")
        assert window._preparation_song_counts()["dos83_midi"] == 2
        profile = get_preparation_profile(profile_key)
        window._apply_preparation_profile(profile, get_preparation_medium(profile, medium_key))
        if shorten:
            assert set(window.pendingRegularRenames) == {str(paths[1]), str(paths[2])}
            names = list(window.pendingRegularRenames.values())
            assert len({name.upper() for name in names}) == 2
            assert all(window._validate_image_filename(name, enforce_dos83=True) is None for name in names)
            assert not {name.upper() for name in names} & {"LONG_SON.MID", "LONG_SO1.MID"}
            window.undo_last_staged_batch()
            assert window.pendingRegularRenames == {}
        else:
            assert window.pendingRegularRenames == {}
        assert all(path.read_bytes() == _midi_song() for path in paths)
    finally:
        window.pendingRegularRenames.clear()
        window.close()


def test_persisted_eseq_destination_controls_startup_and_clear_without_staging(
    application, monkeypatch, tmp_path,
):
    settings = QSettings(str(tmp_path / "startup.ini"), QSettings.IniFormat)
    settings.setValue(SETTING_PROFILE, "mark_ii")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args: main_window.QMessageBox.Yes)
    window = MidiTitleWindow()
    try:
        assert window.regularEseqMode
        assert window._regular_file_count() == 0
        assert not window.pendingGeneratePianodir
        window.clear_list()
        assert window.regularEseqMode
        assert window._regular_file_count() == 0
        assert window.pendingRegularConversions == {}
    finally:
        window.close()


def test_reloading_saved_image_does_not_schedule_destination_preparation(
    application, monkeypatch, tmp_path,
):
    settings = QSettings(str(tmp_path / "reload.ini"), QSettings.IniFormat)
    settings.setValue(SETTING_PROFILE, "mark_ii")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    window = MidiTitleWindow()
    calls = []
    window._schedule_destination_preparation = lambda: calls.append("prepare")
    try:
        session = FloppyImageSession.create_blank_session(DISK_FORMAT_BY_KEY["ibm.720"])
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
        assert calls == []
        assert window.pendingImageReplacements == {}
        imported_session = FloppyImageSession.create_blank_session(DISK_FORMAT_BY_KEY["ibm.720"])
        window._activate_disk_session(imported_session, imported_session.list_entries())
        assert calls == ["prepare"]
    finally:
        window.close()


@pytest.mark.parametrize("image_mode", (False, True))
def test_mark_ii_prepares_clavinova_containers_and_catalog_then_midi_clears_pending_catalog(
    application, monkeypatch, tmp_path, image_mode,
):
    settings = QSettings(str(tmp_path / "clavinova.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args: main_window.QMessageBox.Yes)
    mda = tmp_path / "CLP_01.MDA"
    track = b"\x00\xff\x58\x04\x04\x02\x18\x08" + _midi_song()[22:]
    midi_with_meter = _midi_song()[:18] + len(track).to_bytes(4, "big") + track
    mda.write_bytes(convert_midi_bytes_to_eseq_bytes(midi_with_meter, container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA))
    original = mda.read_bytes()
    music_dir = tmp_path / "MUSIC.DIR"
    music_dir.write_bytes(build_music_dir_bytes([]))
    midi = tmp_path / "SECOND.MID"
    midi.write_bytes(_midi_song())
    window = MidiTitleWindow()
    try:
        if image_mode:
            image_path = tmp_path / "clavinova.img"
            create_floppy_images_from_files(
                [(str(path), path.name) for path in (mda, music_dir, midi)], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
            )
            session = FloppyImageSession.load(image_path)
            window._activate_disk_session(session, session.list_entries())
            assert window.imageEseqVariant == ESEQ_VARIANT_CLAVINOVA
        else:
            window._load_regular_files([str(mda), str(midi)], "Clavinova loaded")
            assert window.regularEseqVariant == ESEQ_VARIANT_CLAVINOVA
        assert window._preparation_song_counts()["clavinova"] == 1
        profile = get_preparation_profile("mark_ii")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        assert window._active_eseq_variant() == ESEQ_VARIANT_DISKLAVIER
        assert window._eseq_directory_filename() == "PIANODIR.FIL"
        assert window.pendingGeneratePianodir
        assert window._preparation_song_counts()["clavinova"] == 0
        assert window._preparation_song_counts()["midi"] == 0
        assert all(filename.endswith(".FIL") for _row, _path, _kind, filename in window._preparation_song_rows())
        if image_mode:
            assert "MUSIC.DIR" in window.pendingImageDeletes
            assert not is_clavinova_mda_file(window.pendingImageReplacements["CLP_01.MDA"])
        else:
            assert not window.regularPianodirSourcePath
            assert not is_clavinova_mda_file(window.pendingRegularConversions[str(mda)]["temp_path"])
        assert mda.read_bytes() == original
        window.undo_last_staged_batch()
        assert window._active_eseq_variant() == ESEQ_VARIANT_CLAVINOVA
        assert window._preparation_profile().key == "unsure"
        assert window._preparation_song_counts()["clavinova"] == 1
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        modern = get_preparation_profile("enspire")
        window._apply_preparation_profile(modern, get_preparation_medium(modern, "usb"))
        assert window._preparation_song_counts()["eseq"] == 0
        assert not window.pendingGeneratePianodir
        assert not (window.imageEseqMode if image_mode else window.regularEseqMode)
        assert mda.read_bytes() == original
    finally:
        window.pendingRegularConversions.clear()
        window.pendingImageReplacements.clear()
        window.pendingImageRenames.clear()
        window.pendingImageDeletes.clear()
        window.pendingGeneratePianodir = False
        window.close()


def test_failed_clavinova_preparation_remains_visible_and_is_counted_as_unprepared(
    application, monkeypatch, tmp_path,
):
    settings = QSettings(str(tmp_path / "clavinova-failure.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    track = b"\x00\xff\x58\x04\x04\x02\x18\x08" + _midi_song()[22:]
    source = tmp_path / "CLP_01.MDA"
    original = convert_midi_bytes_to_eseq_bytes(
        _midi_song()[:18] + len(track).to_bytes(4, "big") + track,
        container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA,
    )
    source.write_bytes(original)
    window = MidiTitleWindow()
    errors = []
    window._show_error_list = lambda _title, _summary, details, **_kwargs: errors.extend(details)
    try:
        window._load_regular_files([str(source)], "Loaded")
        monkeypatch.setattr(main_window, "convert_eseq_file_to_midi_path", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("Unrecoverable event data")))
        profile = get_preparation_profile("mark_ii")
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        assert window.pendingRegularConversions == {}
        assert window._preparation_song_counts()["clavinova"] == 1
        assert "1 file(s) still need conversion" in window.status_label.text()
        assert errors == ["CLP_01.MDA: Unrecoverable event data"]
        assert source.read_bytes() == original
    finally:
        window.pendingGeneratePianodir = False
        window.close()
