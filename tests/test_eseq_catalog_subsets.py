"""Split E-SEQ catalogs retain album metadata and refer only to their own songs."""

from dataclasses import replace
import struct

import pytest

from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import (
    CLAVINOVA_MUSICDIR_HEADER_SIZE,
    CLAVINOVA_MUSICDIR_RECORD_SIZE,
    PIANODIR_COUNT_OFFSET,
    PIANODIR_DISK_METADATA_OFFSET,
    PIANODIR_DISK_METADATA_SIZE,
    PIANODIR_HEADER,
    PIANODIR_SECONDARY_AGGREGATE_OFFSET,
    PIANODIR_TARGET_FILE_SIZE,
    PIANODIR_TOTAL_DURATION_OFFSET,
    PIANODIR_TRACK_SIZE,
    PianodirTrackEntry,
    build_dos83_name_bytes,
    build_eseq_directory_subset,
    build_music_dir_bytes,
    build_pianodir_bytes,
    decode_dos83_name,
    parse_music_dir,
)


def _song(tmp_path, name, variant="disklavier"):
    track = b"\x00\x90\x3C\x50\x60\x80\x3C\x00\x00\xFF\x2F\x00"
    midi = (struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 96)
            + b"MTrk" + len(track).to_bytes(4, "big") + track)
    path = tmp_path / name
    path.write_bytes(convert_midi_bytes_to_eseq_bytes(midi, container_variant=variant, title_override=name))
    return PianodirTrackEntry(name, str(path), name)


