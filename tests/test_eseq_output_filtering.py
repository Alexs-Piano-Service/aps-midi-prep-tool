"""E-SEQ exports retain songs by content and consume, rather than copy, catalogs."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import (
    convert_midi_bytes_to_eseq_bytes, parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_COUNT_OFFSET, PIANODIR_HEADER, PIANODIR_TRACK_SIZE,
    PianodirMetadata, PianodirTrackEntry, build_pianodir_bytes, parse_pianodir_metadata,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from test_save_as_overwrite import _song, _title
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes


EXPORT_ROUTES = ("folder", "folder_to_image", "image_to_folder", "image_to_image")


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "eseq-output.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args, **_kwargs: pytest.fail("Unexpected question"))
    instance = main_window.MidiTitleWindow()
    instance.album_subfolder_checkbox.setChecked(False)
    errors = []
    monkeypatch.setattr(instance, "_show_error_list", lambda *args, **kwargs: errors.append((args, kwargs)))
    monkeypatch.setattr(instance, "_show_operation_error", lambda *args, **kwargs: errors.append((args, kwargs)))
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args, **kwargs: errors.append((args, kwargs)))
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()
    assert not errors


def _load(window, tmp_path, files, route):
    source = tmp_path / "source"
    source.mkdir()
    paths = []
    for name, payload in files.items():
        path = source / name
        path.write_bytes(payload)
        paths.append(path)
    if route.startswith("image_"):
        image_path = tmp_path / "original.img"
        create_floppy_images_from_files(
            [{"host_path": str(path), "image_path": path.name} for path in paths],
            image_path, "img", DISK_FORMAT_BY_KEY["ibm.720"],
        )
        session = FloppyImageSession.load(image_path)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
        paths.append(image_path)
    else:
        window._load_regular_files([str(path) for path in paths], "Test source", prepare_destination=False)
    window.table.setSortingEnabled(False)
    return {path: path.read_bytes() for path in paths}


def _export(window, tmp_path, route):
    destination = tmp_path / "export"
    destination.mkdir()
    if route == "folder_to_image":
        specs = window._stage_files_for_image_export(str(destination))
        return {spec["image_path"]: Path(spec["host_path"]).read_bytes() for spec in specs}
    if route == "image_to_image":
        paths = window.image_session.export_to_images(
            str(destination / "saved.img"), "img", DISK_FORMAT_BY_KEY["ibm.720"],
            **window._collect_current_image_write_operations(),
        )
        assert paths == [str(destination / "saved.img")]
        saved = FloppyImageSession.load(paths[0])
        try:
            return {
                entry.path: Path(saved.extract_file(entry.path)).read_bytes()
                for entry in saved.list_entries().entries
            }
        finally:
            saved.cleanup()
    if route == "image_to_folder":
        paths = window._export_image_session_files_to_folder(str(destination))
    else:
        errors, paths, _mapping, _summary = window._export_regular_files_to_folder(str(destination))
        assert not errors
    assert {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()} == {
        str(Path(path).relative_to(destination)) for path in paths
    }
    return {str(Path(path).relative_to(destination)): Path(path).read_bytes() for path in paths}


def _catalog_tracks(payload):
    count = int.from_bytes(payload[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") - 1
    tracks = []
    for index in range(count):
        start = len(PIANODIR_HEADER) + index * PIANODIR_TRACK_SIZE
        record = payload[start:start + PIANODIR_TRACK_SIZE]
        stem, extension = record[:8].decode("ascii").rstrip(), record[8:11].decode("ascii").rstrip()
        name = stem + ("." + extension if extension else "")
        tracks.append((name, record[0x30:0x50].decode("cp1252").strip()))
    return tracks


@pytest.mark.parametrize("route", EXPORT_ROUTES)
def test_eseq_output_excludes_catalogs_and_unrelated_files_regardless_of_extension(window, tmp_path, route):
    files = {
        "EXTLESS": convert_midi_bytes_to_eseq_bytes(_song("Without extension", 60)),
        "NORMAL.FIL": convert_midi_bytes_to_eseq_bytes(_song("Normal song", 64)),
        "JUNK.FIL": b"A .FIL suffix does not make this an E-SEQ song." * 8,
        "UNREL.MID": _song("Unconverted MIDI", 67),
        "PDISK.MNG": _pdisk_bytes("Catalog album"),
        "PSONG.MNG": _psong_bytes([("UNREL.MID", "Catalog song")]),
    }
    originals = _load(window, tmp_path, files, route)
    assert window.imageEseqMode if route.startswith("image_") else window.is_local_eseq_mode()

    output = _export(window, tmp_path, route)

    assert set(output) == {"EXTLESS", "NORMAL.FIL", "PIANODIR.FIL"}
    assert sorted(_catalog_tracks(output["PIANODIR.FIL"])) == [
        ("EXTLESS", "Without extension"), ("NORMAL.FIL", "Normal song"),
    ]
    assert {parse_eseq_bytes(output[name]).title for name in ("EXTLESS", "NORMAL.FIL")} == {
        "Without extension", "Normal song",
    }
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("route", EXPORT_ROUTES)
def test_midi_output_keeps_smart_pianosoft_catalogs(window, tmp_path, route):
    files = {
        "FIRST.MID": _song("Embedded title", 60),
        "PDISK.MNG": _pdisk_bytes("Catalog album"),
        "PSONG.MNG": _psong_bytes([("FIRST.MID", "Catalog song")]),
    }
    originals = _load(window, tmp_path, files, route)
    assert not (window.imageEseqMode if route.startswith("image_") else window.is_local_eseq_mode())

    output = _export(window, tmp_path, route)

    assert set(output) == set(files)
    for name in ("PDISK.MNG", "PSONG.MNG"):
        assert output[name] == files[name]
    if not route.startswith("image_"):
        assert _title(output["FIRST.MID"]) == "Embedded title"
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("route", EXPORT_ROUTES)
def test_conversion_uses_smart_pianosoft_song_and_album_titles_but_omits_catalogs(window, tmp_path, route):
    files = {
        "FIRST.MID": _song("Embedded title", 60),
        "PDISK.MNG": _pdisk_bytes("Catalog album"),
        "PSONG.MNG": _psong_bytes([("FIRST.MID", "Catalog song")]),
        "STALE.MNG": b"Unrelated stale management file",
    }
    originals = _load(window, tmp_path, files, route)
    if route.startswith("image_"):
        window._convert_all_image_rows("midi", "eseq", confirm=False)
        assert window.imageEseqMode
    else:
        window._convert_all_regular_rows("midi", "eseq", confirm=False)
        assert window.is_local_eseq_mode()

    output = _export(window, tmp_path, route)

    assert set(output) == {"FIRST.FIL", "PIANODIR.FIL"}
    assert parse_eseq_bytes(output["FIRST.FIL"]).title == "Catalog song"
    assert _catalog_tracks(output["PIANODIR.FIL"]) == [("FIRST.FIL", "Catalog song")]
    assert parse_pianodir_metadata(output["PIANODIR.FIL"]).disk_title == "Catalog album"
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("route", ("image_to_folder", "image_to_image"))
def test_unchanged_native_album_refreshes_populated_catalog_to_exclude_non_songs(window, tmp_path, route):
    song = convert_midi_bytes_to_eseq_bytes(_song("Original performance", 60))
    catalog_source = tmp_path / "catalog-song-source"
    catalog_source.write_bytes(song)
    catalog = build_pianodir_bytes(
        [PianodirTrackEntry("EXTLESS", str(catalog_source), "Original performance")],
        PianodirMetadata(catalog_number="NATIVE-1", disk_title="Original album"),
    )
    files = {
        "EXTLESS": song,
        "PIANODIR.FIL": catalog,
        "PSONG.MNG": _psong_bytes([("UNUSED.MID", "Unused title")]),
        "PDISK.MNG": _pdisk_bytes("Do not override the original album"),
        "JUNK.FIL": b"Not an E-SEQ performance",
        "NOTES.TXT": b"Original disk notes must not be destroyed",
    }
    originals = _load(window, tmp_path, files, route)
    assert window.imageHasPianodir and window.imagePianodirPopulated
    assert not window.pendingImageReplacements and not window.pendingImageDeletes
    assert window._should_generate_pianodir()

    output = _export(window, tmp_path, route)

    assert set(output) == {"EXTLESS", "PIANODIR.FIL"}
    assert _catalog_tracks(output["PIANODIR.FIL"]) == [("EXTLESS", "Original performance")]
    assert parse_pianodir_metadata(output["PIANODIR.FIL"]) == parse_pianodir_metadata(catalog)
    assert parse_eseq_bytes(output["EXTLESS"]) == parse_eseq_bytes(song)
    assert catalog_source.read_bytes() == song
    assert {path: path.read_bytes() for path in originals} == originals


def test_explicit_folder_title_edit_wins_old_catalog_title_during_eseq_conversion(window, tmp_path, monkeypatch):
    files = {
        "FIRST.MID": _song("Embedded title", 60),
        "PDISK.MNG": _pdisk_bytes("Catalog album"),
        "PSONG.MNG": _psong_bytes([("FIRST.MID", "Old catalog song")]),
    }
    originals = _load(window, tmp_path, files, "folder")
    source_path = str(tmp_path / "source" / "FIRST.MID")
    row = window._find_regular_row_for_path(source_path)
    assert window._row_raw_title(row) == "Embedded title"
    monkeypatch.setattr(window, "_prompt_for_title", lambda *_args, **_kwargs: ("My corrected title", True))

    window.edit_via_dialog(row)
    assert window.pendingEdits[source_path] == "My corrected title"
    window._convert_all_regular_rows("midi", "eseq", confirm=False)
    output = _export(window, tmp_path, "folder")

    assert set(output) == {"FIRST.FIL", "PIANODIR.FIL"}
    assert parse_eseq_bytes(output["FIRST.FIL"]).title == "My corrected title"
    assert _catalog_tracks(output["PIANODIR.FIL"]) == [("FIRST.FIL", "My corrected title")]
    assert parse_pianodir_metadata(output["PIANODIR.FIL"]).disk_title == "Catalog album"
    assert {path: path.read_bytes() for path in originals} == originals
