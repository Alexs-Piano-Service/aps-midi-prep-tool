import os
import threading
import subprocess
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog, QPushButton

from aps_midi_prep_tool_app import disk_device_discovery as discovery


@pytest.fixture
def app(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(discovery, "_probes", {})
    yield app
    app.processEvents()


def test_stalled_probe_keeps_qt_responsive_and_returns_other_devices(app):
    release = threading.Event()
    threads = []
    heartbeats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: heartbeats.append(True))
    timer.start()

    def stalled_probe():
        threads.append(threading.current_thread())
        release.wait(5)
        return ["late USB drive"]

    try:
        result = discovery.discover_floppy_devices(
            None,
            floppy_probe=stalled_probe,
            greaseweazle_probe=lambda: ["available Greaseweazle"],
            timeout=0.1,
        )
        assert result[0] == []
        assert result[1] == ["available Greaseweazle"]
        assert "did not finish" in result[2][0]
        assert heartbeats
        assert threads[0] is not threading.current_thread()
        assert threads[0].daemon  # A stuck driver must not block app exit.

        # Retry must reuse the existing OS call instead of spawning another.
        discovery.discover_floppy_devices(None, floppy_probe=stalled_probe, timeout=0)
        assert len(threads) == 1
    finally:
        timer.stop()
        release.set()
        assert discovery._probes["floppy"].done.wait(2)

    # A slow completed result remains usable on retry, rather than restarting
    # a probe that could repeatedly take longer than the timeout.
    assert discovery.discover_floppy_devices(None, floppy_probe=stalled_probe) == (
        ["late USB drive"], [], [],
    )
    assert len(threads) == 1


def test_cancel_returns_without_waiting_for_the_driver(app):
    release = threading.Event()

    def stalled_probe():
        release.wait(5)
        return []

    def cancel_dialog():
        dialog = next(widget for widget in app.topLevelWidgets()
                      if isinstance(widget, QProgressDialog) and widget.isVisible())
        dialog.findChild(QPushButton).click()

    try:
        QTimer.singleShot(20, cancel_dialog)
        result = discovery.discover_floppy_devices(None, floppy_probe=stalled_probe, timeout=3)
        assert result is None
        assert not discovery._probes["floppy"].done.is_set()
    finally:
        release.set()
        assert discovery._probes["floppy"].done.wait(2)


def test_probe_failure_is_reported_and_a_later_attempt_can_succeed(app):
    def failed_probe():
        raise OSError("The device is not ready")

    first = discovery.discover_floppy_devices(None, floppy_probe=failed_probe)
    assert first == ([], [], ["Floppy Drive: The device is not ready"])
    assert discovery.discover_floppy_devices(None, floppy_probe=lambda: ["A:"]) == (["A:"], [], [])


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
