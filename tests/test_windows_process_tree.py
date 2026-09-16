"""Exercise descendant selection, bounded waits, and Win32 handle ownership."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from aps_midi_prep_tool_app import subprocess_utils as helpers


class ProcessSnapshot:
    def __init__(self, monkeypatch, *, gone=()):
        # Deliberately put a grandchild before its parent in the snapshot.
        self.entries = [(30, 20), (40, 1), (10, 1), (20, 10), (50, 40)]
        self.index = 0
        self.CreateToolhelp32Snapshot = Mock(return_value=100)
        self.Process32FirstW = Mock(side_effect=self.first)
        self.Process32NextW = Mock(side_effect=self.next)
        self.OpenProcess = Mock(side_effect=lambda access, inherit, pid: 0 if pid in gone else pid + 1000)
        self.WaitForSingleObject = Mock(return_value=0)
        self.CloseHandle = Mock(return_value=True)
        monkeypatch.setattr(helpers.ctypes, "WinDLL", lambda *args, **kwargs: self, raising=False)

    def current(self, entry):
        if self.index == len(self.entries):
            return False
        entry._obj.th32ProcessID, entry._obj.th32ParentProcessID = self.entries[self.index]
        return True

    def first(self, snapshot, entry):
        assert snapshot == 100
        assert entry._obj.dwSize == helpers.ctypes.sizeof(entry._obj)
        self.index = 0
        return self.current(entry)

    def next(self, snapshot, entry):
        self.index += 1
        return self.current(entry)


def test_waits_for_children_and_grandchildren_without_opening_unrelated_processes(monkeypatch):
    kernel = ProcessSnapshot(monkeypatch)
    tree = helpers.WindowsProcessTreeWaiter(10)
    assert kernel.CloseHandle.call_args_list == [call(100)]
    assert kernel.OpenProcess.call_args_list == [call(0x00100000, False, 20), call(0x00100000, False, 30)]
    # Waiting uses the retained process handles, even if the snapshot's PIDs
    # have since disappeared. No PID lookup or broad process-name kill occurs.
    kernel.OpenProcess.side_effect = AssertionError("Must retain original handles")
    tree.wait(timeout=2)
    assert [call.args[0] for call in kernel.WaitForSingleObject.call_args_list] == [1020, 1030]
    tree.close()
    tree.close()
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [100, 1020, 1030]


def test_already_exited_child_does_not_hide_live_grandchild(monkeypatch):
    kernel = ProcessSnapshot(monkeypatch, gone={20})
    tree = helpers.WindowsProcessTreeWaiter(10)
    try:
        tree.wait(timeout=2)
        assert [call.args[0] for call in kernel.WaitForSingleObject.call_args_list] == [1030]
    finally:
        tree.close()


def test_descendants_share_one_timeout_budget(monkeypatch):
    kernel = ProcessSnapshot(monkeypatch)
    tree = helpers.WindowsProcessTreeWaiter(10)
    times = iter([100.0, 100.0, 101.5])
    monkeypatch.setattr(helpers, "time", SimpleNamespace(monotonic=lambda: next(times)))
    kernel.WaitForSingleObject.side_effect = [0, 0x102]
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            tree.wait(timeout=2)
        assert kernel.WaitForSingleObject.call_args_list == [call(1020, 2000), call(1030, 500)]
    finally:
        tree.close()
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [100, 1020, 1030]


def test_partial_handle_capture_is_closed_on_error(monkeypatch):
    kernel = ProcessSnapshot(monkeypatch)
    kernel.OpenProcess.side_effect = [1020, OSError("Could not open process")]
    with pytest.raises(OSError):
        helpers.WindowsProcessTreeWaiter(10)
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [100, 1020]


def test_snapshot_handle_is_closed_on_enumeration_error(monkeypatch):
    kernel = ProcessSnapshot(monkeypatch)
    kernel.Process32NextW.side_effect = OSError("Could not enumerate processes")
    with pytest.raises(OSError):
        helpers.WindowsProcessTreeWaiter(10)
    kernel.CloseHandle.assert_called_once_with(100)
