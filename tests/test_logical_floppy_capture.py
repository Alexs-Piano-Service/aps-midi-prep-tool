"""An exact capture failure requires consent before logical reconstruction."""

import json
import builtins
import os
import stat
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from aps_midi_prep_tool_app import disk_session_worker, floppy_image as image, main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from test_fat12_directory_cycles import _set_fat
from test_save_as_overwrite import _song, window


def _windows_disk(tmp_path, monkeypatch, *, protected_boot=False, unreadable_song=False,
                  unreadable_root=False, unreadable_fat=False, unreadable_subdirectory=False):
    path = tmp_path / "original.img"
    image._create_blank_fat12_image_from_layout(path, image._PROTECTED_FAT12_LAYOUTS[0], "ORIGINAL")
    data = bytearray(path.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    songs = {"SONG.FIL": convert_midi_bytes_to_eseq_bytes(_song("ESEQ original", 60)),
             "SONG.MID": _song("MIDI original", 64)}
    for index, (name, payload) in enumerate(songs.items()):
        cluster = index + 2
        assert len(payload) <= geometry.cluster_size
        _set_fat(data, geometry, cluster, 0xFFF)
        stem, ext = name.split(".")
        entry = image._dos_directory_entry(stem.encode().ljust(8) + ext.encode(), cluster, len(payload))
        start = geometry.root_offset + index * 32
        data[start:start + 32] = entry
        start = geometry.data_offset + index * geometry.cluster_size
        data[start:start + len(payload)] = payload
    if unreadable_subdirectory:
        # Keep the MIDI in the readable root so a partial listing is nonempty.
        # The E-SEQ's only directory entry lives in the unreadable folder.
        song_entry = bytes(data[geometry.root_offset:geometry.root_offset + 32])
        _set_fat(data, geometry, 4, 0xFFF)
        data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"MUSIC", 4, 0, attr=0x10)
        start = image._cluster_offset(geometry, 4)
        data[start:start + 96] = (
            image._dos_directory_entry(b".", 4, 0, attr=0x10)
            + image._dos_directory_entry(b"..", 0, 0, attr=0x10)
            + song_entry
        )
    path.write_bytes(data)
    bad = {0 if protected_boot else 200 * 512}
    if unreadable_song:
        bad.add(geometry.data_offset)
    if unreadable_root:
        bad.add(geometry.root_offset)
    if unreadable_fat:
        bad.add(geometry.fat_offset + 512)
    if unreadable_subdirectory:
        bad.add(image._cluster_offset(geometry, 4))
    devices = []

    class Volume:
        def __init__(self, _path, *, write=False):
            assert write is False
            self.calls = []
            self.closed = False
            devices.append(self)

        def read_at_recovery(self, offset, size, _label, **_kwargs):
            self.calls.append((offset, size))
            if any(offset <= sector < offset + size for sector in bad):
                raise OSError("[WinError 23] Data error (cyclic redundancy check)")
            return bytes(data[offset:offset + size])

        def close(self):
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_WindowsRecoveryVolumeHandle", Volume)
    return image.FloppyDriveInfo("A:", len(data), model="Mock USB floppy"), path, songs, geometry, devices


@pytest.mark.parametrize("area", ["root", "song"])
@pytest.mark.parametrize("protected_boot", [False, True])
def test_normal_floppy_open_rejects_unreadable_directory_or_song_data(
    tmp_path, monkeypatch, area, protected_boot,
):
    drive, source, _songs, _geometry, devices = _windows_disk(
        tmp_path, monkeypatch, protected_boot=protected_boot, **{f"unreadable_{area}": True},
    )
    original = source.read_bytes()
    output = tmp_path / "previous.img"
    output.write_bytes(b"previous image")
    monkeypatch.setattr(image, "_read_windows_block_device_bytes",
                        lambda *_a, **_k: pytest.fail("Known unreadable data must require explicit recovery"))
    with pytest.raises(image.FastFloppyReadError, match="Start in recovery mode") as caught:
        image._read_floppy_device_fast_image(drive.path, output, drive.size_bytes)
    assert not caught.value.fallback_allowed
    assert output.read_bytes() == b"previous image"
    with pytest.raises(image.FloppyImageError, match="Start in recovery mode"):
        image.FloppyImageSession.load_floppy(drive)
    assert source.read_bytes() == original
    assert all(device.closed for device in devices)


