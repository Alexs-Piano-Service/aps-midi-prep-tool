"""Every split E-SEQ disk needs a catalog describing its own resident songs."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    ESEQ_CONTAINER_DISKLAVIER,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    MUSICDIR_FILENAME,
    PIANODIR_COUNT_OFFSET,
    PIANODIR_DISK_METADATA_OFFSET,
    PIANODIR_DISK_METADATA_SIZE,
    PIANODIR_FILENAME,
    PIANODIR_HEADER,
    PIANODIR_SECONDARY_AGGREGATE_OFFSET,
    PIANODIR_TOTAL_DURATION_OFFSET,
    PIANODIR_TRACK_DURATION_OFFSET,
    PIANODIR_TRACK_SECONDARY_WORD_OFFSET,
    PIANODIR_TRACK_SIZE,
    PianodirMetadata,
    PianodirTrackEntry,
    build_music_dir_bytes,
    build_pianodir_bytes,
    decode_dos83_name,
    parse_music_dir,
    parse_pianodir_metadata,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageError,
    FloppyImageSession,
    create_blank_floppy_image,
    create_floppy_images_from_files,
    read_image_listing,
)
from test_eseq_image_contents import _midi
from test_save_as_overwrite import window


def _catalog_records(data, variant):
    if variant == "clavinova":
        return [(song["filename"], song["raw_record"]) for song in parse_music_dir(data)]
    count = int.from_bytes(data[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") - 1
    assert count >= 0
    records = []
    for index in range(count):
        start = len(PIANODIR_HEADER) + index * PIANODIR_TRACK_SIZE
        record = data[start:start + PIANODIR_TRACK_SIZE]
        assert len(record) == PIANODIR_TRACK_SIZE
        records.append((decode_dos83_name(record[:11]), record))
    return records


def _prepare_collection(tmp_path, variant, song_sizes, *, spec_kind="dict"):
    directory_name = MUSICDIR_FILENAME if variant == "clavinova" else PIANODIR_FILENAME
    container = ESEQ_CONTAINER_CLAVINOVA_MDA if variant == "clavinova" else ESEQ_CONTAINER_DISKLAVIER
    extension = "MDA" if variant == "clavinova" else "FIL"
    source = tmp_path / "songs"
    source.mkdir()
    originals, tracks = {}, []
    for index, size in enumerate(song_sizes, start=1):
        name = f"SONG{index:02d}.{extension}"
        path = source / name
        title = f"Performance {index}"
        payload = convert_midi_bytes_to_eseq_bytes(
            _midi(), container_variant=container, title_override=title,
        )
        assert len(payload) <= size
        # A valid song header and events remain intact; padding controls the
        # real FAT allocation without constructing long musical performances.
        payload = payload.ljust(size, b"\0")
        path.write_bytes(payload)
        originals[name] = payload
        tracks.append(PianodirTrackEntry(name, str(path), title))

    # Existing catalogs can carry a different play order from the file list.
    # Splitting must retain that order among the songs resident on each disk.
    catalog_tracks = list(reversed(tracks))
    if variant == "clavinova":
        catalog = build_music_dir_bytes(catalog_tracks)
    else:
        catalog = build_pianodir_bytes(
            catalog_tracks,
            PianodirMetadata(catalog_number="APS-42", disk_title="Preserved album title"),
        )
    (source / directory_name).write_bytes(catalog)
    originals[directory_name] = catalog
    if spec_kind == "tuple":
        specs = [(str(source / name), name) for name in originals]
    else:
        specs = [{"host_path": str(source / name), "image_path": name} for name in originals]
    return source, specs, originals, directory_name


def _read_image_files(path):
    session = FloppyImageSession.load(str(path))
    try:
        listing = session.list_entries()
        assert listing.free_space >= 0
        return {entry.path: Path(session.extract_file(entry.path)).read_bytes()
                for entry in listing.entries}
    finally:
        session.cleanup()


def _assert_catalogs_match_resident_songs(paths, originals, directory_name, variant):
    expected_records = _catalog_records(originals[directory_name], variant)
    all_songs = {name: payload for name, payload in originals.items() if name != directory_name}
    delivered, song_counts, playback_order = {}, [], []
    for path in paths:
        files = _read_image_files(path)
        assert directory_name in files, f"{path} has songs but no {directory_name}"
        catalog = files.pop(directory_name)
        assert files, f"{path} contains only a catalog"
        assert not delivered.keys() & files.keys(), "A song was exported on more than one disk"
        assert files == {name: all_songs[name] for name in files}
        catalog_records = _catalog_records(catalog, variant)
        assert catalog_records == [
            record for record in expected_records if record[0] in files
        ]
        assert {name for name, _record in catalog_records} == set(files)
        playback_order.extend(name for name, _record in catalog_records)
        if variant == "disklavier":
            assert parse_pianodir_metadata(catalog) == parse_pianodir_metadata(originals[directory_name])
            metadata = slice(PIANODIR_DISK_METADATA_OFFSET,
                             PIANODIR_DISK_METADATA_OFFSET + PIANODIR_DISK_METADATA_SIZE)
            assert catalog[metadata] == originals[directory_name][metadata]
            records = [record for _name, record in catalog_records]
            duration = sum(int.from_bytes(record[PIANODIR_TRACK_DURATION_OFFSET:
                                                PIANODIR_TRACK_DURATION_OFFSET + 4], "little")
                           for record in records) & 0xFFFFFFFF
            secondary = min(0xFFFF, sum(int.from_bytes(record[PIANODIR_TRACK_SECONDARY_WORD_OFFSET:
                                                            PIANODIR_TRACK_SECONDARY_WORD_OFFSET + 2], "little")
                                       for record in records))
            assert int.from_bytes(catalog[PIANODIR_TOTAL_DURATION_OFFSET:
                                          PIANODIR_TOTAL_DURATION_OFFSET + 4], "little") == duration
            assert int.from_bytes(catalog[PIANODIR_SECONDARY_AGGREGATE_OFFSET:
                                          PIANODIR_SECONDARY_AGGREGATE_OFFSET + 2], "little") == secondary
        delivered.update(files)
        song_counts.append(len(files))
    assert delivered == all_songs
    assert playback_order == [name for name, _record in expected_records]
    return song_counts


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
@pytest.mark.parametrize("song_count,image_count", [(3, 2), (5, 3)])
@pytest.mark.parametrize("spec_kind", ["dict", "tuple"])
def test_split_eseq_images_each_have_matching_catalog_and_preserved_order(
    tmp_path, variant, song_count, image_count, spec_kind,
):
    source, specs, originals, directory_name = _prepare_collection(
        tmp_path, variant, [300 * 1024] * song_count, spec_kind=spec_kind,
    )
    output = tmp_path / "exports" / "Album.img"

    paths = create_floppy_images_from_files(
        specs, output, "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )

    assert paths == [str(output.with_name(f"Album_{number:02d}.img"))
                     for number in range(1, image_count + 1)]
    assert not output.exists()
    assert _assert_catalogs_match_resident_songs(paths, originals, directory_name, variant) == (
        [2] * (image_count - 1) + [1]
    )
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_split_reserves_catalog_space_instead_of_creating_catalog_only_disk(tmp_path, variant):
    blank = tmp_path / "capacity.img"
    create_blank_floppy_image(blank, DISK_FORMAT_BY_KEY["ibm.720"])
    listing = read_image_listing(blank)
    first_size = listing.free_space // (2 * listing.cluster_size) * listing.cluster_size
    # The two songs alone fill one disk exactly. Each can share a disk with
    # its catalog, so the correct result is two complete, playable disks.
    sizes = [first_size, listing.free_space - first_size]
    source, specs, originals, directory_name = _prepare_collection(tmp_path, variant, sizes)
    output = tmp_path / "Album.img"

    paths = create_floppy_images_from_files(
        specs, output, "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )

    assert len(paths) == 2
    assert _assert_catalogs_match_resident_songs(paths, originals, directory_name, variant) == [1, 1]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_unsplit_eseq_image_preserves_original_catalog_bytes(tmp_path, variant):
    source, specs, originals, directory_name = _prepare_collection(tmp_path, variant, [1024, 2048])
    # Native reserved bytes must survive when no split requires new catalogs.
    catalog = bytearray(originals[directory_name])
    marker = b"Native metadata!"
    offset = 0x20 if variant == "clavinova" else len(catalog) - len(marker)
    catalog[offset:offset + len(marker)] = marker
    originals[directory_name] = bytes(catalog)
    (source / directory_name).write_bytes(catalog)
    output = tmp_path / "Album.img"

    paths = create_floppy_images_from_files(
        specs, output, "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )

    assert paths == [str(output)]
    assert _read_image_files(output) == originals
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
@pytest.mark.parametrize("catalog_mode", ["generated", "preserved"])
def test_save_as_image_splits_staged_eseq_catalog_on_every_disk(
    window, tmp_path, monkeypatch, variant, catalog_mode,
):
    source, _specs, originals, directory_name = _prepare_collection(tmp_path, variant, [300 * 1024] * 3)
    if catalog_mode == "generated":
        (source / directory_name).unlink()
        originals.pop(directory_name)
    window._load_regular_files(
        [str(source / name) for name in originals], "Split E-SEQ test", prepare_destination=False,
    )
    assert window.is_local_eseq_mode() and window.regularEseqVariant == variant
    assert window._should_generate_pianodir(for_export=True) == (catalog_mode == "generated")
    window.fileCreateImageAlbumSubfolderAction.setChecked(False)
    window.settings.setValue(window.SETTING_HIDE_SAVE_AS_IMAGE_COMPLETE_DIALOG, True)
    output = tmp_path / "exports" / "Album.img"
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
        str(output), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    ))
    stage = window._stage_files_for_image_export
    staged = {}

    def capture_prepared_files(*args, **kwargs):
        specs = stage(*args, **kwargs)
        staged.update({spec["image_path"]: Path(spec["host_path"]).read_bytes() for spec in specs})
        return specs

    monkeypatch.setattr(window, "_stage_files_for_image_export", capture_prepared_files)

    paths = window.save_as_image()

    assert not window._test_errors
    assert paths == tuple(str(output.with_name(f"Album_{number:02d}.img")) for number in (1, 2))
    assert directory_name in staged
    if catalog_mode == "preserved":
        assert staged[directory_name] == originals[directory_name]
    assert _assert_catalogs_match_resident_songs(paths, staged, directory_name, variant) == [2, 1]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
@pytest.mark.parametrize("generate", [False, True], ids=["preserved", "regenerated"])
def test_export_to_smaller_images_keeps_per_disk_catalog_order(tmp_path, variant, generate):
    source, specs, originals, directory_name = _prepare_collection(tmp_path, variant, [300 * 1024] * 3)
    original_image = tmp_path / "source.img"
    assert create_floppy_images_from_files(
        specs, original_image, "img", DISK_FORMAT_BY_KEY["ibm.1440"],
    ) == [str(original_image)]
    original_image_bytes = original_image.read_bytes()
    output = tmp_path / "exports" / "Album.img"
    options = {"generate_pianodir": generate, "eseq_variant": variant}
    if generate:
        options["eseq_directory_order"] = {
            name: number.to_bytes(12, "big")
            for number, (name, _record) in enumerate(_catalog_records(originals[directory_name], variant), 1)
        }
        if variant == "disklavier":
            options["pianodir_metadata"] = parse_pianodir_metadata(originals[directory_name])
    session = FloppyImageSession.load(str(original_image))
    try:
        paths = session.export_to_images(str(output), "img", DISK_FORMAT_BY_KEY["ibm.720"], **options)
    finally:
        session.cleanup()

    assert paths == [str(output.with_name(f"Album_{number:02d}.img")) for number in (1, 2)]
    assert _assert_catalogs_match_resident_songs(paths, originals, directory_name, variant) == [2, 1]
    assert original_image.read_bytes() == original_image_bytes
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_song_that_cannot_fit_with_catalog_is_rejected_before_publication(tmp_path, monkeypatch, variant):
    blank = tmp_path / "capacity.img"
    create_blank_floppy_image(blank, DISK_FORMAT_BY_KEY["ibm.720"])
    source, specs, originals, _directory_name = _prepare_collection(
        tmp_path, variant, [read_image_listing(blank).free_space],
    )
    output = tmp_path / "exports" / "Album.img"
    output.parent.mkdir()
    output.write_bytes(b"Keep the previous selected image")
    monkeypatch.setattr(floppy_image, "_finish_temp_output", lambda *_args, **_kwargs:
                        pytest.fail("An oversized song must be rejected before publication"))

    with pytest.raises(FloppyImageError, match="too large.*with its E-SEQ directory catalog"):
        create_floppy_images_from_files(specs, output, "img", DISK_FORMAT_BY_KEY["ibm.720"])

    assert {path.name: path.read_bytes() for path in output.parent.iterdir()} == {
        output.name: b"Keep the previous selected image",
    }
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_malformed_split_catalog_is_rejected_before_publication(tmp_path, monkeypatch, variant):
    source, specs, originals, directory_name = _prepare_collection(tmp_path, variant, [300 * 1024] * 3)
    originals[directory_name] = originals[directory_name][:10]
    (source / directory_name).write_bytes(originals[directory_name])
    output = tmp_path / "exports" / "Album.img"
    output.parent.mkdir()
    output.write_bytes(b"Keep the previous selected image")
    monkeypatch.setattr(floppy_image, "_finish_temp_output", lambda *_args, **_kwargs:
                        pytest.fail("A malformed catalog must be rejected before publication"))

    with pytest.raises(FloppyImageError) as caught:
        create_floppy_images_from_files(specs, output, "img", DISK_FORMAT_BY_KEY["ibm.720"])

    assert directory_name in str(caught.value) and "split export" in str(caught.value)
    assert {path.name: path.read_bytes() for path in output.parent.iterdir()} == {
        output.name: b"Keep the previous selected image",
    }
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_split_without_source_catalog_does_not_synthesize_one(tmp_path, variant):
    source, specs, originals, directory_name = _prepare_collection(tmp_path, variant, [300 * 1024] * 3)
    specs = [spec for spec in specs if spec["image_path"] != directory_name]
    originals.pop(directory_name)
    (source / directory_name).unlink()
    output = tmp_path / "exports" / "Album.img"

    paths = create_floppy_images_from_files(specs, output, "img", DISK_FORMAT_BY_KEY["ibm.720"])

    assert paths == [str(output.with_name(f"Album_{number:02d}.img")) for number in (1, 2)]
    delivered = {}
    for path in paths:
        files = _read_image_files(path)
        assert PIANODIR_FILENAME not in files and MUSICDIR_FILENAME not in files
        assert files and not delivered.keys() & files.keys()
        delivered.update(files)
    assert list(delivered) == list(originals)
    assert delivered == originals
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
