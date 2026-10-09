import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from aps_midi_prep_tool_app.helpers import file_batch


def _capture_directory_in(tmp_path, monkeypatch):
    real_mkdtemp = floppy_image.tempfile.mkdtemp

    def make_directory(*args, **kwargs):
        kwargs.setdefault("dir", tmp_path)
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(floppy_image.tempfile, "mkdtemp", make_directory)


def test_cancelled_usb_capture_exports_recovered_bytes_and_coverage_without_reread(tmp_path, monkeypatch):
    _capture_directory_in(tmp_path, monkeypatch)
    monkeypatch.setattr(floppy_image, "USB_FLOPPY_RECOVERY_CHUNK_SIZE", 512)
    reads = []

    class Device:
        def read_at(self, offset, size, label):
            reads.append((offset, size))
            if offset == 0:
                return b"A" * size
            raise floppy_image.FloppyOperationCancelled("Cancelled")

        def close(self):
            pass

    monkeypatch.setattr(floppy_image, "_open_block_device_for_recovery_read", lambda path: Device())
    drive = floppy_image.FloppyDriveInfo("fake-drive", 2048)

    with pytest.raises(floppy_image.FloppyOperationCancelled) as error:
        floppy_image.FloppyImageSession._recover_usb_floppy(drive)

    diagnostics = error.value.diagnostics
    capture = Path(diagnostics["partial_capture_path"])
    assert capture.read_bytes() == b"A" * 512 + b"\0" * 1536
    assert diagnostics["sector_states"] == [1, 4, 0, 0]
    assert diagnostics["readable_sectors"] == 1
    assert diagnostics["recovery_cancelled"]
    report = json.loads(Path(diagnostics["partial_capture_diagnostics_path"]).read_text())
    assert report["sector_states"] == [1, 4, 0, 0]
    assert report["sha256"] == diagnostics["sha256"]
    assert not list(tmp_path.glob("aps_recover_floppy_*"))

    image_path, report_path = floppy_image.save_floppy_recovery_capture(diagnostics, tmp_path / "keep.img")

    assert Path(image_path).read_bytes() == capture.read_bytes()
    assert json.loads(Path(report_path).read_text())["partial_capture_path"] == image_path
    assert reads == [(0, 512), (512, 512)]


def _capture_export_paths(tmp_path, existing):
    source = tmp_path / "retained.img"
    source.write_bytes(b"new partial capture")
    destination = tmp_path / "export"
    destination.mkdir()
    image = destination / "keep.img"
    report = destination / "keep.img.json"
    if existing:
        image.write_bytes(b"previous capture")
        report.write_bytes(b"previous diagnostics")
    diagnostics = {"partial_capture_path": str(source), "sector_states": [1, 3, 0]}
    return source, image, report, diagnostics


def _assert_capture_export_unchanged(source, image, report, diagnostics, existing):
    assert source.read_bytes() == b"new partial capture"
    assert diagnostics == {"partial_capture_path": str(source), "sector_states": [1, 3, 0]}
    if existing:
        assert image.read_bytes() == b"previous capture"
        assert report.read_bytes() == b"previous diagnostics"
        assert set(image.parent.iterdir()) == {image, report}
    else:
        assert not list(image.parent.iterdir())
    assert not list(source.parent.glob("aps_file_batch_*"))


def test_report_replacement_failure_restores_existing_capture_pair(tmp_path, monkeypatch):
    _capture_directory_in(tmp_path, monkeypatch)
    source, image, report, diagnostics = _capture_export_paths(tmp_path, existing=True)
    real_replace = floppy_image.os.replace

    def fail_report_replace(staged, destination):
        if Path(destination) == report:
            assert image.read_bytes() == source.read_bytes()
            raise PermissionError("Report replacement failed")
        return real_replace(staged, destination)

    monkeypatch.setattr(floppy_image.os, "replace", fail_report_replace)

    with pytest.raises(PermissionError, match="Report replacement failed"):
        floppy_image.save_floppy_recovery_capture(diagnostics, image)

    _assert_capture_export_unchanged(source, image, report, diagnostics, existing=True)


