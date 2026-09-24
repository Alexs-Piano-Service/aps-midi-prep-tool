from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image


def _song_image(tmp_path):
    source = tmp_path / "source.img"
    floppy_image._create_blank_fat12_image_from_layout(
        source, floppy_image._PROTECTED_FAT12_LAYOUTS[0], "TEST"
    )
    data = bytearray(source.read_bytes())
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    for index in range(geometry.num_fats):
        fat_start = geometry.fat_offset + index * geometry.fat_size
        data[fat_start + 3:fat_start + 5] = b"\xff\x0f"
    data[geometry.root_offset:geometry.root_offset + 32] = (
        floppy_image._dos_directory_entry(b"SONG    FIL", 2, 4)
    )
    data[geometry.data_offset:geometry.data_offset + 4] = b"SONG"
    return data, geometry


@pytest.mark.parametrize("boot_kind", ["valid", "blank", "f6", "unsigned", "omitted"])
def test_prepared_image_records_boot_repair_and_preserves_song(tmp_path, boot_kind):
    data, geometry = _song_image(tmp_path)
    if boot_kind == "blank":
        data[:512] = bytes(512)
    elif boot_kind == "f6":
        data[:512] = b"\xf6" * 512
    elif boot_kind == "unsigned":
        data[510:512] = b"\x00\x00"
    elif boot_kind == "omitted":
        data = data[512:]
    output = tmp_path / "prepared.img"

    result = floppy_image.prepare_yamaha_bytes(bytes(data), output)

    prepared = output.read_bytes()
    assert result.boot_sector_repaired == (boot_kind != "valid")
    assert floppy_image._geometry_from_boot_sector(prepared[:512]) == geometry
    assert prepared[geometry.data_offset:geometry.data_offset + 4] == b"SONG"
    assert [entry.path for entry in floppy_image.read_image_listing(output).entries] == ["SONG.FIL"]


@pytest.mark.parametrize("fallback", [False, True], ids=["fast-read", "full-image-read"])
@pytest.mark.parametrize("blank_boot", [False, True], ids=["valid-boot", "blank-boot"])
def test_floppy_session_retains_source_boot_repair_evidence(tmp_path, monkeypatch, fallback, blank_boot):
    data, geometry = _song_image(tmp_path)
    if blank_boot:
        data[:512] = bytes(512)

    class Device:
        def read_at(self, offset, size, _label):
            # File recovery changes the image even when its boot sector was valid.
            if offset >= geometry.data_offset:
                raise OSError("Unreadable song sector")
            return bytes(data[offset:offset + size])

        def close(self):
            pass

    monkeypatch.setattr(floppy_image, "_open_block_device_for_read", lambda _path: Device())
    if fallback:
        def reject_fast_read(*_args, **_kwargs):
            raise floppy_image.FastFloppyReadError("Full capture required", fallback_allowed=True)

        monkeypatch.setattr(floppy_image, "_read_floppy_device_fast_image", reject_fast_read)
        monkeypatch.setattr(
            floppy_image, "_read_block_device",
            lambda _source, output, *_args, **_kwargs: Path(output).write_bytes(data),
        )
        monkeypatch.setattr(floppy_image, "_read_windows_block_device_bytes", lambda *_a, **_k: bytes(data))

    session = floppy_image.FloppyImageSession.load_floppy(
        floppy_image.FloppyDriveInfo("A:", len(data))
    )
    try:
        assert session.source_boot_sector_repaired == blank_boot
        if not fallback:
            assert session.repair_changed  # Song recovery alone must not imply boot repair.
        assert floppy_image._geometry_from_boot_sector(Path(session.working_img_path).read_bytes()[:512]) == geometry
        assert [entry.path for entry in session.list_entries().entries] == ["SONG.FIL"]
    finally:
        session.cleanup()
