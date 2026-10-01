"""The beta utility owns its workers and preserves source media through the UI."""

import json
from itertools import combinations
from pathlib import Path
import threading
import time
import wave

import mido
import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from aps_midi_prep_tool_app import smart_pianosoft_dialog as dialogs
from aps_midi_prep_tool_app import smart_pianosoft_media as media
from aps_midi_prep_tool_app import smart_pianosoft_sync as sync
from aps_midi_prep_tool_app.floppy_image import FloppyOperationCancelled
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.smart_pianosoft_workflow import LoadedAlbumSource, ListedAlbumTrack
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes
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


def test_scan_pair_and_clear_leave_sources_untouched(dialog, source, tmp_path, monkeypatch):
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    _scan(dialog, source)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "Synthetic song"
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
    assert dialog.table.item(0, 3).text() == dialog.text("cd_hint", number=1)
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


def test_start_scans_and_runs_cd_pipeline_without_blocking_ui(dialog, source, tmp_path, monkeypatch):
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
    dialog.cd_combo.setEditText("/dev/test-cd")
    dialog.start_preparation()
    assert dialog.is_busy
    assert not dialog.options.isEnabled()
    assert not dialog.start_button.isEnabled()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result_folder is not None, dialog.details.toPlainText()
    report = json.loads((dialog.result_folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    assert report["hardware_verified"] is False
    assert len(list((dialog.result_folder / "Disklavier").glob("*.wav"))) == 1
    assert (dialog.result_folder / "MIDI/SONG.MID").read_bytes() == originals["song.mid"]
    assert calls == ["toc", "rip", "toc"]
    assert dialog.open_button.isEnabled()
    assert dialog.options.isEnabled()
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


def test_manual_pairing_runs_without_accessing_cd(dialog, source, tmp_path, monkeypatch):
    _scan(dialog, source)
    paired = tmp_path / "paired.wav"
    _wav(paired)
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: pytest.fail("Unexpected CD access"))
    monkeypatch.setattr(sync, "synchronize", lambda *_a, **_k: sync.Alignment(0, 1, .99, .99, .99))
    dialog.audio_paths[1] = paired
    dialog.output_edit.setText(str(tmp_path))
    dialog.start_preparation()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result_folder is not None, dialog.details.toPlainText()
    assert (dialog.result_folder / "WAV/Paired01.wav").read_bytes() == paired.read_bytes()


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


def test_utilities_menu_opens_one_modal_beta_dialog_and_respects_busy_guard(window, monkeypatch):
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
        assert button.width() >= button.sizeHint().width(), button.text()
        assert button.height() >= button.sizeHint().height(), button.text()
        assert button.parentWidget().rect().contains(button.geometry()), button.text()
    for box in (instance.pairing_buttons, instance.buttons):
        for first, second in combinations(box.buttons(), 2):
            assert not first.geometry().intersects(second.geometry())
    for label in instance.findChildren(QLabel):
        if label.wordWrap() and label.isVisible():
            assert label.height() >= label.heightForWidth(label.width()), label.text()
    assert instance.rect().contains(instance.buttons.geometry())
    assert instance.scroll_area.geometry().bottom() < instance.buttons.geometry().top()
    assert instance.scroll_area.horizontalScrollBar().maximum() == 0


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
            else:
                assert instance.scroll_area.verticalScrollBar().maximum() > 0
            geometry = instance.geometry()
            footer = instance.buttons.geometry()
            viewport = instance.scroll_area.viewport()
            # Pairing controls and progress remain reachable even though the
            # large-font body is taller than the narrow window's viewport.
            for target in (instance.scan_button, instance.pair_button,
                           instance.automatic_button, instance.status, instance.progress):
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