@pytest.mark.parametrize("protected_boot", [False, True])
def test_explicit_logical_recovery_reports_missing_root_directory_data(tmp_path, monkeypatch, protected_boot):
    drive, source, songs, geometry, devices = _windows_disk(
        tmp_path, monkeypatch, unreadable_root=True, protected_boot=protected_boot,
    )
    original = source.read_bytes()
    result = image.capture_logical_floppy_image(drive, tmp_path / "partial-root.img")
    details = result["diagnostics"]
    assert details["file_data_status"] == "incomplete_or_uncertain"
    assert not details.get("file_map_complete", False)
    if not protected_boot:
        assert {"area": "root", "offset_bytes": geometry.root_offset, "length_bytes": 512} in details["unreadable_ranges"]
    assert details["read_method"] == "bounded_full_disk_recovery"
    recovered = {
        entry.path: image._read_fat12_file_bytes(result["output_path"], entry.path)
        for entry in image.read_image_listing(result["output_path"]).entries
    }
    # The MIDI's original filename existed only in the unreadable directory;
    # recovery must preserve its bytes even when it assigns a generated name.
    assert set(recovered.values()) == set(songs.values())
    assert details["files_listed"] == 2
    assert not details["recovery_diagnostics"]["root_directory_readable"]
    root_sector = geometry.root_offset // geometry.bytes_per_sector
    assert details["recovery_diagnostics"]["sector_states"][root_sector] == 3
    assert all(device.closed for device in devices)
    assert source.read_bytes() == original


@pytest.mark.parametrize("area", ["root", "subdirectory"])
def test_fast_recovery_does_not_publish_an_incomplete_directory_map(tmp_path, monkeypatch, area):
    drive, _source, _songs, _geometry, devices = _windows_disk(
        tmp_path, monkeypatch, **{f"unreadable_{area}": True},
    )
    output = tmp_path / "previous.img"
    output.write_bytes(b"existing image")
    diagnostics = {}
    with pytest.raises(image.FastFloppyReadError, match="file map incomplete") as caught:
        image._read_floppy_device_fast_image(
            drive.path, output, drive.size_bytes, allow_incomplete=True, diagnostics=diagnostics,
        )
    assert caught.value.fallback_allowed
    assert not diagnostics["file_map_complete"]
    assert output.read_bytes() == b"existing image"
    assert all(device.closed for device in devices)


def test_logical_recovery_salvages_song_from_unreadable_subdirectory(tmp_path, monkeypatch):
    drive, source, songs, geometry, devices = _windows_disk(tmp_path, monkeypatch, unreadable_subdirectory=True)
    original = source.read_bytes()
    result = image.capture_logical_floppy_image(drive, tmp_path / "partial-directory.img")
    details = result["diagnostics"]
    assert details["read_method"] == "bounded_full_disk_recovery"
    assert not details["file_map_complete"]
    assert details["file_data_status"] == "incomplete_or_uncertain"
    assert {"area": "directory", "offset_bytes": image._cluster_offset(geometry, 4), "length_bytes": 512} in details["unreadable_ranges"]
    recovered = {
        entry.path: image._read_fat12_file_bytes(result["output_path"], entry.path)
        for entry in image.read_image_listing(result["output_path"]).entries
    }
    assert recovered["SONG.MID"] == songs["SONG.MID"]
    assert set(recovered.values()) == set(songs.values())
    assert details["files_listed"] == 2
    assert not details["recovery_diagnostics"]["filesystem_repair_succeeded"]
    assert all(device.closed for device in devices)
    assert source.read_bytes() == original


def test_normal_floppy_open_accepts_a_fully_readable_alternate_fat(tmp_path, monkeypatch):
    drive, _source, songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch, unreadable_fat=True)
    session = image.FloppyImageSession.load_floppy(drive)
    try:
        assert "FAT 2" in session.repair_note
        for name, payload in songs.items():
            assert image._read_fat12_file_bytes(session.working_img_path, name) == payload
    finally:
        session.cleanup()


@pytest.mark.parametrize("protected_boot", [False, True])
def test_windows_exact_capture_fails_but_open_and_explicit_logical_capture_preserve_eseq(
    tmp_path, monkeypatch, protected_boot,
):
    drive, original, songs, geometry, devices = _windows_disk(tmp_path, monkeypatch, protected_boot=protected_boot)
    source_bytes = original.read_bytes()
    exact = tmp_path / "exact.img"
    exact.write_bytes(b"previous exact image")
    with pytest.raises(image.FloppyCaptureReadError) as failed:
        image.capture_floppy_drive_image(drive, exact)
    assert exact.read_bytes() == b"previous exact image"
    assert failed.value.diagnostics["failed_read_length_bytes"] == 65536
    assert not failed.value.diagnostics["failed_sector_exact"]

    session = image.FloppyImageSession.load_floppy(drive)
    try:
        for name, payload in songs.items():
            assert image._read_fat12_file_bytes(session.working_img_path, name) == payload
    finally:
        session.cleanup()

    output = tmp_path / "logical-recovery.img"
    result = image.capture_logical_floppy_image(drive, output, exact_failure=failed.value.diagnostics)
    details = json.loads(Path(result["diagnostics_path"]).read_text())
    assert details["capture_kind"] == "logical_recovery"
    assert details["exact_raw_copy"] is details["archival_capture"] is False
    assert details["files_independently_verified"] is False
    assert details["file_data_status"] == "read_without_sector_errors"
    assert details["source_label"] == drive.display_name
    assert details["boot_sector_reconstructed"] == protected_boot
    assert details["omitted_ranges"] and details["omitted_sectors_zero_filled"]
    assert any(r["offset_bytes"] <= 200 * 512 < r["offset_bytes"] + r["length_bytes"]
               for r in details["omitted_ranges"])
    assert bool(details["unreadable_ranges"]) == protected_boot
    if protected_boot:
        assert {"area": "boot", "offset_bytes": 0, "length_bytes": 512} in details["unreadable_ranges"]
    for name, payload in songs.items():
        assert image._read_fat12_file_bytes(output, name) == payload
    assert original.read_bytes() == source_bytes
    assert all(device.closed for device in devices)
    assert not list(tmp_path.glob(".aps_capture_*"))


