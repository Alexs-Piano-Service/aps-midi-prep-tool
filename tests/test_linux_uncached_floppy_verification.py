"""Direct verification tests use mocked descriptors and memory, never devices."""

import builtins
import ctypes
import errno
import io
import mmap
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Linux direct-I/O verification tests", allow_module_level=True)

fcntl = pytest.importorskip("fcntl", reason="Linux direct-I/O verification requires fcntl")

from aps_midi_prep_tool_app import floppy_image as image


def _device(monkeypatch, payload, *, cached=None, max_read=None, fail_at=None,
            open_error=None, ioctl_error=None, sector_size=512, direct_mode=stat.S_IFBLK):
    target = "/mock-linux-floppy"
    buffered_fd, direct_fd = 901, 902
    state = SimpleNamespace(reads=[], opened=[], closed=[], buffered_reads=0,
                            buffered_closed=0, buffers=[])

    class BufferedReader(io.BytesIO):
        def fileno(self):
            return buffered_fd

        def read(self, size=-1):
            state.buffered_reads += 1
            return super().read(size)

        def close(self):
            if not self.closed:
                state.buffered_closed += 1
            return super().close()

    def buffered_open(path, mode="r", **kwargs):
        if os.fspath(path) == target:
            assert mode == "rb" and kwargs == {"buffering": 0}
            return BufferedReader(payload if cached is None else cached)
        return builtins.open(path, mode, **kwargs)

    def direct_open(path, flags):
        assert path == target
        assert flags & os.O_DIRECT
        assert not flags & os.O_WRONLY and not flags & os.O_RDWR
        state.opened.append(flags)
        if open_error is not None:
            raise open_error
        return direct_fd

    def fstat(fd):
        assert fd in (buffered_fd, direct_fd)
        return SimpleNamespace(st_mode=stat.S_IFBLK if fd == buffered_fd else direct_mode)

    def ioctl(fd, request, buffer, mutate):
        assert (fd, request, mutate) == (direct_fd, 0x1268, True)
        if ioctl_error is not None:
            raise ioctl_error
        buffer[0] = sector_size
        return 0

    def preadv(fd, views, offset):
        assert fd == direct_fd and len(views) == 1
        view = views[0]
        assert offset % sector_size == len(view) % sector_size == 0
        assert ctypes.addressof(ctypes.c_char.from_buffer(view)) % sector_size == 0
        state.reads.append((offset, len(view)))
        if fail_at is not None and offset >= fail_at[0]:
            if isinstance(fail_at[1], Exception):
                raise fail_at[1]
            return fail_at[1]
        count = min(len(view), len(payload) - offset)
        if max_read is not None:
            count = min(count, max_read)
        view[:count] = payload[offset:offset + count]
        return count

    def close(fd):
        assert fd == direct_fd
        state.closed.append(fd)

    actual_mmap = mmap.mmap

    def allocation(*args, **kwargs):
        assert kwargs["flags"] == mmap.MAP_SHARED
        result = actual_mmap(*args, **kwargs)
        state.buffers.append(result)
        return result

    fake_os = SimpleNamespace(**{**vars(os), "name": "posix", "open": direct_open,
                               "fstat": fstat, "preadv": preadv, "close": close})
    monkeypatch.setattr(image, "os", fake_os)
    monkeypatch.setattr(image, "sys", SimpleNamespace(**{**vars(sys), "platform": "linux"}))
    monkeypatch.setattr(image, "open", buffered_open, raising=False)
    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    monkeypatch.setattr(mmap, "mmap", allocation)
    return target, state


@pytest.mark.parametrize("direct_corrupt", [False, True], ids=["bypass-stale-cache", "reject-bad-media-despite-good-cache"])
def test_physical_verification_uses_direct_bytes_instead_of_buffered_cache(tmp_path, monkeypatch, direct_corrupt):
    prepared = tmp_path / "prepared.img"
    selected = image.DISK_FORMAT_BY_KEY["ibm.720"]
    image.create_blank_floppy_image(prepared, selected)
    expected = prepared.read_bytes()
    bad = bytes([expected[0] ^ 1]) + expected[1:]
    target, state = _device(monkeypatch, bad if direct_corrupt else expected,
                            cached=expected if direct_corrupt else bad)
    drive = image.FloppyDriveInfo(target, len(expected))
    if direct_corrupt:
        with pytest.raises(image.FloppyImageError, match="physical floppy differs"):
            image._verify_physical_floppy_contents(prepared, "floppy_usb", drive, selected,
                                                   verify_entire_image=True)
    else:
        result = image._verify_physical_floppy_contents(prepared, "floppy_usb", drive, selected,
                                                       verify_entire_image=True)
        assert result["confidence"] == "contents_verified"
    assert state.buffered_reads == 0 and state.buffered_closed == 1
    assert len(state.opened) == 1 and len(state.closed) == 1
    assert state.reads and all(buffer.closed for buffer in state.buffers)


@pytest.mark.parametrize("max_read", [None, 512])
def test_aligned_direct_reads_handle_short_sector_runs_and_preserve_bytes(tmp_path, monkeypatch, max_read):
    payload = bytes(range(256)) * 16
    target, state = _device(monkeypatch, payload, max_read=max_read)
    output = tmp_path / "readback.img"
    diagnostics = {}
    image._read_block_device(target, output, len(payload), uncached=True, diagnostics=diagnostics)
    assert output.read_bytes() == payload
    assert diagnostics["read_method"] == "linux_direct_io"
    assert diagnostics["os_cache_bypassed"] is True
    assert diagnostics["exact_capture_completed"] is True
    assert [offset for offset, _length in state.reads] == ([0] if max_read is None else list(range(0, len(payload), 512)))
    assert len(state.closed) == 1 and all(buffer.closed for buffer in state.buffers)
    assert state.buffered_reads == 0


