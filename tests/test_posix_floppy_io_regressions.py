"""Raw write and verification regressions; memory and ordinary files only."""
import errno
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.fixture(autouse=True)
def posix_file_io(monkeypatch):
    # Keep ordinary-file readback fixtures on the POSIX path on every host;
    # native Windows device APIs are exercised by their separate mock tests.
    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))


def _mock_writer(monkeypatch, *, max_write=None, failure=None):
    payload = bytes(range(256)) * 80 + b"final"
    state = SimpleNamespace(data=bytearray(), calls=0, flushes=0, syncs=0, progress=[])

    class Target:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, value):
            state.calls += 1
            if failure is not None and state.calls == 2:
                result = failure(len(value))
                return result
            size = min(len(value), max_write) if max_write else len(value)
            state.data.extend(value[:size])
            return size

        def flush(self):
            state.flushes += 1

        def fileno(self):
            return -99

    def fake_open(path, mode, **kwargs):
        if (path, mode) == ("MOCK_INPUT", "rb"):
            return io.BytesIO(payload)
        assert (path, mode) == ("MOCK_TARGET", "r+b")
        assert kwargs == {"buffering": 0}
        return Target()

    def sync(descriptor):
        assert descriptor == -99
        state.syncs += 1

    monkeypatch.setattr(image, "open", fake_open, raising=False)
    monkeypatch.setattr(image, "os", SimpleNamespace(**{
        **vars(os), "name": "posix", "access": lambda *args: True, "fsync": sync,
        "path": SimpleNamespace(**{**vars(os.path), "getsize": lambda path: len(payload)}),
    }))

    def run(cancel=None):
        image._write_block_device("MOCK_INPUT", "MOCK_TARGET",
            progress_callback=lambda step, total, message: state.progress.append(step),
            cancel_callback=cancel)

    return payload, state, run


@pytest.mark.parametrize("max_write", [None, 512])
def test_posix_raw_write_preserves_all_bytes_after_short_writes(monkeypatch, max_write):
    payload, state, run = _mock_writer(monkeypatch, max_write=max_write)
    run()
    assert bytes(state.data) == payload
    assert state.calls == (3 if max_write is None else 41)
    assert state.flushes == state.syncs == 1
    assert state.progress[-1] == 100
    assert state.progress == sorted(state.progress)


@pytest.mark.parametrize("result", [0, None, -1, "overreported"])
def test_posix_raw_write_rejects_no_progress_or_invalid_counts(monkeypatch, result):
    payload, state, run = _mock_writer(monkeypatch, max_write=512,
        failure=lambda remaining: remaining + 1 if result == "overreported" else result)
    with pytest.raises(image.FloppyImageError, match="write"):
        run()
    assert bytes(state.data) == payload[:512]
    assert state.calls == 2
    assert state.flushes == state.syncs == 0
    assert 100 not in state.progress


def test_posix_raw_write_checks_cancellation_between_short_writes(monkeypatch):
    payload, state, run = _mock_writer(monkeypatch, max_write=512)
    with pytest.raises(image.FloppyOperationCancelled):
        run(cancel=lambda: state.calls > 0)
    assert bytes(state.data) == payload[:512]
    assert state.calls == 1
    assert 100 not in state.progress


def test_posix_raw_write_reports_io_failure_after_partial_progress(monkeypatch):
    def fail(_remaining):
        raise OSError(errno.EIO, "simulated media failure")

    payload, state, run = _mock_writer(monkeypatch, max_write=512, failure=fail)
    with pytest.raises(image.FloppyImageError, match="simulated media failure"):
        run()
    assert bytes(state.data) == payload[:512]
    assert 100 not in state.progress


@pytest.fixture
def smaller_volume(tmp_path):
    prepared = tmp_path / "prepared.img"
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    image.create_blank_floppy_image(prepared, disk_format)
    target_path = tmp_path / "target.img"
    target_path.write_bytes(prepared.read_bytes() + b"\xff" * disk_format.size_bytes)
    return prepared, target_path, disk_format