def _pianodir_records(data):
    count = int.from_bytes(data[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") - 1
    return [data[16 + index * PIANODIR_TRACK_SIZE:16 + (index + 1) * PIANODIR_TRACK_SIZE]
            for index in range(count)]


def test_pianodir_subset_retains_template_order_and_metadata_but_recomputes_aggregates(tmp_path):
    alpha, stale, beta, new, last = [_song(tmp_path, name) for name in (
        "ALPHA.FIL", "STALE.FIL", "BETA.FIL", "NEW.FIL", "LAST.FIL",
    )]
    template = bytearray(build_pianodir_bytes([alpha, stale, beta]))
    metadata = bytes(range(0x80, 0x80 + PIANODIR_DISK_METADATA_SIZE))
    template[PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + len(metadata)] = metadata
    # Intentionally preserved catalog fields can differ from staged song headers.
    for slot, duration, secondary, marker in ((0, 0xFFFFFFF0, 40000, 0xD5), (2, 40, 30000, 0xA7)):
        start = 16 + slot * PIANODIR_TRACK_SIZE
        template[start + 0x10:start + 0x14] = duration.to_bytes(4, "little")
        template[start + 0x16:start + 0x18] = secondary.to_bytes(2, "little")
        template[start + 0x2C:start + 0x30] = bytes([marker]) * 4
    old_records = _pianodir_records(template)
    alpha_out = replace(alpha, image_path="alpha.fil", short_name_bytes=b"ALPHA~1 FIL")
    beta_out = replace(beta, short_name_bytes=b"beta    fil")

    output = build_eseq_directory_subset("pianodir.fil", template, [new, beta_out, last, alpha_out])

    records = _pianodir_records(output)
    assert [decode_dos83_name(record[:11]) for record in records] == ["ALPHA~1.FIL", "beta.fil", "NEW.FIL", "LAST.FIL"]
    assert records[0] == alpha_out.short_name_bytes + old_records[0][11:]
    assert records[1] == beta_out.short_name_bytes + old_records[2][11:]
    assert records[2:] == _pianodir_records(build_pianodir_bytes([new, last]))
    assert output[PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + len(metadata)] == metadata
    assert int.from_bytes(output[PIANODIR_TOTAL_DURATION_OFFSET:PIANODIR_TOTAL_DURATION_OFFSET + 4], "little") == (
        sum(int.from_bytes(record[0x10:0x14], "little") for record in records) & 0xFFFFFFFF
    )
    assert int.from_bytes(output[PIANODIR_SECONDARY_AGGREGATE_OFFSET:PIANODIR_SECONDARY_AGGREGATE_OFFSET + 2], "little") == 0xFFFF
    assert len(output) == PIANODIR_TARGET_FILE_SIZE
    assert not any(output[16 + len(records) * PIANODIR_TRACK_SIZE:PIANODIR_DISK_METADATA_OFFSET])
    assert b"STALE   FIL" not in output


def test_music_subset_follows_logical_map_and_preserves_opaque_header_and_record_fields(tmp_path):
    alpha, stale, beta, unused, new = [_song(tmp_path, name, "clavinova_mda") for name in (
        "ALPHA.MDA", "STALE.MDA", "BETA.MDA", "UNUSED.MDA", "NEW.MDA",
    )]
    template = bytearray(build_music_dir_bytes([alpha, stale, beta, unused]))
    template[0x10:0x50] = bytes(range(64))
    template[0x50] = 0x75
    template[0x52:0x60] = bytes(range(0xA0, 0xAE))
    template[0x60:0x64] = bytes([2, 0xFF, 0, 1])
    template[CLAVINOVA_MUSICDIR_HEADER_SIZE + 2 * CLAVINOVA_MUSICDIR_RECORD_SIZE + 13] = 0xEA
    old_songs = parse_music_dir(template)
    alpha_out = replace(alpha, image_path="alpha.mda", short_name_bytes=b"ALPHA~1 MDA")
    beta_out = replace(beta, short_name_bytes=b"beta    mda")

    output = build_eseq_directory_subset("MUSIC.DIR", template, [new, alpha_out, beta_out])

    songs = parse_music_dir(output)
    assert [song["filename"] for song in songs] == ["beta.mda", "ALPHA~1.MDA", "NEW.MDA"]
    assert songs[0]["raw_record"] == beta_out.short_name_bytes + old_songs[0]["raw_record"][11:]
    assert songs[1]["raw_record"] == alpha_out.short_name_bytes + old_songs[1]["raw_record"][11:]
    assert songs[2]["raw_record"] == parse_music_dir(build_music_dir_bytes([new]))[0]["raw_record"]
    assert len(output) == 160 + 48 * 3
    assert int.from_bytes(output[3:7], "little") == len(output)
    assert output[0x51] == 3
    assert output[0x60:0xA0] == bytes([0, 1, 2]) + b"\xFF" * 61
    for index in list(range(3)) + list(range(7, 0x51)) + list(range(0x52, 0x60)):
        assert output[index] == template[index]
    assert b"STALE   MDA" not in output and b"UNUSED  MDA" not in output


@pytest.mark.parametrize("directory,variant", [("PIANODIR.FIL", "disklavier"), ("MUSIC.DIR", "clavinova_mda")])
def test_empty_catalog_adds_all_tracks_in_supplied_order_with_actual_dos_names(tmp_path, directory, variant):
    tracks = [_song(tmp_path, name, variant) for name in ("FIRST", "SECOND")]
    tracks[0] = replace(tracks[0], short_name_bytes=b"\xE1ONG    FIL")
    template = PIANODIR_HEADER if directory == "PIANODIR.FIL" else build_music_dir_bytes([])

    output = build_eseq_directory_subset(directory, template, tracks)

    records = _pianodir_records(output) if directory == "PIANODIR.FIL" else [song["raw_record"] for song in parse_music_dir(output)]
    assert [record[:11] for record in records] == [b"\xE1ONG    FIL", build_dos83_name_bytes("SECOND")]
    assert len(output) == (6144 if directory == "PIANODIR.FIL" else 160 + 48 * 2)


@pytest.mark.parametrize("directory,builder", [("PIANODIR.FIL", build_pianodir_bytes), ("MUSIC.DIR", build_music_dir_bytes)])
def test_empty_subset_has_no_references(directory, builder):
    output = build_eseq_directory_subset(directory, builder([]), [])
    assert output == builder([])


@pytest.mark.parametrize("data", [
    b"not a directory",
    PIANODIR_HEADER[:-1],
    PIANODIR_HEADER + b"\0",
    PIANODIR_HEADER + bytes(6144 - len(PIANODIR_HEADER)),
    build_pianodir_bytes([])[:PIANODIR_COUNT_OFFSET + 1],
])
def test_invalid_pianodir_templates_fail_even_for_empty_subset(data):
    with pytest.raises(ValueError):
        build_eseq_directory_subset("PIANODIR.FIL", data, [])


@pytest.mark.parametrize("mutation", ["signature", "short", "length_zero", "length_past_end", "partial_record", "slot_count", "index", "empty_name", "duplicate_name"])
def test_invalid_music_templates_are_rejected_instead_of_using_lenient_parser(tmp_path, mutation):
    tracks = [_song(tmp_path, name, "clavinova_mda") for name in ("A.MDA", "B.MDA")]
    data = bytearray(build_music_dir_bytes(tracks))
    if mutation == "signature":
        data[7] = 0
    elif mutation == "short":
        data = data[:159]
    elif mutation == "length_zero":
        data[3:7] = bytes(4)
    elif mutation == "length_past_end":
        data[3:7] = (len(data) + 48).to_bytes(4, "little")
    elif mutation == "partial_record":
        data[3:7] = (len(data) - 1).to_bytes(4, "little")
    elif mutation == "slot_count":
        data[0x51] = 65
    elif mutation == "index":
        data[0x60] = 2
    elif mutation == "empty_name":
        data[160:171] = b" " * 11
    else:
        data[208:219] = data[160:171]
    with pytest.raises(ValueError):
        build_eseq_directory_subset("MUSIC.DIR", data, [])


@pytest.mark.parametrize("mutation", ["count", "empty_name", "duplicate_name"])
def test_invalid_pianodir_records_are_rejected(tmp_path, mutation):
    tracks = [_song(tmp_path, name) for name in ("A.FIL", "B.FIL")]
    data = bytearray(build_pianodir_bytes(tracks))
    if mutation == "count":
        data[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2] = (62).to_bytes(2, "little")
    elif mutation == "empty_name":
        data[16:27] = bytes(11)
    else:
        data[96:107] = data[16:27]
    with pytest.raises(ValueError):
        build_eseq_directory_subset("PIANODIR.FIL", data, [])


@pytest.mark.parametrize("directory,builder,limit", [("PIANODIR.FIL", build_pianodir_bytes, 60), ("MUSIC.DIR", build_music_dir_bytes, 64)])
def test_subset_rejects_track_limits_and_ambiguous_short_names(directory, builder, limit):
    entry = PianodirTrackEntry("A.FIL", "missing-source", "")
    with pytest.raises(ValueError, match="at most"):
        build_eseq_directory_subset(directory, builder([]), [entry] * (limit + 1))
    with pytest.raises(ValueError, match="ambiguous"):
        build_eseq_directory_subset(directory, builder([]), [entry, replace(entry, image_path="a.fil")])


def test_unknown_catalog_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown E-SEQ directory"):
        build_eseq_directory_subset("OTHER.DIR", b"", [])
