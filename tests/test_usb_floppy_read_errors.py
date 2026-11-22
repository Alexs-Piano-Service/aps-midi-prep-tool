"""USB capacity is not evidence that a drive can read 720 KB media."""

import os
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


class ReadDevice:
    def __init__(self, data=None):
        self.data = data
        self.closed = False

    def read_at(self, offset, size, _label):
        if self.data is None:
            raise image.FloppyImageError("Windows refused the disk read", winerror=1785)
        return bytes(self.data[offset:offset + size])

    def close(self):
        self.closed = True


def test_720k_reported_capacity_with_all_144_attempted_sectors_unreadable(monkeypatch, tmp_path):
    drive = image.FloppyDriveInfo("A:", 737280)
    selected = image.DISK_FORMAT_BY_KEY["ibm.720"]
    diagnostics = image._new_usb_floppy_recovery_diagnostics(
        drive, selected, selected.size_bytes, sector_size=512, soft_deadline_seconds=300,
    )
    device = ReadDevice()
    monkeypatch.setattr(image, "_open_block_device_for_recovery_read", lambda _path: device)
    capture = tmp_path / "unreadable.img"
    diagnostics = image._read_block_device_recovery_image(
        drive.path, capture, selected.size_bytes, diagnostics=diagnostics,
    )

    assert device.closed
    assert diagnostics["drive_reported_bytes"] == 737280
    assert diagnostics["selected_format_label"] == "IBM 720K DD"
    assert diagnostics["attempted_sectors"] == diagnostics["bad_sectors"] == 144
    assert diagnostics["readable_sectors"] == 0
    assert diagnostics["unattempted_sectors"] == 1296
    assert diagnostics["stop_reason"] == "all_sectors_bad"
    with pytest.raises(image.FloppyRecoveryError) as caught:
        image.FloppyImageSession._recover_from_raw_image(
            capture, str(tmp_path), source_name="Unreadable USB floppy",
            disk_format_hint=selected, recovery_diagnostics=diagnostics,
        )
    message = str(caught.value).split("Floppy diagnostics", 1)[0]
    assert "The disk could not be read" in message
    assert "no readable sectors" in message
    assert "format could not be determined" in message
    assert "144 were unreadable" in message
    assert image.USB_FLOPPY_720K_READ_GUIDANCE in message
    assert "blank" not in message.lower()
    assert "invalid" not in message.lower()
    assert caught.value.diagnostics["readable_sectors"] == 0


