"""Updates must not interrupt writes or discard unapproved pending work."""

import os
import time
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QProcess, QSettings, QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog

from aps_midi_prep_tool_app import main_window, self_update_ui as ui


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "update.ini"), QSettings.IniFormat)
    settings.setValue("preparation_profile", "custom")
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow(settings=settings)
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    instance.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("worker_name", [
    "diskLoadWorker", "diskRecoveryWorker", "diskFormatWorker", "diskCommitWorker",
    "diskWriteTargetWorker", "diskImageCaptureWorker", "bulkExtractionWorker", "emulatorImageWorker",
    "bugReportWorker", "feedbackWorker", "updateCheckWorker",
])
def test_busy_workers_defer_update_without_cancelling_work(window, monkeypatch, worker_name):
    worker = SimpleNamespace(cancel=lambda: pytest.fail("Updating must not cancel disk work"))
    setattr(window, worker_name, worker)
    messages = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *_a: messages.append(_a[-1]))
    monkeypatch.setattr(ui, "SelfUpdateDialog", lambda *_a: pytest.fail("Download must not begin"))
    try:
        window._start_self_update("9.0.0")
        assert messages
        assert getattr(window, worker_name) is worker
    finally:
        setattr(window, worker_name, None)


def test_busy_guard_includes_synchronous_work_and_child_tool_windows(window):
    assert not window._self_update_busy()
    window._staging_depth = 1
    assert window._self_update_busy()
    window._staging_depth = 0
    window.table._zip_import_use_depth = 1
    assert window._self_update_busy()
    window.table._zip_import_use_depth = 0
    window.markivBackupDialog = SimpleNamespace(is_busy=True)
    assert window._self_update_busy()
    window.markivBackupDialog = None
    tool = QDialog(window)
    tool.show()
    try:
        assert window._self_update_busy()
    finally:
        tool.hide()
        tool.deleteLater()


def test_hidden_tool_worker_still_blocks_update(window):
    finished = threading.Event()

    class Worker(QThread):
        def run(self):
            finished.wait(5)

    tool = QDialog(window)
    worker = Worker(tool)
    tool.show()
    worker.start()
    try:
        tool.reject()
        assert not tool.isVisible()
        assert window._self_update_busy()
    finally:
        finished.set()
        assert worker.wait(5000)
        tool.deleteLater()


def test_stopped_qt_preview_process_does_not_block_or_crash_update_check(window):
    tool = QDialog(window)
    tool.live_synth_process = QProcess(tool)
    try:
        assert not window._self_update_busy()
    finally:
        tool.deleteLater()


@pytest.mark.parametrize("choice", [ui.QMessageBox.Save, ui.QMessageBox.Discard, ui.QMessageBox.Cancel])
def test_pending_regular_changes_are_resolved_before_download(window, monkeypatch, choice):
    window.pendingEdits["song.mid"] = "New title"
    saved = []
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *_a: choice)
    monkeypatch.setattr(window, "save_pending_changes", lambda: saved.append(True))
    assert window._self_update_has_pending_changes()
    result = window._self_update_resolve_pending()
    assert result is (choice == ui.QMessageBox.Discard)
    assert saved == ([True] if choice == ui.QMessageBox.Save else [])
    # A discard decision is applied only if the eventual close succeeds.
    assert window.pendingEdits == {"song.mid": "New title"}


def test_successful_save_can_continue_but_async_save_must_finish(window, monkeypatch):
    window.pendingEdits["song.mid"] = "New title"
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *_a: ui.QMessageBox.Save)
    monkeypatch.setattr(window, "save_pending_changes", window.pendingEdits.clear)
    assert window._self_update_resolve_pending()
    window.pendingEdits["song.mid"] = "New title"

    def start_save():
        window.pendingEdits.clear()
        window.diskCommitWorker = object()

    monkeypatch.setattr(window, "save_pending_changes", start_save)
    try:
        assert not window._self_update_resolve_pending()
    finally:
        window.diskCommitWorker = None


def test_pending_order_and_image_repairs_require_confirmation(window, monkeypatch):
    window.pendingRegularOrderKeyEdits["song.fil"] = b"key"
    assert window._self_update_has_pending_changes()
    window.pendingRegularOrderKeyEdits.clear()
    monkeypatch.setattr(window, "is_image_mode", lambda: True)
    monkeypatch.setattr(window, "_pending_changes_to_discard", lambda: False)
    monkeypatch.setattr(window, "_has_pending_image_changes", lambda: True)
    assert window._self_update_has_pending_changes()


def _fake_download(window, monkeypatch, events, *, error=""):
    handoff = SimpleNamespace(commit=lambda: events.append("commit"), cancel=lambda: events.append("cancel"))
    dialog = SimpleNamespace(handoff=handoff if not error else None, error=error, deleteLater=lambda: None)
    monkeypatch.setattr(ui, "SelfUpdateDialog", lambda *_a: dialog)
    monkeypatch.setattr(window, "_exec_child_dialog", lambda *_a, **_k: events.append("download"))
    monkeypatch.setattr(window, "_self_update_show_error", lambda message: events.append(("error", str(message))))
    monkeypatch.setattr(ui.QApplication, "quit", lambda: events.append("quit"))
    return handoff


