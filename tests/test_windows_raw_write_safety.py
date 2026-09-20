"""Fault-injected Windows I/O tests; no physical floppy is accessed."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image, windows_write_guard


def _windows_mode(monkeypatch):
    # Replace this module's os binding instead of changing pathlib's platform.
    monkeypatch.setattr(floppy_image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))


@pytest.mark.parametrize("error_code", [1, 21])
def test_failed_flush_is_never_an_ordinary_success(tmp_path, monkeypatch, error_code):
    image = tmp_path / "source.img"
    image.write_bytes(b"x" * 8192)
    volume = object.__new__(floppy_image._WindowsVolumeHandle)
    volume.handle, volume.path = 1, "A:"
    volume._seek = lambda *_: None
    volume._ctypes = SimpleNamespace(byref=ctypes.byref, get_last_error=lambda: error_code)
    volume._wintypes = wintypes

    def write(handle, buffer, size, count, _):
        assert ctypes.addressof(buffer) % 4096 == 0
        count._obj.value = size
        return True

    volume._kernel32 = SimpleNamespace(WriteFile=write, FlushFileBuffers=lambda _: False)
    monkeypatch.setattr(floppy_image, "_windows_last_error_message", lambda _: f"[WinError {error_code}] flush failed")
    diagnostics = {}
    if error_code == 21:
        with pytest.raises(floppy_image.FloppyImageError, match="21"):
            volume.write_file(image, diagnostics=diagnostics)
    else:
        assert volume.write_file(image, diagnostics=diagnostics)["confidence"] == "flush_unconfirmed"
    assert diagnostics["stage"] == "flush"
    assert diagnostics["bytes_written"] == 8192
    assert diagnostics["winerror"] == error_code
    assert diagnostics["target_mutation_attempted"] is True


@pytest.mark.parametrize("stop_reason", ["cancelled", "timeout"])
def test_watchdog_terminates_a_blocked_native_operation(tmp_path, monkeypatch, stop_reason):
    marker = tmp_path / "cancel"
    if stop_reason == "cancelled":
        marker.touch()
    requested, terminated = [], threading.Event()
    monkeypatch.setattr(windows_write_guard, "CANCEL_GRACE_SECONDS", 0.01)
    stopped = windows_write_guard.start_watchdog(
        str(marker), requested.append, terminated.set,
        timeout=0 if stop_reason == "timeout" else 10,
    )
    try:
        assert terminated.wait(2)
        assert requested == [stop_reason]
    finally:
        stopped.set()


@pytest.mark.parametrize("failure", ["blocked", "failed"])
def test_watchdog_termination_survives_stop_diagnostic_failure(tmp_path, monkeypatch, failure):
    requested = threading.Event()
    release_request = threading.Event()
    terminated = threading.Event()
    monkeypatch.setattr(windows_write_guard, "CANCEL_GRACE_SECONDS", 0.01)

    def request_stop(reason):
        assert reason == "timeout"
        requested.set()
        if failure == "blocked":
            assert release_request.wait(3)
        else:
            raise OSError("Could not publish cancellation diagnostics")

    stopped = windows_write_guard.start_watchdog(
        "", request_stop, terminated.set, timeout=0,
    )
    try:
        assert requested.wait(2)
        assert terminated.wait(2), "Blocked or failed diagnostics must not prevent forced termination"
    finally:
        stopped.set()
        release_request.set()


def test_helper_keeps_watchdog_active_until_final_result_is_flushed(tmp_path, monkeypatch):
    _windows_mode(monkeypatch)
    marker, result = tmp_path / "cancel", tmp_path / "result.json"
    stopped = threading.Event()
    flushes = []
    monkeypatch.setattr(windows_write_guard, "start_watchdog", lambda *args: stopped)
    monkeypatch.setattr(
        floppy_image, "_write_block_device_windows_direct",
        lambda *args, **kwargs: {"confidence": "written"},
    )

    def flush(descriptor):
        flushes.append(descriptor)
        assert not stopped.is_set(), "Final publication must remain bounded by the watchdog"

    monkeypatch.setattr(floppy_image.os, "fsync", flush)
    status = floppy_image.run_windows_raw_write_helper_from_argv([
        "app.exe", floppy_image._WINDOWS_RAW_WRITE_HELPER_ARG,
        "image", "A:", str(result), str(marker),
    ])

    assert status == 0
    assert json.loads(result.read_text())["ok"] is True
    assert flushes
    assert stopped.is_set()


def test_watchdog_disarm_cannot_return_before_pending_termination():
    stopped = windows_write_guard._WatchdogStop()
    terminating, release_termination, disarmed = (threading.Event() for _ in range(3))

    def terminate():
        terminating.set()
        assert release_termination.wait(3)

    watchdog = threading.Thread(target=stopped.terminate_unless_stopped, args=(terminate,))
    watchdog.start()
    assert terminating.wait(2)

    def finish():
        stopped.set()
        disarmed.set()

    completion = threading.Thread(target=finish)
    completion.start()
    try:
        assert not disarmed.wait(0.05)
    finally:
        release_termination.set()
        watchdog.join(2)
        completion.join(2)

    assert disarmed.is_set()
    later_termination = []
    stopped.terminate_unless_stopped(lambda: later_termination.append(True))
    assert not later_termination


def test_elevated_wait_sends_cancel_during_timeouts_and_waits_for_exit(tmp_path, monkeypatch):
    marker = tmp_path / "cancel"
    waits, closed = [], []

    def launch(pointer):
        pointer._obj.hProcess = 12
        return True

    def wait(handle, delay):
        waits.append(handle)
        if len(waits) >= 2:
            assert marker.exists(), "WAIT_TIMEOUT must not skip cancellation"
        return 0 if len(waits) == 4 else 258

    def exit_code(handle, pointer):
        assert len(waits) == 4
        pointer._obj.value = 1
        return True

    kernel = SimpleNamespace(WaitForSingleObject=wait, GetExitCodeProcess=exit_code,
                             CloseHandle=lambda handle: closed.append(handle))
    shell = SimpleNamespace(ShellExecuteExW=launch)
    native = SimpleNamespace(**vars(ctypes))
    native.WinDLL = lambda name, **kw: shell if name == "shell32" else kernel
    monkeypatch.setattr(floppy_image, "_windows_ctypes", lambda: (native, wintypes, kernel))
    result = floppy_image._run_windows_process_as_admin(
        "app.exe", "args", cancel_callback=lambda: len(waits) >= 1, cancel_path=str(marker),
    )
    assert result == 1
    assert len(waits) == 4
    assert closed == [12]


def test_no_second_writer_starts_until_cancelled_helper_exits(tmp_path, monkeypatch):
    _windows_mode(monkeypatch)
    first_started, release_first, second_entered = threading.Event(), threading.Event(), threading.Event()
    starts, errors = [], []
    def command(image, drive, result, cancel):
        return "app.exe", json.dumps([result, cancel])

    def elevated(executable, parameters, **kwargs):
        result, cancel = json.loads(parameters)
        starts.append(result)
        if len(starts) == 1:
            first_started.set()
            assert release_first.wait(3)
        Path(result).write_text(json.dumps({"ok": False, "stop_reason": "cancelled",
                                          "diagnostics": {"target_mutation_attempted": True}}))
        return 1

    monkeypatch.setattr(floppy_image, "_windows_raw_write_helper_command", command)
    monkeypatch.setattr(floppy_image, "_run_windows_process_as_admin", elevated)
    def run(second=False):
        if second:
            second_entered.set()
        try:
            floppy_image._write_block_device_windows_helper("image", "A:", elevated=True)
        except floppy_image.FloppyOperationCancelled as exc:
            errors.append(exc)

    first = threading.Thread(target=run)
    second = threading.Thread(target=run, args=(True,))
    first.start()
    assert first_started.wait(2)
    second.start()
    try:
        assert second_entered.wait(2)
        assert len(starts) == 1
        assert floppy_image._WINDOWS_RAW_WRITE_LOCK.locked()
    finally:
        release_first.set()
        first.join(3)
        second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert len(starts) == len(errors) == 2


def test_helper_acknowledges_cancellation_and_persists_partial_write(tmp_path, monkeypatch):
    _windows_mode(monkeypatch)
    marker, result = tmp_path / "cancel", tmp_path / "result.json"
    def direct(image, device, *, diagnostics, progress_callback, cancel_callback):
        diagnostics.update(stage="write", bytes_written=512, target_mutation_attempted=True)
        marker.touch()
        assert cancel_callback()
        raise floppy_image.FloppyOperationCancelled("cancelled")
    monkeypatch.setattr(floppy_image, "_write_block_device_windows_direct", direct)
    status = floppy_image.run_windows_raw_write_helper_from_argv([
        "app.exe", floppy_image._WINDOWS_RAW_WRITE_HELPER_ARG, "image", "A:", str(result), str(marker),
    ])
    payload = json.loads(result.read_text())
    assert status == 1 and payload["ok"] is False
    assert payload["stop_reason"] == "cancelled"
    assert payload["diagnostics"]["bytes_written"] == 512


def test_fallback_capacity_probe_uses_bounded_full_sectors(monkeypatch):
    _windows_mode(monkeypatch)
    reads = []
    class Volume:
        handle = 1
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read_at_recovery(self, offset, size, label, **kwargs):
            assert offset % 512 == 0 and size == 512
            assert kwargs["deadline_at"] > 0
            reads.append(offset)
            if offset + size > 720 * 1024:
                raise floppy_image.FloppyImageError("outside medium")
            return b"x" * size
    monkeypatch.setattr(floppy_image, "_WindowsVolumeHandle", Volume)
    monkeypatch.setattr(floppy_image, "_WindowsRecoveryVolumeHandle", Volume)
    monkeypatch.setattr(floppy_image, "_windows_ctypes", lambda: (ctypes, wintypes, None))
    monkeypatch.setattr(floppy_image, "_windows_device_io_control", lambda *a: False)
    assert floppy_image._windows_detect_floppy_size("A:") == 720 * 1024
    assert reads[-1] == 720 * 1024 - 512
    assert ctypes.addressof(floppy_image._windows_io_buffer(512)) % 4096 == 0
