import ctypes
from ctypes import wintypes
import math
import os
import subprocess
import time


def windows_subprocess_kwargs():
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


class WindowsProcessTreeWaiter:
    """Hold descendant handles before shutdown, then wait for their file cleanup."""

    def __init__(self, parent_pid):
        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ("Process32FirstW", "Process32NextW"):
            function = getattr(kernel, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            function.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handles = []

        snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            parents = {}
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)

        descendants = {parent_pid}
        while True:
            children = {pid for pid, parent in parents.items()
                        if parent in descendants and pid not in descendants}
            if not children:
                break
            descendants.update(children)
        try:
            for pid in sorted(descendants - {parent_pid}):
                # Keep handles across taskkill: the launcher can exit before its
                # descendants, and looking up their PIDs later risks PID reuse.
                handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if handle:  # A child can already have exited since the snapshot.
                    self._handles.append(handle)
        except BaseException:
            self.close()
            raise

    def wait(self, timeout):
        # One shared budget, regardless of how many children the helper spawned.
        deadline = time.monotonic() + timeout
        for handle in self._handles:
            remaining_ms = max(0, math.ceil((deadline - time.monotonic()) * 1000))
            result = self._kernel.WaitForSingleObject(handle, remaining_ms)
            if result == 0x00000102:  # WAIT_TIMEOUT
                raise subprocess.TimeoutExpired("Windows helper descendants", timeout)
            if result != 0:  # WAIT_OBJECT_0 means termination has completed.
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        for handle in self._handles:
            self._kernel.CloseHandle(handle)
        self._handles.clear()
