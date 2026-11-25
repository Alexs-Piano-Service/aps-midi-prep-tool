"""Management catalogs remain companion files until an E-SEQ conversion consumes them."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.preparation_profiles import SETTING_PROFILE
from test_catalog_inspection import disk_catalog, song_catalog
from test_zip_drop import application, window, _archive, _drop, _midi_bytes


def _album(tmp_path):
    files = {"FIRST.MID": _midi_bytes("Embedded first"),
             "SECOND.MID": _midi_bytes("Embedded second"),
             "PDISK.MNG": disk_catalog(), "PSONG.MNG": song_catalog()}
    paths = {}
    for name, data in files.items():
        path = tmp_path / name
        path.write_bytes(data)
        paths[name] = path
    return paths


def _listed(window):
    return {Path(window.table.item(row, 1).text()).name: row
            for row in window._regular_file_rows()}


def test_dragging_catalogs_alone_accepts_preserves_and_inspects_them(window, tmp_path, monkeypatch):
    files = _album(tmp_path)
    calls = []
    monkeypatch.setattr(window, "_show_error_list", lambda *args, **kwargs: pytest.fail(str(args)))
    monkeypatch.setattr(window, "_apply_imported_catalogs", lambda *args, **kwargs: calls.append((args, kwargs)), raising=False)
    _drop(window.table, files["PDISK.MNG"], files["PSONG.MNG"])
    assert set(_listed(window)) == {"PDISK.MNG", "PSONG.MNG"}
    assert not calls
    assert not window.renameAllButton.isEnabled()
    assert not window.convertType0Button.isEnabled()
    assert not window.utilitiesRenderAudioAction.isEnabled()
    assert not window.utilitiesSongListAction.isEnabled()
    assert window._current_song_rows_for_listing() == []
    for path in (files["PDISK.MNG"], files["PSONG.MNG"]):
        assert window._listed_file_title_mode(str(path)) == "catalog"
        assert window.can_accept_regular_drop_path(str(path))
    inspected = []
    monkeypatch.setattr(window, "show_file_inspection_tool", lambda **kwargs: inspected.append(kwargs["selected_row"]))
    window.handle_cell_double_clicked(_listed(window)["PSONG.MNG"], 3)
    assert inspected == [_listed(window)["PSONG.MNG"]]
    window.clear_list()
    assert window.table.rowCount() == 0
    assert files["PDISK.MNG"].read_bytes() == disk_catalog()
    assert files["PSONG.MNG"].read_bytes() == song_catalog()


@pytest.mark.parametrize("catalogs_first", [False, True])
def test_mixed_drop_calls_apply_after_all_rows_are_loaded(window, tmp_path, monkeypatch, catalogs_first):
    files = _album(tmp_path)
    calls = []
    def applied(catalogs, *, song_paths):
        assert len(_listed(window)) == 4
        calls.append(({Path(path) for path in catalogs}, {Path(path) for path in song_paths}))
        return "Catalog titles applied"
    monkeypatch.setattr(window, "_apply_imported_catalogs", applied, raising=False)
    paths = list(files.values())
    _drop(window.table, *(reversed(paths) if catalogs_first else paths))
    assert calls == [({files["PDISK.MNG"], files["PSONG.MNG"]},
                     {files["FIRST.MID"], files["SECOND.MID"]})]
    assert "Catalog titles applied" in window.status_label.text()
    assert len(window._current_song_rows_for_listing()) == 2
    assert len(tuple(window._preparation_song_rows())) == 2


def test_folder_scan_retains_catalogs_and_reload_does_not_reapply(window, tmp_path, monkeypatch):
    files = _album(tmp_path)
    assert set(window._regular_folder_file_paths(str(tmp_path))) == {str(path) for path in files.values()}
    calls = []
    monkeypatch.setattr(window, "_apply_imported_catalogs", lambda *args, **kwargs: calls.append((args, kwargs)) or "Applied", raising=False)
    window._load_regular_files([str(path) for path in files.values()], "Imported")
    assert len(calls) == 1
    window._load_regular_files([str(path) for path in files.values()], "Saved", prepare_destination=False)
    assert len(calls) == 1
    assert set(_listed(window)) == set(files)


def test_zip_catalog_only_import_keeps_extraction_alive_until_clear(window, tmp_path):
    archive = _archive(tmp_path, "catalogs.zip", {"PDISK.MNG": disk_catalog(), "PSONG.MNG": song_catalog()})
    _drop(window.table, archive)
    roots = [Path(directory.name) for directory, _source in window.table._zip_imports]
    assert set(_listed(window)) == {"PDISK.MNG", "PSONG.MNG"}
    assert roots and all(path.is_dir() for path in roots)
    window.clear_list()
    assert all(not path.exists() for path in roots)


def test_save_as_preserves_companion_bytes_and_keeps_deliberate_titles(window, tmp_path, monkeypatch):
    files = _album(tmp_path)
    # Load without application to isolate the ordinary copy path.
    window._load_regular_files([str(path) for path in files.values()], "Imported", prepare_destination=False)
    target = tmp_path / "saved"
    target.mkdir()
    window.album_subfolder_checkbox.setChecked(False)
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_kw: str(target))
    window.pendingEdits[str(files["FIRST.MID"])] = "Deliberate edit"
    row = _listed(window)["FIRST.MID"]
    window.table.setItem(row, 4, window._make_title_item("Deliberate edit", title_mode="midi", edited=True))
    window.save_as_changes()
    assert (target / "PDISK.MNG").read_bytes() == disk_catalog()
    assert (target / "PSONG.MNG").read_bytes() == song_catalog()
    assert main_window.extract_first_title_from_midi(str(target / "FIRST.MID")) == "Deliberate edit"
    assert set(_listed(window)) == set(files)
    assert not window.pendingEdits


def test_dos_rename_and_type0_actions_leave_catalog_names_unchanged(window, tmp_path):
    files = _album(tmp_path)
    window._load_regular_files([str(path) for path in files.values()], "Imported", prepare_destination=False)
    window.rename_all_for_disk()
    assert set(window.pendingRegularRenames) == {str(files["FIRST.MID"]), str(files["SECOND.MID"])}
    assert window._regular_output_filename_for_path(str(files["PSONG.MNG"])) == "PSONG.MNG"
    assert window._regular_output_filename_for_path(str(files["PDISK.MNG"])) == "PDISK.MNG"


def test_explicit_eseq_conversion_drops_catalog_rows_and_undo_restores_them(window, tmp_path):
    files = _album(tmp_path)
    window.settings.setValue(SETTING_PROFILE, "custom")
    window._load_regular_files([str(path) for path in files.values()], "Imported", prepare_destination=False)
    window.convert_all_midi_to_eseq()
    assert all(window._listed_file_title_mode(path) == "eseq" for path in window.listedFileInfo)
    assert not {"PDISK.MNG", "PSONG.MNG"} & set(_listed(window))
    assert files["PDISK.MNG"].read_bytes() == disk_catalog()
    assert files["PSONG.MNG"].read_bytes() == song_catalog()
    window.undo_last_staged_batch()
    assert set(_listed(window)) == set(files)
    assert not window.pendingRegularConversions


def _image(window, tmp_path, paths):
    from aps_midi_prep_tool_app.floppy_image import (
        DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
    )
    image = tmp_path / "catalogs.img"
    create_floppy_images_from_files(
        [{"host_path": str(path), "image_path": path.name} for path in paths],
        image, "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original = image.read_bytes()
    session = FloppyImageSession.load(image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    return image, original


def test_image_drop_applies_only_successfully_imported_songs_and_catalogs(window, tmp_path, monkeypatch):
    files = _album(tmp_path)
    image, original = _image(window, tmp_path, [files["FIRST.MID"]])
    calls = []
    apply_catalogs = window._apply_imported_catalogs

    def applied(catalogs, *, song_paths):
        # Qt supplies forward slashes on Windows; compare host paths by path
        # identity while preserving the exact image-relative song names.
        calls.append(([Path(path) for path in catalogs], {"song_paths": song_paths}))
        return apply_catalogs(catalogs, song_paths=song_paths)

    monkeypatch.setattr(window, "_apply_imported_catalogs", applied)
    _drop(window.table, files["PDISK.MNG"], files["SECOND.MID"], files["PSONG.MNG"])
    assert calls == [([files["PDISK.MNG"], files["PSONG.MNG"]], {"song_paths": ["SECOND.MID"]})]
    assert set(window.pendingImageAdditions) == {"PDISK.MNG", "PSONG.MNG", "SECOND.MID"}
    assert window.imagePianodirTitleEdit.text() == "An original album"
    assert window.pendingImageTitleEdits == {"SECOND.MID": "First in catalog"}
    assert image.read_bytes() == original


def test_image_eseq_conversion_omits_catalogs_and_undo_restores(window, tmp_path):
    files = _album(tmp_path)
    window.settings.setValue(SETTING_PROFILE, "custom")
    image, original = _image(window, tmp_path, list(files.values()))
    window.convert_all_midi_to_eseq()
    assert {"PDISK.MNG", "PSONG.MNG"} <= window.pendingImageDeletes
    assert not {"PDISK.MNG", "PSONG.MNG"} & set(_listed(window))
    assert image.read_bytes() == original
    window.undo_last_staged_batch()
    assert not window.pendingImageDeletes
    assert {"PDISK.MNG", "PSONG.MNG"} <= set(_listed(window))


@pytest.mark.parametrize("image_mode", [False, True], ids=["folder", "image"])
def test_destination_switch_restores_catalogs_consumed_by_eseq_preparation(window, tmp_path, image_mode):
    from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile
    files = _album(tmp_path)
    window.settings.setValue(SETTING_PROFILE, "custom")
    if image_mode:
        _image(window, tmp_path, list(files.values()))
    else:
        window._load_regular_files([str(path) for path in files.values()], "Imported", prepare_destination=False)
    def apply(key):
        profile = get_preparation_profile(key)
        window._apply_preparation_profile(profile, get_preparation_medium(profile, profile.default_medium))
    apply("mark_i")
    assert not {"PDISK.MNG", "PSONG.MNG"} & set(_listed(window))
    apply("midi_export")
    assert {"PDISK.MNG", "PSONG.MNG"} <= set(_listed(window))
    assert files["PDISK.MNG"].read_bytes() == disk_catalog()
    assert files["PSONG.MNG"].read_bytes() == song_catalog()


def test_catalogs_are_preserved_in_image_exports_and_are_not_song_tag_sidecars(window, tmp_path):
    files = _album(tmp_path)
    window._load_regular_files([str(path) for path in files.values()], "Imported", prepare_destination=False)
    scratch = tmp_path / "export"
    scratch.mkdir()
    specs = window._stage_files_for_image_export(str(scratch))
    assert {spec["image_path"] for spec in specs} == set(files)
    for spec in specs:
        if spec["image_path"] in {"PDISK.MNG", "PSONG.MNG"}:
            assert Path(spec["host_path"]).read_bytes() == files[spec["image_path"]].read_bytes()
    window.settings.setValue(window.SETTING_WRITE_TAG_SIDECARS, True)
    assert not window._write_tag_sidecars_for_regular_rows()
    assert (tmp_path / "FIRST.tags.txt").is_file()
    assert not (tmp_path / "PSONG.tags.txt").exists()
    assert not (tmp_path / "PDISK.tags.txt").exists()
    destinations = window._regular_save_as_destination_paths(str(scratch))
    assert not any(Path(path).name in {"PSONG.tags.txt", "PDISK.tags.txt"} for path in destinations)


def test_other_mng_names_are_still_unsupported(window, tmp_path):
    path = tmp_path / "OTHER.MNG"
    path.write_bytes(b"Unrelated file")
    assert not window.can_accept_regular_drop_path(str(path))
    assert window.add_regular_file_from_drop(str(path))["status"] == "error"


def test_redropped_existing_catalog_still_applies_to_new_songs(window, tmp_path, monkeypatch):
    files = _album(tmp_path)
    _drop(window.table, files["PSONG.MNG"])
    calls = []
    def applied(catalogs, *, song_paths):
        calls.append(([Path(path) for path in catalogs], {"song_paths": [Path(path) for path in song_paths]}))
        return "Applied"

    monkeypatch.setattr(window, "_apply_imported_catalogs", applied)
    _drop(window.table, files["PSONG.MNG"], files["SECOND.MID"])
    assert calls == [([files["PSONG.MNG"]], {"song_paths": [files["SECOND.MID"]]})]
    assert set(_listed(window)) == {"PSONG.MNG", "SECOND.MID"}
