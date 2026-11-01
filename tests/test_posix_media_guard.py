"""Active Linux driver checks distinguish disks with identical filesystem bytes."""

import ctypes
import errno
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import posix_media_guard as media


def test_regular_image_can_change_contents_without_changing_its_identity(tmp_path):
    image = tmp_path / "disk.img"
    image.write_bytes(b"original boot sector and songs")
    guard = media.PosixMediaGuard(image)
    try:
        assert guard.method == "regular_file"
        image.write_bytes(b"updated FAT and songs")
        guard.check()
    finally:
        guard.close()
    guard.close()
    with pytest.raises(OSError, match="closed"):
        guard.check()


def test_identical_image_replacement_permanently_invalidates_guard(tmp_path):
    image, original = tmp_path / "disk.img", tmp_path / "original.img"
    image.write_bytes(b"identical boot sector and songs")
    guard = media.PosixMediaGuard(image)
    try:
        # On Windows, renaming an open file is not generally available.
        if os.name == "nt":
            pytest.skip("POSIX descriptor lifetime test")
        image.rename(original)
        image.write_bytes(original.read_bytes())
        with pytest.raises(OSError, match="path changed") as caught:
            guard.check()
        image.unlink()
        original.rename(image)
        with pytest.raises(OSError) as again:
            guard.check()
        assert again.value is caught.value
    finally:
        guard.close()


@pytest.fixture
def device(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux block-device ABI and sysfs simulation")
    path = str(tmp_path / "device")
    sysfs = tmp_path / "sysfs-device"
    sysfs.mkdir()
    (sysfs / "removable").write_text("1\n")
    (sysfs / "events").write_text("media_change\n")
    (sysfs / "device" / "driver").mkdir(parents=True)
    # A real sysfs driver link resolves to a directory whose final name is sd.
    driver = tmp_path / "drivers" / "sd"
    driver.mkdir(parents=True)
    (sysfs / "device" / "driver").rmdir()
    try:
        (sysfs / "device" / "driver").symlink_to(driver, target_is_directory=True)
    except OSError:
        # The syscall mocks themselves are platform independent; a directory
        # named sd plus an overridden validator path avoids requiring Windows
        # symlink privileges merely to exercise Linux driver responses.
        pytest.skip("Symlinks required for simulated sysfs")
    state = SimpleNamespace(
        path=path, sysfs=sysfs, device_number=os.makedev(8, 0),
        generation=4, diskseq=100, pending_sequence=None, drive_flags=0,
        params_flags=0, ioctl_error=None, malformed=False, open_error=None,
        opened=[], closed=[], queries=[], descriptors=set(), next_descriptor=100000,
        path_present=True, node_inode=9, native_states=[],
    )
    real_open, real_close = os.open, os.close
    real_fstat, real_stat = os.fstat, os.stat

    def info():
        return SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_dev=7,
                               st_ino=state.node_inode, st_rdev=state.device_number)

    def open_device(path, flags, *args, **kwargs):
        if os.fspath(path) != state.path:
            return real_open(path, flags, *args, **kwargs)
        if state.open_error is not None:
            raise OSError(state.open_error, "Device open failed")
        descriptor = state.next_descriptor
        state.next_descriptor += 1
        state.opened.append((descriptor, flags))
        state.descriptors.add(descriptor)
        # The kernel refreshes sd media events on a new blocking open. Merely
        # querying BLKGETDISKSEQ intentionally leaves the old value in place.
        if state.pending_sequence is not None:
            state.diskseq = state.pending_sequence
            state.pending_sequence = None
        return descriptor

    def close_device(descriptor):
        if descriptor not in state.descriptors:
            return real_close(descriptor)
        state.closed.append(descriptor)

    def descriptor_stat(descriptor):
        return info() if descriptor in state.descriptors else real_fstat(descriptor)

    def path_stat(path, *args, **kwargs):
        if os.fspath(path) != state.path:
            return real_stat(path, *args, **kwargs)
        if not state.path_present:
            raise FileNotFoundError(state.path)
        return info()

    def ioctl(descriptor, request, buffer):
        assert descriptor in state.descriptors
        state.queries.append((descriptor, request))
        if state.ioctl_error is not None:
            raise OSError(state.ioctl_error, "Media query failed")
        if state.malformed:
            return b""
        if request == media._FDGETDRVPRM:
            result = media._FloppyDriveParams(flags=state.params_flags)
        elif request == media._FDPOLLDRVSTAT:
            flags, generation = (state.native_states.pop(0) if state.native_states
                                 else (state.drive_flags, state.generation))
            result = media._FloppyDriveState(flags=flags, generation=generation)
        else:
            assert request == media._BLKGETDISKSEQ
            result = ctypes.c_uint64(state.diskseq)
        assert len(buffer) == ctypes.sizeof(result)
        return bytes(result)

    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr(media.os, "open", open_device)
    monkeypatch.setattr(media.os, "close", close_device)
    monkeypatch.setattr(media.os, "fstat", descriptor_stat)
    monkeypatch.setattr(media.os, "stat", path_stat)
    monkeypatch.setattr(media, "_ioctl", ioctl)
    monkeypatch.setattr(media, "_sd_device_directory", lambda _info: sysfs)
    return state