def test_report_publication_failure_removes_new_capture_image(tmp_path, monkeypatch):
    _capture_directory_in(tmp_path, monkeypatch)
    source, image, report, diagnostics = _capture_export_paths(tmp_path, existing=False)
    real_publish = file_batch.atomic_write_bytes

    def fail_report_publish(destination, payload, **kwargs):
        if Path(destination) == report:
            assert image.read_bytes() == source.read_bytes()
            raise PermissionError("Report publication failed")
        return real_publish(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_report_publish)

    with pytest.raises(PermissionError, match="Report publication failed"):
        floppy_image.save_floppy_recovery_capture(diagnostics, image)

    _assert_capture_export_unchanged(source, image, report, diagnostics, existing=False)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["image_copy", "report_write"])
def test_capture_export_staging_failure_leaves_outputs_unchanged(tmp_path, monkeypatch, existing, failure):
    source, image, report, diagnostics = _capture_export_paths(tmp_path, existing)

    if failure == "image_copy":
        def fail_copy(source_path, staged):
            Path(staged).write_bytes(b"incomplete copy")
            raise OSError("Capture staging failed")

        monkeypatch.setattr(floppy_image.shutil, "copy2", fail_copy)
    else:
        def fail_report_write(details, handle, **kwargs):
            handle.write("{\"incomplete\":")
            raise OSError("Capture staging failed")

        monkeypatch.setattr(floppy_image.json, "dump", fail_report_write)

    with pytest.raises(OSError, match="Capture staging failed"):
        floppy_image.save_floppy_recovery_capture(diagnostics, image)

    _assert_capture_export_unchanged(source, image, report, diagnostics, existing)


def test_capture_export_incomplete_rollback_reports_retained_recovery_copies(tmp_path, monkeypatch):
    _capture_directory_in(tmp_path, monkeypatch)
    source, image, report, diagnostics = _capture_export_paths(tmp_path, existing=True)
    real_publish = file_batch.atomic_write_bytes

    def fail_report_and_restore(destination, payload, **kwargs):
        if Path(destination) == report:
            raise OSError("Report publication failed")
        if Path(destination) == image and payload == b"previous capture":
            raise PermissionError("Image restoration failed")
        return real_publish(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_report_and_restore)

    with pytest.raises(floppy_image.FloppyImageError, match="Recovery copies and a manifest") as error:
        floppy_image.save_floppy_recovery_capture(diagnostics, image)

    recovery = Path(error.value.recovery_directory)
    assert str(recovery) in str(error.value)
    assert str(image) in error.value.rollback_errors[0]
    manifest = json.loads((recovery / "manifest.json").read_text())
    originals = {record["destination"]: (recovery / record["original_file"]).read_bytes()
                 for record in manifest["files"]}
    assert originals == {str(image): b"previous capture", str(report): b"previous diagnostics"}
    assert image.read_bytes() == source.read_bytes() == b"new partial capture"
    assert report.read_bytes() == b"previous diagnostics"
    assert set(image.parent.iterdir()) == {image, report}


def test_failed_file_recovery_keeps_completed_acquisition(tmp_path, monkeypatch):
    _capture_directory_in(tmp_path, monkeypatch)

    class Device:
        def read_at(self, offset, size, label):
            return b"B" * size

        def close(self):
            pass

    monkeypatch.setattr(floppy_image, "_open_block_device_for_recovery_read", lambda path: Device())

    def fail_analysis(*args, **kwargs):
        raise floppy_image.FloppyRecoveryError("No recognizable songs", diagnostics=kwargs["recovery_diagnostics"])

    monkeypatch.setattr(floppy_image.FloppyImageSession, "_recover_from_raw_image", fail_analysis)

    with pytest.raises(floppy_image.FloppyRecoveryError) as error:
        floppy_image.FloppyImageSession._recover_usb_floppy(floppy_image.FloppyDriveInfo("fake", 2048))

    diagnostics = error.value.diagnostics
    assert Path(diagnostics["partial_capture_path"]).read_bytes() == b"B" * 2048
    assert diagnostics["sector_states"] == [1, 1, 1, 1]
    assert Path(diagnostics["partial_capture_diagnostics_path"]).exists()
    assert not list(tmp_path.glob("aps_recover_floppy_*"))


