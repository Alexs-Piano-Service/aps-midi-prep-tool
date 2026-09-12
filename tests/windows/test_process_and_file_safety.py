"""Exercise Windows kernel file sharing and actual child-process trees."""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.subprocess_utils import windows_subprocess_kwargs
from test_image_workflows import make_image, opened, contents
from scripts.build_windows_test_kit import midi_bytes


@contextmanager
def locked_for_reading(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Permit reads, deny writes and deletes, like another application holding the image open.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def test_locked_destination_is_preserved_and_retry_succeeds(tmp_path, bundled_mtools):
    path = make_image(tmp_path, {"SONG.MID": midi_bytes("Source")})
    output = tmp_path / "Existing destination.img"
    output.write_bytes(path.read_bytes())
    before = output.read_bytes()
    with opened(path) as session:
        with locked_for_reading(output):
            with pytest.raises(OSError):
                session.export_to(str(output), "img", renames={"SONG.MID": "RENAMED.MID"})
            assert output.read_bytes() == before
            assert path.read_bytes() == before
        assert not list(Path(session.temp_dir).glob(".aps_image_*"))
        session.export_to(str(output), "img", renames={"SONG.MID": "RENAMED.MID"})
    with opened(output) as session:
        assert contents(session) == {"RENAMED.MID": midi_bytes("Source")}


def test_cancelled_export_preserves_both_images_and_removes_partial_output(tmp_path, bundled_mtools):
    path = make_image(tmp_path, {"SONG.MID": midi_bytes("Source")})
    output = tmp_path / "Existing.img"
    output.write_bytes(path.read_bytes())
    before = path.read_bytes()
    with opened(path) as session:
        cancel = False
        def progress(current, total, message):
            nonlocal cancel
            if "Writing raw" in message:
                cancel = True
        with pytest.raises(image.FloppyOperationCancelled):
            session.export_to(str(output), "img", renames={"SONG.MID": "RENAMED.MID"},
                              progress_callback=progress, cancel_callback=lambda: cancel)
        assert cancel, "The test must reach the output-writing stage"
        assert output.read_bytes() == path.read_bytes() == before
        assert not list(Path(session.temp_dir).glob(".aps_image_*"))
        assert not list(Path(session.temp_dir).glob("modified_*"))


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_windows_stops_actual_helper_descendants_and_leaves_unrelated_process(tmp_path, stop):
    marker = tmp_path / "descendant.json"
    held_file = tmp_path / "child-holds-this.txt"
    parent_code = """
import json, subprocess, sys, time
child_code = 'import sys,time; f=open(sys.argv[1], "w"); f.write("ready"); f.flush(); time.sleep(60)'
p = subprocess.Popen([sys.executable, '-c', child_code, sys.argv[2]])
with open(sys.argv[1], 'w') as f:
    json.dump(p.pid, f)
time.sleep(60)
"""
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                  **windows_subprocess_kwargs())
    pid = None
    started = time.monotonic()
    try:
        def ready():
            return marker.exists() and held_file.exists() and held_file.stat().st_size > 0
        expected = image.FloppyOperationCancelled if stop == "cancel" else image.FloppyImageError
        with pytest.raises(expected):
            image._run_command([sys.executable, "-c", parent_code, str(marker), str(held_file)],
                               "Helper stopped", timeout=5 if stop == "timeout" else 15,
                               cancel_callback=ready if stop == "cancel" else None)
        assert ready(), "A real descendant must have acquired its file before shutdown"
        pid = json.loads(marker.read_text())
        assert time.monotonic() - started < 18
        # On Windows an open CRT file handle prevents deletion after a parent-only kill.
        held_file.unlink()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 2000) == 0, "Descendant is still running"
            finally:
                kernel.CloseHandle(handle)
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
        # Clean up only the PID created by this test, including on assertion failure.
        if marker.exists():
            try:
                pid = json.loads(marker.read_text())
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                                "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10,
                               **windows_subprocess_kwargs())
            except (OSError, ValueError):
                pass


def test_hidden_helper_flags_are_used_on_windows():
    options = windows_subprocess_kwargs()
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert options["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert options["startupinfo"].wShowWindow == subprocess.SW_HIDE
