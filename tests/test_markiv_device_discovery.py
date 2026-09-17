"""A hung volume query must not trap the Mark IV dialog or a later retry."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import disk_device_discovery as process_discovery
from aps_midi_prep_tool_app import disk_device_probe as helper
from aps_midi_prep_tool_app import markiv_device_discovery as discovery
from aps_midi_prep_tool_app.markiv_backup.devices import MountedDevice
from aps_midi_prep_tool_app.markiv_backup_dialog import MarkIVBackupDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _wait(app, condition, *, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert condition(), "The Mark IV dialog exceeded its recovery deadline"


@pytest.fixture
def dialog(app, tmp_path):
    instance = MarkIVBackupDialog(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
                                  refresh_on_open=False)
    instance.show()
    yield instance
    instance.cancel_operation()
    _wait(app, lambda: not instance.is_busy)
    instance.close()
    app.processEvents()


@pytest.fixture
def probes(monkeypatch):
    started = []
    probe_class = process_discovery._DeviceProbe

    def record(kind):
        probe = probe_class(kind)
        started.append(probe)
        return probe

    monkeypatch.setattr(process_discovery, "_DeviceProbe", record)
    yield started
    for probe in started:
        probe.close()
        assert probe.stopped.wait(15)
        assert probe.process is None or probe.process.poll() is not None


def _blocked_command(entered):
    # The controlled query waits forever. Only terminating the helper can end
    # it; the tests never release the discovery event to make Cancel work.
    def command(kind, result_path):
        assert kind == "markiv"
        return [sys.executable, "-c", "\n".join([
            "import pathlib, sys, threading",
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})",
            "from aps_midi_prep_tool_app import markiv_device_discovery as discovery",
            "from aps_midi_prep_tool_app.disk_device_probe import run_device_probe_from_argv, DEVICE_PROBE_ARG",
            "def blocked_query():",
            "    pathlib.Path(sys.argv[1]).write_text('entered')",
            "    threading.Event().wait()",
            "discovery.mounted_sources = blocked_query",
            "raise SystemExit(run_device_probe_from_argv(['aps', DEVICE_PROBE_ARG, 'markiv', sys.argv[2]]))",
        ]), str(entered), str(result_path)]
    return command


def _successful_command(_kind, result_path):
    result = {"devices": [{"device": "/dev/test", "mountpoint": "/media/Pianó",
                           "filesystem": "ext3", "read_only": True, "is_mark_iv": True}],
              "error": ""}
    return [sys.executable, "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')",
            str(result_path), json.dumps(result)]


def _retry(app, dialog, probes, monkeypatch):
    monkeypatch.setattr(process_discovery, "_probe_command", _successful_command)
    dialog.refresh_drives()
    _wait(app, lambda: not dialog.is_busy)
    assert dialog.source_combo.currentText() == str(Path("/media/Pianó"))
    assert dialog.refresh_button.isEnabled()
    assert len(probes) == 2
    assert probes[0].process.pid != probes[1].process.pid


def test_cancel_does_not_wait_for_a_stalled_query_or_process_cleanup(
        app, dialog, probes, tmp_path, monkeypatch):
    entered = tmp_path / "entered"
    monkeypatch.setattr(process_discovery, "_probe_command", _blocked_command(entered))
    cleanup_release = threading.Event()
    # Simulate a driver that also stalls process-tree termination. A fresh
    # discovery must work even before that cleanup eventually finishes.
    dialog.refresh_drives()
    _wait(app, entered.exists, timeout=5)
    original_reap = probes[0]._reap

    def stalled_cleanup():
        cleanup_release.wait(10)
        original_reap()

    monkeypatch.setattr(probes[0], "_reap", stalled_cleanup)
    try:
        dialog.cancel_button.click()
        _wait(app, lambda: not dialog.is_busy)
        assert dialog.isVisible()
        assert probes[0].process.poll() is None
        _retry(app, dialog, probes, monkeypatch)
    finally:
        cleanup_release.set()


@pytest.mark.parametrize("close_method", ("close", "button", "escape"))
def test_stalled_discovery_allows_close_and_a_fresh_dialog(
        app, dialog, probes, tmp_path, monkeypatch, close_method):
    entered = tmp_path / "entered"
    monkeypatch.setattr(process_discovery, "_probe_command", _blocked_command(entered))
    dialog.refresh_drives()
    _wait(app, entered.exists, timeout=5)
    if close_method == "close":
        dialog.close()
    elif close_method == "button":
        dialog.close_button.click()
    else:
        QTest.keyClick(dialog, Qt.Key_Escape)
    _wait(app, lambda: not dialog.is_busy and not dialog.isVisible())

    retry = MarkIVBackupDialog(dialog.settings, refresh_on_open=False)
    try:
        _retry(app, retry, probes, monkeypatch)
    finally:
        retry.cancel_operation()
        _wait(app, lambda: not retry.is_busy)
        retry.close()


def test_discovery_timeout_keeps_qt_responsive_and_allows_retry(
        app, dialog, probes, tmp_path, monkeypatch):
    entered = tmp_path / "entered"
    monkeypatch.setattr(process_discovery, "_probe_command", _blocked_command(entered))
    monkeypatch.setattr(discovery, "DISCOVERY_TIMEOUT_SECONDS", 2.0)
    heartbeats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: heartbeats.append(True))
    timer.start()
    try:
        dialog.refresh_drives()
        _wait(app, lambda: not dialog.is_busy, timeout=3)
        assert entered.exists()
        assert heartbeats
        assert "did not finish within 2 seconds" in "\n".join(dialog._report_messages)
        assert dialog.refresh_button.isEnabled()
        _retry(app, dialog, probes, monkeypatch)
    finally:
        timer.stop()


def test_helper_serializes_markiv_volume_paths(tmp_path, monkeypatch):
    device = MountedDevice("/dev/music", tmp_path / "Pianó", filesystem="ext3",
                           label="Disklavier", read_only=True, is_mark_iv=True)
    monkeypatch.setattr(discovery, "mounted_sources", lambda: [device])
    result_path = tmp_path / "result.json"
    assert helper.run_device_probe_from_argv([
        "aps", helper.DEVICE_PROBE_ARG, "markiv", str(result_path),
    ]) == 0
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "devices": [{**asdict(device), "mountpoint": str(device.mountpoint)}], "error": "",
    }


def test_markiv_helper_launch_failure_is_reported_and_retry_succeeds(
        app, dialog, probes, monkeypatch):
    monkeypatch.setattr(process_discovery, "_probe_command",
                        lambda *_args: ["/does-not-exist/aps-discovery"])
    dialog.refresh_drives()
    _wait(app, lambda: not dialog.is_busy)
    assert dialog._report_messages
    monkeypatch.setattr(process_discovery, "_probe_command", _successful_command)
    dialog.refresh_drives()
    _wait(app, lambda: not dialog.is_busy)
    assert dialog.source_combo.currentText() == str(Path("/media/Pianó"))
