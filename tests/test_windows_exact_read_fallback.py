"""Exact Windows imaging retries smaller reads without inventing sector bytes."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


SECTOR_SIZE = 512
CHUNK_SIZE = 64 * 1024


def _payload(size):
    return b"".join(index.to_bytes(2, "little") * 256 for index in range(size // SECTOR_SIZE))


def _failure(code):
    error = image.FloppyImageError("Le média disque n’est pas reconnu.")
    error.winerror = code
    return error


def _install_windows_volume(monkeypatch, reader):
    devices = []

    class Volume:
        def __init__(self, _path, *, write=False):
            assert write is False
            self.calls = []
            self.closed = False
            devices.append(self)

        def read_at_recovery(self, offset, size, label, **_kwargs):
            self.calls.append((offset, size))
            assert label == "floppy image"
            return reader(offset, size)

        def close(self):
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_WindowsRecoveryVolumeHandle", Volume)
    return devices


def test_exact_reader_splits_size_rejections_to_correct_sector_offsets(monkeypatch):
    original = _payload(2 * CHUNK_SIZE + 2 * SECTOR_SIZE)

    def read(offset, size):
        if size > SECTOR_SIZE:
            raise _failure(1785)
        return original[offset:offset + size]

    devices = _install_windows_volume(monkeypatch, read)
    diagnostics = {}

    recovered = image._read_windows_block_device_bytes("A:", len(original), diagnostics=diagnostics)

    assert recovered == original
    calls = devices[0].calls
    assert [offset for offset, size in calls if size == SECTOR_SIZE] == list(
        range(0, len(original), SECTOR_SIZE)
    )
    assert len(calls) == 2 * (2 * (CHUNK_SIZE // SECTOR_SIZE) - 1) + 3
    assert diagnostics["split_read_requests"] == len(calls) - 3
    assert devices[0].closed
    assert diagnostics["first_failed_read"]["offset_bytes"] == 0
    assert diagnostics["first_failed_read"]["length_bytes"] == CHUNK_SIZE
    assert diagnostics["first_failed_read"]["winerror"] == 1785
    assert diagnostics["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": len(original)}]
    assert diagnostics["exact_capture_completed"] is True
    assert not diagnostics.get("final_failed_read")


def test_exact_capture_accepts_successful_smaller_requests_and_preserves_source(tmp_path, monkeypatch):
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    original = _payload(disk_format.size_bytes)
    source = tmp_path / "source.img"
    source.write_bytes(original)
    output = tmp_path / "captured.img"
    output.write_bytes(b"previous exact capture")

    def read(offset, size):
        if size == CHUNK_SIZE:
            raise _failure(1785)
        return original[offset:offset + size]

    devices = _install_windows_volume(monkeypatch, read)
    drive = image.FloppyDriveInfo("A:", len(original))

    assert image.capture_floppy_drive_image(drive, output) == str(output)

    assert devices[0].calls[:3] == [(0, CHUNK_SIZE), (0, CHUNK_SIZE // 2), (CHUNK_SIZE // 2, CHUNK_SIZE // 2)]
    assert all(device.closed for device in devices)
    assert output.read_bytes() == original
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source, output}


@pytest.mark.parametrize("failure", ["error", "eof"])
def test_exact_capture_still_fails_at_missing_sector_and_keeps_destination(tmp_path, monkeypatch, failure):
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    original = _payload(disk_format.size_bytes)
    bad_offset = CHUNK_SIZE + 9 * SECTOR_SIZE
    output = tmp_path / "captured.img"
    previous = b"previous exact capture"
    output.write_bytes(previous)

    def read(offset, size):
        if offset <= bad_offset < offset + size:
            if failure == "eof" and size == SECTOR_SIZE:
                return b""
            raise _failure(1785 if size > SECTOR_SIZE else 23)
        return original[offset:offset + size]

    devices = _install_windows_volume(monkeypatch, read)
    drive = image.FloppyDriveInfo("A:", len(original))

    with pytest.raises(image.FloppyCaptureReadError) as caught:
        image.capture_floppy_drive_image(drive, output)

    diagnostics = caught.value.diagnostics
    assert diagnostics["failed_read_offset_bytes"] == CHUNK_SIZE
    assert diagnostics["failed_read_length_bytes"] == CHUNK_SIZE
    assert diagnostics["failed_sector_exact"] is False
    assert diagnostics["first_failed_read"]["winerror"] == 1785
    final = diagnostics["final_failed_read"]
    assert final["offset_bytes"] == bad_offset
    assert final["length_bytes"] == SECTOR_SIZE
    if failure == "error":
        assert final["winerror"] == 23
    else:
        assert "winerror" not in final
    assert diagnostics["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": bad_offset}]
    assert diagnostics["exact_capture_completed"] is False
    assert devices[0].calls[-1] == (bad_offset, SECTOR_SIZE)
    assert len(devices[0].calls) < 30
    assert all(device.closed for device in devices)
    assert output.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("readable_bytes", [SECTOR_SIZE, CHUNK_SIZE - 128])
def test_failure_diagnostics_retain_short_read_bytes_and_numeric_cause(monkeypatch, readable_bytes):
    def read(offset, size):
        if offset < readable_bytes:
            return b"S" * min(size, readable_bytes - offset)
        error = OSError("Localized device error without numeric text")
        error.winerror = 23
        raise error

    devices = _install_windows_volume(monkeypatch, read)

    with pytest.raises(image.FloppyImageError) as caught:
        image._read_windows_block_device_bytes("A:", CHUNK_SIZE)

    diagnostics = caught.value.read_diagnostics
    assert diagnostics["first_failed_read"]["offset_bytes"] == readable_bytes
    assert diagnostics["first_failed_read"]["length_bytes"] == CHUNK_SIZE - readable_bytes
    assert diagnostics["first_failed_read"]["winerror"] == 23
    assert diagnostics["failed_sector_exact"] is False
    assert diagnostics["final_failed_read"]["offset_bytes"] == readable_bytes // SECTOR_SIZE * SECTOR_SIZE
    assert diagnostics["final_failed_read"]["length_bytes"] == SECTOR_SIZE
    assert diagnostics["final_failed_read"]["winerror"] == 23
    assert diagnostics["final_error"]["winerror"] == 23
    assert diagnostics["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": readable_bytes}]
    assert devices[0].closed


@pytest.mark.parametrize("size", [0, -SECTOR_SIZE])
def test_exact_reader_requires_a_positive_selected_size(monkeypatch, size):
    devices = _install_windows_volume(monkeypatch, lambda *_args: pytest.fail("No size means no read"))

    with pytest.raises(image.FloppyImageError, match="disk size could not be detected"):
        image._read_windows_block_device_bytes("A:", size)

    assert devices == []


def test_stalled_exact_read_does_not_start_smaller_requests(monkeypatch):
    def read(_offset, _size):
        raise image._FloppyReadStalled("The floppy read did not finish within 30 seconds.")

    devices = _install_windows_volume(monkeypatch, read)

    with pytest.raises(image._FloppyReadStalled):
        image._read_windows_block_device_bytes("A:", CHUNK_SIZE)

    assert devices[0].calls == [(0, CHUNK_SIZE)]
    assert devices[0].closed


def test_cancellation_during_exact_fallback_keeps_existing_destination(tmp_path, monkeypatch):
    output = tmp_path / "captured.img"
    output.write_bytes(b"previous exact capture")
    cancelled = False

    def read(_offset, size):
        nonlocal cancelled
        if size > SECTOR_SIZE:
            raise _failure(1785)
        cancelled = True
        return b"S" * size

    devices = _install_windows_volume(monkeypatch, read)
    with pytest.raises(image.FloppyOperationCancelled):
        image.capture_floppy_drive_image(
            image.FloppyDriveInfo("A:", CHUNK_SIZE), output,
            cancel_callback=lambda: cancelled,
        )

    assert devices[0].calls[-1] == (0, SECTOR_SIZE)
    assert len(devices[0].calls) == 8
    assert devices[0].closed
    assert output.read_bytes() == b"previous exact capture"
    assert list(tmp_path.iterdir()) == [output]


def test_short_successful_reads_are_joined_without_offset_repeats(monkeypatch):
    original = _payload(CHUNK_SIZE)

    def read(offset, size):
        return original[offset:offset + min(size, SECTOR_SIZE)]

    devices = _install_windows_volume(monkeypatch, read)

    assert image._read_windows_block_device_bytes("A:", len(original)) == original

    assert [offset for offset, _size in devices[0].calls] == list(range(0, len(original), SECTOR_SIZE))
    assert devices[0].closed
