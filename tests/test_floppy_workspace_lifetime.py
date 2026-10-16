"""Local acquisition failures must not masquerade as damaged floppy sectors."""

from concurrent.futures import ThreadPoolExecutor
import errno
from pathlib import Path
import shutil
import tempfile
from threading import Barrier
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import disk_session_worker, floppy_image as image
from test_floppy_targeted_read import _nested
from test_inspection_staging import window


@pytest.fixture
def acquisition(tmp_path, monkeypatch):
    source, data, _geometry, payload = _nested(tmp_path)
    source.write_bytes(data)
    workspaces, devices = [], []
    mkdtemp = tempfile.mkdtemp

    def create_workspace(*args, **kwargs):
        kwargs.setdefault("dir", tmp_path)
        directory = mkdtemp(*args, **kwargs)
        workspaces.append(Path(directory))
        return directory

    class Device:
        closed = False

        def read_at(self, offset, size, _label):
            return bytes(data[offset:offset + size])

        def close(self):
            self.closed = True

    def open_device(_path):
        device = Device()
        devices.append(device)
        return device

    monkeypatch.setattr(image, "tempfile", SimpleNamespace(**{
        **vars(tempfile), "mkdtemp": create_workspace,
    }))
    monkeypatch.setattr(image, "_open_block_device_for_read", open_device)
    for reader in ("_read_block_device", "_read_windows_block_device_bytes"):
        monkeypatch.setattr(image, reader, lambda *_a, **_k: pytest.fail("Do not reread the disk for a local failure"))
    return SimpleNamespace(
        drive=image.FloppyDriveInfo("memory-floppy", len(data)),
        source=source, original=bytes(data), payload=payload,
        workspaces=workspaces, devices=devices,
    )


def test_concurrent_sessions_keep_independent_workspaces(acquisition):
    barrier = Barrier(3)

    def read():
        def progress(step, _total, _message):
            if step == 97:
                barrier.wait(timeout=10)
        return image.FloppyImageSession.load_floppy(acquisition.drive, progress_callback=progress)

    sessions = []
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(read) for _ in range(3)]
            sessions = [future.result(timeout=20) for future in futures]
        assert len({session.temp_dir for session in sessions}) == 3
        for index, session in enumerate(sessions):
            session.cleanup()
            session.cleanup()
            assert not Path(session.temp_dir).exists()
            for active in sessions[index + 1:]:
                assert Path(active.extract_file("SONGS/LIVE/SONG.MID")).read_bytes() == acquisition.payload
        assert acquisition.source.read_bytes() == acquisition.original
        assert all(device.closed for device in acquisition.devices)
    finally:
        for session in sessions:
            session.cleanup()


def test_missing_workspace_retains_read_evidence_and_allows_fresh_read(acquisition):
    def remove_workspace(step, _total, _message):
        if step == 97:
            shutil.rmtree(acquisition.workspaces[-1])

    with pytest.raises(image.FloppyWorkingCopyError, match="temporary working folder is no longer available") as caught:
        image.FloppyImageSession.load_floppy(acquisition.drive, progress_callback=remove_workspace)

    diagnostics = caught.value.read_diagnostics
    assert diagnostics["failure_scope"] == "local_working_copy"
    assert diagnostics["stage"] == "saving_working_image"
    assert diagnostics["working_copy"]["directory_exists"] is False
    assert diagnostics["working_copy"]["error"]["errno"] == errno.ENOENT
    assert diagnostics["final_error"]["errno"] == errno.ENOENT
    assert diagnostics["fast_path"]["successful_ranges"]
    assert diagnostics["fast_path"]["failed_requests"] == []
    assert "raw_fallback" not in diagnostics
    assert not acquisition.workspaces[-1].exists()
    assert acquisition.devices[-1].closed

    session = image.FloppyImageSession.load_floppy(acquisition.drive)
    try:
        assert Path(session.extract_file("SONGS/LIVE/SONG.MID")).read_bytes() == acquisition.payload
        assert session.read_diagnostics["stage"] == "complete"
        assert "failure_scope" not in session.read_diagnostics
        assert acquisition.source.read_bytes() == acquisition.original
    finally:
        session.cleanup()


def test_local_write_permission_error_is_not_a_sector_failure(acquisition, monkeypatch):
    real_open = open

    def fail_local_write(path, mode="r", *args, **kwargs):
        if Path(path).name == "working.img" and mode == "wb":
            error = PermissionError(errno.EACCES, "Local write denied", str(path))
            error.winerror = 5
            raise error
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(image, "open", fail_local_write, raising=False)
    with pytest.raises(image.FloppyWorkingCopyError, match="local working image could not be saved") as caught:
        image.FloppyImageSession.load_floppy(acquisition.drive)
    diagnostics = caught.value.read_diagnostics
    assert diagnostics["working_copy"]["directory_exists"] is True
    assert diagnostics["working_copy"]["error"]["winerror"] == 5
    assert diagnostics["final_error"]["errno"] == errno.EACCES
    assert diagnostics["fast_path"]["failed_requests"] == []
    assert not acquisition.workspaces[-1].exists()
    assert acquisition.devices[-1].closed


