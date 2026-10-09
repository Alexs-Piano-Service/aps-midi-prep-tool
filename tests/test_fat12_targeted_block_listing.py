"""Physical listings read directory sectors without imaging unused media."""

import os

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from test_fat12_block_listing_directories import _disk, _set_fat
from test_floppy_targeted_read import _nested


pytestmark = pytest.mark.skipif(not hasattr(os, "pread"), reason="POSIX block-device reader")


def _track_reads(monkeypatch, allowed, *, bad=()):
    original_read = image.os.pread
    original_open = image.os.open
    reads, descriptors = [], []

    def read(fd, size, offset):
        reads.append((offset, size))
        assert any(start <= offset and offset + size <= start + length for start, length in allowed), (
            f"Listing read song data or unused sectors at {offset}+{size}"
        )
        if any(offset < start + length and offset + size > start for start, length in bad):
            raise OSError("unreadable required directory sector")
        return original_read(fd, size, offset)

    def open_file(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    monkeypatch.setattr(image.os, "pread", read)
    monkeypatch.setattr(image.os, "open", open_file)
    monkeypatch.setattr(image, "_read_fat12_image_listing", lambda *_a, **_k: pytest.fail("Listing must not image the disk"))
    return reads, descriptors


def _assert_closed(descriptors):
    assert descriptors
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("layout", [0, 2])
def test_hidden_directory_listing_reads_no_song_data_or_unused_sectors(tmp_path, monkeypatch, layout):
    source, original, geometry = _disk(tmp_path, layout)
    directory = image._cluster_offset(geometry, 3)
    # Unused sectors and even the song payload are deliberately unavailable.
    # The active directory ends within its first sector; its tail is unused.
    reads, descriptors = _track_reads(monkeypatch, [(0, geometry.data_offset), (directory, 512)])
    diagnostics = {}

    listing = image._read_fat12_block_device_listing(source, diagnostics=diagnostics)

    assert [(entry.path, entry.size) for entry in listing.entries] == [("SONG.MID", 4)]
    assert diagnostics["fat_copies_structurally_valid"] == [1, 2]
    assert sum(size for offset, size in reads if offset >= geometry.data_offset) == 512
    assert source.read_bytes() == original
    _assert_closed(descriptors)


def test_unreadable_hidden_directory_fails_without_accepting_synthetic_zeros(tmp_path, monkeypatch):
    source, original, geometry = _disk(tmp_path, 2)
    directory = image._cluster_offset(geometry, 3)
    reads, descriptors = _track_reads(monkeypatch, [(0, geometry.data_offset), (directory, 512)], bad=[(directory, 512)])

    with pytest.raises(image.FloppyImageError, match="No FAT12 copy.*unreadable sectors"):
        image._read_fat12_block_device_listing(source)

    assert any(offset == directory for offset, _size in reads)
    assert source.read_bytes() == original
    _assert_closed(descriptors)


def test_targeted_listing_can_select_mirror_with_readable_directory_chain(tmp_path, monkeypatch):
    source, data, geometry = _disk(tmp_path, 2)
    directory = image._cluster_offset(geometry, 3)
    data[directory + 32:directory + geometry.cluster_size] = b"\xe5" * (geometry.cluster_size - 32)
    _set_fat(data, geometry, 3, 5)
    _set_fat(data, geometry, 5, 0xFFF)
    _set_fat(data, geometry, 9, 0xFFF)
    _set_fat(data, geometry, 3, 9, copies=[0])
    source.write_bytes(data)
    good_tail = image._cluster_offset(geometry, 5)
    bad_tail = image._cluster_offset(geometry, 9)
    reads, descriptors = _track_reads(monkeypatch, [
        (0, geometry.data_offset), (directory, 512), (good_tail, 512), (bad_tail, 512),
    ], bad=[(bad_tail, 512)])
    diagnostics = {}

    listing = image._read_fat12_block_device_listing(source, diagnostics=diagnostics)

    assert [entry.path for entry in listing.entries] == ["SONG.MID"]
    assert diagnostics["fat_selected_copy"] == 2
    assert diagnostics["fat_copies_structurally_valid"] == [2]
    assert sum(offset == directory for offset, _size in reads) == 1
    assert source.read_bytes() == data
    _assert_closed(descriptors)


def test_targeted_listing_rejects_different_valid_allocation_maps(tmp_path, monkeypatch):
    source, data, geometry = _disk(tmp_path, 2)
    _set_fat(data, geometry, 9, 0xFFF, copies=[0])
    source.write_bytes(data)
    directory = image._cluster_offset(geometry, 3)
    _reads, descriptors = _track_reads(monkeypatch, [(0, geometry.data_offset), (directory, 512)])

    with pytest.raises(image.FloppyImageError, match="conflicting allocation chains"):
        image._read_fat12_block_device_listing(source)

    assert source.read_bytes() == data
    _assert_closed(descriptors)


@pytest.mark.parametrize("rejected_mirror_tail", [False, True])
def test_fragmented_nested_directories_read_only_active_directory_sectors(tmp_path, monkeypatch, rejected_mirror_tail):
    source, data, geometry, _payload = _nested(tmp_path)
    if rejected_mirror_tail:
        # The second candidate's unused outer-directory tail aliases its own
        # child. It must not erase bytes already acquired for the good mirror.
        _set_fat(data, geometry, 6, 3, copies=[1])
    source.write_bytes(data)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    _reads, descriptors = _track_reads(monkeypatch, [
        (0, geometry.data_offset), (offset(2), geometry.cluster_size),
        (offset(6), 512), (offset(3), 512),
    ])
    diagnostics = {}

    listing = image._read_fat12_block_device_listing(source, diagnostics=diagnostics)

    assert [entry.path for entry in listing.entries] == ["SONGS/LIVE/SONG.MID", "SONGS/SIDE.TXT"]
    assert diagnostics["fat_selected_copy"] == 1
    assert diagnostics["fat_copies_structurally_valid"] == ([1] if rejected_mirror_tail else [1, 2])
    assert source.read_bytes() == data
    _assert_closed(descriptors)


@pytest.mark.parametrize("failure", ["cancelled", "stalled"])
def test_cancelled_or_stalled_directory_acquisition_closes_descriptor_without_retry(tmp_path, monkeypatch, failure):
    source, _data, geometry = _disk(tmp_path, 2)
    directory = image._cluster_offset(geometry, 3)
    _reads, descriptors = _track_reads(monkeypatch, [(0, geometry.data_offset), (directory, 512)])
    calls = []
    exception_type = image.FloppyOperationCancelled if failure == "cancelled" else image._FloppyReadStalled

    def fail(*_args, **_kwargs):
        calls.append(True)
        raise exception_type("stop directory acquisition")

    monkeypatch.setattr(image, "_read_floppy_directory_sectors", fail)
    with pytest.raises(exception_type, match="stop directory acquisition"):
        image._read_fat12_block_device_listing(source)

    assert len(calls) == 1
    _assert_closed(descriptors)


def test_mdr_directory_disk_retains_existing_contiguous_file_reader(tmp_path, monkeypatch):
    source, _data, _geometry = _disk(tmp_path, 2)
    monkeypatch.setattr(image.electone_mdr_to_midi, "root_directory_has_mdr_entries", lambda _root: True)
    marker = object()
    monkeypatch.setattr(image, "_read_fat12_image_listing", lambda path, **_kwargs: marker if path == source else None)
    monkeypatch.setattr(image, "_acquire_floppy_subdirectories", lambda *_a, **_k: pytest.fail("MDR compatibility must retain the full reader"))

    assert image._read_fat12_block_device_listing(source) is marker
