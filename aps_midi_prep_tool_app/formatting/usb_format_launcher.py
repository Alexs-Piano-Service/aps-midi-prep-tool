"""Launch USB formatting either directly or through the helper executable."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

from .usb_format_core import (
    UsbFormatCancelled,
    UsbFormatError,
    create_usb_format_job,
    read_usb_format_result,
    run_usb_format_job,
    write_usb_format_job,
)


def run_usb_format_for_gui(
    drive_info,
    layout_kind,
    *,
    volume_label="DISKLAV",
    progress_callback=None,
    cancel_callback=None,
):
    job = create_usb_format_job(drive_info, layout_kind, volume_label=volume_label, source="gui")
    invocation = _windows_helper_invocation() if os.name == "nt" else None
    if invocation is None:
        return run_usb_format_job(
            job,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            preselected_drive=drive_info,
        )
    return _run_helper_job(
        invocation,
        job,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )


def _windows_helper_invocation():
    configured = os.environ.get("APS_FORMAT_HELPER", "").strip()
    if configured:
        return configured, []

    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    helper_name = "aps-format-helper.exe"
    for candidate in (
        os.path.join(executable_dir, helper_name),
        os.path.join(executable_dir, "helpers", helper_name),
        os.path.join(executable_dir, "aps-format-helper", helper_name),
        os.path.join(executable_dir, "helpers", "aps-format-helper", helper_name),
        os.path.join(os.getcwd(), helper_name),
    ):
        if os.path.exists(candidate):
            return candidate, []

    if not getattr(sys, "frozen", False):
        return sys.executable, ["-m", "aps_midi_prep_tool_app.helpers.windows_format_helper"]
    return None


def _run_helper_job(invocation, job, *, progress_callback=None, cancel_callback=None):
    executable, base_args = invocation
    with tempfile.TemporaryDirectory(prefix="aps-usb-format-") as temp_dir:
        job_path = os.path.join(temp_dir, "job.json")
        result_path = os.path.join(temp_dir, "result.json")
        write_usb_format_job(job, job_path)
        args = [*base_args, "--job", job_path, "--result", result_path]
        _notify(progress_callback, 0, 0, "Waiting for the USB format helper...")

        if os.name == "nt":
            exit_code = _run_windows_elevated(executable, args, cancel_callback=cancel_callback)
        else:
            exit_code = _run_subprocess(executable, args, cancel_callback=cancel_callback)

        if not os.path.exists(result_path):
            raise UsbFormatError(f"The USB format helper did not write a result file (exit code {exit_code}).")
        result = read_usb_format_result(result_path)
        if result.get("cancelled"):
            raise UsbFormatCancelled(result.get("error") or "Operation cancelled.")
        if not result.get("ok"):
            raise UsbFormatError(result.get("error") or "The USB format helper failed.")
        _notify(progress_callback, 100, 100, "USB format complete.")
        return result


def _run_subprocess(executable, args, *, cancel_callback=None):
    process = subprocess.Popen([executable, *args])
    try:
        while process.poll() is None:
            _raise_if_cancelled(cancel_callback)
            time.sleep(0.1)
    except UsbFormatCancelled:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    return int(process.returncode or 0)


def _run_windows_elevated(executable, args, *, cancel_callback=None):
    parameters = subprocess.list2cmdline([str(arg) for arg in args])
    directory = os.path.dirname(os.path.abspath(executable)) or None
    return _shell_execute_runas_and_wait(
        str(executable),
        parameters,
        directory=directory,
        cancel_callback=cancel_callback,
    )


def _shell_execute_runas_and_wait(executable, parameters, *, directory=None, cancel_callback=None):
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_SHOWNORMAL = 1
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HANDLE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HANDLE),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(ShellExecuteInfo)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.hwnd = None
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = parameters
    info.lpDirectory = directory
    info.nShow = SW_SHOWNORMAL

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise UsbFormatError(_format_windows_error("Could not start the elevated USB format helper"))

    handle = info.hProcess
    try:
        while True:
            wait_result = kernel32.WaitForSingleObject(handle, 100)
            if wait_result == WAIT_OBJECT_0:
                break
            if wait_result != WAIT_TIMEOUT:
                raise UsbFormatError(_format_windows_error("Could not wait for the USB format helper"))
            if cancel_callback is not None and cancel_callback():
                kernel32.TerminateProcess(handle, 1)
                raise UsbFormatCancelled("Operation cancelled.")

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise UsbFormatError(_format_windows_error("Could not read the USB format helper result"))
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(handle)


def _format_windows_error(prefix):
    code = ctypes.get_last_error()
    if code:
        return f"{prefix}: {ctypes.FormatError(code).strip()}"
    return f"{prefix}."


def _raise_if_cancelled(cancel_callback=None):
    if cancel_callback is not None and cancel_callback():
        raise UsbFormatCancelled("Operation cancelled.")


def _notify(progress_callback, step, total, message):
    if progress_callback is not None:
        progress_callback(int(step or 0), int(total or 0), str(message or ""))
