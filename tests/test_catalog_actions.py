"""Catalog application stages real MIDI metadata and respects source identity."""

import pytest
from PySide6.QtCore import Qt

from aps_midi_prep_tool_app.midi_metadata import read_first_title_from_midi
from test_catalog_imports import _album, _image
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes
from test_zip_drop import application, window, _drop, _midi_bytes


def _rows(window):
    return {window.table.item(row, 1).text(): row for row in window._regular_file_rows()}


def _load(window, paths):
    window._load_regular_files([str(path) for path in paths], "Loaded", prepare_destination=False)


def _item(path):
    return {"kind": "catalog", "path": str(path), "source_path": str(path), "session_id": None}


@pytest.mark.parametrize("catalogs_first", [False, True])
def test_real_mixed_drop_applies_album_and_embedded_titles_without_writing_sources(window, tmp_path, catalogs_first):
    files = _album(tmp_path)
    originals = {path: path.read_bytes() for path in files.values()}
    paths = list(files.values())
    _drop(window.table, *(reversed(paths) if catalogs_first else paths))
    assert window.pendingEdits == {
        str(files["FIRST.MID"]): "Second in catalog",
        str(files["SECOND.MID"]): "First in catalog",
    }
    assert window.imagePianodirTitleEdit.text() == "An original album"
    assert "Applied 2 song title(s)" in window.status_label.text()
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_explicit_apply_uses_filenames_despite_sorted_rows_and_saves_real_midi_titles(window, tmp_path):
    files = _album(tmp_path)
    _load(window, files.values())
    window.table.setSortingEnabled(True)
    window.table.sortItems(3, Qt.DescendingOrder)
    result = window._stage_inspected_catalog_action(_item(files["PSONG.MNG"]))
    assert result["applied"]
    expected = {"FIRST.MID": "Second in catalog", "SECOND.MID": "First in catalog"}
    for name, title in expected.items():
        path = str(files[name])
        assert window._row_raw_title(_rows(window)[path]) == title
        assert window.pendingEdits[path] == title
        output = tmp_path / ("saved-" + name)
        window._write_listed_file_to_path(path, title, str(output))
        assert read_first_title_from_midi(output) == title
        assert read_first_title_from_midi(path).startswith("Embedded")
    window.undo_last_staged_batch()
    assert not window.pendingEdits
    assert all(window._row_raw_title(_rows(window)[str(files[name])]).startswith("Embedded") for name in expected)


def test_apply_supplies_missing_embedded_midi_title(window, tmp_path):
    files = _album(tmp_path)
    files["FIRST.MID"].write_bytes(_midi_bytes(""))
    _load(window, files.values())
    path = str(files["FIRST.MID"])
    assert read_first_title_from_midi(path) == ""
    assert not window.pendingEdits
    window._stage_inspected_catalog_action(_item(files["PSONG.MNG"]))
    assert window.pendingEdits[path] == "Second in catalog"


def test_combined_apply_undo_restores_prior_title_edit_and_album(window, tmp_path):
    files = _album(tmp_path)
    _load(window, files.values())
    first = str(files["FIRST.MID"])
    window.pendingEdits[first] = "Previous edit"
    window.table.setItem(_rows(window)[first], 4, window._make_title_item("Previous edit", title_mode="midi", edited=True))
    window.imagePianodirTitleEdit.setText("Previous album")
    message = window._apply_imported_catalogs(
        [str(files["PDISK.MNG"]), str(files["PSONG.MNG"])],
        song_paths=[str(files["FIRST.MID"]), str(files["SECOND.MID"])],
    )
    assert "An original album" in message
    assert len(window.pendingEdits) == 2
    window.undo_last_staged_batch()
    assert window.pendingEdits == {first: "Previous edit"}
    assert window.imagePianodirTitleEdit.text() == "Previous album"


@pytest.mark.parametrize("automatic", [False, True])
def test_catalog_applies_only_to_own_folder_with_duplicate_song_names(window, tmp_path, automatic):
    first_folder = tmp_path / "one"
    second_folder = tmp_path / "two"
    first_folder.mkdir()
    second_folder.mkdir()
    first = _album(first_folder)
    second = _album(second_folder)
    _load(window, [*first.values(), *second.values()])
    if automatic:
        window._apply_imported_catalogs([str(first["PSONG.MNG"])], song_paths=list(_rows(window)))
    else:
        window._stage_inspected_catalog_action(_item(first["PSONG.MNG"]))
    assert set(window.pendingEdits) == {str(first["FIRST.MID"]), str(first["SECOND.MID"])}