def test_unread_sectors_are_attributed_only_with_readable_fat_and_directory(tmp_path):
    disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    image = tmp_path / "songs.img"
    song = tmp_path / "SONG.MID"
    song.write_bytes(b"test recording")
    floppy_image.create_blank_floppy_image(image, disk_format)
    floppy_image._copy_host_file_into_image(image, song, "SONG.MID")
    data, geometry, fat, root = floppy_image._read_fat12_image_context(image)
    entry = next(item for item in floppy_image._iter_fat_directory_entries(root) if item["name"] == "SONG.MID")
    song_sector = floppy_image._cluster_offset(geometry, entry["cluster"]) // 512
    states = [1] * (len(data) // 512)
    states[song_sector] = 3
    diagnostics = {"sector_states": states, "sector_size": 512}

    affected, note = floppy_image._recovery_affected_files(image, diagnostics)

    assert affected == [{"path": "SONG.MID", "status": "contains unread sectors", "unread_sectors": [song_sector]}]
    assert "readable FAT" in note
    states[geometry.fat_offset // 512] = 3
    affected, note = floppy_image._recovery_affected_files(image, diagnostics)
    assert affected == [{"path": "SONG.MID", "status": "contains unread sectors", "unread_sectors": [song_sector]}]
    assert "FAT 2" in note
    states[(geometry.fat_offset + geometry.fat_size) // 512] = 3
    affected, note = floppy_image._recovery_affected_files(image, diagnostics)
    assert affected == []
    assert "unreadable" in note


@pytest.mark.parametrize("fat_sector", [0, 1], ids=["missing-signature", "missing-unused-allocations"])
def test_retained_capture_maps_song_damage_using_readable_second_fat(tmp_path, monkeypatch, fat_sector):
    from test_fat12_mirror_validation import _fragmented_image

    source, data, geometry, _payload = _fragmented_image(tmp_path)
    source.write_bytes(data)
    song_sector = floppy_image._cluster_offset(geometry, 4) // geometry.bytes_per_sector
    bad_offsets = {
        geometry.fat_offset + fat_sector * geometry.bytes_per_sector,
        song_sector * geometry.bytes_per_sector,
    }

    class Device:
        closed = False

        def read_at(self, offset, size, _label):
            if any(offset <= start < offset + size for start in bad_offsets):
                raise OSError("Unreadable test sector")
            return bytes(data[offset:offset + size])

        def close(self):
            self.closed = True

    device = Device()
    monkeypatch.setattr(floppy_image, "_open_block_device_for_recovery_read", lambda _path: device)
    capture = tmp_path / "capture.img"
    diagnostics = floppy_image._read_block_device_recovery_image("mock", capture, len(data))
    details = floppy_image.retain_floppy_recovery_capture(capture, diagnostics, temporary_parent=tmp_path)

    expected = [{"path": "SONG.MID", "status": "contains unread sectors", "unread_sectors": [song_sector]}]
    assert details["affected_files"] == expected
    assert "FAT 2" in details["affected_files_note"]
    assert details["bad_sectors"] == 2
    report = json.loads(Path(details["partial_capture_diagnostics_path"]).read_text())
    assert report["affected_files"] == expected
    assert "FAT 2" in report["affected_files_note"]
    assert Path(details["partial_capture_path"]).read_bytes() == capture.read_bytes()
    assert source.read_bytes() == data
    assert device.closed
