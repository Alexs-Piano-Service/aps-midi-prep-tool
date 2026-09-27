"""Visibility repair must not block formats recognized without a FAT12 boot."""

from pathlib import Path

from aps_midi_prep_tool_app import floppy_image as image
from test_boot_sector_repair import song_image, visible_song_image


def test_recovery_retains_electone_geometry_fallback_without_a_signed_boot_sector(tmp_path):
    source = tmp_path / "electone.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[0], "MDR")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    data[:512] = bytes(512)
    data[geometry.fat_offset + 3:geometry.fat_offset + 5] = b"\xff\x0f"
    # A bad second FAT prevents boot reconstruction, but the Electone reader
    # still recognizes the directory and reads the file through the first FAT.
    fat2 = geometry.fat_offset + geometry.fat_size
    data[fat2:fat2 + 3] = bytes(3)
    payload = b"Electone performance data"
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(
        b"MDR_00  EVT", 2, len(payload),
    )
    data[geometry.root_offset + 32:geometry.root_offset + 64] = bytes(32)
    data[geometry.data_offset:geometry.data_offset + len(payload)] = payload
    source.write_bytes(data)

    prepared = tmp_path / "prepared.img"
    repair = image.prepare_yamaha_image(source, prepared)
    assert not repair.changed
    assert image._geometry_from_boot_sector(prepared.read_bytes()[:512]) is None
    assert [entry.path for entry in image.read_image_listing(prepared).entries] == ["MDR_00.EVT"]

    session = image.FloppyImageSession.recover("image", str(source))
    try:
        assert Path(session.working_img_path).read_bytes() == data
        assert image._read_fat12_file_bytes(session.working_img_path, "MDR_00.EVT") == payload
        assert source.read_bytes() == data
    finally:
        session.cleanup()


def test_recovery_preserves_trailing_capture_bytes_outside_fat_volume(tmp_path):
    source, data, geometry = song_image(tmp_path)
    # A full 1.44 MB capture may contain a smaller, valid 720 KB FAT volume.
    # Keep the capture size supported by the existing recovery format selector.
    capture_size = max(image._layout_total_size(layout) for layout in image._PROTECTED_FAT12_LAYOUTS)
    padding = (b"trailing capture data " * capture_size)[:capture_size - len(data)]
    original = bytes(data) + padding
    source.write_bytes(original)

    session = image.FloppyImageSession.recover("image", str(source))
    try:
        assert Path(session.working_img_path).read_bytes() == visible_song_image(data, geometry) + padding
        assert image._read_fat12_file_bytes(session.working_img_path, "SONG.FIL") == b"SONG"
        assert source.read_bytes() == original
    finally:
        session.cleanup()