@pytest.mark.parametrize("accepted", [False, True])
def test_helper_commits_only_after_accepted_close(window, monkeypatch, accepted):
    events = []
    _fake_download(window, monkeypatch, events)
    monkeypatch.setattr(window, "close", lambda: events.append("close") or accepted)
    window._start_self_update("9.0.0")
    assert events == (["download", "close", "commit", "quit"] if accepted else
                      ["download", "close", "cancel"])
    assert not window._self_update_discard_authorized


def test_failed_download_keeps_discard_authorized_edits(window, monkeypatch):
    events = []
    window.pendingEdits["song.mid"] = "New title"
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *_a: ui.QMessageBox.Discard)
    _fake_download(window, monkeypatch, events, error="Checksum mismatch")
    monkeypatch.setattr(window, "close", lambda: pytest.fail("Failed update must not close APS"))
    window._start_self_update("9.0.0")
    assert window.pendingEdits == {"song.mid": "New title"}
    assert events == ["download", ("error", "Checksum mismatch")]


def test_failed_commit_restores_window_without_cleaning_pending_work(window, monkeypatch):
    events = []
    window.show()
    window.pendingEdits["song.mid"] = "New title"
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *_a: ui.QMessageBox.Discard)
    handoff = _fake_download(window, monkeypatch, events)

    def failed_commit():
        assert not window.isVisible()
        raise OSError("The drive was disconnected")

    handoff.commit = failed_commit
    monkeypatch.setattr(window, "_cleanup_for_close", lambda: pytest.fail("Keep the current session intact"))
    window._start_self_update("9.0.0")
    assert window.isVisible()
    assert window.pendingEdits == {"song.mid": "New title"}
    assert not window._self_update_close_pending
    assert events == ["download", ("error", "The drive was disconnected"), "cancel"]


def test_cleanup_error_does_not_prevent_an_already_authorized_restart(window, monkeypatch, capsys):
    events = []
    _fake_download(window, monkeypatch, events)
    monkeypatch.setattr(window, "close", lambda: True)

    def failed_cleanup():
        raise OSError("Temporary directory locked")

    monkeypatch.setattr(window, "_cleanup_for_close", failed_cleanup)
    window._start_self_update("9.0.0")
    assert events == ["download", "commit", "quit"]
    assert "Temporary directory locked" in capsys.readouterr().err


def test_main_close_cancels_download_and_keeps_application_open(window):
    cancelled = []
    window.selfUpdateDialog = SimpleNamespace(is_running=True, reject=lambda: cancelled.append(True))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert cancelled == [True]
    window.selfUpdateDialog = None


def test_download_dialog_waits_for_worker_finished_before_accepting(window, monkeypatch):
    events = []
    handoff = SimpleNamespace(cancel=lambda: events.append("cancel"))
    monkeypatch.setattr(ui, "prepare_update", lambda *_a, **_k: object())
    monkeypatch.setattr(ui, "prepare_handoff", lambda *_a, **_k: handoff)
    dialog = ui.SelfUpdateDialog(window, "9.0.0")
    worker = dialog.worker
    # A deadline is a safety guard against a broken signal connection.
    QTimer.singleShot(5000, dialog.reject)
    try:
        assert dialog.exec() == QDialog.Accepted
        assert not worker.isRunning()
        assert dialog.worker is None
        assert dialog.handoff is handoff
    finally:
        dialog.deleteLater()


def test_cancel_before_thread_start_does_not_download(window, monkeypatch):
    monkeypatch.setattr(ui, "prepare_update", lambda *_a, **_k: pytest.fail("Cancelled before download"))
    dialog = ui.SelfUpdateDialog(window, "9.0.0")
    dialog.reject()
    try:
        assert dialog.exec() == QDialog.Rejected
        assert dialog.worker is None
        assert dialog.handoff is None
    finally:
        dialog.deleteLater()


def test_cancel_during_download_waits_for_cleanup(window, monkeypatch):
    cancelled = []

    def download(_version, *, cancel_callback, **_kwargs):
        deadline = time.monotonic() + 3
        while not cancel_callback() and time.monotonic() < deadline:
            time.sleep(0.01)
        if cancel_callback():
            cancelled.append(True)
            raise ui.UpdateCancelled()
        raise RuntimeError("The cancellation signal did not reach the downloader")

    monkeypatch.setattr(ui, "prepare_update", download)
    monkeypatch.setattr(ui, "prepare_handoff", lambda *_a, **_k: pytest.fail("Cancelled update cannot prepare restart"))
    dialog = ui.SelfUpdateDialog(window, "9.0.0")
    QTimer.singleShot(30, dialog.reject)
    try:
        assert dialog.exec() == QDialog.Rejected
        assert cancelled == [True]
        assert dialog.worker is None
        assert not dialog.error
    finally:
        dialog.deleteLater()


def test_new_edits_during_download_require_new_permission(window, monkeypatch):
    events = []
    _fake_download(window, monkeypatch, events)

    def complete_download(*_args, **_kwargs):
        events.append("download")
        window.pendingEdits["song.mid"] = "New title"

    monkeypatch.setattr(window, "_exec_child_dialog", complete_download)
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *_a: ui.QMessageBox.Cancel)
    monkeypatch.setattr(window, "close", lambda: pytest.fail("New edits were not approved for discard"))
    window._start_self_update("9.0.0")
    assert window.pendingEdits == {"song.mid": "New title"}
    assert events == ["download", "cancel"]
