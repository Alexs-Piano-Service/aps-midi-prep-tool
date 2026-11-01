"""Driver media-change counters protect saves even when disks have cloned metadata."""

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.windows_media_guard import WindowsMediaGuard


def _windows_error(code):
    error = OSError(f"[WinError {code}] simulated device failure")
    error.winerror = code
    return error


@pytest.fixture
def device():
    state = SimpleNamespace(
        count=10, returned=4, error=None, handle=123,
        opens=[], queries=[], closes=[],
    )

    def create_file(*args):
        state.opens.append(args)
        return state.handle

    def query(handle, control, incoming, incoming_size, output, output_size, returned, overlapped):
        state.queries.append((handle, control, incoming, incoming_size, output_size, overlapped))
        assert ctypes.sizeof(output._obj) == ctypes.sizeof(returned._obj) == 4
        if state.error is not None:
            return False
        output._obj.value = state.count
        returned._obj.value = state.returned
        return True

    def close(handle):
        state.closes.append(handle)
        return True

    # Explicit ctypes widths make these tests meaningful on non-Windows hosts.
    ctypes_api = SimpleNamespace(
        c_uint32=ctypes.c_uint32, c_void_p=ctypes.c_void_p, c_wchar_p=ctypes.c_wchar_p,
        c_int=ctypes.c_int, POINTER=ctypes.POINTER, byref=ctypes.byref, sizeof=ctypes.sizeof,
        WinError=_windows_error, get_last_error=lambda: state.error or 5,
    )
    state.api = (ctypes_api, wintypes, SimpleNamespace(
        CreateFileW=create_file, DeviceIoControl=query, CloseHandle=close,
    ))
    return state


@pytest.mark.parametrize("count", [0, 10, 0xFFFFFFFF])
def test_guard_uses_one_metadata_only_handle_and_four_byte_counter(device, count):
    device.count = count
    guard = WindowsMediaGuard(r"\\.\A:", device.api)
    assert guard.count == count
    guard.check()
    guard.check()
    assert device.opens == [(r"\\.\A:", 0x0080, 0x0007, None, 3, 0, None)]
    assert device.queries == [(123, 0x002D0800, None, 0, 4, None)] * 3
    guard.close()
    guard.close()
    assert device.closes == [123]


@pytest.mark.parametrize("handle", [None, ctypes.c_void_p(-1).value])
def test_failed_open_does_not_query_or_close_invalid_handle(device, handle):
    device.handle = handle
    device.error = 5
    with pytest.raises(OSError) as caught:
        WindowsMediaGuard(r"\\.\A:", device.api)
    assert caught.value.winerror == 5
    assert device.queries == device.closes == []


@pytest.mark.parametrize("failure", ["unsupported", "empty", "short", "oversized"])
def test_unavailable_initial_counter_closes_handle_without_fallback(device, failure):
    if failure == "unsupported":
        device.error = 50
    else:
        device.returned = {"empty": 0, "short": 3, "oversized": 8}[failure]
    with pytest.raises(OSError):
        WindowsMediaGuard(r"\\.\A:", device.api)
    assert len(device.opens) == len(device.queries) == 1
    assert device.closes == [123]


@pytest.mark.parametrize("failure", ["changed", "wrapped", "io_error", "short_reply"])
def test_uncertain_media_permanently_invalidates_guard(device, failure):
    if failure == "wrapped":
        device.count = 0xFFFFFFFF
    guard = WindowsMediaGuard(r"\\.\A:", device.api)
    if failure == "changed":
        device.count += 1
    elif failure == "wrapped":
        device.count = 0
    elif failure == "io_error":
        device.error = 1110
    else:
        device.returned = 0

    with pytest.raises(OSError) as first:
        guard.check()
    assert len(device.queries) == 2

    # Returning the original media or recovering the device must not resume a
    # partially completed save or authorize recovery writes on uncertain media.
    device.count = guard.count
    device.error = None
    device.returned = 4
    with pytest.raises(OSError) as again:
        guard.check()
    assert again.value is first.value
    assert len(device.queries) == 2
    assert len(device.opens) == 1
    guard.close()
    assert device.closes == [123]


def test_closed_guard_cannot_authorize_operations(device):
    guard = WindowsMediaGuard(r"\\.\A:", device.api)
    guard.close()
    with pytest.raises(OSError, match="closed"):
        guard.check()
    assert len(device.queries) == 1
    assert device.closes == [123]
