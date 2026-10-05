"""Logical floppy reads acquire required sectors and bounded directory graphs."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from test_fat12_directory_cycles import _directory_entry, _set_fat
from test_fat12_mirror_validation import _set_fat as _set_copy_fat


def _blank(tmp_path):
    source = tmp_path / "source.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[0], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    assert geometry.cluster_size == 1024
    return source, data, geometry


def _device(monkeypatch, data, bad_sectors):
    class Device:
        def __init__(self):
            self.calls = []
            self.closed = False

        def read_at(self, offset, size, _label):
            self.calls.append((offset, size))
            if any(offset < bad + 512 and offset + size > bad for bad in bad_sectors):
                raise OSError("unreadable test sector")
            return bytes(data[offset:offset + size])

        def close(self):
            self.closed = True

    device = Device()
    monkeypatch.setattr(image, "_open_block_device_for_read", lambda _path: device)
    return device


@pytest.mark.parametrize("required", [False, True], ids=["unreadable-slack", "unreadable-song-data"])
def test_fast_read_protects_file_sectors_without_reading_cluster_slack(tmp_path, monkeypatch, required):
    source, data, geometry = _blank(tmp_path)
    payload = bytes(range(100)) * (7 if required else 1)
    _set_fat(data, geometry, 2, 0xFFF)
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    MID", 2, len(payload))
    start = image._cluster_offset(geometry, 2)
    data[start:start + len(payload)] = payload
    source.write_bytes(data)
    bad = start + 512
    device = _device(monkeypatch, data, {bad})
    output = tmp_path / "working.img"
    output.write_bytes(b"previous image")
    diagnostics = {}

    if required:
        with pytest.raises(image.FastFloppyReadError, match="unreadable.*file-data") as error:
            image._read_floppy_device_fast_image("mock", output, len(data), diagnostics=diagnostics)
        assert not error.value.fallback_allowed
        assert output.read_bytes() == b"previous image"
    else:
        image._read_floppy_device_fast_image("mock", output, len(data), diagnostics=diagnostics)
        assert image._read_fat12_file_bytes(output, "SONG.MID") == payload
        assert not any(offset <= bad < offset + size for offset, size in device.calls)
        assert diagnostics["allocated_file_data_complete"]
        assert any(item["offset_bytes"] <= bad < item["offset_bytes"] + item["length_bytes"]
                   for item in diagnostics["omitted_ranges"])
    assert device.closed
    assert source.read_bytes() == data


@pytest.mark.parametrize("terminator_sector", [0, 1])
@pytest.mark.parametrize("protected_boot", [False, True])
def test_fast_read_stops_root_acquisition_after_a_readable_terminator(
    tmp_path, monkeypatch, terminator_sector, protected_boot,
):
    _source, data, geometry = _blank(tmp_path)
    _set_fat(data, geometry, 2, 0xFFF)
    root = geometry.root_offset + terminator_sector * 512
    data[geometry.root_offset:root] = b"\xE5" * (root - geometry.root_offset)
    data[root:root + 32] = image._dos_directory_entry(b"SONG    MID", 2, 4)
    data[geometry.data_offset:geometry.data_offset + 4] = b"SONG"
    if protected_boot:
        data[:512] = b"\xF6" * 512
    bad = root + 512
    device = _device(monkeypatch, data, {bad})
    output = tmp_path / "working.img"

    image._read_floppy_device_fast_image("mock", output, len(data))

    assert image._read_fat12_file_bytes(output, "SONG.MID") == b"SONG"
    assert not any(offset <= bad < offset + size for offset, size in device.calls)


def test_wrong_protected_layout_bad_root_does_not_prevent_a_later_valid_layout(tmp_path, monkeypatch):
    source = tmp_path / "source.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[2], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    _set_fat(data, geometry, 2, 0xFFF)
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    MID", 2, 4)
    data[geometry.data_offset:geometry.data_offset + 4] = b"SONG"
    data[:512] = b"\xF6" * 512
    wrong = image._fat12_geometry_from_layout(image._PROTECTED_FAT12_LAYOUTS[0])
    # A spare signature makes the wrong layout plausible, but its root offset
    # is in the damaged first FAT of the actual 1.44 MiB disk.
    offset = wrong.fat_offset + wrong.fat_size
    data[offset:offset + 3] = b"\xF9\xFF\xFF"
    _device(monkeypatch, data, {wrong.root_offset})
    output = tmp_path / "working.img"
    diagnostics = {}

    image._read_floppy_device_fast_image("mock", output, 2880 * 1024, diagnostics=diagnostics)

    assert diagnostics["detected_capacity_bytes"] == len(data)
    assert diagnostics["layout_probe"]["recognized"]
    assert diagnostics["fat_selected_copy"] == 2
    assert image._read_fat12_file_bytes(output, "SONG.MID") == b"SONG"


def _nested(tmp_path):
    source, data, geometry = _blank(tmp_path)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    # Both the directory and its MIDI file are fragmented.
    for cluster, following in ((2, 6), (3, 0xFFF), (4, 8), (5, 0xFFF), (6, 0xFFF), (8, 0xFFF)):
        _set_fat(data, geometry, cluster, following)
    data[geometry.root_offset:geometry.root_offset + 32] = _directory_entry("SONGS", 2)
    data[offset(2):offset(2) + 64] = _directory_entry(".", 2) + _directory_entry("..", 0)
    data[offset(2) + 64:offset(2) + geometry.cluster_size] = b"\xE5" * (geometry.cluster_size - 64)
    data[offset(6):offset(6) + 64] = _directory_entry("LIVE", 3) + image._dos_directory_entry(b"SIDE    TXT", 5, 4)
    data[offset(3):offset(3) + 64] = _directory_entry(".", 3) + _directory_entry("..", 2)
    text = b"A" * 1100
    track = b"\0\xFF\x01\x88\x4C" + text + b"\0\x90\x3C\x40\x20\x80\x3C\0\0\xFF\x2F\0"
    payload = b"MThd\0\0\0\x06\0\0\0\x01\x01\xE0MTrk" + len(track).to_bytes(4, "big") + track
    data[offset(3) + 64:offset(3) + 96] = image._dos_directory_entry(b"SONG    MID", 4, len(payload))
    data[offset(4):offset(4) + geometry.cluster_size] = payload[:geometry.cluster_size]
    tail = payload[geometry.cluster_size:]
    data[offset(8):offset(8) + len(tail)] = tail
    data[offset(5):offset(5) + 4] = b"SIDE"
    return source, data, geometry, payload


@pytest.mark.parametrize("alternate_fat", [False, True])
def test_targeted_acquisition_reads_fragmented_nested_songs_without_unused_sectors(
    tmp_path, monkeypatch, alternate_fat,
):
    source, data, geometry, payload = _nested(tmp_path)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    bad = {offset(7), offset(6) + 512, offset(8) + 512}
    if alternate_fat:
        _set_copy_fat(data, geometry, 0, 2, 9)
        _set_copy_fat(data, geometry, 0, 9, 0xFFF)
        bad.add(offset(9))
    source.write_bytes(data)
    device = _device(monkeypatch, data, bad)
    output = tmp_path / "working.img"
    diagnostics = {}

    result = image._read_floppy_device_fast_image("mock", output, len(data), diagnostics=diagnostics)

    assert [entry.path for entry in image.read_image_listing(output).entries] == ["SONGS/LIVE/SONG.MID", "SONGS/SIDE.TXT"]
    assert image._read_fat12_file_bytes(output, "SONGS/LIVE/SONG.MID") == payload
    assert image._read_fat12_file_bytes(output, "SONGS/SIDE.TXT") == b"SIDE"
    assert diagnostics["allocated_file_data_complete"] and diagnostics["file_map_complete"]
    if alternate_fat:
        assert diagnostics["fat_selected_copy"] == 2
        assert "FAT 2" in result.note
    assert not any(start <= sector < start + size for start, size in device.calls
                   for sector in bad - ({offset(9)} if alternate_fat else set()))
    assert device.closed
    assert source.read_bytes() == data


def test_ordinary_floppy_open_keeps_nested_song_bytes_without_full_disk_fallback(tmp_path, monkeypatch):
    source, data, geometry, payload = _nested(tmp_path)
    source.write_bytes(data)
    _device(monkeypatch, data, {image._cluster_offset(geometry, 7)})
    monkeypatch.setattr(image, "_read_block_device", lambda *_args, **_kwargs: pytest.fail("Unallocated sectors must not trigger full imaging"))
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", lambda *_args, **_kwargs: pytest.fail("Unallocated sectors must not trigger full imaging"))
    drive = image.FloppyDriveInfo("mock", len(data), model="Mock floppy")

    session = image.FloppyImageSession.load_floppy(drive)
    try:
        assert Path(session.extract_file("SONGS/LIVE/SONG.MID")).read_bytes() == payload
        assert session.read_diagnostics["read_method"] == "logical_working_image"
        assert session.read_diagnostics["exact_raw_copy"] is False
    finally:
        session.cleanup()
    assert source.read_bytes() == data


def test_rejected_fat_directory_tail_cannot_erase_an_acquired_nested_directory(tmp_path, monkeypatch):
    _source, data, geometry, payload = _nested(tmp_path)
    # FAT 2 falsely allocates LIVE's cluster as an unused tail of SONGS.
    # Its terminator prevents reading that tail; validating this bad mirror
    # must not replace the already acquired LIVE directory with synthetic zeros.
    _set_copy_fat(data, geometry, 1, 6, 3)
    _device(monkeypatch, data, set())
    output = tmp_path / "working.img"
    diagnostics = {}

    image._read_floppy_device_fast_image("mock", output, len(data), diagnostics=diagnostics)

    assert diagnostics["fat_selected_copy"] == 1
    assert image._read_fat12_file_bytes(output, "SONGS/LIVE/SONG.MID") == payload


def test_directory_bytes_read_from_a_rejected_graph_are_not_reported_as_omitted(tmp_path, monkeypatch):
    _source, data, geometry, payload = _nested(tmp_path)
    # FAT 2 supplies a readable but incorrect SONGS tail, whose nested entry
    # cross-links with SONGS itself. It is rejected after that sector is read.
    _set_copy_fat(data, geometry, 1, 2, 9)
    _set_copy_fat(data, geometry, 1, 9, 0xFFF)
    offset = image._cluster_offset(geometry, 9)
    data[offset:offset + 32] = _directory_entry("CYCLE", 2)
    _device(monkeypatch, data, set())
    output = tmp_path / "working.img"
    diagnostics = {}

    image._read_floppy_device_fast_image("mock", output, len(data), diagnostics=diagnostics)

    assert diagnostics["fat_selected_copy"] == 1
    assert image._read_fat12_file_bytes(output, "SONGS/LIVE/SONG.MID") == payload
    assert output.read_bytes()[offset:offset + 512] == data[offset:offset + 512]
    assert not any(item["offset_bytes"] <= offset < item["offset_bytes"] + item["length_bytes"]
                   for item in diagnostics["omitted_ranges"])


def test_targeted_acquisition_rejects_conflicting_valid_nested_file_chains(tmp_path, monkeypatch):
    _source, data, geometry, payload = _nested(tmp_path)
    _set_copy_fat(data, geometry, 0, 4, 9)
    _set_copy_fat(data, geometry, 0, 9, 0xFFF)
    start = image._cluster_offset(geometry, 9)
    tail = payload[geometry.cluster_size:]
    data[start:start + len(tail)] = tail.replace(b"A", b"B")
    _device(monkeypatch, data, set())
    output = tmp_path / "previous.img"
    output.write_bytes(b"previous image")

    with pytest.raises(image.FastFloppyReadError, match="conflicting.*Recover Damaged Image") as error:
        image._read_floppy_device_fast_image("mock", output, len(data))

    assert not error.value.fallback_allowed
    assert output.read_bytes() == b"previous image"


def test_targeted_acquisition_requires_readable_active_subdirectory_sectors(tmp_path, monkeypatch):
    _source, data, geometry, _payload = _nested(tmp_path)
    _device(monkeypatch, data, {image._cluster_offset(geometry, 3)})
    output = tmp_path / "previous.img"
    output.write_bytes(b"previous image")

    with pytest.raises(image.FastFloppyReadError, match="Start in recovery mode") as error:
        image._read_floppy_device_fast_image("mock", output, len(data))

    assert not error.value.fallback_allowed
    assert output.read_bytes() == b"previous image"


@pytest.mark.parametrize("failure", ["cancelled", "stalled"])
def test_directory_acquisition_does_not_retry_cancellation_or_a_stalled_read(tmp_path, monkeypatch, failure):
    _source, data, geometry, _payload = _nested(tmp_path)
    bad = image._cluster_offset(geometry, 3)
    calls = []

    class Device:
        def read_at_recovery(self, offset, size, _label, **_kwargs):
            if offset == bad:
                calls.append(offset)
                if failure == "cancelled":
                    raise image.FloppyOperationCancelled("cancelled directory read")
                raise image._RecoveryReadDeadlineExceeded("stalled directory read")
            return bytes(data[offset:offset + size])

        def close(self):
            pass

    monkeypatch.setattr(image, "_open_block_device_for_read", lambda _path: Device())
    output = tmp_path / "previous.img"
    output.write_bytes(b"previous image")
    error_type = image.FloppyOperationCancelled if failure == "cancelled" else image.FastFloppyReadError

    with pytest.raises(error_type):
        image._read_floppy_device_fast_image("mock", output, len(data))

    assert calls == [bad]
    assert output.read_bytes() == b"previous image"


@pytest.mark.parametrize("damage", ["directory_cycle", "directory_alias", "file_directory_crosslink", "file_tail_crosslink"])
def test_targeted_acquisition_rejects_directory_corruption_before_publication(tmp_path, monkeypatch, damage):
    _source, data, geometry, _payload = _nested(tmp_path)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    if damage == "directory_cycle":
        data[offset(6):offset(6) + 32] = _directory_entry("SELF", 2)
    elif damage == "directory_alias":
        data[offset(6) + 32:offset(6) + 64] = _directory_entry("ALIAS", 3)
    elif damage == "file_directory_crosslink":
        data[offset(6) + 32:offset(6) + 64] = image._dos_directory_entry(b"SIDE    TXT", 3, 4)
    else:
        _set_fat(data, geometry, 5, 8)
    _device(monkeypatch, data, set())
    output = tmp_path / "previous.img"
    output.write_bytes(b"previous image")

    with pytest.raises(image.FastFloppyReadError, match="corrupt.*Recover Damaged Image") as error:
        image._read_floppy_device_fast_image("mock", output, len(data))

    assert not error.value.fallback_allowed
    assert output.read_bytes() == b"previous image"