@pytest.mark.parametrize("reported_capacity", [-1, 0, 1_474_560])
def test_full_readback_verifies_prepared_volume_without_unwritten_tail(smaller_volume, monkeypatch, reported_capacity):
    prepared, target_path, disk_format = smaller_volume
    target = image.FloppyDriveInfo(str(target_path), reported_capacity)
    reads = []
    read = image._read_block_device

    def track(source, output, size, **kwargs):
        assert source == str(target_path)
        reads.append(size)
        return read(source, output, size, **kwargs)

    monkeypatch.setattr(image, "_read_block_device", track)
    result = image._verify_physical_floppy_contents(prepared, "floppy_usb", target,
        disk_format, verify_entire_image=True)
    assert result["confidence"] == "contents_verified"
    assert reads == [prepared.stat().st_size]
    assert target_path.stat().st_size == 1_474_560


def test_full_readback_still_rejects_corruption_inside_prepared_volume(smaller_volume):
    prepared, target_path, disk_format = smaller_volume
    payload = bytearray(target_path.read_bytes())
    payload[100] ^= 1
    target_path.write_bytes(payload)
    target = image.FloppyDriveInfo(str(target_path), len(payload))
    with pytest.raises(image.FloppyImageError, match="physical floppy differs"):
        image._verify_physical_floppy_contents(prepared, "floppy_usb", target,
            disk_format, verify_entire_image=True)


def test_full_readback_rejects_prepared_volume_larger_than_known_capacity(smaller_volume, monkeypatch):
    prepared, target_path, disk_format = smaller_volume
    target = image.FloppyDriveInfo(str(target_path), disk_format.size_bytes // 2)
    monkeypatch.setattr(image, "_read_block_device", lambda *args, **kwargs: pytest.fail("Must not read beyond reported capacity"))
    with pytest.raises(image.FloppyImageError, match="exceeds the reported floppy capacity"):
        image._verify_physical_floppy_contents(prepared, "floppy_usb", target,
            disk_format, verify_entire_image=True)


def test_full_readback_rejects_empty_prepared_image(tmp_path, monkeypatch):
    prepared = tmp_path / "empty.img"
    prepared.write_bytes(b"")
    monkeypatch.setattr(image, "_read_block_device", lambda *args, **kwargs: pytest.fail("Must not perform an unbounded read for an empty image"))
    with pytest.raises(image.FloppyImageError, match="prepared format image is empty"):
        image._verify_physical_floppy_contents(prepared, "floppy_usb",
            image.FloppyDriveInfo("MOCK_TARGET", 0), image.DISK_FORMAT_BY_KEY["ibm.720"],
            verify_entire_image=True)


def test_content_only_readback_retains_target_geometry_for_file_level_save(tmp_path, monkeypatch):
    prepared = tmp_path / "source-1440.img"
    target_path = tmp_path / "target-720.img"
    source_format = image.DISK_FORMAT_BY_KEY["ibm.1440"]
    target_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    image.create_blank_floppy_image(prepared, source_format)
    image.create_blank_floppy_image(target_path, target_format)
    song = tmp_path / "song.mid"
    song.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")
    for path in (prepared, target_path):
        image._copy_host_file_into_image(path, song, "SONG.MID")
    reads = []
    read = image._read_block_device

    def track(source, output, size, **kwargs):
        assert source == str(target_path)
        reads.append(size)
        return read(source, output, size, **kwargs)

    monkeypatch.setattr(image, "_read_block_device", track)
    result = image._verify_physical_floppy_contents(prepared, "floppy_usb",
        image.FloppyDriveInfo(str(target_path), target_format.size_bytes), source_format)
    assert result["confidence"] == "contents_verified"
    assert result["files_verified"] == 1
    assert reads == [target_format.size_bytes]


def test_usb_format_accepts_smaller_logical_volume_on_larger_medium(tmp_path, monkeypatch):
    target_path = tmp_path / "larger-medium.img"
    target_path.write_bytes(b"\xff" * 1_474_560)
    target = image.FloppyDriveInfo(str(target_path), 1_474_560)
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    monkeypatch.setattr(image.FloppyImageSession, "_try_prepare_existing_usb_floppy", classmethod(lambda *args, **kwargs: None))

    def write_prefix(source, destination, **kwargs):
        assert destination == str(target_path)
        with open(destination, "r+b") as handle:
            handle.write(Path(source).read_bytes())

    monkeypatch.setattr(image, "_write_block_device", write_prefix)
    session = image.FloppyImageSession.format_usb_floppy(target, disk_format)
    try:
        assert session.last_write_verification["confidence"] == "contents_verified"
        assert session.disk_format == disk_format
        assert target_path.read_bytes()[disk_format.size_bytes:] == b"\xff" * disk_format.size_bytes
    finally:
        session.cleanup()