@pytest.mark.parametrize("result", [0, 257, 8192, None])
def test_invalid_direct_read_counts_cannot_pass_verification(tmp_path, monkeypatch, result):
    target, state = _device(monkeypatch, b"a" * 2048, fail_at=(0, result))
    diagnostics = {}
    with pytest.raises(image.FloppyImageError, match="incomplete or unaligned"):
        image._read_block_device(target, tmp_path / "readback.img", 2048,
                                 uncached=True, diagnostics=diagnostics)
    assert diagnostics["exact_capture_completed"] is False
    assert diagnostics["failed_read_offset_bytes"] == 0
    assert state.buffered_reads == 0
    assert len(state.closed) == 1 and all(buffer.closed for buffer in state.buffers)


@pytest.mark.parametrize("error_code", [errno.EIO, errno.EINVAL])
def test_direct_io_error_after_partial_capture_never_retries_buffered(tmp_path, monkeypatch, error_code):
    payload = b"a" * 2048
    target, state = _device(monkeypatch, payload, max_read=512,
                            fail_at=(512, OSError(error_code, "Injected direct-I/O failure")))
    output = tmp_path / "readback.img"
    with pytest.raises(image.FloppyImageError, match="Injected direct-I/O failure") as failure:
        image._read_block_device(target, output, len(payload), uncached=True)
    assert output.read_bytes() == payload[:512]
    assert failure.value.read_diagnostics["exact_capture_completed"] is False
    assert failure.value.read_diagnostics["failed_read_offset_bytes"] == 512
    assert state.buffered_reads == 0
    assert len(state.closed) == 1 and all(buffer.closed for buffer in state.buffers)


@pytest.mark.parametrize("failure", ["open", "sector_query", "changed_source"])
def test_unavailable_direct_io_fails_without_buffered_fallback(tmp_path, monkeypatch, failure):
    options = {
        "open": {"open_error": OSError(errno.EINVAL, "Direct reads unavailable")},
        "sector_query": {"ioctl_error": OSError(errno.ENOTTY, "Sector query unavailable")},
        "changed_source": {"direct_mode": stat.S_IFREG},
    }[failure]
    target, state = _device(monkeypatch, b"a" * 512, **options)
    output = tmp_path / "readback.img"
    with pytest.raises(image.FloppyImageError):
        image._read_block_device(target, output, 512, uncached=True)
    assert not output.exists()
    assert state.buffered_reads == 0 and state.buffered_closed == 1
    assert len(state.closed) == (0 if failure == "open" else 1)


@pytest.mark.parametrize("size", [-1, 0, 513])
def test_misaligned_or_unbounded_verification_extent_is_rejected_before_read(tmp_path, monkeypatch, size):
    target, state = _device(monkeypatch, b"a" * 2048)
    output = tmp_path / "readback.img"
    with pytest.raises(image.FloppyImageError, match="positive, sector-aligned"):
        image._read_block_device(target, output, size, uncached=True)
    assert not state.reads and not output.exists()
    assert state.buffered_reads == 0 and len(state.closed) == 1


def test_cancellation_between_direct_reads_cleans_buffers_and_descriptor(tmp_path, monkeypatch):
    target, state = _device(monkeypatch, b"a" * 2048, max_read=512)
    with pytest.raises(image.FloppyOperationCancelled) as failure:
        image._read_block_device(target, tmp_path / "readback.img", 2048, uncached=True,
                                 cancel_callback=lambda: bool(state.reads))
    assert failure.value.read_diagnostics["cancelled"] is True
    assert failure.value.read_diagnostics["exact_capture_completed"] is False
    assert len(state.reads) == len(state.closed) == 1
    assert state.buffered_reads == 0 and all(buffer.closed for buffer in state.buffers)


def test_cancel_before_direct_open_never_reads_or_allocates(tmp_path, monkeypatch):
    target, state = _device(monkeypatch, b"a" * 512)
    with pytest.raises(image.FloppyOperationCancelled):
        image._read_block_device(target, tmp_path / "readback.img", 512, uncached=True,
                                 cancel_callback=lambda: True)
    assert not state.opened and not state.reads and not state.buffers
    assert state.buffered_reads == 0 and state.buffered_closed == 1


def test_regular_files_keep_normal_reads_for_offline_verification(tmp_path):
    source, output = tmp_path / "source.img", tmp_path / "readback.img"
    source.write_bytes(b"An ordinary fixture need not have sector-aligned length.")
    image._read_block_device(source, output, source.stat().st_size, uncached=True)
    assert output.read_bytes() == source.read_bytes()


def test_non_linux_posix_reads_keep_existing_behavior(tmp_path, monkeypatch):
    payload = b"a" * 1024
    target, state = _device(monkeypatch, payload)
    image.sys.platform = "darwin"
    output = tmp_path / "readback.img"
    image._read_block_device(target, output, len(payload), uncached=True)
    assert output.read_bytes() == payload
    assert state.buffered_reads > 0 and not state.opened and not state.reads