def test_allocated_song_read_error_is_reported_as_incomplete_not_verified(tmp_path, monkeypatch):
    drive, _source, songs, geometry, _devices = _windows_disk(tmp_path, monkeypatch, unreadable_song=True)
    output = tmp_path / "partial-logical.img"
    result = image.capture_logical_floppy_image(drive, output)
    details = result["diagnostics"]
    assert details["file_data_status"] == "incomplete_or_uncertain"
    assert details["file_data_zero_filled"] and not details["allocated_file_data_complete"]
    assert details["files_independently_verified"] is details["archival_capture"] is False
    assert {"area": "file_data", "offset_bytes": geometry.data_offset, "length_bytes": 512} in details["unreadable_ranges"]
    assert image._read_fat12_file_bytes(output, "SONG.FIL") != songs["SONG.FIL"]
    assert image._read_fat12_file_bytes(output, "SONG.MID") == songs["SONG.MID"]


@pytest.mark.parametrize("decision", ["accept", "decline", "cancel_destination"])
def test_image_floppy_offers_recovery_after_worker_stops_and_requires_consent(
    window, tmp_path, monkeypatch, decision,
):
    drive, source, songs, _geometry, devices = _windows_disk(tmp_path, monkeypatch)
    original = source.read_bytes()
    exact, recovered = tmp_path / "exact.img", tmp_path / "chosen-logical.img"
    prompts, completions = [], []

    def consent(prompt):
        assert window.diskImageCaptureWorker is None
        assert not window._disk_worker_busy()
        assert prompt.defaultButton() is prompt.button(QMessageBox.No)
        assert prompt.button(QMessageBox.Yes).text() == "Create Logical Recovery"
        assert "not a bit-exact" in prompt.informativeText()
        prompts.append(prompt.windowTitle())
        return QMessageBox.No if decision == "decline" else QMessageBox.Yes

    def choose(_parent, title, default, filters):
        assert title == "Save Logical Recovery Image"
        assert default.endswith("exact-logical-recovery.img")
        return ("" if decision == "cancel_destination" else str(recovered), filters)

    monkeypatch.setattr(window, "_exec_child_dialog", consent)
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: completions.append(args))
    window._start_floppy_image_capture_worker("floppy_usb", drive, str(exact), source_name=drive.display_name)
    deadline = time.monotonic() + 5
    while window.diskImageCaptureWorker is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.005)
    assert window.diskImageCaptureWorker is None
    assert prompts == ["Exact Floppy Capture Failed"]
    assert not exact.exists()
    assert recovered.exists() == (decision == "accept")
    assert len(devices) == (2 if decision == "accept" else 1)
    if decision == "accept":
        assert completions[0][1] == "Logical Recovery Saved"
        assert "not a bit-exact or archival capture" in completions[0][2]
        assert "not opened, scanned" not in completions[0][2]
        for name, payload in songs.items():
            assert image._read_fat12_file_bytes(recovered, name) == payload
        report, = tmp_path.glob("chosen-logical.img.recovery-*.json")
        assert json.loads(report.read_text())["exact_capture_failure"]["failed_read_offset_bytes"] == 65536
    else:
        assert not completions
        assert not list(tmp_path.glob("*.recovery-*.json"))
    assert source.read_bytes() == original


@pytest.mark.parametrize("unreadable_root", [False, True])
def test_logical_recovery_cancellation_keeps_existing_destination(tmp_path, monkeypatch, unreadable_root):
    drive, _source, _songs, _geometry, _devices = _windows_disk(
        tmp_path, monkeypatch, unreadable_root=unreadable_root,
    )
    output = tmp_path / "logical.img"
    output.write_bytes(b"existing image")
    stopped = []
    with pytest.raises(image.FloppyOperationCancelled):
        image.capture_logical_floppy_image(
            drive, output, cancel_callback=lambda: bool(stopped),
            progress_callback=lambda step, *_args: stopped.append(True) if step >= 98 else None,
        )
    assert output.read_bytes() == b"existing image"
    assert not list(tmp_path.glob("*.recovery-*.json"))
    assert not list(tmp_path.glob(".aps_capture_*"))


