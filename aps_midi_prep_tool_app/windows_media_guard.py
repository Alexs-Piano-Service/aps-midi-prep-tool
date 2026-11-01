"""Detect removable-media exchanges without trusting on-disk volume metadata.

Windows exposes a driver-maintained ULONG media-change count through
IOCTL_STORAGE_CHECK_VERIFY2 on a device opened with FILE_READ_ATTRIBUTES:
https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ni-ntddstor-ioctl_storage_check_verify2
https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ni-ntddstor-ioctl_storage_check_verify

Keep one handle for the entire save so reconnecting a device cannot silently
establish a fresh baseline. An unavailable or malformed counter must stop the
save; a matching serial number or file listing cannot replace this check.
"""


_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_ALL = 0x0007
_OPEN_EXISTING = 3
_IOCTL_STORAGE_CHECK_VERIFY2 = 0x002D0800


class WindowsMediaGuard:
    """Retain a device handle and reject any media-change uncertainty.

    ``device_path`` is a Windows device path such as ``\\\\.\\A:``.
    ``windows_api`` is the application's ``(ctypes, wintypes, kernel32)``
    tuple. Opening requests metadata access only, never raw read/write access.
    The caller must close the guard after all save and recovery actions.
    """

    def __init__(self, device_path, windows_api):
        self._ctypes, _wintypes, self._kernel32 = windows_api
        self.handle = None
        self._failure = None
        self._configure_api()
        self.handle = self._kernel32.CreateFileW(
            str(device_path), _FILE_READ_ATTRIBUTES, _FILE_SHARE_ALL, None,
            _OPEN_EXISTING, 0, None,
        )
        if self.handle is None or self.handle == self._ctypes.c_void_p(-1).value:
            self.handle = None
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            self.count = self._query_count()
        except BaseException:
            self.close()
            raise

    def _configure_api(self):
        ctypes = self._ctypes
        dword = ctypes.c_uint32
        handle = ctypes.c_void_p
        self._kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, dword, dword, handle, dword, dword, handle,
        ]
        self._kernel32.CreateFileW.restype = handle
        self._kernel32.DeviceIoControl.argtypes = [
            handle, dword, handle, dword, handle, dword,
            ctypes.POINTER(dword), handle,
        ]
        self._kernel32.DeviceIoControl.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [handle]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    def _query_count(self):
        ctypes = self._ctypes
        count = ctypes.c_uint32()
        returned = ctypes.c_uint32()
        if not self._kernel32.DeviceIoControl(
            self.handle, _IOCTL_STORAGE_CHECK_VERIFY2, None, 0,
            ctypes.byref(count), ctypes.sizeof(count), ctypes.byref(returned), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if returned.value != ctypes.sizeof(count):
            raise OSError("Windows did not return a reliable floppy media-change counter. Saving stopped.")
        return count.value

    def check(self):
        """Verify the original media; a failure permanently invalidates this guard."""
        if self._failure is not None:
            raise self._failure
        try:
            if self.handle is None:
                raise OSError("The floppy media-change guard is closed. Saving stopped.")
            if self._query_count() != self.count:
                raise OSError("The target floppy changed during saving; further writes were stopped.")
        except OSError as exc:
            self._failure = exc
            raise

    def close(self):
        """Release the retained handle once, including after a failed check."""
        if self.handle is not None:
            handle, self.handle = self.handle, None
            self._kernel32.CloseHandle(handle)