def test_sd_checks_refresh_media_events_before_reading_sequence(device):
    guard = media.PosixMediaGuard(device.path)
    try:
        assert guard.method == "linux_disk_sequence"
        guard.check()
        assert len(device.opened) == 2
        assert device.closed == [device.opened[1][0]]
        assert all(not flags & getattr(os, "O_NONBLOCK", 0) for _fd, flags in device.opened)
        device.pending_sequence = guard.count + 1
        assert guard._disk_sequence(device.opened[0][0]) == guard.count
        with pytest.raises(OSError, match="changed") as caught:
            guard.check()
        assert len(device.opened) == 3
        assert device.closed == [item[0] for item in device.opened[1:]]
        device.diskseq = guard.count
        with pytest.raises(OSError) as again:
            guard.check()
        assert again.value is caught.value
        assert len(device.opened) == 3
    finally:
        guard.close()
    assert sorted(device.closed) == sorted(fd for fd, _flags in device.opened)


@pytest.mark.parametrize("unsupported", ["partition", "fixed", "driver", "events", "query"])
def test_unsupported_sd_checks_fail_closed_and_close_the_device(device, unsupported):
    if unsupported == "partition":
        (device.sysfs / "partition").write_text("1\n")
    elif unsupported == "fixed":
        (device.sysfs / "removable").write_text("0\n")
    elif unsupported == "driver":
        (device.sysfs / "device" / "driver").unlink()
        (device.sysfs / "device" / "driver").mkdir()
    elif unsupported == "events":
        (device.sysfs / "events").write_text("\n")
    else:
        device.ioctl_error = errno.ENOTTY
    with pytest.raises(OSError):
        media.PosixMediaGuard(device.path)
    assert device.closed == [device.opened[0][0]]


@pytest.mark.parametrize("failure", ["missing", "inode", "open", "ioctl", "short", "sysfs"])
def test_uncertain_sd_check_remains_failed_after_device_recovers(device, failure):
    guard = media.PosixMediaGuard(device.path)
    try:
        if failure == "missing":
            device.path_present = False
        elif failure == "inode":
            device.node_inode += 1
        elif failure == "open":
            device.open_error = errno.ENOMEDIUM
        elif failure == "ioctl":
            device.ioctl_error = errno.EIO
        elif failure == "short":
            device.malformed = True
        else:
            (device.sysfs / "events").write_text("")
        with pytest.raises(OSError) as caught:
            guard.check()
        device.path_present, device.node_inode = True, 9
        device.open_error = device.ioctl_error = None
        device.malformed = False
        (device.sysfs / "events").write_text("media_change\n")
        with pytest.raises(OSError) as again:
            guard.check()
        assert again.value is caught.value
    finally:
        guard.close()
    assert sorted(device.closed) == sorted(fd for fd, _flags in device.opened)