def test_multiple_albums_apply_song_titles_per_folder_and_leave_album_choice_to_user(window, tmp_path):
    paths = []
    catalogs = []
    for index in range(2):
        folder = tmp_path / str(index)
        folder.mkdir()
        files = _album(folder)
        files["PDISK.MNG"].write_bytes(_pdisk_bytes(f"Album {index}"))
        files["PSONG.MNG"].write_bytes(_psong_bytes([("FIRST.MID", f"First {index}")]))
        paths.extend(files.values())
        catalogs.extend([str(files["PDISK.MNG"]), str(files["PSONG.MNG"])])
    _load(window, paths)
    window.imagePianodirTitleEdit.setText("Existing album")
    result = window._apply_imported_catalogs(catalogs, song_paths=list(_rows(window)))
    assert window.imagePianodirTitleEdit.text() == "Existing album"
    assert "different album titles" in result
    assert window.pendingEdits == {str(tmp_path / str(i) / "FIRST.MID"): f"First {i}" for i in range(2)}


def test_empty_first_disk_catalog_does_not_block_later_album_title(window, tmp_path):
    files = _album(tmp_path)
    empty = tmp_path / "empty" / "PDISK.MNG"
    empty.parent.mkdir()
    empty.write_bytes(_pdisk_bytes(""))
    _load(window, files.values())
    window.imagePianodirTitleEdit.setText("Before")
    result = window._apply_imported_catalogs([str(empty), str(files["PDISK.MNG"])], song_paths=list(_rows(window)))
    assert window.imagePianodirTitleEdit.text() == "An original album"
    assert "Could not apply" not in result


def test_missing_and_empty_records_leave_unrelated_titles_unchanged(window, tmp_path):
    files = _album(tmp_path)
    files["PSONG.MNG"].write_bytes(_psong_bytes([
        ("FIRST.MID", "New first"), ("SECOND.MID", ""), ("ABSENT.MID", "Missing"),
    ]))
    _load(window, files.values())
    result = window._stage_inspected_catalog_action(_item(files["PSONG.MNG"]))
    assert window.pendingEdits == {str(files["FIRST.MID"]): "New first"}
    assert "Skipped 1 catalog record(s)" in result["message"]
    assert "Skipped 1 empty catalog title(s)" in result["message"]


def test_changed_invalid_midi_aborts_entire_application_without_partial_edits(window, tmp_path):
    files = _album(tmp_path)
    _load(window, files.values())
    # SECOND is first in catalog order; FIRST fails after a valid planned edit.
    files["FIRST.MID"].write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        window._stage_inspected_catalog_action(_item(files["PSONG.MNG"]))
    assert not window.pendingEdits


def test_image_catalog_apply_updates_embedded_midi_and_undo_restores_catalog_editing(window, tmp_path):
    from aps_midi_prep_tool_app.floppy_image import FloppyImageSession

    files = _album(tmp_path)
    image, original = _image(window, tmp_path, list(files.values()))
    catalog = next(item for item in window._inspection_catalog_items([]) if item["display_name"] == "PSONG.MNG")
    assert window._image_title_is_smart_pianosoft_catalog_backed("FIRST.MID")
    result = window._stage_inspected_catalog_action(catalog)
    assert result["applied"]
    assert window.pendingImageTitleEdits == {"FIRST.MID": "Second in catalog", "SECOND.MID": "First in catalog"}
    assert not window._image_title_is_smart_pianosoft_catalog_backed("FIRST.MID")
    assert window._collect_current_image_write_operations()["title_edits"] == window.pendingImageTitleEdits
    exported = tmp_path / "applied.img"
    window.image_session.export_to(exported, "img", **window._collect_current_image_write_operations(for_export=True))
    session = FloppyImageSession.load(exported)
    try:
        for path, title in window.pendingImageTitleEdits.items():
            assert read_first_title_from_midi(session.extract_file(path)) == title
        with open(session.extract_file("PSONG.MNG"), "rb") as handle:
            assert handle.read() == files["PSONG.MNG"].read_bytes()
    finally:
        session.cleanup()
    assert image.read_bytes() == original
    window.undo_last_staged_batch()
    assert not window.pendingImageTitleEdits
    assert window._image_title_is_smart_pianosoft_catalog_backed("FIRST.MID")


def test_catalog_from_old_image_session_is_rejected(window, tmp_path):
    files = _album(tmp_path)
    _load(window, files.values())
    item = _item(files["PSONG.MNG"])
    item["session_id"] = 42
    with pytest.raises(ValueError, match="no longer available"):
        window._stage_inspected_catalog_action(item)
    assert not window.pendingEdits


def test_image_mixed_drop_applies_titles_only_to_songs_in_the_drop(window, tmp_path):
    files = _album(tmp_path)
    image, original = _image(window, tmp_path, [files["FIRST.MID"]])
    _drop(window.table, files["PDISK.MNG"], files["SECOND.MID"], files["PSONG.MNG"])
    assert window.pendingImageTitleEdits == {"SECOND.MID": "First in catalog"}
    assert window.imagePianodirTitleEdit.text() == "An original album"
    assert image.read_bytes() == original
