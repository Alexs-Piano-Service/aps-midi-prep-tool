"""New FAT12 volumes get independent serials; existing volumes keep theirs."""

from pathlib import Path
import uuid

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.mark.parametrize("layout", image._PROTECTED_FAT12_LAYOUTS, ids=lambda layout: layout["label"])
def test_new_volumes_with_matching_labels_have_independent_serials(tmp_path, monkeypatch, layout):
    values = iter([
        uuid.UUID("11111111-1111-4111-8111-111112345678"),
        uuid.UUID("22222222-2222-4222-8222-222287654321"),
    ])
    monkeypatch.setattr(image.uuid, "uuid4", lambda: next(values))
    disks = [tmp_path / "first.img", tmp_path / "second.img"]
    for disk in disks:
        image._create_blank_fat12_image_from_layout(disk, layout, "NO NAME")

    first, second = (disk.read_bytes() for disk in disks)
    assert int.from_bytes(first[39:43], "little") == 0x12345678
    assert int.from_bytes(second[39:43], "little") == 0x87654321
    assert first[:39] + first[43:] == second[:39] + second[43:]
    assert first[43:54] == b"NO NAME    "
    for disk in disks:
        assert disk.stat().st_size == image._layout_total_size(layout)
        assert image._geometry_from_boot_sector(disk.read_bytes()[:512]) is not None
        assert image._read_fat12_image_listing(disk).entries == []


def test_new_800k_images_get_independent_serials_through_public_creation(tmp_path, monkeypatch):
    values = iter([uuid.UUID(int=0x10203040), uuid.UUID(int=0x50607080)])
    monkeypatch.setattr(image.uuid, "uuid4", lambda: next(values))
    first, second = tmp_path / "first.img", tmp_path / "second.img"
    for disk in (first, second):
        image.create_blank_floppy_image(disk, image.DISK_FORMAT_BY_KEY["ibm.800"])

    assert first.read_bytes()[39:43] == bytes.fromhex("40302010")
    assert second.read_bytes()[39:43] == bytes.fromhex("80706050")


@pytest.mark.parametrize("repair_boot", [False, True], ids=["valid-boot", "relocated-boot"])
def test_preparing_and_editing_an_existing_image_preserves_its_serial(tmp_path, repair_boot):
    source = tmp_path / "original.img"
    image.create_blank_floppy_image(source, image.DISK_FORMAT_BY_KEY["ibm.800"], "ORIGINAL")
    song = tmp_path / "BEFORE.TXT"
    song.write_bytes(b"An existing file on the disk")
    image._copy_host_file_into_image(source, song, song.name)
    data = bytearray(source.read_bytes())
    serial = bytes.fromhex("f970d5af")
    data[39:43] = serial
    expected = bytes(data)
    if repair_boot:
        # Yamaha-protected disks can store the intact original boot sector
        # over FAT2. Restoring that boot must preserve its existing serial.
        geometry = image._geometry_from_boot_sector(data[:512])
        second_fat = geometry.fat_offset + geometry.fat_size
        data[second_fat:second_fat + 512] = data[:512]
        data[:512] = bytes(512)
    source.write_bytes(data)
    prepared = tmp_path / "prepared.img"

    result = image.prepare_yamaha_bytes(bytes(data), prepared)

    assert result.boot_sector_repaired is repair_boot
    assert prepared.read_bytes() == expected
    addition = tmp_path / "NOTE.TXT"
    addition.write_bytes(b"A file added to an existing disk")
    session = image.FloppyImageSession.load(prepared)
    try:
        modified = session.create_modified_image(additions={addition.name: str(addition)})
        output = tmp_path / "edited.img"
        session.write_image(modified, output, "img")
        assert Path(modified).read_bytes()[39:43] == serial
        assert output.read_bytes()[39:43] == serial
        assert image._read_fat12_file_bytes(output, addition.name) == addition.read_bytes()
        assert source.read_bytes() == data
    finally:
        session.cleanup()
