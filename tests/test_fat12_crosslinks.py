"""Every allocated cluster must have one owner before an image is editable."""

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from test_fat12_directory_cycles import _blank_image, _directory_entry, _set_fat
from test_fat12_mirror_validation import _no_external_fallback, _set_fat as _set_copy_fat


def _file(name, cluster, size=4):
    return image._dos_directory_entry(name.encode().ljust(8) + b"MID", cluster, size)


@pytest.mark.parametrize("damage", [
    "same_start", "converging_chains", "allocated_tail", "file_before_directory",
    "directory_before_file", "nested_file", "hidden_metadata", "empty_allocated_file",
])
def test_crosslinked_files_and_directories_reject_listing_preparation_and_extraction(
    tmp_path, monkeypatch, damage,
):
    source, data, geometry = _blank_image(tmp_path)
    for cluster in (2, 3, 4):
        _set_fat(data, geometry, cluster, 0xFFF)
    entries = [_file("FIRST", 2), _file("SECOND", 2)]
    if damage == "converging_chains":
        _set_fat(data, geometry, 2, 4)
        _set_fat(data, geometry, 3, 4)
        entries = [_file("FIRST", 2, geometry.cluster_size + 4),
                   _file("SECOND", 3, geometry.cluster_size + 4)]
    elif damage == "allocated_tail":
        _set_fat(data, geometry, 2, 4)
        entries = [_file("FIRST", 2), _file("SECOND", 4)]
    elif damage in {"file_before_directory", "directory_before_file"}:
        entries = [_file("FIRST", 2), _directory_entry("FOLDER", 2)]
        if damage == "directory_before_file":
            entries.reverse()
    elif damage == "nested_file":
        entries = [_file("FIRST", 3), _directory_entry("FOLDER", 2)]
        start = image._cluster_offset(geometry, 2)
        data[start:start + 32] = _file("NESTED", 3)
    elif damage == "hidden_metadata":
        entries[1] = image._dos_directory_entry(b"WPSETT~1DAT", 2, 4, 0x06)
    elif damage == "empty_allocated_file":
        entries[0] = _file("EMPTY", 2, 0)
    data[geometry.root_offset:geometry.root_offset + 64] = b"".join(entries)
    source.write_bytes(data)
    original = source.read_bytes()
    _no_external_fallback(monkeypatch)

    with pytest.raises(image._Fat12CorruptionError, match="cross-linked.*Recover Damaged Image"):
        image.read_image_listing(source)
    working = tmp_path / "working.img"
    with pytest.raises(image._Fat12CorruptionError, match="cross-linked"):
        image.prepare_yamaha_image(source, working)
    assert not working.exists()
    session = object.__new__(image.FloppyImageSession)
    with pytest.raises(image._Fat12CorruptionError, match="cross-linked"):
        session._extract_from_image(source, "FIRST.MID", tmp_path / "song.mid")
    assert source.read_bytes() == original


def test_crosslinked_fat_is_rejected_in_favor_of_a_complete_unshared_mirror(tmp_path):
    source, data, geometry = _blank_image(tmp_path)
    for cluster in (2, 3, 4):
        _set_fat(data, geometry, cluster, 0xFFF)
    _set_copy_fat(data, geometry, 0, 2, 4)
    _set_copy_fat(data, geometry, 0, 3, 4)
    data[geometry.root_offset:geometry.root_offset + 64] = _file("FIRST", 2) + _file("SECOND", 3)
    for cluster, payload in ((2, b"ONE!"), (3, b"TWO!")):
        start = image._cluster_offset(geometry, cluster)
        data[start:start + 4] = payload
    source.write_bytes(data)
    diagnostics = {}
    assert len(image.read_image_listing(source, diagnostics=diagnostics).entries) == 2
    assert diagnostics["fat_selected_copy"] == 2
    assert diagnostics["fat_copies_structurally_valid"] == [2]
    assert "cross-linked" in diagnostics["fat_copy_errors"][0]
    assert image._read_fat12_file_bytes(source, "FIRST.MID") == b"ONE!"
    assert image._read_fat12_file_bytes(source, "SECOND.MID") == b"TWO!"


def test_distinct_empty_files_can_share_the_unallocated_cluster_zero_marker(tmp_path):
    source, data, geometry = _blank_image(tmp_path)
    data[geometry.root_offset:geometry.root_offset + 64] = _file("FIRST", 0, 0) + _file("SECOND", 0, 0)
    source.write_bytes(data)
    assert [entry.path for entry in image.read_image_listing(source).entries] == ["FIRST.MID", "SECOND.MID"]
