"""Physical-drive listings validate directories hidden from the song list."""

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(not hasattr(image.os, "pread"), reason="POSIX block-device reader")


def _set_fat(data, geometry, cluster, following, *, copies=None):
    for copy in range(geometry.num_fats) if copies is None else copies:
        offset = geometry.fat_offset + copy * geometry.fat_size + cluster + cluster // 2
        pair = int.from_bytes(data[offset:offset + 2], "little")
        pair = (pair & 0x000F) | (following << 4) if cluster & 1 else (pair & 0xF000) | following
        data[offset:offset + 2] = pair.to_bytes(2, "little")


def _disk(tmp_path, layout_index, *, with_directory=True):
    source = tmp_path / "floppy.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[layout_index], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    _set_fat(data, geometry, 2, 0xFFF)
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    MID", 2, 4)
    offset = image._cluster_offset(geometry, 2)
    data[offset:offset + 4] = b"SONG"
    if with_directory:
        _set_fat(data, geometry, 3, 0xFFF)
        _set_fat(data, geometry, 4, 0xFFF)
        data[geometry.root_offset + 32:geometry.root_offset + 64] = image._dos_directory_entry(
            b"SYSTEM~1   ", 3, 0, 0x16,
        )
        offset = image._cluster_offset(geometry, 3)
        data[offset:offset + 32] = image._dos_directory_entry(b"WPSETT~1DAT", 4, 4)
        offset = image._cluster_offset(geometry, 4)
        data[offset:offset + 4] = b"META"
    source.write_bytes(data)
    return source, data, geometry


@pytest.mark.parametrize("layout_index", [0, 2])
def test_physical_listing_validates_hidden_windows_directory_without_listing_its_files(tmp_path, monkeypatch, layout_index):
    source, data, geometry = _disk(tmp_path, layout_index)
    monkeypatch.setattr(image, "_is_block_device_path", lambda _path: True)
    diagnostics = {}

    listing = image.read_image_listing(source, diagnostics=diagnostics)

    assert [(entry.path, entry.size) for entry in listing.entries] == [("SONG.MID", 4)]
    assert listing.free_space == (image._fat12_data_cluster_count(geometry) - 3) * geometry.cluster_size
    assert diagnostics["fat_copies_structurally_valid"] == [1, 2]
    assert source.read_bytes() == data


def test_physical_listing_uses_sound_mirror_for_hidden_directory(tmp_path, monkeypatch):
    source, data, geometry = _disk(tmp_path, 2)
    _set_fat(data, geometry, 3, 0, copies=[0])
    source.write_bytes(data)
    monkeypatch.setattr(image, "_is_block_device_path", lambda _path: True)
    diagnostics = {}

    listing = image.read_image_listing(source, diagnostics=diagnostics)

    assert [entry.path for entry in listing.entries] == ["SONG.MID"]
    assert diagnostics["fat_selected_copy"] == 2
    assert diagnostics["fat_copies_structurally_valid"] == [2]
    assert source.read_bytes() == data


@pytest.mark.parametrize("damage", ["cross_linked_file", "invalid_child_cluster", "directory_cycle"])
def test_hidden_directory_contents_still_fail_closed_when_corrupt(tmp_path, monkeypatch, damage):
    source, data, geometry = _disk(tmp_path, 2)
    offset = image._cluster_offset(geometry, 3)
    if damage == "cross_linked_file":
        data[offset:offset + 32] = image._dos_directory_entry(b"SHARED  DAT", 2, 4)
    elif damage == "invalid_child_cluster":
        invalid_cluster = image._fat12_data_cluster_count(geometry) + 2
        data[offset:offset + 32] = image._dos_directory_entry(b"INVALID DAT", invalid_cluster, 4)
    else:
        data[offset:offset + 32] = image._dos_directory_entry(b"SELF       ", 3, 0, 0x10)
    source.write_bytes(data)
    monkeypatch.setattr(image, "_is_block_device_path", lambda _path: True)
    monkeypatch.setattr(image, "_read_image_listing_with_7z", lambda *_args: pytest.fail("Corruption must not use external fallback"))

    with pytest.raises(image.FloppyImageError, match="corrupt.*Recover Damaged Image"):
        image.read_image_listing(source)

    assert source.read_bytes() == data


def test_flat_physical_listing_does_not_read_nonempty_file_payload(tmp_path, monkeypatch):
    source, data, geometry = _disk(tmp_path, 2, with_directory=False)
    original_pread = image.os.pread
    reads = []

    def read(fd, size, offset):
        assert offset + size <= geometry.data_offset, "Flat listing must stay within filesystem metadata"
        reads.append((offset, size))
        return original_pread(fd, size, offset)

    monkeypatch.setattr(image.os, "pread", read)
    monkeypatch.setattr(image, "_is_block_device_path", lambda _path: True)
    monkeypatch.setattr(image, "_read_fat12_image_listing", lambda *_args, **_kwargs: pytest.fail("Flat listing must not use full-image fallback"))

    listing = image.read_image_listing(source)

    assert [(entry.path, entry.size) for entry in listing.entries] == [("SONG.MID", 4)]
    assert reads
    assert source.read_bytes() == data