def test_readable_unsupported_720k_disk_is_distinct_from_failed_io(monkeypatch, tmp_path):
    selected = image.DISK_FORMAT_BY_KEY["ibm.720"]
    # Non-zero, non-blank-fill data with no supported filesystem or song headers.
    data = bytes(range(256)) * (selected.size_bytes // 256)
    device = ReadDevice(data)
    monkeypatch.setattr(image, "_open_block_device_for_read", lambda _path: device)
    fast_diagnostics = {}
    with pytest.raises(image.FastFloppyReadError, match="obtained sector data"):
        image._read_floppy_device_fast_image(
            "A:", tmp_path / "fast.img", selected.size_bytes, diagnostics=fast_diagnostics,
        )
    assert fast_diagnostics["boot_probe"]["status"] == "read_success"
    assert not fast_diagnostics.get("failed_read_calls")
    diagnostics = image._new_usb_floppy_recovery_diagnostics(
        image.FloppyDriveInfo("A:", selected.size_bytes), selected, selected.size_bytes,
        sector_size=512, soft_deadline_seconds=300,
    )
    monkeypatch.setattr(image, "_open_block_device_for_recovery_read", lambda _path: ReadDevice(data))
    capture = tmp_path / "unsupported.img"
    diagnostics = image._read_block_device_recovery_image("A:", capture, selected.size_bytes, diagnostics=diagnostics)
    with pytest.raises(image.FloppyRecoveryError) as caught:
        image.FloppyImageSession._recover_from_raw_image(
            capture, str(tmp_path), source_name="Readable unsupported floppy",
            disk_format_hint=selected, recovery_diagnostics=diagnostics,
        )
    assert diagnostics["readable_sectors"] == 1440
    assert "successfully read all 1440 sectors" in str(caught.value)
    assert "unsupported format" in str(caught.value)
    assert "disk could not be read" not in str(caught.value)
    assert image.USB_FLOPPY_720K_READ_GUIDANCE not in str(caught.value)


def test_successful_720k_read_opens_song_without_compatibility_warning(monkeypatch, tmp_path):
    source = tmp_path / "original.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[0], "TEST")
    data = bytearray(source.read_bytes())
    assert len(data) == 737280
    geometry = image._geometry_from_boot_sector(data[:512])
    for index in range(geometry.num_fats):
        fat = geometry.fat_offset + index * geometry.fat_size
        data[fat + 3:fat + 5] = b"\xff\x0f"  # Cluster 2 ends the file.
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    MID", 2, 4)
    data[geometry.data_offset:geometry.data_offset + 4] = b"SONG"
    device = ReadDevice(data)
    monkeypatch.setattr(image, "_open_block_device_for_read", lambda _path: device)
    messages = []
    session = image.FloppyImageSession.load_floppy(
        image.FloppyDriveInfo("A:", len(data)),
        progress_callback=lambda _current, _total, message: messages.append(message),
    )
    try:
        assert image._read_fat12_file_bytes(session.working_img_path, "SONG.MID") == b"SONG"
        assert session.read_diagnostics["fast_path"]["layout_probe"]["recognized"]
        assert session.read_diagnostics["detected_capacity_bytes"] == 737280
        assert not any("2DD" in message for message in messages)
        assert device.closed
    finally:
        session.cleanup()


@pytest.mark.parametrize("report_diagnostics", [False, True])
def test_fast_probe_io_failure_does_not_claim_invalid_structure(monkeypatch, tmp_path, report_diagnostics):
    device = ReadDevice()
    monkeypatch.setattr(image, "_open_block_device_for_read", lambda _path: device)
    diagnostics = {} if report_diagnostics else None
    with pytest.raises(image.FastFloppyReadError) as caught:
        image._read_floppy_device_fast_image("A:", tmp_path / "copy.img", 737280, diagnostics=diagnostics)
    message = str(caught.value)
    assert "could not be read well enough to determine its format" in message
    assert "Windows refused the disk read" in message
    assert image.USB_FLOPPY_720K_READ_GUIDANCE in message
    assert "valid FAT12" not in message
    assert device.closed
    assert not (tmp_path / "copy.img").exists()
    if diagnostics is not None:
        assert not diagnostics.get("successful_ranges")
        assert diagnostics["first_failed_read"]["winerror"] == 1785


@pytest.mark.parametrize("failure_stage", ["open", "raw_fallback"])
def test_windows_refusal_keeps_read_error_and_conditional_guidance(monkeypatch, failure_stage):
    def fail(*_args, **_kwargs):
        raise image.FloppyImageError("Windows refused the disk read", winerror=1785)

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_open_block_device_for_read", fail if failure_stage == "open" else lambda _path: ReadDevice())
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", fail)
    progress = []
    with pytest.raises(image.FloppyImageError) as caught:
        image.FloppyImageSession.load_floppy(
            image.FloppyDriveInfo("A:", 737280),
            progress_callback=lambda _step, _total, message: progress.append(message),
        )
    message = str(caught.value)
    assert "disk could not be read" in message
    assert "Windows refused the disk read" in message
    assert "recognized this disk" not in message
    assert "invalid" not in message.lower()
    assert image.USB_FLOPPY_720K_READ_GUIDANCE in message
    assert caught.value.read_diagnostics["final_error"]["winerror"] == 1785
    if failure_stage == "raw_fallback":
        fallback = next(message for message in progress if message.startswith("Fast floppy read unavailable"))
        assert "\n\nReading full floppy image" in fallback
        assert image.USB_FLOPPY_720K_READ_GUIDANCE in fallback
