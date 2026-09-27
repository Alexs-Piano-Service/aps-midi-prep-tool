"""Image repair exposes protected files without rewriting their contents."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.boot_sector_repair import apply_boot_sector_repair
from test_boot_sector_repair import song_image, protect


def _set_fat(data, geometry, cluster, following):
    for copy in range(geometry.num_fats):
        offset = geometry.fat_offset + copy * geometry.fat_size + cluster + cluster // 2
        pair = int.from_bytes(data[offset:offset + 2], "little")
        if cluster & 1:
            pair = (pair & 0x000F) | (following << 4)
        else:
            pair = (pair & 0xF000) | following
        data[offset:offset + 2] = pair.to_bytes(2, "little")


def nested_image(tmp_path):
    source = tmp_path / "nested.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[0], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    root = geometry.root_offset
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    for cluster in range(2, 8):
        _set_fat(data, geometry, cluster, 5 if cluster == 3 else 0xFFF)
    edits = []

    def entry(position, name, cluster, size, attr, *, visible=True):
        data[position:position + 32] = image._dos_directory_entry(name, cluster, size, attr)
        if visible and attr & 0x06:
            edits.append(position + 11)

    entry(root, b"FOLDER     ", 3, 0, 0x17)
    entry(root + 32, b"ROOT    FIL", 2, 4, 0x27)
    entry(root + 64, b"\xe5ELETED FIL", 0, 0, 0x26, visible=False)
    entry(root + 96, b"TEST       ", 0, 0, 0x0E, visible=False)
    # Long-name records must retain their 0x0F attribute, including bits 0x06.
    entry(root + 128, b"Along name ", 0, 0, 0x0F, visible=False)
    entry(root + 160, b"EMPTY   FIL", 0, 0, 0x04)
    entry(root + 224, b"UNUSED  FIL", 0, 0, 0x26, visible=False)

    entry(offset(3), b".          ", 3, 0, 0x16, visible=False)
    entry(offset(3) + 32, b"..         ", 0, 0, 0x16, visible=False)
    entry(offset(3) + 64, b"DEEP       ", 6, 0, 0x16)
    data[offset(3) + 96:offset(3) + geometry.cluster_size] = b"\xe5" * (geometry.cluster_size - 96)
    entry(offset(5), b"NESTED  FIL", 4, 6, 0x22)
    entry(offset(5) + 64, b"UNUSED  FIL", 0, 0, 0x26, visible=False)
    entry(offset(6), b".          ", 6, 0, 0x10, visible=False)
    entry(offset(6) + 32, b"..         ", 3, 0, 0x10, visible=False)
    entry(offset(6) + 64, b"DEEP    FIL", 7, 4, 0x24)
    for cluster, content in [(2, b"ROOT"), (4, b"NESTED"), (7, b"DEEP")]:
        data[offset(cluster):offset(cluster) + len(content)] = content
    source.write_bytes(data)
    return source, bytes(data), geometry, edits


def test_visibility_edits_only_live_short_entry_attributes_in_fragmented_nested_directories(tmp_path):
    source, original, _, offsets = nested_image(tmp_path)
    expected = bytearray(original)
    for offset in offsets:
        expected[offset] &= ~0x06

    repaired = image._clear_fat12_hidden_system_flags(original)

    assert repaired == expected
    assert {i for i, (before, after) in enumerate(zip(original, repaired)) if before != after} == set(offsets)
    assert image._clear_fat12_hidden_system_flags(repaired) == repaired
    assert source.read_bytes() == original


@pytest.mark.parametrize("damage", ["cycle", "free_cluster", "reserved", "outside", "bad_dot", "parent_cycle", "file_overlap"])
def test_unsafe_directories_are_rejected_before_in_place_repair(tmp_path, damage):
    source, original, geometry, _ = nested_image(tmp_path)
    data = bytearray(original)
    if damage in {"cycle", "free_cluster", "reserved", "outside"}:
        following = {"cycle": 3, "free_cluster": 0, "reserved": 0xFF0, "outside": 0xF00}[damage]
        _set_fat(data, geometry, 5, following)
    elif damage == "bad_dot":
        data[image._cluster_offset(geometry, 3)] = ord("X")
    elif damage == "parent_cycle":
        start = image._cluster_offset(geometry, 3) + 64 + 26
        data[start:start + 2] = (3).to_bytes(2, "little")
    else:
        start = geometry.root_offset + 32 + 26
        data[start:start + 2] = (3).to_bytes(2, "little")
    source.write_bytes(data)

    with pytest.raises(image.FloppyImageError, match="safely read the image directories"):
        apply_boot_sector_repair(source, backup=True)

    assert source.read_bytes() == data
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("kind", ["valid", "blank", "unsigned", "omitted"])
def test_recover_damaged_image_exposes_files_and_keeps_source_and_song_bytes(tmp_path, kind):
    source, data, geometry = song_image(tmp_path)
    data[geometry.root_offset + 32 + 11] = 0x27
    original = protect(data, geometry, kind)
    source.write_bytes(original)

    session = image.FloppyImageSession.recover("image", str(source))
    try:
        repaired = Path(session.working_img_path).read_bytes()
        expected = bytearray(data)
        expected[geometry.root_offset + 32 + 11] = 0x21
        assert repaired[512:] == expected[512:]
        assert image._read_fat12_file_bytes(session.working_img_path, "SONG.FIL") == b"SONG"
        assert session.repair_changed
        assert session.source_boot_sector_repaired == (kind != "valid")
        assert source.read_bytes() == original
    finally:
        session.cleanup()


def test_recover_damaged_image_exposes_nested_directories(tmp_path):
    source, original, _, offsets = nested_image(tmp_path)
    session = image.FloppyImageSession.recover("image", str(source))
    try:
        repaired = Path(session.working_img_path).read_bytes()
        expected = bytearray(original)
        for offset in offsets:
            expected[offset] &= ~0x06
        assert repaired == expected
        for name, content in [("ROOT.FIL", b"ROOT"), ("FOLDER/NESTED.FIL", b"NESTED"), ("FOLDER/DEEP/DEEP.FIL", b"DEEP")]:
            assert image._read_fat12_file_bytes(session.working_img_path, name) == content
        assert source.read_bytes() == original
    finally:
        session.cleanup()
