"""Linux floppy reads use mocked devices; these tests never access hardware."""

import ctypes
import errno
import mmap
import os
import stat
import sys
from types import SimpleNamespace

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Linux direct floppy reads", allow_module_level=True)

fcntl = pytest.importorskip("fcntl")

from aps_midi_prep_tool_app import floppy_image as image


def _mock_device(monkeypatch, *, bad_boot=False, max_read=None, read_count=None,
                 ioctl_error=None, descriptor_mode=stat.S_IFBLK, sector_size=512):
    target, descriptor = "/mock-sector-floppy", 913
    payload = bytes(range(256)) * 16
    state = SimpleNamespace(opened=[], closed=[], reads=[], buffers=[])

    def open_device(path, flags):
        assert os.fspath(path) == target
        assert flags & os.O_DIRECT
        assert flags & os.O_ACCMODE == os.O_RDONLY
        assert flags & os.O_CLOEXEC
        state.opened.append(flags)
        return descriptor

    def descriptor_stat(fd):
        assert fd == descriptor
        return SimpleNamespace(st_mode=descriptor_mode)

    def device_stat(path, *args, **kwargs):
        assert os.fspath(path) == target
        return SimpleNamespace(st_mode=stat.S_IFBLK)

    def ioctl(fd, request, buffer, mutate=True):
        assert (fd, request, mutate) == (descriptor, 0x1268, True)
        if ioctl_error:
            raise ioctl_error
        buffer[0] = sector_size
        return 0

    def read_vector(fd, views, offset):
        assert fd == descriptor and len(views) == 1
        view = views[0]
        assert offset % sector_size == len(view) % sector_size == 0
        assert ctypes.addressof(ctypes.c_char.from_buffer(view)) % sector_size == 0
        state.reads.append((offset, len(view)))
        if bad_boot and offset == 0:
            raise OSError(errno.EIO, "Protected boot sector cannot be read")
        if read_count is not None:
            return read_count
        count = min(len(view), len(payload) - offset)
        if max_read is not None:
            count = min(count, max_read)
        view[:count] = payload[offset:offset + count]
        return count

    def close(fd):
        assert fd == descriptor
        state.closed.append(fd)

    def buffered_read(*args):
        pytest.fail("Physical sector reads must not use buffered pread")

    actual_mmap = mmap.mmap

    def allocate(*args, **kwargs):
        assert kwargs.get("flags") == mmap.MAP_SHARED
        buffer = actual_mmap(*args, **kwargs)
        state.buffers.append(buffer)
        return buffer

    fake_os = SimpleNamespace(**{
        **vars(os), "name": "posix", "open": open_device,
        "stat": device_stat, "fstat": descriptor_stat,
        "preadv": read_vector, "pread": buffered_read, "close": close,
    })
    monkeypatch.setattr(image, "os", fake_os)
    monkeypatch.setattr(image, "sys", SimpleNamespace(**{**vars(sys), "platform": "linux"}))
    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    monkeypatch.setattr(mmap, "mmap", allocate)
    return target, payload, state


def _assert_released(state):
    assert len(state.closed) == 1
    assert all(buffer.closed for buffer in state.buffers)


def test_readable_fat_sector_is_not_combined_with_protected_boot_sector(monkeypatch):
    target, payload, state = _mock_device(monkeypatch, bad_boot=True)
    reader = image._open_block_device_for_read(target)
    try:
        with pytest.raises((OSError, image.FloppyImageError), match="Protected boot"):
            image._read_device_exact(reader, 0, 512, "boot sector")
        assert image._read_device_exact(reader, 512, 512, "first FAT sector") == payload[512:1024]
        assert state.reads == [(0, 512), (512, 512)]
    finally:
        image._close_block_device(reader)
    _assert_released(state)


@pytest.mark.parametrize("offset,length,physical_range", [
    (1024, 137, (1024, 512)),
    (529, 99, (512, 512)),
    (1000, 100, (512, 1024)),
])
def test_partial_file_extents_expand_only_to_their_device_sectors(monkeypatch, offset, length, physical_range):
    target, payload, state = _mock_device(monkeypatch)
    reader = image._LinuxFloppyReadHandle(target)
    try:
        assert reader.read_at(offset, length, "file tail") == payload[offset:offset + length]
        assert state.reads == [physical_range]
    finally:
        reader.close()
    _assert_released(state)


def test_short_aligned_reads_preserve_data_without_synthesizing_padding(monkeypatch):
    target, payload, state = _mock_device(monkeypatch, max_read=512)
    reader = image._LinuxFloppyReadHandle(target)
    try:
        actual = image._read_device_exact(reader, 512, 1536, "allocated file sectors")
        assert actual == payload[512:2048]
        assert [offset for offset, _length in state.reads] == [512, 1024, 1536]
    finally:
        reader.close()
    _assert_released(state)


@pytest.mark.parametrize("count", [0, 257, 2048])
def test_invalid_direct_transfer_cannot_be_returned_as_complete_file_bytes(monkeypatch, count):
    target, _payload, state = _mock_device(monkeypatch, read_count=count)
    reader = image._LinuxFloppyReadHandle(target)
    try:
        with pytest.raises(image.FloppyImageError):
            image._read_device_exact(reader, 512, 512, "one file sector")
    finally:
        reader.close()
    assert state.reads == [(512, 512)]
    _assert_released(state)


@pytest.mark.parametrize("options", [
    {"ioctl_error": OSError(errno.ENOTTY, "Sector query unavailable")},
    {"descriptor_mode": stat.S_IFREG},
    {"sector_size": 0},
    {"sector_size": 513},
])
def test_invalid_device_setup_closes_descriptor_before_any_read(monkeypatch, options):
    target, _payload, state = _mock_device(monkeypatch, **options)
    with pytest.raises((OSError, image.FloppyImageError)):
        image._LinuxFloppyReadHandle(target)
    assert not state.reads
    _assert_released(state)


def test_regular_image_files_keep_portable_reads(tmp_path, monkeypatch):
    source = tmp_path / "short.img"
    source.write_bytes(b"Regular image bytes do not require sector alignment.")

    def unexpected_direct_handle(*args, **kwargs):
        pytest.fail("A regular image must not open a direct block-device reader")

    monkeypatch.setattr(image, "_LinuxFloppyReadHandle", unexpected_direct_handle)
    reader = image._open_block_device_for_read(source)
    try:
        assert image._read_device_exact(reader, 3, 19, "ordinary image") == source.read_bytes()[3:22]
    finally:
        image._close_block_device(reader)
