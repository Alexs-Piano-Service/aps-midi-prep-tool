import json
import os
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog, QPushButton

from aps_midi_prep_tool_app import disk_device_discovery as discovery
from aps_midi_prep_tool_app import disk_device_probe as helper
from aps_midi_prep_tool_app.floppy_image import FloppyDriveInfo, GreaseweazleDeviceInfo


def _result_command(result_path, devices=(), error=""):
    return [sys.executable, "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')",
            str(result_path), json.dumps({"devices": list(devices), "error": error})]


def _hung_command(*_args):
    # This process never finishes on its own; no test releases it to allow retry.
    return [sys.executable, "-c", "import time\nwhile True: time.sleep(60)"]


def _drive_command(_kind, result_path):
    return _result_command(result_path, [{"path": "A:\\", "size_bytes": 1474560,
                                          "mountpoints": ["A:\\"]}])


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def probes(monkeypatch):
    started = []
    start = discovery._start_probe

    def record(kind):
        probe = start(kind)
        started.append(probe)
        return probe

    monkeypatch.setattr(discovery, "_start_probe", record)
    yield started
    for probe in started:
        probe.close()
        assert probe.stopped.wait(15)
        if probe.process is not None:
            assert probe.process.poll() is not None


def test_stalled_probe_keeps_qt_responsive_and_retry_starts_fresh(app, probes, monkeypatch):
    heartbeats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: heartbeats.append(True))
    timer.start()

    def command(kind, result_path):
        if kind == "floppy":
            return _hung_command()
        return _result_command(result_path, [{"path": "COM4", "label": "Greaseweazle"}])

    monkeypatch.setattr(discovery, "_probe_command", command)
    try:
        result = discovery.discover_floppy_devices(None, timeout=2)
        assert result[0] == []
        assert result[1] == [GreaseweazleDeviceInfo("COM4", "Greaseweazle")]
        assert "did not finish" in result[2][0]
        assert heartbeats
        assert not probes[0].done

        # Retry succeeds without releasing the first process or restarting APS.
        monkeypatch.setattr(discovery, "_probe_command", _drive_command)
        retry = discovery.discover_floppy_devices(None, include_greaseweazle=False, timeout=5)
        assert retry == ([FloppyDriveInfo("A:\\", 1474560, mountpoints=("A:\\",))], [], [])
        assert len(probes) == 3
        assert probes[0].process.pid != probes[2].process.pid
        assert probes[0].stopped.wait(15)
        assert probes[0].process.poll() is not None
    finally:
        timer.stop()


def test_cancel_terminates_helper_and_allows_retry(app, probes, monkeypatch):
    monkeypatch.setattr(discovery, "_probe_command", _hung_command)

    def cancel_dialog():
        dialog = next(widget for widget in app.topLevelWidgets()
                      if isinstance(widget, QProgressDialog) and widget.isVisible())
        dialog.findChild(QPushButton).click()

    QTimer.singleShot(100, cancel_dialog)
    result = discovery.discover_floppy_devices(None, include_greaseweazle=False, timeout=5)
    assert result is None
    assert not probes[0].done
    monkeypatch.setattr(discovery, "_probe_command", _drive_command)
    assert discovery.discover_floppy_devices(None, include_greaseweazle=False)[0][0].path == "A:\\"


def test_probe_failure_is_reported_and_a_later_attempt_can_succeed(app, probes, monkeypatch):
    monkeypatch.setattr(discovery, "_probe_command", lambda kind, path:
                        _result_command(path, error="The device is not ready"))
    first = discovery.discover_floppy_devices(None, include_greaseweazle=False)
    assert first == ([], [], ["Floppy Drive: The device is not ready"])
    monkeypatch.setattr(discovery, "_probe_command", _drive_command)
    assert discovery.discover_floppy_devices(None, include_greaseweazle=False)[0][0].path == "A:\\"


def test_helper_launch_failure_is_reported(app, probes, monkeypatch):
    def failed_launch(*args, **kwargs):
        raise OSError("Cannot start discovery")

    monkeypatch.setattr(discovery.subprocess, "Popen", failed_launch)
    assert discovery.discover_floppy_devices(None, include_greaseweazle=False) == (
        [], [], ["Floppy Drive: Cannot start discovery"],
    )


@pytest.mark.parametrize("kind, callback, device", [
    ("floppy", "list_floppy_drives", FloppyDriveInfo("A:\\", 1474560, label="Pianó", mountpoints=("A:\\",))),
    ("greaseweazle", "list_greaseweazle_devices", GreaseweazleDeviceInfo("COM4", "Greaseweazle")),
])
def test_helper_serializes_device_information(kind, callback, device, tmp_path, monkeypatch):
    from dataclasses import asdict
    from aps_midi_prep_tool_app import floppy_image

    monkeypatch.setattr(floppy_image, callback, lambda: [device])
    result_path = tmp_path / "result.json"
    assert helper.run_device_probe_from_argv(["aps", helper.DEVICE_PROBE_ARG, kind, str(result_path)]) == 0
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "devices": json.loads(json.dumps([asdict(device)])), "error": "",
    }


def test_source_entry_point_dispatches_helper_without_starting_qt(tmp_path):
    result_path = tmp_path / "result.json"
    result = subprocess.run(helper.probe_command("unknown-kind", result_path),
                            capture_output=True, timeout=15)
    assert result.returncode == 1
    assert "unknown-kind" in json.loads(result_path.read_text(encoding="utf-8"))["error"]


def test_frozen_helper_command_uses_bundled_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    result_path = tmp_path / "result.json"
    assert helper.probe_command("floppy", result_path) == [
        sys.executable, helper.DEVICE_PROBE_ARG, "floppy", str(result_path),
    ]


@pytest.mark.parametrize("method_name", [
    "_choose_format_floppy_options", "_choose_floppy_image_capture_options",
    "_choose_floppy_read_options", "_choose_save_to_floppy_drive",
    "_choose_write_image_floppy_target",
])
def test_all_physical_drive_choosers_respect_discovery_cancellation(method_name):
    from aps_midi_prep_tool_app.main_window import MidiTitleWindow

    calls = []
    window = SimpleNamespace(_discover_floppy_devices=lambda **kwargs: calls.append(kwargs))
    assert getattr(MidiTitleWindow, method_name)(window) is None
    assert calls == ([{"include_greaseweazle": False}]
                     if method_name == "_choose_save_to_floppy_drive" else [{}])


def test_windows_pnp_timeout_still_uses_registry_detection(monkeypatch):
    from aps_midi_prep_tool_app import floppy_image

    calls = []

    def timed_out(command, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(floppy_image, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(floppy_image.shutil, "which", lambda _name: "powershell")
    monkeypatch.setattr(floppy_image.subprocess, "run", timed_out)
    monkeypatch.setattr(floppy_image, "_list_windows_greaseweazle_devices_from_registry", lambda: ["COM4"])
    assert floppy_image._list_windows_greaseweazle_devices() == ["COM4"]
    assert calls == [5.0]


def test_drive_discovery_messages_are_translated_with_valid_placeholders():
    from aps_midi_prep_tool_app.disk_discovery_translations import DISK_DISCOVERY_TRANSLATIONS
    from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text

    for language in SUPPORTED_LANGUAGES:
        for source in DISK_DISCOVERY_TRANSLATIONS:
            translated = translate_text(source, language.code, device="A:", seconds="10")
            assert "{" not in translated
            if language.code != "en":
                assert translated != source.format(device="A:", seconds="10")
