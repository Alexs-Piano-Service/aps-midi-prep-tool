"""Recovery keeps distinct performances even when their catalog headers match."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.eseq_converter import parse_eseq_bytes


def _eseq(pitch):
    stream = bytes((0x90, pitch, 80, 0xF3, 32, 0x80, pitch, 0, 0xF2))
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x27:0x33] = image.build_eseq_order_key_from_path("SONG.FIL")
    header[0x33] = 91
    header[0x57:0x77] = b"Shared title".ljust(32, b" ")
    return bytes(header) + stream


def _midi(pitch):
    track = bytes((0, 0x90, pitch, 80, 32, 0x80, pitch, 0, 0, 0xFF, 0x2F, 0))
    return (
        b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk"
        + len(track).to_bytes(4, "big")
        + track
    )


def _set_fat(data, geometry, copy, cluster, following):
    offset = geometry.fat_offset + copy * geometry.fat_size + cluster + cluster // 2
    pair = int.from_bytes(data[offset:offset + 2], "little")
    pair = (pair & 0x000F) | (following << 4) if cluster & 1 else (pair & 0xF000) | following
    data[offset:offset + 2] = pair.to_bytes(2, "little")


def _assert_preserved_song(recovered, original, pitch):
    # Recovery may rewrite the filename/order key; every other source byte,
    # including the event stream, must survive unchanged.
    expected = bytearray(original)
    expected[0x27:0x33] = image.build_eseq_order_key_from_path(recovered.image_path)
    assert recovered.data == bytes(expected)
    notes = [
        event[2][1]
        for event in parse_eseq_bytes(recovered.data).events
        if event[2][0] & 0xF0 == 0x90
    ]
    assert notes == [pitch]


def _assert_ambiguity(diagnostics, songs):
    assert diagnostics["eseq_recovery_ambiguities"] == [[
        {"path": song.image_path, "source_offset": song.source_offset, "origin": song.origin}
        for song in songs
    ]]
    report = image.format_floppy_recovery_diagnostics(diagnostics)
    assert "E-SEQ header ambiguity" in report
    assert "different payloads" in report
    assert all(song.image_path in report for song in songs)


def _fat_image_with_songs(tmp_path, originals, names=(b"FIRST   FIL", b"SECOND  FIL")):
    source = tmp_path / "source.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[0], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    offsets = []
    for index, (name, original) in enumerate(zip(names, originals)):
        cluster = index + 2
        for copy in range(geometry.num_fats):
            _set_fat(data, geometry, copy, cluster, 0xFFF)
        root_offset = geometry.root_offset + index * 32
        data[root_offset:root_offset + 32] = image._dos_directory_entry(name, cluster, len(original))
        offset = image._cluster_offset(geometry, cluster)
        offsets.append(offset)
        data[offset:offset + len(original)] = original
    return source, data, geometry, offsets


def test_raw_recovery_preserves_distinct_fat_songs_with_identical_headers(tmp_path):
    originals = [_eseq(60), _eseq(67)]
    assert originals[0][0x27:0x77] == originals[1][0x27:0x77]
    _source, data, _geometry, offsets = _fat_image_with_songs(tmp_path, originals)

    diagnostics = {}
    recovered = image._recover_files_from_raw_image_bytes(bytes(data), diagnostics=diagnostics)

    assert [song.image_path for song in recovered] == ["FIRST.FIL", "SECOND.FIL"]
    assert [song.source_offset for song in recovered] == offsets
    assert all(song.origin == "fat" for song in recovered)
    for song, original, pitch in zip(recovered, originals, (60, 67)):
        _assert_preserved_song(song, original, pitch)
    assert diagnostics["recovered_files"] == 2
    _assert_ambiguity(diagnostics, recovered)
    assert "E-SEQ header ambiguity" in diagnostics["human_report"]


@pytest.mark.parametrize("second_pitch", [60, 67], ids=["identical-copies", "distinct-performances"])
@pytest.mark.parametrize("with_diagnostics", [False, True])
def test_original_and_repaired_scans_compare_bytes_before_renaming(
    tmp_path, monkeypatch, second_pitch, with_diagnostics,
):
    originals = [_eseq(60), _eseq(second_pitch)]
    source, data, geometry, offsets = _fat_image_with_songs(tmp_path, originals)
    source.write_bytes(data)
    prepared_data = bytearray(data)
    for index, name in enumerate((b"RENAMED FIL", b"COPY    FIL")):
        start = geometry.root_offset + index * 32
        prepared_data[start:start + 11] = name

    def incomplete_repair(_source, prepared):
        Path(prepared).write_bytes(prepared_data)
        raise image.FloppyImageError("Directory repair left an image requiring a raw scan")

    monkeypatch.setattr(image, "prepare_yamaha_image", incomplete_repair)
    work = tmp_path / "recovery"
    work.mkdir()
    diagnostics = {} if with_diagnostics else None
    session = image.FloppyImageSession._recover_from_raw_image(
        source, str(work), source_name="Test recovery", recovery_diagnostics=diagnostics,
    )
    try:
        paths = [entry.path for entry in session.list_entries().entries]
        assert paths == ["FIRST.FIL", "SECOND.FIL"]
        songs = [image.RecoveredFile(
            path, Path(session.extract_file(path)).read_bytes(), "E-SEQ", offset, "fat",
        ) for path, offset in zip(paths, offsets)]
        for song, original, pitch in zip(songs, originals, (60, second_pitch)):
            _assert_preserved_song(song, original, pitch)
        if second_pitch == 67:
            _assert_ambiguity(session.recovery_diagnostics, songs)
            assert "E-SEQ header ambiguity" in session.repair_note
            if with_diagnostics:
                assert "E-SEQ header ambiguity" in diagnostics["human_report"]
        else:
            assert "E-SEQ header ambiguity" not in session.repair_note
            assert not session.recovery_diagnostics.get("eseq_recovery_ambiguities")
            if not with_diagnostics:
                assert not session.recovery_diagnostics
    finally:
        session.cleanup()
    assert source.read_bytes() == data


def test_same_named_distinct_songs_receive_unique_recovery_names():
    originals = [_eseq(60), _eseq(67)]
    files = [image.RecoveredFile("SONG.FIL", original, "E-SEQ", offset, "fat")
             for original, offset in zip(originals, (1024, 2048))]
    diagnostics = {}

    recovered = image._dedupe_recovered_files(files, diagnostics=diagnostics)

    assert [song.image_path for song in recovered] == ["SONG.FIL", "SONG1.FIL"]
    for song, original, pitch in zip(recovered, originals, (60, 67)):
        _assert_preserved_song(song, original, pitch)
    _assert_ambiguity(diagnostics, recovered)


def test_matching_offset_carve_is_suppressed_while_distinct_fat_copy_survives():
    original = _eseq(60)
    files = [
        image.RecoveredFile("SONG.FIL", original, "E-SEQ", 1024, "carve"),
        image.RecoveredFile("COPY.FIL", original, "E-SEQ", 2048, "fat"),
        image.RecoveredFile("FIRST.FIL", original, "E-SEQ", 1024, "fat"),
    ]
    diagnostics = {}

    recovered = image._dedupe_recovered_files(files, diagnostics=diagnostics)

    assert len(recovered) == 2
    assert (recovered[0].image_path, recovered[0].source_offset, recovered[0].origin) == ("FIRST.FIL", 1024, "fat")
    assert (recovered[1].image_path, recovered[1].source_offset, recovered[1].origin) == ("COPY.FIL", 2048, "fat")
    for song in recovered:
        _assert_preserved_song(song, original, 60)
    assert not diagnostics.get("eseq_recovery_ambiguities")


def test_damaged_image_recovers_distinct_fat_entries_with_identical_midi_bytes(tmp_path, monkeypatch):
    original = _midi(60)
    _source, data, _geometry, offsets = _fat_image_with_songs(
        tmp_path, [original, original], (b"SONG1   MID", b"COPY    MID"),
    )
    data[:512] = bytes(512)
    monkeypatch.setattr(image, "_carve_recovery_files_from_bytes", lambda _data: [])

    recovered = image._recover_files_from_raw_image_bytes(bytes(data))

    assert [file.image_path for file in recovered] == ["SONG1.MID", "COPY.MID"]
    assert [file.source_offset for file in recovered] == offsets
    assert all(file.origin == "fat" and file.data == original for file in recovered)
    assert len({file.source_entry_offset for file in recovered}) == 2


@pytest.mark.parametrize("container", ["type1", "extended-header", "intertrack-junk", "opaque-trailer"])
def test_complete_midi_carving_does_not_create_artificial_fat_duplicates(tmp_path, container):
    original = bytearray(_midi(60))
    original[8:10] = (1).to_bytes(2, "big")
    junk = b"JUNK" + (8).to_bytes(4, "big") + b"metadata"
    if container == "extended-header":
        original[4:8] = (8).to_bytes(4, "big")
        original[14:14] = b"\x12\x34"
    elif container == "intertrack-junk":
        original[10:12] = (2).to_bytes(2, "big")
        original.extend(junk + _midi(67)[14:])
    elif container == "opaque-trailer":
        original.extend(junk + b"opaque source bytes")
    original = bytes(original)
    _source, data, _geometry, offsets = _fat_image_with_songs(
        tmp_path, [original, original], (b"SONG1   MID", b"COPY    MID"),
    )

    carved = image._carve_recovery_files_from_bytes(bytes(data))
    assert len(carved) == 2
    assert all(original.startswith(file.data) for file in carved)
    recovered = image._recover_files_from_raw_image_bytes(bytes(data))

    assert [file.image_path for file in recovered] == ["SONG1.MID", "COPY.MID"]
    assert [file.source_offset for file in recovered] == offsets
    assert all(file.origin == "fat" and file.data == original for file in recovered)


def test_distinct_fat_directory_entries_sharing_a_cluster_survive():
    original = _midi(60)
    files = [
        image.RecoveredFile("FIRST.MID", original, "MIDI", 1024, "fat", 512),
        image.RecoveredFile("COPY.MID", original, "MIDI", 1024, "fat", 544),
        image.RecoveredFile("REC001.MID", original, "MIDI", 1024, "carve"),
    ]

    recovered = image._dedupe_recovered_files(files)

    assert [file.image_path for file in recovered] == ["FIRST.MID", "COPY.MID"]
    assert [file.source_entry_offset for file in recovered] == [512, 544]
    assert all(file.data == original for file in recovered)


def test_identical_carved_files_at_different_offsets_survive():
    original = _midi(60)
    files = [
        image.RecoveredFile("REC001.MID", original, "MIDI", 1024, "carve"),
        image.RecoveredFile("REC002.MID", original, "MIDI", 2048, "carve"),
    ]

    recovered = image._dedupe_recovered_files(files)

    assert [file.image_path for file in recovered] == ["REC001.MID", "REC002.MID"]
    assert all(file.data == original for file in recovered)


def test_different_payloads_at_the_same_source_offset_survive():
    originals = [_eseq(60), _eseq(67)]
    files = [
        image.RecoveredFile("SONG.FIL", originals[0], "E-SEQ", 1024, "fat"),
        image.RecoveredFile("SONG.FIL", originals[1], "E-SEQ", 1024, "carve"),
    ]
    diagnostics = {}

    recovered = image._dedupe_recovered_files(files, diagnostics=diagnostics)

    assert len(recovered) == 2
    assert [song.origin for song in recovered] == ["fat", "carve"]
    assert [song.source_offset for song in recovered] == [1024, 1024]
    for song, original, pitch in zip(recovered, originals, (60, 67)):
        _assert_preserved_song(song, original, pitch)
    _assert_ambiguity(diagnostics, recovered)


def test_crc32_collision_does_not_discard_distinct_payloads(monkeypatch):
    originals = [_midi(60), _midi(67)]
    assert len(originals[0]) == len(originals[1])
    monkeypatch.setattr(image.zlib, "crc32", lambda _payload: 123)
    files = [image.RecoveredFile(name, original, "MIDI", offset, "fat")
             for name, original, offset in zip(("FIRST.MID", "SECOND.MID"), originals, (1024, 2048))]

    recovered = image._dedupe_recovered_files(files)

    assert [song.image_path for song in recovered] == ["FIRST.MID", "SECOND.MID"]
    assert [song.data for song in recovered] == originals


def test_shared_catalog_order_key_keeps_the_first_priority_song_mapping():
    originals = [_eseq(60), _eseq(67)]
    catalog = bytearray(image.PIANODIR_TARGET_FILE_SIZE)
    catalog[:len(image.PIANODIR_HEADER)] = image.PIANODIR_HEADER
    record_offset = len(image.PIANODIR_HEADER)
    catalog[record_offset:record_offset + image.PIANODIR_TRACK_SIZE] = originals[0][0x27:0x77]
    files = [
        image.RecoveredFile("SECOND.FIL", originals[1], "E-SEQ", 2048, "fat"),
        image.RecoveredFile("PIANODIR.FIL", bytes(catalog), "PIANODIR", 0, "fat"),
        image.RecoveredFile("FIRST.FIL", originals[0], "E-SEQ", 1024, "fat"),
    ]
    diagnostics = {}

    recovered = image._dedupe_recovered_files(files, diagnostics=diagnostics)

    assert [file.image_path for file in recovered] == ["PIANODIR.FIL", "FIRST.FIL", "SECOND.FIL"]
    expected_catalog = bytearray(catalog)
    expected_catalog[record_offset:record_offset + image.ESEQ_ORDER_KEY_SIZE] = image.build_eseq_order_key_from_path("FIRST.FIL")
    assert recovered[0].data == bytes(expected_catalog)
    for song, original, pitch in zip(recovered[1:], originals, (60, 67)):
        _assert_preserved_song(song, original, pitch)
    _assert_ambiguity(diagnostics, recovered[1:])
