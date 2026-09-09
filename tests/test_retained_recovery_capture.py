import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image


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
    assert affected == []
    assert "FAT was not fully read" in note
