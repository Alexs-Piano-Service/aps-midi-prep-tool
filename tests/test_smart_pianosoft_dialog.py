"""The utility owns its workers and preserves source media through the UI."""

import json
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import wave

import mido
import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFormLayout, QLabel, QPushButton, QWidget

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app import smart_pianosoft_dialog as dialogs
from aps_midi_prep_tool_app import smart_pianosoft_media as media
from aps_midi_prep_tool_app import smart_pianosoft_sync as sync
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyDriveInfo, FloppyOperationCancelled, GreaseweazleDeviceInfo,
    GreaseweazleFloppySource,
)
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.responsive_button_box import ResponsiveDialogButtonBox
from aps_midi_prep_tool_app.smart_pianosoft_workflow import AlbumBuildError, LoadedAlbumSource, ListedAlbumTrack
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes
from test_smart_pianosoft_sync import _cd_reference_message
from test_window_menu_behavior import window


def _wait(condition, *, timeout=5):
    application = QApplication.instance()
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(10)
    application.processEvents()
    assert condition(), "The Smart PianoSoft worker did not finish in time"


def _wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((2, 2, 44100, 4410, "NONE", "not compressed"))
        audio.writeframes(b"\x10\0\x10\0" * 4410)


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "album"
    source.mkdir()
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    for kind, second in ((0, 1), (2, 10)):
        track.append(mido.Message("sysex", data=[
            0x43, 0x71, 0x7B, kind, 0, second, 0, 0, kind // 2, 3,
            *([1, 126] * 128),
        ]))
    track.extend([
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=96),
    ])
    midi.tracks.append(track)
    midi.save(source / "song.mid")
    (source / "PSONG.MNG").write_bytes(_psong_bytes([("SONG.MID", "Synthetic song")]))
    (source / "PDISK.MNG").write_bytes(_pdisk_bytes("Synthetic album"))
    return source


