"""Exact HFE capture verifies all acquired sectors before replacing its output."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.mark.parametrize("decoded", ["changed", "short", "exact"])
def test_hfe_capture_verifies_full_non_fat_image_before_publication(tmp_path, monkeypatch, decoded):
    disk_format = next(item for item in image.DISK_FORMATS if item.key == "ibm.720")
    captured = b"\x00" * (disk_format.size_bytes - 1) + b"\x63"
    source = tmp_path / "source.img"
    source.write_bytes(captured)
    output = tmp_path / "capture.hfe"
    previous = b"previous good HFE"
    output.write_bytes(previous)
    conversions = []

    def read(device, destination, size, **_kwargs):
        assert device == str(source)
        assert size == len(captured)
        Path(destination).write_bytes(captured)

    def convert(source_path, destination, selected_format, cancel_callback=None):
        assert selected_format == disk_format.key
        assert output.read_bytes() == previous
        conversions.append((Path(source_path).suffix, Path(destination).suffix))
        if Path(source_path).suffix == ".img":
            assert Path(source_path).read_bytes() == captured
            Path(destination).write_bytes(b"staged HFE")
        else:
            assert Path(source_path).read_bytes() == b"staged HFE"
            raw = captured if decoded == "exact" else captured[:-1]
            if decoded == "changed":
                raw += b"\x64"
            Path(destination).write_bytes(raw)
        return ""

    monkeypatch.setattr(image, "_read_block_device", read)
    monkeypatch.setattr(image, "_gw_convert", convert)
    drive = image.FloppyDriveInfo(str(source), len(captured))

    if decoded == "exact":
        assert image.capture_floppy_drive_image(drive, output, disk_format) == str(output)
        assert output.read_bytes() == b"staged HFE"
    else:
        with pytest.raises(image.FloppyImageError, match="sector data differs"):
            image.capture_floppy_drive_image(drive, output, disk_format)
        assert output.read_bytes() == previous
    assert conversions == [(".img", ".hfe"), (".hfe", ".img")]
    assert source.read_bytes() == captured
    assert set(tmp_path.iterdir()) == {source, output}


def test_cancelled_hfe_readback_preserves_existing_destination(tmp_path, monkeypatch):
    disk_format = next(item for item in image.DISK_FORMATS if item.key == "ibm.720")
    output = tmp_path / "capture.hfe"
    output.write_bytes(b"previous good HFE")
    cancelled = False

    def read(_device, destination, _size, **_kwargs):
        Path(destination).write_bytes(b"captured sectors")

    def convert(source_path, destination, selected_format, cancel_callback=None):
        nonlocal cancelled
        assert selected_format == disk_format.key
        if Path(source_path).suffix == ".hfe":
            cancelled = True
            assert cancel_callback is not None and cancel_callback()
            raise image.FloppyOperationCancelled("Operation cancelled.")
        Path(destination).write_bytes(b"staged HFE")
        return ""

    monkeypatch.setattr(image, "_read_block_device", read)
    monkeypatch.setattr(image, "_gw_convert", convert)
    with pytest.raises(image.FloppyOperationCancelled):
        image.capture_floppy_drive_image(
            image.FloppyDriveInfo("fake drive", disk_format.size_bytes), output, disk_format,
            cancel_callback=lambda: cancelled,
        )
    assert output.read_bytes() == b"previous good HFE"
    assert list(tmp_path.iterdir()) == [output]


def test_real_hfe_capture_round_trip_preserves_non_fat_sectors(tmp_path, monkeypatch):
    if not image._find_gw():
        pytest.skip("Greaseweazle is required")
    disk_format = next(item for item in image.DISK_FORMATS if item.key == "ibm.720")
    captured = bytes(512) + bytes(range(256)) * ((disk_format.size_bytes - 512) // 256)
    source = tmp_path / "source.img"
    source.write_bytes(captured)
    output = tmp_path / "capture.hfe"

    def read(_device, destination, _size, **_kwargs):
        Path(destination).write_bytes(captured)

    monkeypatch.setattr(image, "_read_block_device", read)
    assert image.capture_floppy_drive_image(
        image.FloppyDriveInfo(str(source), len(captured)), output, disk_format,
    ) == str(output)
    readback = tmp_path / "readback.img"
    image._gw_convert(output, readback, disk_format.key)
    assert readback.read_bytes() == captured
    assert source.read_bytes() == captured