@pytest.mark.parametrize("pianodisc", [False, True], ids=["fat-image", "pianodisc-image"])
def test_raw_fallback_local_write_failure_retains_completed_capture(acquisition, monkeypatch, pianodisc):
    reads = []

    def require_raw_read(*_args, **_kwargs):
        raise image.FastFloppyReadError("Full image required", fallback_allowed=True)

    def read_raw(path, size, *, diagnostics, **_kwargs):
        reads.append((path, size))
        diagnostics.update(exact_capture_completed=True)
        return acquisition.original

    def remove_workspace(_step, _total, message):
        if message in {"Creating working copy...", "Decoding PianoDisc System 3 songs..."}:
            shutil.rmtree(acquisition.workspaces[-1])

    # Windows acquires raw bytes in memory before saving the local working
    # image. Do not change the process-wide os.name used by pathlib or pytest.
    with monkeypatch.context() as patch:
        patch.setattr(image, "os", SimpleNamespace(**{**vars(image.os), "name": "nt"}))
        patch.setattr(image, "_read_floppy_device_fast_image", require_raw_read)
        patch.setattr(image, "_read_windows_block_device_bytes", read_raw)
        patch.setattr(image.pianodisc_system3, "looks_like_pianodisc_system3_bytes", lambda _data: pianodisc)
        with pytest.raises(image.FloppyWorkingCopyError) as caught:
            image.FloppyImageSession.load_floppy(acquisition.drive, progress_callback=remove_workspace)

    diagnostics = caught.value.read_diagnostics
    assert reads == [(acquisition.drive.path, acquisition.drive.size_bytes)]
    assert diagnostics["failure_scope"] == "local_working_copy"
    assert diagnostics["stage"] == "saving_working_image"
    assert diagnostics["exact_raw_copy"] is True
    assert diagnostics["raw_fallback"]["exact_capture_completed"] is True
    assert diagnostics["working_copy"]["error"]["errno"] == errno.ENOENT
    assert diagnostics["final_error"]["errno"] == errno.ENOENT
    assert not acquisition.workspaces[-1].exists()
    assert acquisition.source.read_bytes() == acquisition.original


def test_failed_read_keeps_open_session_and_staged_changes(window, acquisition, monkeypatch):
    previous = image.FloppyImageSession.load_floppy(acquisition.drive)
    window.image_session = previous
    staged = Path(previous.patched_dir) / "prepared.mid"
    staged.write_bytes(acquisition.payload)
    window.pendingEdits = {"SONGS/LIVE/SONG.MID": "Pending title"}
    window.pendingRegularConversions = {"prepared.mid": str(staged)}
    window.diskLoadContext = {"load_kind": "floppy_usb", "source": acquisition.drive}
    worker = disk_session_worker.DiskSessionLoadWorker("floppy_usb", acquisition.drive)
    failures, shown = [], []
    worker.loadFailedWithDetails.connect(failures.append)

    def remove_workspace(step, _total, _message):
        if step == 97:
            shutil.rmtree(acquisition.workspaces[-1])

    worker.progressChanged.connect(remove_workspace)
    worker.run()
    assert len(failures) == 1
    monkeypatch.setattr(window, "_show_operation_error", lambda *args, **kwargs: shown.append(args))
    window._on_disk_load_failure_with_details(failures[0])
    request = window.pendingDiskRecoveryRequest
    assert request["failure_scope"] == "local_working_copy"
    # The normal finished handler schedules this after releasing the worker.
    window._offer_disk_recovery(request)
    assert len(shown) == 1
    assert "temporary working folder" in shown[0][2]
    assert window.image_session is previous
    assert staged.read_bytes() == acquisition.payload
    assert window.pendingEdits == {"SONGS/LIVE/SONG.MID": "Pending title"}
    assert window.pendingRegularConversions == {"prepared.mid": str(staged)}
    assert Path(previous.extract_file("SONGS/LIVE/SONG.MID")).read_bytes() == acquisition.payload
    assert acquisition.source.read_bytes() == acquisition.original
    window.pendingDiskRecoveryRequest = None


def test_format_probe_stops_before_fallback_after_local_write_failure(acquisition, monkeypatch):
    def fail(*_args, **_kwargs):
        raise image.FloppyWorkingCopyError(
            acquisition.workspaces[-1] / "working.img", OSError(errno.ENOSPC, "No local space"),
        )

    monkeypatch.setattr(image, "_read_floppy_device_fast_image", fail)
    with pytest.raises(image.FloppyWorkingCopyError):
        image.FloppyImageSession._try_prepare_existing_usb_floppy(
            acquisition.drive, image.DISK_FORMAT_BY_SIZE[acquisition.drive.size_bytes],
        )
    assert not acquisition.workspaces[-1].exists()


def test_logical_capture_stops_after_local_write_failure(acquisition, tmp_path, monkeypatch):
    monkeypatch.setattr(image, "_guard_logical_capture_destination", lambda *_a: None)
    monkeypatch.setattr(image.FloppyImageSession, "_recover_usb_floppy", lambda *_a, **_k: pytest.fail("No hardware recovery for local storage failure"))

    def fail(*_args, **_kwargs):
        raise image.FloppyWorkingCopyError(tmp_path / "working.img", OSError(errno.ENOSPC, "No local space"))

    monkeypatch.setattr(image, "_read_floppy_device_fast_image", fail)
    destination = tmp_path / "capture.img"
    with pytest.raises(image.FloppyWorkingCopyError):
        image.capture_logical_floppy_image(acquisition.drive, destination)
    assert not destination.exists()
