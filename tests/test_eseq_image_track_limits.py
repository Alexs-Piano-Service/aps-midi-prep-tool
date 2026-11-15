"""Small E-SEQ collections split at catalog limits before disk space runs out."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    ESEQ_CONTAINER_DISKLAVIER,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    CLAVINOVA_MUSICDIR_MAX_TRACKS,
    MUSICDIR_FILENAME,
    PIANODIR_FILENAME,
    PIANODIR_MAX_TRACKS,
    PianodirMetadata,
    PianodirTrackEntry,
    build_music_dir_bytes,
    build_pianodir_bytes,
    parse_pianodir_metadata,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageError,
    create_floppy_images_from_files,
)
from test_eseq_image_contents import _midi
from test_save_as_overwrite import window
from test_split_eseq_catalogs import _catalog_records, _read_image_files


def _small_collection(tmp_path, variant, extra, *, catalog=True):
    source = tmp_path / "songs"
    source.mkdir()
    clavinova = variant == "clavinova"
    limit = CLAVINOVA_MUSICDIR_MAX_TRACKS if clavinova else PIANODIR_MAX_TRACKS
    directory_name = MUSICDIR_FILENAME if clavinova else PIANODIR_FILENAME
    container = ESEQ_CONTAINER_CLAVINOVA_MDA if clavinova else ESEQ_CONTAINER_DISKLAVIER
    extension = "MDA" if clavinova else "FIL"
    originals, tracks = {}, []
    for index in range(limit + extra):
        name = f"SONG{index:03d}.{extension}"
        title = f"Performance {index}"
        payload = convert_midi_bytes_to_eseq_bytes(
            _midi(), container_variant=container, filename_hint=name, title_override=title,
        )
        path = source / name
        path.write_bytes(payload)
        originals[name] = payload
        tracks.append(PianodirTrackEntry(name, str(path), title))
    if catalog:
        # A native catalog can describe only part of a larger folder. Its
        # playback order differs from both filename and file-spec order.
        catalog_tracks = list(reversed(tracks))[:limit]
        payload = (
            build_music_dir_bytes(catalog_tracks) if clavinova else
            build_pianodir_bytes(catalog_tracks, PianodirMetadata(disk_title="Large collection"))
        )
        (source / directory_name).write_bytes(payload)
        originals[directory_name] = payload
    assert sum(map(len, originals.values())) < 100 * 1024
    return source, originals, directory_name, limit


def _assert_delivered(paths, expected_songs, directory_name, variant, limit, expected_order):
    delivered, playback_order, counts = {}, [], []
    for path in paths:
        files = _read_image_files(path)
        catalog = files.pop(directory_name)
        names = [name for name, _record in _catalog_records(catalog, variant)]
        assert 0 < len(names) <= limit
        assert len(names) == len(files) and set(names) == set(files)
        assert not delivered.keys() & files.keys()
        delivered.update(files)
        playback_order.extend(names)
        counts.append(len(names))
    assert delivered == expected_songs
    assert playback_order == expected_order
    return counts


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
@pytest.mark.parametrize("extra", [0, 1], ids=["at-limit", "over-limit"])
def test_small_catalog_collection_splits_at_per_disk_track_limit(tmp_path, variant, extra):
    source, originals, directory_name, limit = _small_collection(tmp_path, variant, extra)
    output = tmp_path / "Album.img"
    paths = create_floppy_images_from_files(
        [(str(source / name), name) for name in originals],
        output, "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )

    assert paths == ([str(output)] if not extra else [
        str(output.with_name(f"Album_{number:02d}.img")) for number in (1, 2)
    ])
    songs = {name: data for name, data in originals.items() if name != directory_name}
    assert _assert_delivered(
        paths, songs, directory_name, variant, limit, list(reversed(songs)),
    ) == ([limit] if not extra else [limit, 1])
    if not extra:
        assert _read_image_files(output)[directory_name] == originals[directory_name]
    elif variant == "disklavier":
        assert all(parse_pianodir_metadata(_read_image_files(path)[directory_name]) ==
                   parse_pianodir_metadata(originals[directory_name]) for path in paths)
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_single_disk_packing_rejects_catalog_overflow_before_publication(tmp_path, variant):
    source, originals, _directory_name, _limit = _small_collection(tmp_path, variant, 1)
    output = tmp_path / "Album.img"
    output.write_bytes(b"Keep the previous image")

    with pytest.raises(FloppyImageError, match="do not fit on one floppy disk"):
        create_floppy_images_from_files(
            [(str(source / name), name) for name in originals],
            output, "img", DISK_FORMAT_BY_KEY["ibm.720"], allow_split=False,
        )

    assert output.read_bytes() == b"Keep the previous image"
    assert list(tmp_path.glob("Album_*.img")) == []
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
@pytest.mark.parametrize("catalog_mode", ["generated", "preserved"])
@pytest.mark.parametrize("extra", [0, 1], ids=["at-limit", "over-limit"])
def test_save_as_image_applies_catalog_limit_per_image(
    window, tmp_path, monkeypatch, variant, catalog_mode, extra,
):
    source, originals, directory_name, limit = _small_collection(
        tmp_path, variant, extra, catalog=catalog_mode == "preserved",
    )
    window._load_regular_files(
        [str(source / name) for name in originals], "Catalog limit test", prepare_destination=False,
    )
    assert window.is_local_eseq_mode() and window.regularEseqVariant == variant
    assert len(window._regular_eseq_rows()) == limit + extra
    assert window._should_generate_pianodir(for_export=True) == (catalog_mode == "generated")
    expected_order = [window._regular_row_output_filename(row) for row in window._regular_eseq_rows()]
    if catalog_mode == "preserved":
        catalog_order = [name for name, _record in _catalog_records(originals[directory_name], variant)]
        expected_order = catalog_order + [name for name in expected_order if name not in catalog_order]
    window.fileCreateImageAlbumSubfolderAction.setChecked(False)
    window.settings.setValue(window.SETTING_HIDE_SAVE_AS_IMAGE_COMPLETE_DIALOG, True)
    output = tmp_path / "exports" / "Album.img"
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
        str(output), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    ))
    stage = window._stage_files_for_image_export
    staged = {}

    def capture_prepared(*args, **kwargs):
        specs = stage(*args, **kwargs)
        staged.update({spec["image_path"]: Path(spec["host_path"]).read_bytes() for spec in specs})
        return specs

    monkeypatch.setattr(window, "_stage_files_for_image_export", capture_prepared)

    paths = window.save_as_image()

    assert not window._test_errors
    assert paths == ((str(output),) if not extra else tuple(
        str(output.with_name(f"Album_{number:02d}.img")) for number in (1, 2)
    ))
    songs = {name: data for name, data in staged.items() if name != directory_name}
    assert len(songs) == limit + extra
    assert _assert_delivered(
        paths, songs, directory_name, variant, limit, expected_order,
    ) == ([limit] if not extra else [limit, 1])
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("variant", ["disklavier", "clavinova"])
def test_physical_floppy_export_keeps_single_disk_catalog_limit(window, tmp_path, monkeypatch, variant):
    source, originals, _directory_name, _limit = _small_collection(tmp_path, variant, 1, catalog=False)
    window._load_regular_files(
        [str(source / name) for name in originals], "Single disk limit test", prepare_destination=False,
    )
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs:
                        pytest.fail("An over-limit physical floppy export should stop before the picker"))

    assert window.save_as_image(for_floppy=True) is None
    assert len(window._test_errors) == 1
    assert "Too Many E-SEQ Files" in window._test_errors[0][0]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