@pytest.fixture
def dialog(tmp_path, qt_application):
    settings = QSettings(str(tmp_path / "smart-pianosoft.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False)
    instance.show()
    yield instance
    if instance.is_busy:
        instance.cancel_or_close()
        _wait(lambda: not instance.is_busy)
    instance.close()
    qt_application.processEvents()


def _scan(dialog, source):
    dialog.source_edit.setText(str(source))
    dialog.start_scan()
    _wait(lambda: not dialog.is_busy)
    assert dialog.album is not None, dialog.details.toPlainText()


def _loaded_source(source):
    return LoadedAlbumSource(
        label="Loaded floppy.img", original_source=source,
        song_catalog=(source / "PSONG.MNG").read_bytes(),
        disk_catalog=(source / "PDISK.MNG").read_bytes(),
        tracks=(ListedAlbumTrack("SONG.MID", "song.mid", "Synthetic song",
                                 (source / "song.mid").read_bytes()),),
    )


@pytest.mark.parametrize("kind", ["greaseweazle", "usb"])
def test_source_picker_accepts_discovered_floppy_descriptors(dialog, monkeypatch, kind):
    from aps_midi_prep_tool_app import disk_device_discovery

    device = GreaseweazleDeviceInfo("COM42", "Greaseweazle")
    gw_source = GreaseweazleFloppySource(device.path, "B", DISK_FORMAT_BY_KEY["ibm.720"])
    usb_source = FloppyDriveInfo("/dev/example-floppy", 737280, transport="usb")
    selected = gw_source if kind == "greaseweazle" else usb_source

    def discover(_parent, **kwargs):
        assert kwargs["include_greaseweazle"] is True
        return ([], [device], []) if kind == "greaseweazle" else ([usb_source], [], [])

    monkeypatch.setattr(disk_device_discovery, "discover_floppy_devices", discover)
    monkeypatch.setattr(dialog, "_choose_greaseweazle_source", lambda found: gw_source if found is device else None)
    dialog.choose_floppy()
    assert dialog._source() is selected
    assert dialog.source_edit.isReadOnly()
    assert dialog.source_edit.text() == selected.display_name
    dialog._select_external_source("replacement.img")
    assert dialog._source() == "replacement.img"
    assert not dialog.source_edit.isReadOnly()


def test_greaseweazle_options_preserve_selected_drive_and_geometry(dialog, monkeypatch):
    device = GreaseweazleDeviceInfo("COM42", "Greaseweazle")
    dialog.settings.setValue("greaseweazle_drive", "B")
    dialog.settings.setValue("read_floppy_gw_format", "ibm.1440")
    dialog.settings.setValue("read_floppy_gw_revs", 2)
    dialog.settings.setValue("read_floppy_gw_retries", 4)

    def accept(options):
        assert options.findChild(QComboBox, "smartPianoSoftGreaseweazleDrive").currentText() == "B"
        assert options.findChild(QComboBox, "smartPianoSoftGreaseweazleFormat").currentData().key == "ibm.1440"
        return QDialog.Accepted

    monkeypatch.setattr(dialogs.QDialog, "exec", accept)
    selected = dialog._choose_greaseweazle_source(device)
    assert selected == GreaseweazleFloppySource("COM42", "B", DISK_FORMAT_BY_KEY["ibm.1440"], revs=2, retries=4)
    assert dialog.settings.value("greaseweazle_device_path") == "COM42"


def test_cancelled_greaseweazle_options_keep_current_source(dialog, source, monkeypatch):
    from aps_midi_prep_tool_app import disk_device_discovery

    dialog._select_external_source(source)
    monkeypatch.setattr(disk_device_discovery, "discover_floppy_devices", lambda *_a, **_k:
                        ([], [GreaseweazleDeviceInfo("COM42", "Greaseweazle")], []))
    monkeypatch.setattr(dialogs.QDialog, "exec", lambda _dialog: QDialog.Rejected)
    dialog.choose_floppy()
    assert dialog._source() == str(source)


def test_greaseweazle_scan_uses_selected_source_without_saving_its_label(dialog, source, monkeypatch):
    selected = GreaseweazleFloppySource("COM42", "B", DISK_FORMAT_BY_KEY["ibm.720"])
    snapshots = []

    def snapshot(found, destination, **kwargs):
        import shutil
        snapshots.append(found)
        assert callable(kwargs["progress"])
        kwargs["progress"](2, 80, "Reading cylinder 2")
        return shutil.copytree(source, destination)

    monkeypatch.setattr(media, "read_floppy_source", snapshot)
    dialog._select_external_source(selected)
    dialog.start_scan()
    _wait(lambda: not dialog.is_busy)
    assert dialog.album is not None, dialog.details.toPlainText()
    assert dialog.album.title == "Synthetic album"
    assert dialog.table.rowCount() == 1
    assert snapshots == [selected]
    assert not dialog.settings.contains("sps_source")


def test_agreement_continues_scan_and_preparation_without_repeating_notice(source, tmp_path, qt_application, monkeypatch):
    parent = QWidget()
    notices = []
    parent._show_yamaha_personal_use_notice = lambda: notices.append(True) or True
    settings = QSettings(str(tmp_path / "consent.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, parent, discover_on_open=False)
    instance.show()
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    try:
        _scan(instance, source)
        assert notices == [True]
        audio = tmp_path / "paired.wav"
        _wav(audio)
        instance.audio_paths[1] = audio
        instance.output_edit.setText(str(tmp_path))
        instance.start_preparation()
        _wait(lambda: not instance.is_busy)
        assert instance.result_folder is not None, instance.details.toPlainText()
        assert len(list((instance.result_folder / "Disklavier").glob("*.wav"))) == 1
        assert notices == [True]
    finally:
        instance.close()
        parent.close()


def test_declining_consent_stops_direct_preparation_before_encoding(source, tmp_path, qt_application, monkeypatch):
    parent = QWidget()
    parent._show_yamaha_personal_use_notice = lambda: False
    settings = QSettings(str(tmp_path / "decline.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, parent, discover_on_open=False)
    instance.show()
    monkeypatch.setattr(dialogs, "prepare_album", lambda *_a, **_k: pytest.fail("Encoded without agreement"))
    try:
        instance.source_edit.setText(str(source))
        instance.output_edit.setText(str(tmp_path))
        instance.start_preparation()
        _wait(lambda: not instance.is_busy)
        assert instance.album is None and instance.result_folder is None
        assert instance.status.text() == instance.text("cancelled")
        assert instance.options.isEnabled() and instance.start_button.isEnabled()
        assert not instance._source_consent_granted
    finally:
        instance.close()
        parent.close()


@pytest.mark.parametrize("close", [False, True])
def test_cancelling_while_waiting_for_consent_releases_worker(source, tmp_path, qt_application, monkeypatch, close):
    parent = QWidget()
    parent._show_yamaha_personal_use_notice = lambda: pytest.fail("Unexpected synchronous notice")
    settings = QSettings(str(tmp_path / "waiting.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, parent, discover_on_open=False)
    instance.show()
    requests = []
    monkeypatch.setattr(instance, "_request_personal_use_consent", requests.append)
    monkeypatch.setattr(dialogs, "prepare_album", lambda *_a, **_k: pytest.fail("Continued after cancellation"))
    try:
        instance.source_edit.setText(str(source))
        instance.output_edit.setText(str(tmp_path))
        instance.start_preparation()
        _wait(lambda: bool(requests))
        assert instance.is_busy
        assert not requests[0].wait(0)
        instance.close() if close else instance.cancel_or_close()
        _wait(lambda: not instance.is_busy, timeout=2)
        assert requests[0].wait(0) and not requests[0].approved
        assert instance.album is None and instance.result_folder is None
        if close:
            assert not instance.isVisible()
            assert instance._workspace is None
        else:
            assert instance.status.text() == instance.text("cancelled")
            assert instance.start_button.isEnabled()
    finally:
        instance.close()
        parent.close()


def test_loaded_source_inherits_prior_read_agreement(source, tmp_path, qt_application):
    parent = QWidget()
    parent.image_session = SimpleNamespace(_yamaha_consent_granted=True)
    parent._show_yamaha_personal_use_notice = lambda: pytest.fail("Repeated agreement for the loaded disk")
    settings = QSettings(str(tmp_path / "agreed-read.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, parent, discover_on_open=False, loaded_source=_loaded_source(source))
    instance.show()
    try:
        _wait(lambda: instance.album is not None and not instance.is_busy)
        assert instance.table.rowCount() == 1
    finally:
        instance.close()
        parent.close()


def test_smart_worker_pauses_at_copy_protection_before_continuing(qt_application):
    continued, requests, failures = [], [], []

    def read(_cancel, progress):
        progress({"phase": "read", "completed": 1, "total": 80, "message": "Yamaha copy protection?"})
        continued.append(True)

    worker = dialogs.SmartPianoSoftWorker(read, require_consent=True)
    worker.consentRequested.connect(requests.append)
    worker.failed.connect(failures.append)
    worker.start()
    try:
        _wait(lambda: bool(requests))
        assert not continued
        requests[0].resolve(True)
        _wait(lambda: not worker.isRunning())
        assert continued == [True] and not failures
    finally:
        worker.cancelled.set()
        worker.wait(2000)
@pytest.mark.parametrize("profile,expected,route", [
    ("enspire", "midi_mp3", "ENSPIRE USB:"),
    ("mark_iv", "encoded_wav", "Mark IV:"),
    ("mark_iii", "encoded_wav", "External-input playback:"),
    ("e3_850", "encoded_wav", "External-input playback:"),
    ("unsure", "encoded_wav", "compatible PianoSoft PlusAudio decoder"),
])
def test_preparing_for_selects_output_and_explains_playback_route(tmp_path, qt_application, profile, expected, route):
    settings = QSettings(str(tmp_path / (profile + ".ini")), QSettings.IniFormat)
    settings.setValue("preparation_profile", profile)
    # An old manual choice cannot override the current Preparing for target.
    settings.setValue("sps_output_format", "encoded_wav" if expected == "midi_mp3" else "midi_mp3")
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False)
    try:
        assert instance.format_combo.currentData() == expected
        assert route in instance.format_combo.toolTip()
        assert instance.format_combo.accessibleDescription() == instance.format_combo.toolTip()
        assert instance.target_label.text() == instance.profile.label
        if profile == "enspire":
            instance.format_combo.setCurrentIndex(instance.format_combo.findData("encoded_wav"))
            assert "External-input playback:" in instance.format_combo.toolTip()
            assert "Direct ENSPIRE USB playback needs paired MIDI/MP3" in instance.format_combo.toolTip()
    finally:
        instance.close()


@pytest.mark.parametrize("random_suffixes", [False, True])
@pytest.mark.parametrize("output_format", ["encoded_wav", "midi_mp3"])
@pytest.mark.parametrize("profile,catalogs,preserve", [
    ("mark_iii", True, True), ("enspire", False, True), ("midi_export", False, False),
])
def test_selected_output_format_is_passed_to_background_preparation(
    dialog, source, tmp_path, monkeypatch, random_suffixes, output_format, profile, catalogs, preserve,
):
    _scan(dialog, source)
    captured = []
    def prepare(album, destination, **options):
        captured.append(options)
        return Path(destination)
    monkeypatch.setattr(dialogs, "prepare_album", prepare)
    dialog.output_edit.setText(str(tmp_path))
    dialog.settings.setValue("random_output_suffixes", random_suffixes)
    dialog.settings.setValue(dialogs.SETTING_PROFILE, profile)
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(output_format))
    dialog.start_preparation()
    assert not dialog.format_combo.isEnabled()
    _wait(lambda: not dialog.is_busy)
    assert captured[0]["output_format"] == output_format
    assert captured[0]["random_suffixes"] is random_suffixes
    assert captured[0]["generate_yamaha_catalogs"] is catalogs
    assert captured[0]["preserve_yamaha_catalogs"] is preserve
    assert isinstance(captured[0]["cancel"], threading.Event)


@pytest.mark.parametrize("profile,enabled,preserve", [
    ("enspire", True, True), ("mark_iii", False, True), ("custom", True, False),
])
def test_preparation_uses_persisted_manual_catalog_setting_and_reads_updates(
        dialog, source, tmp_path, monkeypatch, profile, enabled, preserve):
    dialog.settings.setValue(dialogs.SETTING_PROFILE, profile)
    dialog.settings.setValue("generate_yamaha_catalogs", enabled)
    dialog.settings.sync()
    settings = QSettings(dialog.settings.fileName(), QSettings.IniFormat)
    reopened = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False)
    captured = []

    def prepare(album, destination, **options):
        captured.append(options)
        return Path(destination)

    monkeypatch.setattr(dialogs, "prepare_album", prepare)
    try:
        _scan(reopened, source)
        reopened.output_edit.setText(str(tmp_path))
        reopened.start_preparation()
        _wait(lambda: not reopened.is_busy)
        settings.setValue("generate_yamaha_catalogs", not enabled)
        reopened.start_preparation()
        _wait(lambda: not reopened.is_busy)
        assert [options["generate_yamaha_catalogs"] for options in captured] == [enabled, not enabled]
        assert all(options["preserve_yamaha_catalogs"] is preserve for options in captured)
    finally:
        if reopened.is_busy:
            reopened.cancel_or_close()
            _wait(lambda: not reopened.is_busy)
        reopened.close()


def test_loaded_list_scans_automatically_before_cd_discovery(source, tmp_path, qt_application, monkeypatch):
    settings = QSettings(str(tmp_path / "loaded.ini"), QSettings.IniFormat)
    settings.setValue("sps_source", str(tmp_path / "stale source.img"))
    discoveries = []
    monkeypatch.setattr(media, "read_floppy_source", lambda *_a, **_k: pytest.fail("Reread loaded media"))
    monkeypatch.setattr(media, "discover_cd_drives", lambda: discoveries.append(True) or [("/dev/test-cd", "CD drive")])
    instance = dialogs.SmartPianoSoftDialog(settings, loaded_source=_loaded_source(source))
    instance.show()
    try:
        _wait(lambda: bool(discoveries) and not instance.is_busy)
        assert instance.album is not None, instance.details.toPlainText()
        assert instance.table.rowCount() == 1
        assert [instance.table.item(0, column).text() for column in range(3)] == ["1", "Synthetic song", "song.mid"]
        assert instance.album_label.text() == "Album: Synthetic album"
        assert instance.album_label.isVisible()
        assert instance.source_edit.isReadOnly()
        assert instance.source_edit.text() == "Current song list: Loaded floppy.img"
        assert instance.cd_combo.currentData() == "/dev/test-cd"
        assert settings.value("sps_source") == str(tmp_path / "stale source.img")
    finally:
        instance.close()
        qt_application.processEvents()


def test_loaded_list_prepares_after_original_source_is_removed(source, tmp_path, qt_application, monkeypatch):
    loaded = _loaded_source(source)
    source.rename(tmp_path / "removed-source")
    settings = QSettings(str(tmp_path / "cached.ini"), QSettings.IniFormat)
    monkeypatch.setattr(media, "read_floppy_source", lambda *_a, **_k: pytest.fail("Reread loaded media"))
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: pytest.fail("Unexpected CD read"))
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    instance = dialogs.SmartPianoSoftDialog(settings, loaded_source=loaded, discover_on_open=False)
    instance.show()
    try:
        _wait(lambda: instance.album is not None and not instance.is_busy)
        paired = tmp_path / "paired.wav"
        _wav(paired)
        instance.audio_paths[1] = paired
        instance.output_edit.setText(str(tmp_path))
        instance.start_preparation()
        _wait(lambda: not instance.is_busy)
        assert instance.result_folder is not None, instance.details.toPlainText()
        assert (instance.result_folder / "MIDI/SONG.MID").read_bytes() == loaded.tracks[0].midi_bytes
        assert json.loads((instance.result_folder / "manifest.json").read_text())["status"] == "complete"
    finally:
        workspace = Path(instance._workspace.name)
        instance.close()
        qt_application.processEvents()
        assert not workspace.exists()


def test_switching_sources_can_restore_current_list_without_remembering_its_label(source, tmp_path, qt_application):
    settings = QSettings(str(tmp_path / "switch.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, loaded_source=_loaded_source(source), discover_on_open=False)
    instance.show()
    try:
        _wait(lambda: instance.album is not None and not instance.is_busy)
        instance.audio_paths[1] = tmp_path / "old-pairing.wav"
        instance._select_external_source(source)
        assert instance.album is None and not instance.audio_paths
        assert not instance.source_edit.isReadOnly()
        assert not instance.album_label.isVisible()
        instance.start_scan()
        _wait(lambda: instance.album is not None and not instance.is_busy)
        assert settings.value("sps_source") == str(source)
        instance.use_current_list()
        _wait(lambda: instance.album is not None and not instance.is_busy)
        assert instance.source_edit.isReadOnly()
        assert settings.value("sps_source") == str(source)
        assert instance.table.item(0, 2).text() == "song.mid"
    finally:
        instance.close()
        qt_application.processEvents()


def test_invalid_loaded_list_does_not_fall_back_to_remembered_source(source, tmp_path, qt_application, monkeypatch):
    settings = QSettings(str(tmp_path / "invalid-loaded.ini"), QSettings.IniFormat)
    settings.setValue("sps_source", str(source))
    loaded = LoadedAlbumSource("Current files", source, b"", b"", (), "This list has no Smart PianoSoft catalog.")
    monkeypatch.setattr(media, "read_floppy_source", lambda *_a, **_k: pytest.fail("Used remembered source"))
    instance = dialogs.SmartPianoSoftDialog(settings, loaded_source=loaded, discover_on_open=False)
    instance.show()
    try:
        _wait(lambda: bool(instance.details.toPlainText()) and not instance.is_busy)
        assert instance.album is None and instance.table.rowCount() == 0
        assert "no Smart PianoSoft catalog" in instance.details.toPlainText()
        assert not instance.pair_button.isEnabled()
    finally:
        instance.close()
        qt_application.processEvents()


def test_immediate_close_prevents_deferred_loaded_scan(source, tmp_path, qt_application, monkeypatch):
    settings = QSettings(str(tmp_path / "closed-loaded.ini"), QSettings.IniFormat)
    monkeypatch.setattr(dialogs, "scan_loaded_album", lambda *_a, **_k: pytest.fail("Scanned after closing"))
    instance = dialogs.SmartPianoSoftDialog(settings, loaded_source=_loaded_source(source))
    instance.close()
    qt_application.processEvents()
    QTest.qWait(20)
    assert instance._workspace is None and not instance.is_busy


@pytest.mark.parametrize("cd_track", [None, 3])
def test_scan_pair_and_clear_leave_sources_untouched(dialog, source, tmp_path, monkeypatch, cd_track):
    if cd_track is not None:
        midi = mido.MidiFile(source / "song.mid")
        midi.tracks[0].insert(0, _cd_reference_message(cd_track))
        midi.save(source / "song.mid")
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    _scan(dialog, source)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "Synthetic song"
    expected = "Match CD track automatically" if cd_track is None else "CD track 3 (disk reference)"
    assert dialog.table.item(0, 3).text() == expected
    assert dialog.table.item(0, 3).toolTip() == dialog.text("cd_hint_details")
    assert dialog.pair_button.isEnabled()
    assert dialog.album.source_directory != source
    paired = tmp_path / "music.wav"
    _wav(paired)
    monkeypatch.setattr(dialogs.QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(paired), ""))
    dialog.pair_audio()
    assert dialog.audio_paths == {1: paired}
    assert dialog.table.item(0, 3).text() == str(paired)
    dialog.clear_pairing()
    assert not dialog.audio_paths
    assert dialog.table.item(0, 3).text() == expected
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("random_suffixes", [False, True])
def test_start_scans_and_runs_cd_pipeline_without_blocking_ui(dialog, source, tmp_path, monkeypatch, random_suffixes):
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    calls = []
    track = media.CDTrack(1, 0, 75)

    def toc(device, cancel=None):
        assert device == "/dev/test-cd"
        assert isinstance(cancel, threading.Event)
        calls.append("toc")
        return (track,)

    def rip(device, selected, destination, cancel=None, progress=None):
        assert selected == track
        assert isinstance(cancel, threading.Event)
        calls.append("rip")
        _wav(destination)
        progress(1, 1, "Ripped synthetic track")
        return Path(destination)

    monkeypatch.setattr(media, "read_cd_toc", toc)
    monkeypatch.setattr(media, "rip_cd_track", rip)
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    dialog.source_edit.setText(str(source))
    dialog.output_edit.setText(str(tmp_path))
    dialog.settings.setValue("random_output_suffixes", random_suffixes)
    dialog.cd_combo.setEditText("/dev/test-cd")
    initial_icon = dialog.start_button.icon().pixmap(24, 24).toImage()
    assert dialog.start_button.text() == dialog.text("start", format="WAV")
    dialog.start_preparation()
    assert dialog.is_busy
    assert not dialog.options.isEnabled()
    assert not dialog.start_button.isEnabled()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result_folder is not None, dialog.details.toPlainText()
    if random_suffixes:
        assert dialog.result_folder.name.startswith("Smart PianoSoft - Synthetic album - ")
    else:
        assert dialog.result_folder.name == "Smart PianoSoft - Synthetic album"
    report = json.loads((dialog.result_folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    assert report["hardware_verified"] is False
    assert len(list((dialog.result_folder / "Disklavier").glob("*.wav"))) == 1
    assert (dialog.result_folder / "MIDI/SONG.MID").read_bytes() == originals["song.mid"]
    assert calls == ["toc", "rip", "toc"]
    assert dialog.open_button.isEnabled()
    assert dialog.options.isEnabled()
    assert dialog.start_button.text() == dialog.text("restart", format="WAV")
    assert dialog.start_button.icon().pixmap(24, 24).toImage() != initial_icon
    first_output = dialog.result_folder
    dialog.start_button.click()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result_folder != first_output
    assert json.loads((dialog.result_folder / "manifest.json").read_text())["status"] == "complete"
    assert calls == ["toc", "rip", "toc"] * 2
    assert dialog.start_button.text() == dialog.text("restart", format="WAV")
    dialog.invalidate_scan()
    assert dialog.start_button.text() == dialog.text("start", format="WAV")
    assert dialog.start_button.icon().pixmap(24, 24).toImage() == initial_icon
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("scan_first", [False, True])
@pytest.mark.parametrize("failure_stage", ["toc", "rip"])
def test_cd_disconnect_retains_album_and_settings_for_fresh_retry(
    dialog, source, tmp_path, monkeypatch, scan_first, failure_stage,
):
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    monkeypatch.setattr(dialogs.QMessageBox, "exec", lambda _box: pytest.fail("No progress to save"))
    calls = []
    scans = []
    scanner = dialogs._scan_source
    track = media.CDTrack(1, 0, 75)

    def scan(*args, **kwargs):
        scans.append(args[0])
        return scanner(*args, **kwargs)

    def toc(device, cancel=None):
        calls.append(("toc", device))
        if device == "/dev/disconnected" and failure_stage == "toc":
            raise media.CDReadError("The CD drive is no longer connected")
        return (track,)

    def rip(device, selected, destination, cancel=None, progress=None):
        calls.append(("rip", device))
        progress(1, 1, "Reading with secure error correction…")
        if device == "/dev/disconnected":
            raise media.CDReadError("The CD drive disconnected while reading")
        _wav(destination)
        return Path(destination)

    monkeypatch.setattr(dialogs, "_scan_source", scan)
    monkeypatch.setattr(media, "read_cd_toc", toc)
    monkeypatch.setattr(media, "rip_cd_track", rip)
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    monkeypatch.setattr(media, "discover_cd_drives", lambda: [("/dev/reconnected", "Reconnected CD drive")])
    dialog.source_edit.setText(str(source))
    if scan_first:
        _scan(dialog, source)
        paired = tmp_path / "manual.wav"
        _wav(paired)
        dialog.audio_paths[1] = paired
    expected_pairs = dict(dialog.audio_paths)
    dialog.output_edit.setText(str(tmp_path))
    dialog.cd_combo.setEditText("/dev/disconnected")
    dialog._completed_cd_device = "/dev/disconnected"
    dialog._update_controls()
    progress = []
    dialog.progress.valueChanged.connect(progress.append)
    dialog.start_preparation()
    dialog.start_preparation()  # Repeated activation cannot launch a second worker.
    _wait(lambda: not dialog.is_busy)

    assert dialog.status.text() == dialog.text("cd_read_failed"), dialog.details.toPlainText()
    assert dialog.text("cd_read_retry") in dialog.details.toPlainText()
    assert dialog.album is not None
    assert dialog.album.source_directory.is_dir()
    assert len(scans) == 1
    assert dialog.table.rowCount() == 1
    assert dialog.audio_paths == expected_pairs
    assert dialog.output_edit.text() == str(tmp_path)
    assert dialog.format_combo.currentData() == "encoded_wav"
    assert dialog._completed_cd_device is None
    assert dialog.start_button.text() == dialog.text("start", format="WAV")
    assert dialog.start_button.isEnabled()
    assert dialog.refresh_button.isEnabled()
    assert dialog.cd_combo.isEnabled()
    assert 100 not in progress
    assert dialog.progress.value() == 0
    failed_output = dialog.result_folder
    assert failed_output is None
    assert not list(tmp_path.glob("Smart PianoSoft*"))
    assert calls == ([("toc", "/dev/disconnected")] if failure_stage == "toc" else
                     [("toc", "/dev/disconnected"), ("rip", "/dev/disconnected")])

    # Refresh may discover a different device path. Start a new attempt from
    # the retained MIDI snapshot, never from stale ripped audio.
    dialog.refresh_button.click()
    _wait(lambda: not dialog.is_busy)
    assert dialog._cd_device() == "/dev/reconnected"
    dialog.cd_combo.setCurrentIndex(dialog.cd_combo.findData("/dev/reconnected"))
    dialog.start_button.click()
    dialog.start_preparation()
    _wait(lambda: not dialog.is_busy)
    assert dialog.status.text() == dialog.text("complete"), dialog.details.toPlainText()
    assert len(scans) == 1
    assert calls[-3:] == [("toc", "/dev/reconnected"), ("rip", "/dev/reconnected"),
                          ("toc", "/dev/reconnected")]
    assert len(calls) == (4 if failure_stage == "toc" else 5)
    assert dialog.result_folder != failed_output
    assert json.loads((dialog.result_folder / "manifest.json").read_text())["status"] == "complete"
    assert dialog.audio_paths == expected_pairs
    assert dialog.progress.value() == 100
    assert dialog._completed_cd_device == "/dev/reconnected"
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


def test_failed_cd_attempt_waits_for_worker_before_enabling_retry(dialog, source, tmp_path, monkeypatch):
    _scan(dialog, source)
    release = threading.Event()
    calls = []
    worker_run = dialogs.SmartPianoSoftWorker.run

    def fail(*_args, **_kwargs):
        calls.append("prepare")
        raise AlbumBuildError("The CD drive disconnected", cd_read_error=True)

    def held_worker(worker):
        worker_run(worker)
        release.wait(3)

    monkeypatch.setattr(dialogs, "prepare_album", fail)
    monkeypatch.setattr(dialogs.SmartPianoSoftWorker, "run", held_worker)
    dialog.output_edit.setText(str(tmp_path))
    dialog.cd_combo.setEditText("/dev/disconnected")
    try:
        dialog.start_preparation()
        _wait(lambda: dialog.status.text() == dialog.text("cd_read_failed"))
        assert dialog.is_busy
        assert not dialog.options.isEnabled()
        assert not dialog.start_button.isEnabled()
        dialog.start_preparation()
        dialog.discover_cd()
        dialog._progress({"phase": "rip", "completed": 1, "total": 1, "message": "Late read progress"})
        assert calls == ["prepare"]
        assert dialog.status.text() == dialog.text("cd_read_failed")
        assert dialog.progress.value() == 0
    finally:
        release.set()
        _wait(lambda: not dialog.is_busy)
    assert dialog.options.isEnabled()
    assert dialog.start_button.isEnabled()


@pytest.mark.parametrize("cancelled", [False, True])
def test_output_failure_and_cancel_do_not_offer_cd_reconnect(dialog, source, tmp_path, monkeypatch, cancelled):
    _scan(dialog, source)

    def fail(*_args, cancel, **_kwargs):
        if cancelled:
            cancel.set()
        raise AlbumBuildError("Cancelled" if cancelled else "The output disk is full", cd_read_error=cancelled)

    monkeypatch.setattr(dialogs, "prepare_album", fail)
    dialog.output_edit.setText(str(tmp_path))
    dialog.cd_combo.setEditText("/dev/test-cd")
    dialog.start_preparation()
    _wait(lambda: not dialog.is_busy)
    assert dialog.status.text() == dialog.text("cancelled" if cancelled else "failed")
    assert dialog.text("cd_read_retry") not in dialog.details.toPlainText()
    assert dialog.album is not None
    assert dialog.start_button.isEnabled()


@pytest.mark.parametrize("close_window", [False, True])
def test_cancel_or_close_during_cd_read_waits_for_cleanup(dialog, source, tmp_path, monkeypatch, close_window):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(dialogs.QMessageBox, "exec", lambda _box: pytest.fail("No progress to save"))
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: (media.CDTrack(1, 0, 75),))

    def blocked_rip(_device, _track, _destination, *, cancel, progress):
        entered.set()
        if not cancel.wait(3):
            raise RuntimeError("Test cancellation was not received")
        release.wait(3)
        # A reader can report a device error while cancellation is in flight;
        # the user's cancellation still determines the UI and manifest status.
        raise media.CDReadError("CD reader stopped")

    monkeypatch.setattr(media, "rip_cd_track", blocked_rip)
    dialog.source_edit.setText(str(source))
    dialog.output_edit.setText(str(tmp_path))
    dialog.cd_combo.setEditText("/dev/test-cd")
    dialog.start_preparation()
    _wait(entered.is_set)
    workspace = Path(dialog._workspace.name)
    try:
        if close_window:
            dialog.close()
        else:
            dialog.cancel_or_close()
        assert dialog.is_busy
        assert dialog.isVisible()
        assert not dialog.options.isEnabled()
        assert workspace.exists()
        dialog._progress({"phase": "rip", "completed": 1, "total": 1, "message": "Late read progress"})
        assert dialog.status.text() == dialog.text("cancelling")
    finally:
        release.set()
        _wait(lambda: not dialog.is_busy)
    assert dialog.status.text() == dialog.text("cancelled")
    assert dialog.text("cd_read_retry") not in dialog.details.toPlainText()
    assert dialog.result_folder is None
    assert not list(tmp_path.glob("Smart PianoSoft*"))
    if close_window:
        assert not dialog.isVisible()
        assert not workspace.exists()
    else:
        assert dialog.album is not None
        assert workspace.exists()
        assert dialog.options.isEnabled()


@pytest.mark.parametrize("ending", ["failure", "cancel", "close"])
@pytest.mark.parametrize("save", [False, True])
def test_incomplete_audio_is_saved_or_discarded_after_worker_stops(
    dialog, source, tmp_path, monkeypatch, ending, save,
):
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    previous = tmp_path / "Smart PianoSoft - previous completed album"
    previous.mkdir()
    (previous / "song.wav").write_bytes(b"previous completed output")
    dialog.result_folder = previous
    entered, release = threading.Event(), threading.Event()
    prompts = []
    worker_run = dialogs.SmartPianoSoftWorker.run
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: (
        media.CDTrack(1, 0, 75), media.CDTrack(2, 75, 75),
    ))

    def rip(_device, track, destination, *, cancel, progress):
        if track.number == 1:
            _wav(destination)
            return destination
        entered.set()
        if ending != "failure":
            assert cancel.wait(3)
        raise media.CDReadError("CD reader stopped on track 2")

    def held_worker(worker):
        worker_run(worker)
        release.wait(5)

    def choose(box):
        assert dialog.is_busy and dialog.worker.isFinished()
        assert not dialog.start_button.isEnabled()
        assert box.defaultButton() == box.button(dialogs.QMessageBox.Save)
        assert box.escapeButton() == box.button(dialogs.QMessageBox.Save)
        assert box.button(dialogs.QMessageBox.Save).text() == dialog.text("save_incomplete")
        assert box.button(dialogs.QMessageBox.Discard).text() == dialog.text("discard_incomplete")
        folder = next(path for path in tmp_path.glob("Smart PianoSoft*") if path != previous)
        assert str(folder) in box.text()
        assert (folder / "WAV/CD01.wav").is_file()
        assert not (folder / "WAV/CD02.wav").exists()
        assert json.loads((folder / "manifest.json").read_text())["status"] == (
            "failed" if ending == "failure" else "cancelled"
        )
        prompts.append(folder)
        dialog.start_preparation()  # Modal reentrancy must not start a new run.
        return dialogs.QMessageBox.Save if save else dialogs.QMessageBox.Discard

    monkeypatch.setattr(media, "rip_cd_track", rip)
    monkeypatch.setattr(dialogs.SmartPianoSoftWorker, "run", held_worker)
    monkeypatch.setattr(dialogs.QMessageBox, "exec", choose)
    dialog.source_edit.setText(str(source))
    dialog.output_edit.setText(str(tmp_path))
    dialog.cd_combo.setEditText("/dev/test-cd")
    try:
        dialog.start_preparation()
        _wait(entered.is_set)
        if ending == "cancel":
            dialog.cancel_or_close()
        elif ending == "close":
            dialog.close()
        _wait(lambda: dialog._operation_done)
        assert dialog.is_busy and prompts == []
    finally:
        release.set()
        _wait(lambda: not dialog.is_busy)
    assert len(prompts) == 1
    folder = prompts[0]
    assert folder.exists() == save
    assert dialog.result_folder == (folder if save else previous)
    message = dialog.text("retained", folder=str(folder)) if save else dialog.text("discarded")
    assert message in dialog.details.toPlainText()
    assert (previous / "song.wav").read_bytes() == b"previous completed output"
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
    if ending == "close":
        assert not dialog.isVisible()
        assert dialog._workspace is None
    else:
        assert dialog.album.source_directory.exists()
        assert dialog.start_button.isEnabled()


@pytest.mark.parametrize("close_window", [False, True])
def test_discard_failure_keeps_output_accessible(dialog, source, tmp_path, monkeypatch, close_window):
    _scan(dialog, source)
    folder = tmp_path / "incomplete"
    folder.mkdir()
    (folder / "track.wav").write_bytes(b"completed audio")
    rmtree = dialogs.shutil.rmtree

    def fail(*_args, **_kwargs):
        raise AlbumBuildError("Read stopped", folder)

    def cannot_remove(path, *args, **kwargs):
        if Path(path) == folder:
            raise PermissionError("Output is in use")
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(dialogs, "prepare_album", fail)
    monkeypatch.setattr(dialogs.shutil, "rmtree", cannot_remove)
    monkeypatch.setattr(dialogs.QMessageBox, "exec", lambda _box: dialogs.QMessageBox.Discard)
    warnings = []
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog.output_edit.setText(str(tmp_path))
    dialog.start_preparation()
    if close_window:
        dialog.close()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result_folder == folder and folder.is_dir()
    assert dialog.open_button.isEnabled()
    assert "Output is in use" in dialog.details.toPlainText()
    assert len(warnings) == 1 and str(folder) in warnings[0]
    assert dialog.text("discarded") not in dialog.details.toPlainText()
    assert dialog.isVisible() != close_window


@pytest.mark.parametrize("discard", [False, True])
def test_real_incomplete_prompt_escape_saves_and_discard_removes(dialog, tmp_path, discard):
    folder = tmp_path / "incomplete"
    folder.mkdir()
    (folder / "track.wav").write_bytes(b"completed audio")
    dialog._incomplete_output = folder
    captions = []

    def answer():
        box = QApplication.activeModalWidget()
        captions.extend([box.button(dialogs.QMessageBox.Save).text(),
                         box.button(dialogs.QMessageBox.Discard).text()])
        if discard:
            QTest.mouseClick(box.button(dialogs.QMessageBox.Discard), Qt.LeftButton)
        else:
            QTest.keyClick(box, Qt.Key_Escape)

    QTimer.singleShot(0, dialog, answer)
    dialog._resolve_incomplete_output()
    assert captions == [dialog.text("save_incomplete"), dialog.text("discard_incomplete")]
    assert folder.exists() != discard
    assert dialog.result_folder == (None if discard else folder)


def test_cancel_after_output_committed_still_reports_success(dialog, source, tmp_path, monkeypatch):
    _scan(dialog, source)
    paired = tmp_path / "paired.wav"
    _wav(paired)
    dialog.audio_paths[1] = paired
    dialog.output_edit.setText(str(tmp_path))
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    progress = dialogs.SmartPianoSoftWorker._progress
    prompts = []

    def late_cancel(worker, detail):
        if detail["phase"] == "complete":
            worker.cancelled.set()
        progress(worker, detail)

    monkeypatch.setattr(dialogs.SmartPianoSoftWorker, "_progress", late_cancel)
    monkeypatch.setattr(dialogs.QMessageBox, "exec", lambda box: prompts.append(box))
    dialog.start_preparation()
    _wait(lambda: not dialog.is_busy)
    assert dialog.status.text() == dialog.text("complete"), dialog.details.toPlainText()
    assert prompts == []
    assert json.loads((dialog.result_folder / "manifest.json").read_text())["status"] == "complete"


@pytest.mark.parametrize("song_count", [1, 2])
def test_manual_pairing_runs_without_accessing_cd(dialog, source, tmp_path, monkeypatch, song_count):
    if song_count == 2:
        (source / "SECOND.MID").write_bytes((source / "song.mid").read_bytes())
        (source / "PSONG.MNG").write_bytes(_psong_bytes([
            ("SONG.MID", "First song"), ("SECOND.MID", "Second song"),
        ]))
    _scan(dialog, source)
    paired = tmp_path / "paired.wav"
    _wav(paired)
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: pytest.fail("Unexpected CD access"))
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    dialog.audio_paths[1] = paired
    if song_count == 2:
        dialog.audio_paths[2] = paired
    dialog.output_edit.setText(str(tmp_path))
    dialog.start_preparation()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result_folder is not None, dialog.details.toPlainText()
    assert (dialog.result_folder / "WAV/Paired01.wav").read_bytes() == paired.read_bytes()
    report = json.loads((dialog.result_folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    encoded = [track["encoded"] for track in report["tracks"]]
    assert len(set(encoded)) == song_count
    assert len(list((dialog.result_folder / "Disklavier").glob("*.wav"))) == song_count


@pytest.mark.parametrize("close_window", [False, True])
def test_cancel_or_close_keeps_worker_alive_until_it_stops(dialog, source, monkeypatch, close_window):
    entered = threading.Event()

    def blocked_copy(_source, _destination, *, cancel=None, progress=None):
        entered.set()
        assert isinstance(cancel, threading.Event)
        if not cancel.wait(3):
            raise RuntimeError("Test cancellation was not received")
        raise FloppyOperationCancelled("Cancelled by the user")

    monkeypatch.setattr(media, "read_floppy_source", blocked_copy)
    dialog.source_edit.setText(str(source))
    dialog.start_scan()
    _wait(entered.is_set)
    workspace = Path(dialog._workspace.name)
    if close_window:
        dialog.close()
        assert dialog.isVisible()
        assert workspace.exists()
    else:
        dialog.cancel_or_close()
    _wait(lambda: not dialog.is_busy)
    assert dialog.album is None
    if close_window:
        assert not dialog.isVisible()
        assert not workspace.exists()
    else:
        assert dialog.isVisible()
        assert dialog.status.text() == dialog.text("cancelled")
        assert dialog.options.isEnabled()


def test_generic_psong_catalog_is_rejected_by_ui_scan(dialog, source):
    ordinary = mido.MidiFile(type=0)
    ordinary.tracks.append(mido.MidiTrack([mido.Message("note_on", note=60, velocity=80)]))
    ordinary.save(source / "song.mid")
    dialog.source_edit.setText(str(source))
    dialog.start_scan()
    _wait(lambda: not dialog.is_busy)
    assert dialog.album is None
    assert dialog.table.rowCount() == 0
    assert "fingerprint" in dialog.details.toPlainText().lower()
    assert not dialog.pair_button.isEnabled()


def test_changing_source_invalidates_previous_pairings(dialog, source, tmp_path):
    _scan(dialog, source)
    dialog.audio_paths[1] = tmp_path / "old.wav"
    dialog.source_edit.setText(str(tmp_path / "different"))
    assert dialog.album is None
    assert dialog.table.rowCount() == 0
    assert not dialog.audio_paths
    assert not dialog.pair_button.isEnabled()


def test_failed_rescan_clears_previous_rows(dialog, source):
    _scan(dialog, source)
    (source / "PSONG.MNG").unlink()
    dialog.start_scan()
    _wait(lambda: not dialog.is_busy)
    assert dialog.album is None
    assert dialog.table.rowCount() == 0


def test_idle_escape_cleans_snapshot_workspace(dialog, source):
    _scan(dialog, source)
    workspace = Path(dialog._workspace.name)
    dialog.reject()
    assert not dialog.isVisible()
    assert not workspace.exists()


def test_immediate_close_prevents_deferred_discovery(tmp_path, qt_application, monkeypatch):
    discoveries = []
    monkeypatch.setattr(media, "discover_cd_drives", lambda: discoveries.append(True) or [])
    settings = QSettings(str(tmp_path / "immediate-close.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings)
    instance.close()
    qt_application.processEvents()
    QTest.qWait(20)
    assert not discoveries
    assert not instance.is_busy


def test_utilities_menu_opens_one_modal_dialog_and_respects_busy_guard(monkeypatch, request):
    # Exercise the utility integration without changing the release default.
    # Enable it before constructing the window, when its menu is assembled.
    monkeypatch.setattr(main_window.MidiTitleWindow, "ENABLE_SMART_PIANOSOFT_UTILITY", True)
    window = request.getfixturevalue("window")
    opened = []
    monkeypatch.setattr(dialogs.SmartPianoSoftDialog, "discover_cd", lambda _self: None)

    def execute(dialog, **options):
        assert options == {"resize_to_contents": False}
        assert dialog.parentWidget() is window
        opened.append(dialog)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    action = window.utilitiesSmartPianoSoftAction
    assert action in window.utilitiesMenu.actions()
    assert action.text().replace("&", "") == window._t("sps.action").replace("&", "")
    action.trigger()
    assert len(opened) == 1
    assert window.smartPianoSoftDialog is None
    with monkeypatch.context() as busy:
        busy.setattr(window, "_disk_worker_busy", lambda: True)
        action.trigger()
    assert len(opened) == 1


@pytest.fixture
def large_dialog_font(qt_application):
    original = QFont(qt_application.font())
    enlarged = QFont(original)
    enlarged.setPointSize(18)
    qt_application.setFont(enlarged)
    yield
    qt_application.setFont(original)
    qt_application.processEvents()


def _bounds_in(parent, widget):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


def _assert_large_font_layout_fits(instance):
    # A full caption needs the native button's text, margins and menu indicator.
    for button in instance.findChildren(QPushButton):
        if not button.isVisible():
            continue
        assert button.width() >= button.sizeHint().width(), button.text()
        assert button.height() >= button.sizeHint().height(), button.text()
        assert button.parentWidget().rect().contains(button.geometry()), button.text()
        if instance.options.isAncestorOf(button):
            assert instance.options.rect().contains(_bounds_in(instance.options, button)), button.text()
    for box in (instance.pairing_buttons, instance.buttons):
        for first, second in combinations([b for b in box.buttons() if b.isVisible()], 2):
            assert not first.geometry().intersects(second.geometry())
    for label in instance.findChildren(QLabel):
        if label.wordWrap() and label.isVisible():
            assert label.height() >= label.heightForWidth(label.width()), label.text()
    assert instance.rect().contains(instance.buttons.geometry())
    assert instance.scroll_area.geometry().bottom() < instance.buttons.geometry().top()
    assert instance.scroll_area.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("point_size,width,height", [(12, 940, 700), (18, 1180, 874), (18, 1000, 800)])
@pytest.mark.parametrize("output_format", ["encoded_wav", "midi_mp3"])
def test_album_layout_uses_table_space_without_scrolling_the_dialog(
    tmp_path, qt_application, source, point_size, width, height, output_format,
):
    original_font = QFont(qt_application.font())
    font = QFont(original_font)
    font.setPointSize(point_size)
    qt_application.setFont(font)
    settings = QSettings(str(tmp_path / "compact-layout.ini"), QSettings.IniFormat)
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False,
                                          loaded_source=_loaded_source(source))
    instance.format_combo.setCurrentIndex(instance.format_combo.findData(output_format))
    instance.resize(width, height)
    instance.show()
    try:
        _wait(lambda: instance.album is not None and not instance.is_busy)
        QTest.qWait(30)
        assert instance.size().toTuple() == (width, height)
        assert instance.scroll_area.verticalScrollBar().maximum() == 0
        _assert_large_font_layout_fits(instance)
        description = instance.scroll_area.widget().layout().itemAt(0).widget()
        assert description.height() == description.heightForWidth(description.width())
        assert _bounds_in(instance, description).top() <= 16
        table_bounds = _bounds_in(instance, instance.table)
        assert table_bounds.left() >= 16
        assert width - table_bounds.right() - 1 == table_bounds.left()
        assert not instance.progress.isVisible()
        assert not instance.details.isVisible()
        # Active work can reveal its status and progress without displacing
        # the always-accessible start/cancel area or forcing an outer scroll.
        instance._progress({"phase": "rip", "completed": 1, "total": 10,
                            "cd_track": 3, "track_index": 2, "track_count": 12,
                            "message": "Reading with secure error correction…"})
        QTest.qWait(30)
        assert instance.progress.isVisible()
        assert instance.scroll_area.verticalScrollBar().maximum() == 0
        assert instance.rect().contains(instance.buttons.geometry())
    finally:
        instance.close()
        qt_application.setFont(original_font)
        qt_application.processEvents()


@pytest.mark.parametrize("style", ["Fusion", "Windows"])
@pytest.mark.parametrize("point_size", [12, 18])
@pytest.mark.parametrize("width", [760, 1400])
@pytest.mark.parametrize("language", ["en", "es"])
def test_footer_layout_stays_stable_through_scan_rip_and_completion(
    tmp_path, qt_application, source, monkeypatch, style, point_size, width, language,
):
    original_style = qt_application.style().objectName()
    original_font = QFont(qt_application.font())
    qt_application.setStyle(style)
    qt_application.setFont(QFont(original_font.family(), point_size))
    settings = QSettings(str(tmp_path / "stable-footer.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    settings.setValue("preparation_profile", "enspire")
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False)
    instance.cd_combo.setEditText("/dev/test-cd")
    instance.resize(width, 700)
    instance.show()
    entered, release = threading.Event(), threading.Event()

    def prepare(_album, _destination, *, progress, **_kwargs):
        progress({"phase": "rip", "completed": 1, "total": 2, "message": "Reading audio"})
        entered.set()
        assert release.wait(5)
        output = tmp_path / "completed"
        output.mkdir()
        return output

    monkeypatch.setattr(dialogs, "prepare_album", prepare)
    try:
        QTest.qWait(30)
        initial_size = instance.size()
        initial_footer = instance.buttons.geometry()
        initial_orientation = instance.buttons.orientation()
        initial_pairing_orientation = instance.pairing_buttons.orientation()

        def wrapped_rows():
            form = instance.options.layout()
            return [form.itemAt(row, QFormLayout.FieldRole).geometry().top()
                    > form.itemAt(row, QFormLayout.LabelRole).geometry().bottom()
                    for row in range(form.rowCount())]

        initial_rows = wrapped_rows()
        assert instance.open_button.isVisible() and not instance.open_button.isEnabled()

        def check_footer():
            QTest.qWait(30)
            assert instance.size() == initial_size
            assert instance.buttons.orientation() == initial_orientation
            assert instance.buttons.geometry() == initial_footer
            assert instance.pairing_buttons.orientation() == initial_pairing_orientation
            assert wrapped_rows() == initial_rows
            _assert_large_font_layout_fits(instance)

        _scan(instance, source)
        check_footer()
        instance.output_edit.setText(str(tmp_path))
        instance.start_preparation()
        _wait(entered.is_set)
        check_footer()
        release.set()
        _wait(lambda: not instance.is_busy)
        assert instance.result_folder == tmp_path / "completed", instance.details.toPlainText()
        assert instance.open_button.isEnabled()
        assert instance.start_button.text() == instance.text("restart", format="MIDI/MP3")
        check_footer()
    finally:
        release.set()
        if instance.is_busy:
            instance.cancel_or_close()
            _wait(lambda: not instance.is_busy)
        instance.close()
        qt_application.setFont(original_font)
        qt_application.setStyle(original_style)
        qt_application.processEvents()


@pytest.mark.parametrize("style", ["Fusion", "Windows"])
def test_pairing_row_reserves_scrollbar_width_before_content_grows(tmp_path, qt_application, style):
    original_style = qt_application.style().objectName()
    qt_application.setStyle(style)
    settings = QSettings(str(tmp_path / "pairing-scrollbar.ini"), QSettings.IniFormat)
    settings.setValue("language", "es")
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False)
    scrollbar = instance.scroll_area.verticalScrollBar()
    scrollbar.setStyleSheet("QScrollBar:vertical { width: 40px; }")
    instance.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    instance.ensurePolished()
    # Choose a width where the row fits only without a scrollbar. Deriving it
    # from the native buttons reproduces CI's font-dependent boundary on every
    # platform, without requiring its exact font family or display DPI.
    row_width = ResponsiveDialogButtonBox.sizeHint(instance.pairing_buttons).width()
    margins = instance.layout().contentsMargins() + instance.scroll_area.widget().layout().contentsMargins()
    gutter = scrollbar.sizeHint().width()
    narrow_width = row_width + margins.left() + margins.right() + gutter // 2
    instance.resize(narrow_width, 700)
    instance.show()
    try:
        QTest.qWait(30)
        assert instance.pairing_buttons.width() >= row_width
        assert instance.pairing_buttons.width() - gutter < row_width
        for width, orientation in ((narrow_width, Qt.Vertical), (narrow_width + gutter, Qt.Horizontal)):
            instance.resize(width, 700)
            QTest.qWait(30)
            footer = instance.buttons.geometry()
            for policy in (Qt.ScrollBarAlwaysOn, Qt.ScrollBarAlwaysOff, Qt.ScrollBarAlwaysOn):
                instance.scroll_area.setVerticalScrollBarPolicy(policy)
                QTest.qWait(30)
                assert instance.size().toTuple() == (width, 700)
                assert instance.pairing_buttons.orientation() == orientation
                assert instance.buttons.geometry() == footer
                _assert_large_font_layout_fits(instance)
    finally:
        instance.close()
        qt_application.setStyle(original_style)
        qt_application.processEvents()


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
@pytest.mark.parametrize("output_format,label", [("encoded_wav", "WAV"), ("midi_mp3", "MIDI/MP3")])
def test_start_action_identifies_cd_ripping_and_paired_file_export(dialog, language, output_format, label):
    dialog.language = language
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(output_format))
    dialog._update_controls()
    assert dialog.start_button.text() == dialog.text("start_files", format=label)
    assert dialog.start_button.toolTip() == dialog.text("start_files_hint", format=label)
    assert label in dialog.start_button.text()
    dialog.cd_combo.addItem("Audio CD", "/dev/sr0")
    dialog.cd_combo.setCurrentIndex(1)
    assert dialog.start_button.text() == dialog.text("start", format=label)
    assert dialog.start_button.toolTip() == dialog.text("start_hint", format=label)
    dialog._completed_cd_device = "/dev/sr0"
    dialog._update_controls()
    assert dialog.start_button.text() == dialog.text("restart", format=label)
    dialog.cd_combo.setCurrentIndex(0)
    assert dialog.start_button.text() == dialog.text("start_files", format=label)
    opposite = "midi_mp3" if output_format == "encoded_wav" else "encoded_wav"
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(opposite))
    assert dialog.start_button.text() == dialog.text(
        "start_files", format="MIDI/MP3" if opposite == "midi_mp3" else "WAV",
    )
    assert dialog.start_button.isDefault()


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_cd_progress_keeps_disc_track_and_audio_track_count_visible(dialog, language):
    dialog.language = language
    for stage, message in (
        ("check_disc", "Checking CD track list…"),
        ("read", "Reading with secure error correction…"),
        ("check_wav", "Checking WAV format and length…"),
        ("save", "Saving audio track…"),
        ("saved", "Audio track saved."),
        ("checksum", "Calculating audio checksum…"),
    ):
        dialog._progress({"phase": "rip", "completed": 125, "total": 200,
                          "cd_track": 3, "track_index": 2, "track_count": 8, "message": message})
        assert dialog.progress.value() == 62
        assert dialog.status.text() == dialog.text(
            "cd_track_progress", number=3, index=2, count=8, message=dialog.text("cd_" + stage),
        )
        if language == "en":
            assert dialog.status.text() == f"CD track 3 (2 of 8): {message}"


@pytest.mark.parametrize("phase", ["scan", "sync", "encode", "export_pairs"])
def test_progress_preserves_song_titles_when_translating_phase_labels(dialog, phase):
    dialog.language = "es"
    dialog._progress({"phase": phase, "completed": 1, "total": 2, "message": "Help"})
    assert dialog.status.text() == dialog.text("phase_" + phase) + ": Help"


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_large_font_translations_reflow_and_scroll_after_repeated_resizes(
    tmp_path, qt_application, large_dialog_font, language, source,
):
    settings = QSettings(str(tmp_path / "large-font.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False,
                                          loaded_source=_loaded_source(source))
    instance.show()
    try:
        _wait(lambda: instance.album is not None and not instance.is_busy)
        assert instance.font().pointSize() == 18
        # Ensure the growing case can accommodate the full translated rows,
        # including platforms whose native font metrics produce wider buttons.
        wide = max(1400, instance.pairing_buttons.sizeHint().width() + 60,
                   instance.buttons.sizeHint().width() + 60)
        for width in (760, wide, 760, wide, 760):
            instance.resize(width, 700)
            QTest.qWait(30)
            assert instance.size().toTuple() == (width, 700)
            _assert_large_font_layout_fits(instance)
            if width == wide:
                assert instance.pairing_buttons.orientation() == Qt.Horizontal
                assert instance.buttons.orientation() == Qt.Horizontal
            geometry = instance.geometry()
            footer = instance.buttons.geometry()
            viewport = instance.scroll_area.viewport()
            # Pairing controls and status remain reachable if a translated
            # large-font body needs the narrow window's scrolling fallback.
            for target in (instance.scan_button, instance.pair_button,
                           instance.automatic_button, instance.status):
                instance.scroll_area.ensureWidgetVisible(target, 0, 0)
                QTest.qWait(10)
                assert viewport.rect().contains(_bounds_in(viewport, target))
                assert not target.visibleRegion().isEmpty()
                assert instance.geometry() == geometry
                assert instance.buttons.geometry() == footer
        # A status update can add wrapped lines after the first layout. It
        # must remain readable without resizing or moving the user's window.
        instance._progress({"phase": "scan", "message": instance.text("description"),
                            "completed": 1, "total": 2})
        QTest.qWait(30)
        _assert_large_font_layout_fits(instance)
        instance.scroll_area.ensureWidgetVisible(instance.status, 0, 0)
        QTest.qWait(10)
        assert instance.scroll_area.viewport().rect().contains(
            _bounds_in(instance.scroll_area.viewport(), instance.status)
        )
        assert instance.geometry() == geometry
    finally:
        instance.close()
        qt_application.processEvents()


def test_windows_style_form_buttons_fit_without_taking_the_primary_default(
    tmp_path, qt_application, large_dialog_font,
):
    original_style = qt_application.style().objectName()
    qt_application.setStyle("Windows")
    settings = QSettings(str(tmp_path / "windows-style.ini"), QSettings.IniFormat)
    settings.setValue("language", "es")
    instance = dialogs.SmartPianoSoftDialog(settings, discover_on_open=False)
    instance.show()
    try:
        form_buttons = instance.options.findChildren(QPushButton)
        assert len(form_buttons) == 3
        for width in (760, 1400, 760):
            instance.resize(width, 700)
            for control in (instance.source_edit, *form_buttons, instance.output_edit):
                control.setFocus()
                QTest.qWait(20)
                # Windows styles reserve extra margins for auto-default
                # buttons. Focusing these secondary actions must not change
                # the primary action or leave stale, narrower row geometry.
                assert instance.start_button.isDefault()
                assert not any(button.isDefault() for button in form_buttons)
                _assert_large_font_layout_fits(instance)
    finally:
        instance.close()
        qt_application.setStyle(original_style)
        qt_application.processEvents()
