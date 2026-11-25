"""Disk cancellation stays visible until the worker has actually stopped."""

import threading
import time
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QLabel, QProgressDialog, QPushButton, QWidget

from aps_midi_prep_tool_app import disk_activity
from aps_midi_prep_tool_app.activity_translations import ACTIVITY_MESSAGES


class FakeWorker:
    def __init__(self):
        self.finished = False
        self.cancel_calls = 0

    def isFinished(self):
        return self.finished

    def cancel(self):
        self.cancel_calls += 1


@pytest.fixture
def activity_clock(monkeypatch):
    # Start before attaching the worker, with exactly representable timestamps.
    # Adding seconds to an arbitrary real clock can round below the boundary.
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(disk_activity, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


class ActivityWindow(QWidget):
    def __init__(self):
        super().__init__()
        for worker_name, dialog_name, _handler, _title in disk_activity._OPERATIONS:
            setattr(self, worker_name, None)
            setattr(self, dialog_name, None)
        self.choose_button = QPushButton(self)
        self.status_label = QLabel(self)
        self.unlocks = 0

    def _t(self, key, **values):
        return ACTIVITY_MESSAGES[key]["en"].format(**values)

    def _lt(self, value):
        return value

    def _set_disk_write_busy(self, busy):
        assert not disk_activity._workers(self)
        self.unlocks += 1
        self.choose_button.setEnabled(not busy)
        disk_activity.stop_disk_activity(self)


def attach(window, worker, *, operation=0):
    worker_name, dialog_name, _handler, _title = disk_activity._OPERATIONS[operation]
    dialog = QProgressDialog("Reading track 14 of 160", "Cancel", 0, 160, window)
    dialog.setWindowTitle("Reading Greaseweazle floppy")
    dialog.setAutoClose(False)
    dialog.setMinimumDuration(0)
    setattr(window, worker_name, worker)
    setattr(window, dialog_name, dialog)
    window.choose_button.setEnabled(False)
    disk_activity.start_disk_activity(window)
    return dialog


@pytest.mark.parametrize("operation", range(len(disk_activity._OPERATIONS)))
def test_running_and_unstarted_workers_keep_controls_locked(qt_application, operation):
    window = ActivityWindow()
    worker = FakeWorker()
    dialog = attach(window, worker, operation=operation)

    assert disk_activity.show_busy_operation(window)
    assert dialog.isVisible()
    assert not window.choose_button.isEnabled()
    assert window.unlocks == 0
    assert "Greaseweazle" in window.status_label.text()
    assert "MIDI processing" not in window.status_label.text()


def test_cancel_reopens_progress_disables_cancel_and_preserves_last_stage(qt_application, activity_clock):
    window = ActivityWindow()
    worker = FakeWorker()
    dialog = attach(window, worker)
    dialog.canceled.connect(lambda: disk_activity.cancel_disk_activity(window, worker, dialog))
    dialog.show()
    cancel_button, = dialog.findChildren(QPushButton)

    cancel_button.click()
    qt_application.processEvents()

    assert worker.cancel_calls == 1
    assert dialog.isVisible()
    assert not cancel_button.isEnabled()
    assert "Stopping Reading Greaseweazle floppy" in dialog.labelText()
    assert "Reading track 14 of 160" in dialog.labelText()
    assert "stop safely" in dialog.labelText()
    assert not window.choose_button.isEnabled()

    monitor = window._disk_activity_monitor
    activity_clock.now += 12
    disk_activity.update_disk_activity(window, dialog, "Closing the floppy connection")
    monitor.refresh()
    assert "(12s)" in dialog.labelText()
    assert "Closing the floppy connection" in dialog.labelText()
    assert "Stopping" in dialog.labelText()
    assert window.unlocks == 0


def test_long_silent_operation_explains_holdup(qt_application, activity_clock):
    window = ActivityWindow()
    dialog = attach(window, FakeWorker())
    activity_clock.now += 25

    assert disk_activity.show_busy_operation(window)

    assert "25s elapsed" in window.status_label.text()
    assert "No new progress for 25s" in window.status_label.text()
    assert "No new progress for 25s" in dialog.labelText()
    assert "Reading track 14 of 160" in window.status_label.text()
    assert dialog.isVisible()
    assert window.unlocks == 0


def test_stale_disabled_controls_recover_without_busy_warning(qt_application, monkeypatch):
    window = ActivityWindow()
    window.choose_button.setEnabled(False)
    monkeypatch.setattr(disk_activity.QMessageBox, "information", lambda *_: pytest.fail("No work is running"))

    assert disk_activity.show_busy_operation(window) is False

    assert window.choose_button.isEnabled()
    assert window.unlocks == 1
    assert window.status_label.text() == "Ready. No disk operation is running."
    assert not window._disk_activity_monitor.timer.isActive()


def test_finished_worker_reconciles_once_through_its_handler(qt_application, activity_clock):
    window = ActivityWindow()
    worker = FakeWorker()
    dialog = attach(window, worker)
    finished_calls = []

    def finish():
        finished_calls.append(worker)
        window.diskLoadWorker = None
        window.diskLoadProgressDialog = None
        dialog.hide()
        window._set_disk_write_busy(False)

    window._on_disk_load_finished = finish
    worker.finished = True
    monitor = window._disk_activity_monitor
    monitor.refresh()
    assert not finished_calls
    assert not window.choose_button.isEnabled()
    activity_clock.now += 1
    monitor.refresh()
    monitor.refresh()

    assert finished_calls == [worker]
    assert window.choose_button.isEnabled()
    assert not monitor.timer.isActive()


def test_stopping_monitor_does_not_unlock_other_worker(qt_application):
    window = ActivityWindow()
    attach(window, FakeWorker())
    second = FakeWorker()
    window.diskImageCaptureWorker = second
    window.diskLoadWorker = None
    window.diskLoadProgressDialog = None

    disk_activity.stop_disk_activity(window)
    window._disk_activity_monitor.refresh()

    assert window._disk_activity_monitor.timer.isActive()
    assert not window.choose_button.isEnabled()
    assert window.unlocks == 0


@pytest.mark.parametrize("finish_path", ["stop", "idle_recovery"])
@pytest.mark.parametrize("result_status", [None, "Image saved.", "Image export failed.", "Disk operation cancelled."])
def test_finished_activity_clears_only_its_own_status(qt_application, finish_path, result_status):
    window = ActivityWindow()
    dialog = attach(window, FakeWorker())
    assert "elapsed" in window.status_label.text()
    if result_status is not None:
        window.status_label.setText(result_status)
    window.diskLoadWorker = None
    window.diskLoadProgressDialog = None
    dialog.hide()

    if finish_path == "stop":
        window.choose_button.setEnabled(True)
        disk_activity.stop_disk_activity(window)
    else:
        window._disk_activity_monitor.refresh()

    assert window.choose_button.isEnabled()
    assert window.status_label.text() == (result_status or "Ready. No disk operation is running.")
    assert not window._disk_activity_monitor.timer.isActive()
    window.status_label.setText("Ready for another disk.")
    disk_activity.stop_disk_activity(window)
    assert window.status_label.text() == "Ready for another disk."


def test_busy_without_progress_dialog_names_current_operation(qt_application, monkeypatch):
    window = ActivityWindow()
    window.diskRecoveryWorker = FakeWorker()
    window.choose_button.setEnabled(False)
    messages = []
    monkeypatch.setattr(disk_activity.QMessageBox, "information", lambda *_args: messages.append(_args[2]))

    assert disk_activity.show_busy_operation(window)

    assert messages == ["Disk recovery — 0s elapsed"]
    assert window.unlocks == 0


class SlowCancellation(QThread):
    def __init__(self, parent):
        super().__init__(parent)
        self.cancel_requested = threading.Event()
        self.release = threading.Event()
        self.entered = threading.Event()

    def run(self):
        self.entered.set()
        self.release.wait(5)

    def cancel(self):
        self.cancel_requested.set()


def test_real_thread_keeps_ui_locked_until_cancellation_cleanup_finishes(qt_application):
    window = ActivityWindow()
    worker = SlowCancellation(window)
    dialog = attach(window, worker)
    monitor = window._disk_activity_monitor

    def finish():
        if window.diskLoadWorker is not worker:
            return
        window.diskLoadWorker = None
        window.diskLoadProgressDialog = None
        dialog.hide()
        window._set_disk_write_busy(False)

    window._on_disk_load_finished = finish
    worker.finished.connect(finish)
    worker.start()
    try:
        assert worker.entered.wait(1)
        disk_activity.cancel_disk_activity(window, worker, dialog)
        qt_application.processEvents()
        assert worker.cancel_requested.is_set()
        assert worker.isRunning()
        assert dialog.isVisible()
        assert not window.choose_button.isEnabled()
        assert window.unlocks == 0

        worker.release.set()
        deadline = time.monotonic() + 2
        while not window.choose_button.isEnabled() and time.monotonic() < deadline:
            qt_application.processEvents()
            threading.Event().wait(0.005)
        assert window.choose_button.isEnabled()
        assert not monitor.timer.isActive()
        assert not dialog.isVisible()
        assert window.unlocks == 1
    finally:
        worker.release.set()
        worker.wait(2000)
