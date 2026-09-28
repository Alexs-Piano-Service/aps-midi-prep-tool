import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _set_fat(data, geometry, cluster, following):
    for copy in range(geometry.num_fats):
        offset = geometry.fat_offset + copy * geometry.fat_size + cluster + cluster // 2
        pair = int.from_bytes(data[offset:offset + 2], "little")
        if cluster & 1:
            pair = (pair & 0x000F) | (following << 4)
        else:
            pair = (pair & 0xF000) | following
        data[offset:offset + 2] = pair.to_bytes(2, "little")


def _blank_image(tmp_path):
    source = tmp_path / "directories.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[2], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    return source, data, geometry


def _directory_entry(name, cluster):
    return image._dos_directory_entry(name.encode("ascii").ljust(11), cluster, 0, 0x10)


@pytest.mark.parametrize("damage", [
    "fat_self_cycle",
    "fat_two_cluster_cycle",
    "self_reference",
    "two_directory_cycle",
    "sibling_alias",
    "shared_chain_tail",
    "deep_cycle",
])
def test_ordinary_listing_rejects_directory_cycles_and_reused_clusters(tmp_path, monkeypatch, damage):
    source, data, geometry = _blank_image(tmp_path)
    data[geometry.root_offset:geometry.root_offset + 32] = _directory_entry("A", 2)
    for cluster in range(2, 5):
        _set_fat(data, geometry, cluster, 0xFFF)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)

    if damage == "fat_self_cycle":
        _set_fat(data, geometry, 2, 2)
    elif damage == "fat_two_cluster_cycle":
        _set_fat(data, geometry, 2, 3)
        _set_fat(data, geometry, 3, 2)
    elif damage == "self_reference":
        data[offset(2):offset(2) + 32] = _directory_entry("SELF", 2)
    elif damage == "two_directory_cycle":
        data[offset(2):offset(2) + 32] = _directory_entry("B", 3)
        data[offset(3):offset(3) + 32] = _directory_entry("A", 2)
    elif damage == "sibling_alias":
        data[geometry.root_offset + 32:geometry.root_offset + 64] = _directory_entry("B", 2)
    elif damage == "shared_chain_tail":
        data[geometry.root_offset + 32:geometry.root_offset + 64] = _directory_entry("B", 3)
        _set_fat(data, geometry, 2, 4)
        _set_fat(data, geometry, 3, 4)
    else:
        # This cycle is deeper than Python's normal recursion limit.
        for cluster in range(2, 1202):
            _set_fat(data, geometry, cluster, 0xFFF)
            following = cluster + 1 if cluster < 1201 else 2
            data[offset(cluster):offset(cluster) + 32] = _directory_entry("D", following)
    source.write_bytes(data)

    # An installed fallback reader must not mask known directory corruption.
    monkeypatch.setattr(image.shutil, "which", lambda _command: "/fake/7z")
    monkeypatch.setattr(
        image, "_read_image_listing_with_7z",
        lambda *_args: pytest.fail("Corrupt FAT12 directories must not fall back to 7z"),
    )

    with pytest.raises(image.FloppyImageError, match="corrupt.*Recover Damaged Image"):
        image.read_image_listing(source)

    assert source.read_bytes() == data


def test_fragmented_nested_directories_allow_normal_dot_entries(tmp_path):
    source, data, geometry = _blank_image(tmp_path)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    data[geometry.root_offset:geometry.root_offset + 32] = _directory_entry("A", 2)
    for cluster in range(2, 7):
        _set_fat(data, geometry, cluster, 6 if cluster == 2 else 0xFFF)

    data[offset(2):offset(2) + 32] = _directory_entry(".", 2)
    data[offset(2) + 32:offset(2) + 64] = _directory_entry("..", 0)
    data[offset(2) + 64:offset(2) + 96] = _directory_entry("B", 3)
    data[offset(2) + 96:offset(2) + geometry.cluster_size] = b"\xe5" * (geometry.cluster_size - 96)
    data[offset(6):offset(6) + 32] = image._dos_directory_entry(b"SONG    FIL", 4, 4)
    data[offset(3):offset(3) + 32] = _directory_entry(".", 3)
    data[offset(3) + 32:offset(3) + 64] = _directory_entry("..", 2)
    data[offset(3) + 64:offset(3) + 96] = image._dos_directory_entry(b"DEEP    FIL", 5, 4)
    data[offset(4):offset(4) + 4] = b"SONG"
    data[offset(5):offset(5) + 4] = b"DEEP"
    source.write_bytes(data)

    listing = image.read_image_listing(source)

    assert [entry.path for entry in listing.entries] == ["A/B/DEEP.FIL", "A/SONG.FIL"]
    assert image._read_fat12_file_bytes(source, "A/B/DEEP.FIL") == b"DEEP"
    assert image._read_fat12_file_bytes(source, "A/SONG.FIL") == b"SONG"