def test_destination_failure_does_not_offer_another_disk_read(tmp_path, monkeypatch):
    def cannot_save(*_args, **_kwargs):
        raise OSError("No space left on destination")

    monkeypatch.setattr(disk_session_worker, "capture_floppy_drive_image", cannot_save)
    worker = disk_session_worker.DiskImageCaptureWorker("floppy_usb", image.FloppyDriveInfo("A:", 737280), tmp_path / "out.img")
    failures, offers = [], []
    worker.captureFailed.connect(failures.append)
    worker.captureRecoveryAvailable.connect(offers.append)
    worker.run()
    assert failures == ["No space left on destination"]
    assert offers == []


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_exact_capture_output_write_error_is_not_a_recoverable_read_failure(tmp_path, monkeypatch, platform):
    source = tmp_path / "synthetic-device.img"
    source.write_bytes(b"readable sectors")
    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": platform}))
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", lambda *_a, **_k: source.read_bytes())

    def fail_output(path, mode="r", *args, **kwargs):
        if "w" in mode:
            raise OSError("No space left on destination")
        return builtins.open(path, mode, *args, **kwargs)

    monkeypatch.setattr(image, "open", fail_output, raising=False)
    with pytest.raises(OSError, match="No space left"):
        image.capture_floppy_drive_image(image.FloppyDriveInfo(str(source), source.stat().st_size), tmp_path / "out.img")
    assert not list(tmp_path.glob(".aps_capture_*"))


@pytest.mark.parametrize("source", ["A:", r"\\.\A:"])
def test_recovery_refuses_windows_source_volume_before_creating_anything(monkeypatch, source):
    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_capture_temp_output_path", lambda *_a, **_k: pytest.fail("Unexpected output write"))
    with pytest.raises(image.FloppyImageError, match="different device"):
        image.capture_logical_floppy_image(image.FloppyDriveInfo(source, 737280), r"A:\new folder\out.img")


@pytest.mark.skipif(os.name == "nt", reason="POSIX block-device identity uses st_rdev and os.major")
def test_recovery_refuses_posix_source_filesystem_before_creating_anything(tmp_path, monkeypatch):
    def fake_stat(path, *_a, **_k):
        if str(path) == "/dev/source-floppy":
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=771)
        return SimpleNamespace(st_dev=771)

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "posix", "stat": fake_stat}))
    monkeypatch.setattr(image, "_capture_temp_output_path", lambda *_a, **_k: pytest.fail("Unexpected output write"))
    with pytest.raises(image.FloppyImageError, match="different device"):
        image.capture_logical_floppy_image(image.FloppyDriveInfo("/dev/source-floppy", 737280), tmp_path / "new" / "out.img")
    assert not (tmp_path / "new").exists()


def test_logical_capture_falls_back_to_existing_bounded_recovery_with_sector_diagnostics(tmp_path, monkeypatch):
    drive, original, songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch)
    before = original.read_bytes()

    def fail_fast(*_args, **_kwargs):
        raise image.FastFloppyReadError("Cannot plan a fast read", fallback_allowed=True)

    monkeypatch.setattr(image, "_read_floppy_device_fast_image", fail_fast)
    result = image.capture_logical_floppy_image(drive, tmp_path / "logical.img")
    details = result["diagnostics"]
    assert details["read_method"] == "bounded_full_disk_recovery"
    assert details["file_data_status"] == "incomplete_or_uncertain"
    recovery = details["recovery_diagnostics"]
    assert recovery["bad_sectors"] == 1
    assert recovery["bad_sector_ranges"] == [[200, 200]]
    assert Path(recovery["partial_capture_path"]).is_relative_to(tmp_path)
    for name, payload in songs.items():
        assert image._read_fat12_file_bytes(result["output_path"], name) == payload
    assert original.read_bytes() == before


def test_failed_logical_publication_preserves_destination_and_removes_own_report(tmp_path, monkeypatch):
    drive, _source, _songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch)
    output = tmp_path / "logical.img"
    output.write_bytes(b"existing image")

    def fail_publication(*_args):
        raise OSError("Destination disconnected")

    monkeypatch.setattr(image, "_finish_capture_output", fail_publication)
    with pytest.raises(OSError, match="disconnected"):
        image.capture_logical_floppy_image(drive, output)
    assert output.read_bytes() == b"existing image"
    assert not list(tmp_path.glob("*.recovery-*.json"))
    assert not list(tmp_path.glob(".aps_capture_*"))
