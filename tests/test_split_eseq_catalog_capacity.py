"""Split catalog planning respects actual FAT allocation and payload types."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    ESEQ_CONTAINER_DISKLAVIER,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    CLAVINOVA_MUSICDIR_HEADER_SIZE,
    CLAVINOVA_MUSICDIR_RECORD_SIZE,
    MUSICDIR_FILENAME,
    PIANODIR_COUNT_OFFSET,
    PIANODIR_FILENAME,
    PIANODIR_HEADER,
    PIANODIR_TRACK_SIZE,
    PianodirTrackEntry,
    build_music_dir_bytes,
    build_pianodir_bytes,
    decode_dos83_name,
    parse_music_dir,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageSession,
    create_blank_floppy_image,
    create_floppy_images_from_files,
    read_image_listing,
)
from test_eseq_image_contents import _midi


def _write_song(directory, name, size, variant=ESEQ_CONTAINER_DISKLAVIER):
    payload = convert_midi_bytes_to_eseq_bytes(
        _midi(), container_variant=variant, filename_hint=name,
    ).ljust(size, b"\0")
    path = directory / name
    path.write_bytes(payload)
    return PianodirTrackEntry(name, str(path), ""), payload


def _catalog_names(name, payload):
    if name == MUSICDIR_FILENAME:
        return [entry["filename"] for entry in parse_music_dir(payload)]
    count = int.from_bytes(payload[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") - 1
    return [decode_dos83_name(payload[offset:offset + 11]) for offset in (
        len(PIANODIR_HEADER) + index * PIANODIR_TRACK_SIZE for index in range(count)
    )]


def _read_images(paths):
    result = []
    for path in paths:
        session = FloppyImageSession.load(path)
        try:
            listing = session.list_entries()
            assert listing.free_space >= 0
            result.append({entry.path: Path(session.extract_file(entry.path)).read_bytes()
                           for entry in listing.entries})
        finally:
            session.cleanup()
    return result


def _export(directory, output, names):
    return create_floppy_images_from_files(
        [(str(directory / name), name) for name in names],
        output, "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )


def test_musicdir_growth_to_another_cluster_moves_a_song_to_the_next_disk(tmp_path):
    blank = create_blank_floppy_image(tmp_path / "blank.img", DISK_FORMAT_BY_KEY["ibm.720"])
    capacity = read_image_listing(blank)
    # This next song grows MUSIC.DIR from one allocated cluster to two.
    count = ((capacity.cluster_size - CLAVINOVA_MUSICDIR_HEADER_SIZE)
             // CLAVINOVA_MUSICDIR_RECORD_SIZE) + 1
    source = tmp_path / "source"
    source.mkdir()
    songs, originals = [], {}
    for index in range(count):
        size = capacity.free_space - count * capacity.cluster_size if index == 0 else 128
        track, payload = _write_song(
            source, f"SONG{index:02d}.MDA", size, ESEQ_CONTAINER_CLAVINOVA_MDA,
        )
        songs.append(track)
        originals[track.image_path] = payload
    catalog = build_music_dir_bytes(songs)
    assert capacity.cluster_size < len(catalog) <= capacity.cluster_size * 2
    (source / MUSICDIR_FILENAME).write_bytes(catalog)

    paths = _export(source, tmp_path / "Album.img", [*originals, MUSICDIR_FILENAME])

    assert len(paths) == 2
    images = _read_images(paths)
    assert [len(_catalog_names(MUSICDIR_FILENAME, files[MUSICDIR_FILENAME]))
            for files in images] == [count - 1, 1]
    assert [name for files in images
            for name in _catalog_names(MUSICDIR_FILENAME, files[MUSICDIR_FILENAME])] == list(originals)
    assert {name: payload for files in images for name, payload in files.items()
            if name != MUSICDIR_FILENAME} == originals
    assert {path.name: path.read_bytes() for path in source.iterdir()} == {
        **originals, MUSICDIR_FILENAME: catalog,
    }


def test_split_catalog_reserves_a_root_slot_with_long_named_sidecars(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first, first_bytes = _write_song(source, "FIRST.FIL", 128)
    second, second_bytes = _write_song(source, "SECOND.FIL", 128)
    originals = {first.image_path: first_bytes}
    # These tiny files exhaust root-directory slots well before data space.
    # Their long names require multiple FAT LFN entries apiece.
    for index in range(40):
        if index == 32:
            originals[second.image_path] = second_bytes
        name = f"Accompaniment notes number {index:04d}.txt"
        payload = f"Keep these notes for song {index}".encode()
        (source / name).write_bytes(payload)
        originals[name] = payload
    catalog = build_pianodir_bytes([first, second])
    (source / PIANODIR_FILENAME).write_bytes(catalog)

    paths = _export(source, tmp_path / "Album.img", [*originals, PIANODIR_FILENAME])

    assert len(paths) == 2
    images = _read_images(paths)
    assert [set(_catalog_names(PIANODIR_FILENAME, files[PIANODIR_FILENAME]))
            for files in images] == [{"FIRST.FIL"}, {"SECOND.FIL"}]
    assert {name: payload for files in images for name, payload in files.items()
            if name != PIANODIR_FILENAME} == originals
    assert {path.name: path.read_bytes() for path in source.iterdir()} == {
        **originals, PIANODIR_FILENAME: catalog,
    }


@pytest.mark.parametrize("catalog_names", [
    (PIANODIR_FILENAME, MUSICDIR_FILENAME), (PIANODIR_FILENAME,), (),
], ids=["both-catalogs", "only-pianodir", "no-catalogs"])
def test_mixed_split_catalogs_follow_content_and_do_not_create_absent_catalogs(tmp_path, catalog_names):
    source = tmp_path / "source"
    source.mkdir()
    originals, tracks_by_catalog = {}, {PIANODIR_FILENAME: [], MUSICDIR_FILENAME: []}
    for name, variant, catalog_name in (
        ("DISK.MDA", ESEQ_CONTAINER_DISKLAVIER, PIANODIR_FILENAME),
        ("CLAV.FIL", ESEQ_CONTAINER_CLAVINOVA_MDA, MUSICDIR_FILENAME),
        ("EXTLESS", ESEQ_CONTAINER_DISKLAVIER, PIANODIR_FILENAME),
        ("OTHER", ESEQ_CONTAINER_CLAVINOVA_MDA, MUSICDIR_FILENAME),
    ):
        track, payload = _write_song(source, name, 300 * 1024, variant)
        originals[name] = payload
        tracks_by_catalog[catalog_name].append(track)
    # Song-like suffixes must not put ordinary data or MIDI in E-SEQ catalogs.
    for name, payload in (("JUNK.FIL", b"Not an E-SEQ file" * 20), ("MIDI.MDA", _midi())):
        (source / name).write_bytes(payload)
        originals[name] = payload
    catalog_bytes = {}
    for name in catalog_names:
        tracks = list(reversed(tracks_by_catalog[name]))
        payload = build_pianodir_bytes(tracks) if name == PIANODIR_FILENAME else build_music_dir_bytes(tracks)
        catalog_bytes[name] = payload
        (source / name).write_bytes(payload)

    paths = _export(source, tmp_path / "Album.img", [*originals, *catalog_names])

    assert len(paths) == 2
    images = _read_images(paths)
    for name, tracks in tracks_by_catalog.items():
        if name not in catalog_names:
            assert all(name not in files for files in images)
            continue
        matching_names = {track.image_path for track in tracks}
        for files in images:
            resident = matching_names & files.keys()
            assert set(_catalog_names(name, files[name])) == resident
        assert [song for files in images for song in _catalog_names(name, files[name])] == [
            track.image_path for track in reversed(tracks)
        ]
    assert {name: payload for files in images for name, payload in files.items()
            if name not in catalog_names} == originals
    assert {path.name: path.read_bytes() for path in source.iterdir()} == {**originals, **catalog_bytes}
