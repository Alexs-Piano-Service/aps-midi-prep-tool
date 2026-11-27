"""Recovery distinguishes FAT12 layouts that have the same disk capacity."""

from dataclasses import asdict
import json

import pytest

from aps_midi_prep_tool_app import floppy_image as image


FORMAT = image.DISK_FORMAT_BY_KEY["ibm.720"]


def _set_fat(data, geometry, cluster, following):
    for index in range(geometry.num_fats):
        offset = geometry.fat_offset + index * geometry.fat_size + cluster + cluster // 2
        pair = int.from_bytes(data[offset:offset + 2], "little")
        pair = (pair & 0x000F) | (following << 4) if cluster & 1 else (pair & 0xF000) | following
        data[offset:offset + 2] = pair.to_bytes(2, "little")


def _fragmented_image(tmp_path, **layout_changes):
    layout = {**image._PROTECTED_FAT12_LAYOUTS[0], **layout_changes}
    source = tmp_path / "fragmented.img"
    image._create_blank_fat12_image_from_layout(source, layout, "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    assert geometry is not None
    track = b"\0\x90\x3c\x50\x20\x80\x3c\0" * 200 + b"\0\xff\x2f\0"
    payload = b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk" + len(track).to_bytes(4, "big") + track
    chunks = [payload[start:start + geometry.cluster_size]
              for start in range(0, len(payload), geometry.cluster_size)]
    assert len(chunks) > 1
    clusters = list(range(2, 2 + 2 * len(chunks), 2))
    for index, (cluster, chunk) in enumerate(zip(clusters, chunks)):
        _set_fat(data, geometry, cluster, clusters[index + 1] if index + 1 < len(clusters) else 0xFFF)
        offset = image._cluster_offset(geometry, cluster)
        data[offset:offset + len(chunk)] = chunk
        # A contiguous carve would include unrelated bytes between fragments.
        gap = offset + geometry.cluster_size
        data[gap:gap + geometry.cluster_size] = b"N" * geometry.cluster_size
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(
        b"SONG    MID", clusters[0], len(payload),
    )
    source.write_bytes(data)
    return source, data, geometry, payload


@pytest.mark.parametrize("layout_changes", [
    {"root_entries": 224},
    {"sectors_per_cluster": 1, "sectors_per_fat": 5},
    {"reserved_sectors": 2},
    {"sectors_per_fat": 4},
    {"num_fats": 1},
], ids=["root-size", "cluster-size", "reserved-sectors", "fat-size", "fat-count"])
@pytest.mark.parametrize("with_diagnostics", [False, True])
def test_recovery_prefers_actual_geometry_to_same_capacity_hint(tmp_path, layout_changes, with_diagnostics):
    source, data, geometry, payload = _fragmented_image(tmp_path, **layout_changes)
    diagnostics = {} if with_diagnostics else None

    recovered = image._recover_files_from_raw_image_bytes(
        bytes(data), disk_format_hint=FORMAT, diagnostics=diagnostics,
    )

    assert [(song.image_path, song.origin, song.data) for song in recovered] == [
        ("SONG.MID", "fat", payload),
    ]
    assert recovered[0].source_offset == geometry.data_offset
    assert source.read_bytes() == data
    if diagnostics is not None:
        scans = {image.Fat12Geometry(**scan["geometry"]): scan
                 for scan in diagnostics["geometry_scans"]}
        actual = scans[geometry]
        assumed = scans[image._geometry_for_disk_format_hint(FORMAT)]
        assert actual["boot_hint"] and not actual["selected_hint"]
        assert actual["attempted"] and actual["recovered_files"] == 1
        assert assumed["selected_hint"] and not assumed["boot_hint"]
        assert not assumed["attempted"] and assumed["recovered_files"] == 0
        assert diagnostics["detection_basis"] == "validated_boot_sector"
        assert diagnostics["fat_copies_expected"] == geometry.num_fats
        assert asdict(geometry) in [scan["geometry"] for scan in json.loads(json.dumps(diagnostics))["geometry_scans"]]
        assert f"root entries {geometry.root_entries}" in diagnostics["human_report"]


def test_recovery_reads_entries_beyond_the_standard_root_size(tmp_path):
    _source, data, geometry, payload = _fragmented_image(tmp_path, root_entries=224)
    entry = data[geometry.root_offset:geometry.root_offset + 32]
    data[geometry.root_offset:geometry.root_offset + 120 * 32] = (b"\xe5" + bytes(31)) * 120
    entry_offset = geometry.root_offset + 120 * 32
    data[entry_offset:entry_offset + 32] = entry

    recovered = image._recover_files_from_raw_image_bytes(bytes(data), disk_format_hint=FORMAT)

    assert len(recovered) == 1
    assert recovered[0].data == payload
    assert recovered[0].source_entry_offset == entry_offset


@pytest.mark.parametrize("boot_damage", ["missing-signature", "wrong-root-size"])
def test_geometry_fallback_still_recovers_fragmented_files(tmp_path, boot_damage):
    _source, data, geometry, payload = _fragmented_image(tmp_path)
    if boot_damage == "missing-signature":
        data[510:512] = bytes(2)
    else:
        data[17:19] = (224).to_bytes(2, "little")

    diagnostics = {}
    recovered = image._recover_files_from_raw_image_bytes(
        bytes(data), disk_format_hint=FORMAT, diagnostics=diagnostics,
    )

    assert [(song.image_path, song.origin, song.data) for song in recovered] == [
        ("SONG.MID", "fat", payload),
    ]
    assert recovered[0].source_offset == geometry.data_offset
    scans = {image.Fat12Geometry(**scan["geometry"]): scan for scan in diagnostics["geometry_scans"]}
    assert scans[geometry]["attempted"]
    assert scans[geometry]["recovered_files"] == 1
    if boot_damage == "wrong-root-size":
        boot_geometry = image._geometry_from_boot_sector(data[:512])
        assert scans[boot_geometry]["attempted"]
        assert scans[boot_geometry]["recovered_files"] == 0


def test_geometry_candidates_keep_distinct_layouts_and_deduplicate_exact_matches(tmp_path):
    _source, data, geometry, _payload = _fragmented_image(tmp_path, root_entries=224)

    geometries = image._recovery_geometries_for_data(bytes(data), disk_format_hint=FORMAT)

    assert geometries == [geometry, image._geometry_for_disk_format_hint(FORMAT)]


def test_validated_boot_layout_wins_over_a_plausible_same_capacity_candidate(tmp_path):
    _source, data, geometry, payload = _fragmented_image(tmp_path, root_entries=224)
    assumed = image._geometry_for_disk_format_hint(FORMAT)
    # Put a false song in unused root-directory space at the assumed data
    # offsets. Both layouts can recover a MIDI header and a complete chain.
    decoy = payload.replace(b"\x3c\x50", b"\x40\x50")
    for cluster, chunk in ((2, decoy[:assumed.cluster_size]), (4, decoy[assumed.cluster_size:])):
        offset = image._cluster_offset(assumed, cluster)
        data[offset:offset + len(chunk)] = chunk
    assert image._recover_files_from_fat_context(bytes(data), assumed)[0].data == decoy
    diagnostics = {}

    candidates = image._recover_file_candidates_from_raw_image_bytes(
        bytes(data), disk_format_hint=FORMAT, diagnostics=diagnostics,
    )

    fat_files = [song for song in candidates if song.origin == "fat"]
    assert [(song.data, song.source_offset) for song in fat_files] == [(payload, geometry.data_offset)]
    assumed_scan = next(scan for scan in diagnostics["geometry_scans"] if scan["selected_hint"])
    assert assumed_scan["fat_copies_valid"] == 2
    assert assumed_scan["root_directory_structurally_valid"]
    assert not assumed_scan["attempted"]


def test_carving_can_extend_a_file_with_a_damaged_directory_size(tmp_path):
    _source, data, geometry, payload = _fragmented_image(tmp_path)
    data[geometry.data_offset:geometry.data_offset + len(payload)] = payload
    data[geometry.root_offset + 28:geometry.root_offset + 32] = (14).to_bytes(4, "little")
    _set_fat(data, geometry, 2, 0xFFF)

    candidates = image._recover_file_candidates_from_raw_image_bytes(bytes(data), disk_format_hint=FORMAT)

    assert [(song.origin, song.data) for song in candidates] == [("fat", payload[:14]), ("carve", payload)]


@pytest.mark.parametrize("reverse_layouts", [False, True])
def test_equally_supported_conflicting_geometries_are_not_silently_merged(tmp_path, monkeypatch, reverse_layouts):
    layout = image._PROTECTED_FAT12_LAYOUTS[0]
    alternate = {**layout, "root_entries": 128}
    source = tmp_path / "ambiguous.img"
    image._create_blank_fat12_image_from_layout(source, layout, "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._fat12_geometry_from_layout(layout)
    other_geometry = image._fat12_geometry_from_layout(alternate)
    data[:512] = bytes(512)
    track = b"\0\x90\x3c\x50\x20\x80\x3c\0\0\xff\x2f\0"
    payload = b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk" + len(track).to_bytes(4, "big") + track
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    MID", 2, len(payload))
    _set_fat(data, geometry, 2, 0xFFF)
    data[geometry.data_offset:geometry.data_offset + len(payload)] = payload
    data[other_geometry.data_offset:other_geometry.data_offset + len(payload)] = payload.replace(b"\x3c", b"\x40")
    layouts = (alternate, layout) if reverse_layouts else (layout, alternate)
    monkeypatch.setattr(image, "_PROTECTED_FAT12_LAYOUTS", layouts)
    diagnostics = {}

    with pytest.raises(image._Fat12RecoveryAmbiguityError, match="Conflicting FAT12 geometries"):
        image._recover_files_from_raw_image_bytes(bytes(data), disk_format_hint=FORMAT, diagnostics=diagnostics)

    scans = diagnostics["geometry_scans"]
    assert len(scans) == 2
    assert all(scan["attempted"] for scan in scans)
    assert scans[0]["recovered_files"] == 1 and not scans[0]["error"]
    assert scans[1]["recovered_files"] == 0 and "Conflicting" in scans[1]["error"]
    assert "Conflicting FAT12 geometries" in diagnostics["fat_recovery_ambiguity"]
