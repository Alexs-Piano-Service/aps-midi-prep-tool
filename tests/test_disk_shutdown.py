import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
import pytest

from aps_midi_prep_tool_app import main_window


@pytest.mark.parametrize("worker_name", [
    "diskLoadWorker", "diskRecoveryWorker", "diskFormatWorker", "diskCommitWorker",
    "diskWriteTargetWorker", "diskImageCaptureWorker", "bulkExtractionWorker", "emulatorImageWorker",
])
def test_close_cancels_disk_work_and_retains_resources_until_finished(tmp_path, monkeypatch, worker_name):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "shutdown.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    window = main_window.MidiTitleWindow()
    cancelled = []
    cleaned = []
    messages = []
    worker = SimpleNamespace(cancel=lambda: cancelled.append(True))
    setattr(window, worker_name, worker)
    monkeypatch.setattr(window, "is_image_mode", lambda: False)
    monkeypatch.setattr(window, "_reset_image_state", lambda: cleaned.append("image"))
    monkeypatch.setattr(window, "_cleanup_midi_scratch_dir", lambda: cleaned.append("scratch"))
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda _parent, title, text: messages.append((title, text)))
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert cancelled == [True]
        assert cleaned == []
        assert getattr(window, worker_name) is worker
        assert messages[0][0] == "Stopping Disk Work"
        assert "close APS again" in messages[0][1]

        # The normal finished slot releases the worker after its process exits.
        setattr(window, worker_name, None)
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        assert cleaned == ["image", "scratch"]
    finally:
        setattr(window, worker_name, None)
        window.deleteLater()
        app.processEvents()
