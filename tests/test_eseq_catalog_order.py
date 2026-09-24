"""Catalogs and song headers must agree with the visible, non-alphabetical order."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA, convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    build_pianodir_bytes, parse_music_dir, read_eseq_order_key_from_file,
)
from test_eseq_output_filtering import window, _load, _catalog_tracks
from test_save_as_overwrite import _song


SONGS = (
    ("READY.FIL", "Ready or Not"),
    ("ANGEL.FIL", "Angel in the Night"),
    ("NOT.FIL", "Not Falling Apart"),
    ("SHOT.FIL", "Shot Me in the Heart"),
)


def _visible_paths(window):
    return [window.table.item(row, 1).text() for row in window._image_eseq_rows()]


def _prepare(window, tmp_path, *, additions):
    songs = {
        name: convert_midi_bytes_to_eseq_bytes(_song(title, 60 + index), filename_hint=name, title_override=title)
        for index, (name, title) in enumerate(SONGS)
    }
    originals = _load(
        window, tmp_path, {"PIANODIR.FIL": build_pianodir_bytes([]), **({} if additions else songs)},
        "image_to_image",
    )
    if additions:
        folder = tmp_path / "additions"
        folder.mkdir()
        for name, payload in songs.items():
            path = folder / name
            path.write_bytes(payload)
            window.queue_image_additions([str(path)])
        assert _visible_paths(window) == list(songs)
    else:
        for index, name in enumerate(songs):
            rows = window._image_eseq_rows()
            source = next(row for row in rows if window.table.item(row, 1).text() == name)
            window._move_table_row(source, rows[index])
    assert _visible_paths(window) == list(songs)
    return originals


@pytest.mark.parametrize("additions, rename", [
    pytest.param(False, False, id="existing-songs"),
    pytest.param(True, False, id="new-conversions"),
    pytest.param(False, True, id="rename-and-reorder"),
])
@pytest.mark.parametrize("route", ["save", "save_as_image", "raw_write"])
def test_catalog_and_header_order_follow_visible_rows(window, tmp_path, monkeypatch, additions, route, rename):
    originals = _prepare(window, tmp_path, additions=additions)
    expected = list(SONGS)
    if rename:
        window.pendingImageRenames["READY.FIL"] = "FIRST.FIL"
        expected[0] = ("FIRST.FIL", SONGS[0][1])
    output = tmp_path / "saved.img"
    edits = window._image_eseq_order_key_edits()
    assert edits  # The row order requires a change to filename-derived keys.
    if route == "save":
        output = Path(window.image_session.source_path)
        monkeypatch.setattr(window, "_original_write_is_allowed", lambda: True)
        window.save_image_changes()
    elif route == "save_as_image":
        monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **kwargs:
                            (str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]))
        monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *a, **k: None)
        window.save_image_as()
    else:
        def write(image, device, **kwargs):
            assert device == "A:"
            output.write_bytes(Path(image).read_bytes())
            return {"confidence": "written"}
        monkeypatch.setattr(floppy_image, "_write_block_device", write)
        window.image_session.write_to_floppy_target(
            "floppy_usb", floppy_image.FloppyDriveInfo("A:", 737280),
            **window._collect_current_image_write_operations(),
        )

    saved = floppy_image.FloppyImageSession.load(str(output))
    try:
        catalog = Path(saved.extract_file("PIANODIR.FIL")).read_bytes()
        assert _catalog_tracks(catalog) == expected
        keys = [read_eseq_order_key_from_file(saved.extract_file(name)) for name, _ in expected]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(SONGS)
    finally:
        saved.cleanup()
    for path, payload in originals.items():
        if path != output:
            assert path.read_bytes() == payload


@pytest.mark.parametrize("reorder", [False, True])
def test_clavinova_catalog_uses_visible_order_with_different_embedded_keys(window, tmp_path, reorder):
    names = [name.replace(".FIL", ".MDA") for name, _ in SONGS]
    songs = {
        name: convert_midi_bytes_to_eseq_bytes(
            _song(title, 60 + index), filename_hint=f"HEADER{index}.MDA",
            title_override=title, container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA,
        )
        for index, (name, (_, title)) in enumerate(zip(names, SONGS))
    }
    _load(window, tmp_path, songs, "image_to_image")
    expected = names if reorder else sorted(names)
    for index, name in enumerate(expected):
        rows = window._image_eseq_rows()
        source = next(row for row in rows if window.table.item(row, 1).text() == name)
        window._move_table_row(source, rows[index])
        assert window._image_path_order_key(name) != read_eseq_order_key_from_file(
            window.image_session.extract_file(name)
        )
    assert _visible_paths(window) == expected
    assert bool(window._image_eseq_order_key_edits()) == reorder
    output = tmp_path / "saved.img"
    window.image_session.export_to_images(
        str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"],
        **window._collect_current_image_write_operations(for_export=True),
    )
    saved = floppy_image.FloppyImageSession.load(str(output))
    try:
        catalog = parse_music_dir(Path(saved.extract_file("MUSIC.DIR")).read_bytes())
        assert [entry["filename"] for entry in sorted(catalog, key=lambda entry: entry["slot"])] == expected
    finally:
        saved.cleanup()
