"""Real read workers keep the UI busy only until cancellation cleanup ends."""

import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from aps_midi_prep_tool_app import disk_session_worker, main_window
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyDriveInfo, FloppyImageSession, FloppyOperationCancelled,
)
from test_mute_workflows import window
from test_smart_pianosoft_loaded_list import _midi_bytes


SOURCE_CONTROLS = (
    "choose_button", "open_image_button", "read_floppy_button",
    "fileOpenFolderAction", "fileOpenImageAction", "fileReadFloppyAction",
)


def _wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    QApplication.processEvents()
    assert predicate(), "The disk read did not reach the expected UI state"


class _DelayedRead:
    def __init__(self):
        self.entered = threading.Event()
        self.cleaning = threading.Event()
        self.release_cleanup = threading.Event()

    def __call__(self, _source, *, progress_callback, cancel_callback):
        self.entered.set()
        while not cancel_callback():
            progress_callback(14, 80, "Reading cylinder 14 of 80")
            if self.release_cleanup.wait(.02):
                break
        self.cleaning.set()
        self.release_cleanup.wait(3)
        raise FloppyOperationCancelled("Read cancelled after closing the floppy connection.")


def _start(window):
    return window._start_disk_load_worker(
        load_kind="floppy_usb", source=FloppyDriveInfo("A:", 737280, transport="usb"),
        progress_title="Reading Floppy", progress_total=80,
        initial_message="Opening floppy connection", final_message="Opening floppy contents",
        failure_title="Floppy Load Failed",
    )


def _stop(window, *readers):
    for reader in readers:
        reader.release_cleanup.set()
    worker = window.diskLoadWorker
    if worker is not None:
        worker.cancel()
        assert worker.wait(3000), "Test reader did not stop"
    QApplication.processEvents()


def test_cancel_keeps_progress_visible_until_cleanup_and_then_allows_another_read(window, monkeypatch):
    read, retry = _DelayedRead(), _DelayedRead()
    monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", read)
    try:
        assert _start(window)
        _wait(read.entered.is_set)
        progress = window.diskLoadProgressDialog
        worker = window.diskLoadWorker
        _wait(lambda: "cylinder 14" in progress.labelText())
        for name in SOURCE_CONTROLS:
            assert not getattr(window, name).isEnabled(), name

        cancel_button, = progress.findChildren(QPushButton)
        cancel_button.click()
        _wait(read.cleaning.is_set)
        _wait(progress.isVisible)
        assert window.diskLoadWorker is worker and worker.isRunning()
        assert "Stopping Reading Floppy" in progress.labelText()
        assert "stop safely" in progress.labelText()
        assert "cylinder 14" in progress.labelText()
        assert "Stopping Reading Floppy" in window.status_label.text()
        assert "MIDI processing" not in window.status_label.text()
        assert not cancel_button.isEnabled()
        for name in SOURCE_CONTROLS:
            assert not getattr(window, name).isEnabled(), name

        read.release_cleanup.set()
        _wait(lambda: window.diskLoadWorker is None)
        assert window.diskLoadProgressDialog is None
        assert not progress.isVisible()
        assert window.status_label.text() == "Disk operation cancelled."
        for name in SOURCE_CONTROLS:
            assert getattr(window, name).isEnabled(), name
        assert not window.fileSaveAction.isEnabled()
        assert not window.fileSaveAsAction.isEnabled()
        assert window._prepare_for_disk_load("another floppy")

        monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", retry)
        assert _start(window)
        _wait(retry.entered.is_set)
        assert window.diskLoadWorker is not None
        assert not window.read_floppy_button.isEnabled()
    finally:
        _stop(window, read, retry)


def test_cancel_from_initial_progress_event_is_connected_before_reader_starts(window, monkeypatch):
    read = _DelayedRead()
    monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", read)
    initial_clicks = []

    def click_initial_cancel():
        worker = window.diskLoadWorker
        progress = window.diskLoadProgressDialog
        initial_clicks.append((worker, progress))
        assert worker is not None and progress is not None
        assert not worker.isRunning()
        button, = progress.findChildren(QPushButton)
        button.click()
        assert worker._cancel_requested()

    QTimer.singleShot(0, click_initial_cancel)
    try:
        assert _start(window)
        assert len(initial_clicks) == 1
        _wait(read.cleaning.is_set)
        assert window.diskLoadWorker is not None
        assert not window.choose_button.isEnabled()
        assert "Stopping Reading Floppy" in window.diskLoadProgressDialog.labelText()
        read.release_cleanup.set()
        _wait(lambda: window.diskLoadWorker is None)
        assert all(getattr(window, name).isEnabled() for name in SOURCE_CONTROLS)
    finally:
        _stop(window, read)


def test_stale_disabled_controls_recover_without_fictitious_midi_busy_message(window, monkeypatch):
    messages = []
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: messages.append(_a))
    window._set_disk_load_busy(True)
    assert not window.choose_button.isEnabled()
    assert not window._disk_worker_busy()
    assert window._prepare_for_disk_load("a floppy disk")
    assert not messages
    assert all(getattr(window, name).isEnabled() for name in SOURCE_CONTROLS)
    assert window.status_label.text() == window._t("activity.idle")
    assert not window.fileSaveAction.isEnabled()


def test_normal_read_closes_progress_without_cancelling_the_completed_operation(window, tmp_path, monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    cancel_calls = []
    original_cancel = disk_session_worker.DiskSessionLoadWorker.cancel
    session = FloppyImageSession(
        source_path=str(tmp_path / "original.img"), source_ext=".img",
        temp_dir=str(tmp_path / "session"), working_img_path=str(tmp_path / "working.img"),
        disk_format=DISK_FORMAT_BY_KEY["ibm.720"],
        repair_result=SimpleNamespace(note="", changed=False, boot_sector_repaired=False),
        source_kind="floppy_usb", source_name="Synthetic floppy",
        virtual_files={"SONG.MID": _midi_bytes(fingerprints=False)},
    )
    def read(_source, *, progress_callback, cancel_callback):
        entered.set()
        while not release.wait(.01):
            if cancel_callback():
                session.cleanup()
                raise FloppyOperationCancelled("Cancelled")
        return session

    def cancel(worker):
        cancel_calls.append(True)
        return original_cancel(worker)

    monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", read)
    monkeypatch.setattr(disk_session_worker.DiskSessionLoadWorker, "cancel", cancel)
    monkeypatch.setattr(window, "_schedule_destination_preparation", lambda: None)
    try:
        assert _start(window)
        _wait(entered.is_set)
        progress = window.diskLoadProgressDialog
        assert progress.isVisible()
        release.set()
        _wait(lambda: window.diskLoadWorker is None)
        assert window.image_session is session
        assert not progress.isVisible()
        assert window.diskLoadProgressDialog is None
        assert not cancel_calls
        assert all(getattr(window, name).isEnabled() for name in SOURCE_CONTROLS)
        assert window.fileSaveAsAction.isEnabled()
        assert window.fileSaveAsImageAction.isEnabled()
        assert not window.mute_workflow_errors
    finally:
        release.set()
        _stop(window)