@pytest.mark.parametrize("failure", ["generation", "verify", "newchange", "changed", "open_fail", "broken_dcl"])
def test_native_floppy_actively_polls_and_rejects_change_uncertainty(device, failure):
    device.device_number = os.makedev(2, 0)
    guard = media.PosixMediaGuard(device.path)
    try:
        assert guard.method == "linux_floppy_generation"
        assert [request for _fd, request in device.queries] == [media._FDGETDRVPRM, media._FDPOLLDRVSTAT]
        guard.check()
        if failure == "generation":
            device.generation += 1
        elif failure == "broken_dcl":
            device.params_flags = media._FD_BROKEN_DCL
        else:
            device.drive_flags = 1 << {"verify": 1, "newchange": 2, "changed": 4, "open_fail": 6}[failure]
        with pytest.raises(OSError) as caught:
            guard.check()
        device.generation = guard.count
        device.drive_flags = device.params_flags = 0
        with pytest.raises(OSError) as again:
            guard.check()
        assert again.value is caught.value
        assert len(device.opened) == 1
    finally:
        guard.close()
    assert device.closed == [device.opened[0][0]]


@pytest.mark.parametrize("uncertain", ["broken_dcl", "pending", "unsupported", "short"])
def test_native_floppy_requires_valid_initial_hardware_state(device, uncertain):
    device.device_number = os.makedev(2, 0)
    if uncertain == "broken_dcl":
        device.params_flags = media._FD_BROKEN_DCL
    elif uncertain == "pending":
        device.drive_flags = 1 << 4
    elif uncertain == "unsupported":
        device.ioctl_error = errno.ENOTTY
    else:
        device.malformed = True
    with pytest.raises(OSError):
        media.PosixMediaGuard(device.path)
    assert device.closed == [device.opened[0][0]]


@pytest.mark.parametrize("phase", ["initial", "check"])
def test_native_transient_normal_io_latch_is_actively_repolled(device, phase):
    device.device_number = os.makedev(2, 0)
    guard = media.PosixMediaGuard(device.path) if phase == "check" else None
    # The kernel can schedule ordinary I/O between polling and copying out
    # state, transiently reasserting NEWCHANGE without a media change.
    writable = 1 << 5
    device.native_states = [(media._FD_NEWCHANGE | writable, device.generation),
                            (writable, device.generation)]
    device.queries.clear()
    try:
        if guard is None:
            guard = media.PosixMediaGuard(device.path)
        else:
            guard.check()
        assert guard.count == device.generation
        assert not device.native_states
        assert sum(request == media._FDPOLLDRVSTAT for _fd, request in device.queries) == 2
    finally:
        if guard is not None:
            guard.close()


@pytest.mark.parametrize("phase", ["initial", "check"])
@pytest.mark.parametrize("failure", ["persistent", "generation", "verify", "changed", "open_fail"])
def test_native_repoll_never_accepts_uncertainty_or_adopts_a_changed_generation(device, phase, failure):
    device.device_number = os.makedev(2, 0)
    guard = media.PosixMediaGuard(device.path) if phase == "check" else None
    expected = device.generation
    first = (media._FD_NEWCHANGE, expected)
    if failure == "persistent":
        device.native_states = [first] * 3
    elif failure == "generation":
        device.native_states = [first, (media._FD_NEWCHANGE, expected + 1)]
    else:
        flag = 1 << {"verify": 1, "changed": 4, "open_fail": 6}[failure]
        device.native_states = [(media._FD_NEWCHANGE | flag, expected)]
    # This apparent recovery must never be reached after an unsafe sample.
    device.native_states.append((0, expected))
    try:
        with pytest.raises(OSError) as caught:
            if guard is None:
                media.PosixMediaGuard(device.path)
            else:
                guard.check()
        assert device.native_states == [(0, expected)]
        if guard is not None:
            with pytest.raises(OSError) as again:
                guard.check()
            assert again.value is caught.value
            assert device.native_states == [(0, expected)]
    finally:
        if guard is not None:
            guard.close()
    assert device.closed == [device.opened[0][0]]


def test_non_linux_block_devices_do_not_fall_back_to_metadata(device, monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "darwin")
    with pytest.raises(OSError, match="supported"):
        media.PosixMediaGuard(device.path)
    assert not device.queries
    assert device.closed == [device.opened[0][0]]
