"""PIANODIR order transfers to MIDI without staging native E-SEQ order edits."""

from pathlib import Path
import ntpath
import posixpath
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import PianodirMetadata, PianodirTrackEntry, build_pianodir_bytes
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files
from aps_midi_prep_tool_app.smart_pianosoft import (
    SmartPianoSoftMetadata, SmartPianoSoftSong, parse_smart_pianosoft_song_catalog,
)
from test_prepared_image_delivery import _apply, _song, window


@pytest.mark.parametrize("path_module,directory", [(posixpath, "/music/album"), (ntpath, r"C:\music\album")],
                         ids=["posix", "windows"])
def test_catalog_conversion_order_matches_native_path_separators(monkeypatch, path_module, directory):
    items = [SimpleNamespace(text=lambda name=name: path_module.join(directory, name), row=lambda row=row: row)
             for row, name in enumerate(("A.FIL", "B.FIL"))]
    metadata = SmartPianoSoftMetadata(songs=(SmartPianoSoftSong(1, "B.FIL", "B"),
                                            SmartPianoSoftSong(2, "A.FIL", "A")))
    view = SimpleNamespace(
        _eseq_order_changed=lambda: False, is_image_mode=lambda: False,
        _regular_eseq_rows=lambda: [0, 1], table=SimpleNamespace(item=lambda row, _column: items[row]),
    )
    monkeypatch.setattr(main_window, "os", SimpleNamespace(path=path_module))
    monkeypatch.setattr(main_window, "catalogs_enabled", lambda _window: True)
    monkeypatch.setattr(main_window, "yamaha_catalog_metadata_from_directory", lambda _directory: metadata)
    ordered = main_window.MidiTitleWindow._yamaha_midi_conversion_order(view)
    assert [item.row() for item in ordered] == [1, 0]


def _load(window, tmp_path, image_source, count=3):
    source = tmp_path / "songs"
    source.mkdir()
    tracks = []
    for index in range(count):
        name = chr(ord("A") + index)
        path = source / f"{name}.FIL"
        path.write_bytes(convert_midi_bytes_to_eseq_bytes(
            _song(f"Song {name}", 60 + index), filename_hint=path.name,
        ))
        tracks.append(PianodirTrackEntry(path.name, str(path), f"Song {name}"))
    catalog = source / "PIANODIR.FIL"
    catalog.write_bytes(build_pianodir_bytes(list(reversed(tracks)), PianodirMetadata(disk_title="Reversed album")))
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    if image_source:
        image = tmp_path / "source.img"
        create_floppy_images_from_files([(str(path), path.name) for path in source.iterdir()],
                                       str(image), "img", DISK_FORMAT_BY_KEY["ibm.720"])
        session = FloppyImageSession.load(image)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    else:
        window._load_regular_files([str(path) for path in source.iterdir()], "", prepare_destination=False)
    return source, originals


@pytest.mark.parametrize("image_source", [False, True], ids=["folder", "image"])
@pytest.mark.parametrize("user_reordered", [False, True], ids=["catalog-order", "user-order"])
def test_midi_conversion_uses_catalog_order_unless_user_reordered(window, tmp_path, monkeypatch, image_source, user_reordered):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    errors = []
    monkeypatch.setattr(window, "_show_error_list", lambda *args, **_kwargs: errors.append(args))
    source, originals = _load(window, tmp_path, image_source)
    rows = window._current_eseq_rows()
    assert [Path(window.table.item(row, 1).text()).name for row in rows] == ["A.FIL", "B.FIL", "C.FIL"]
    assert not window._eseq_order_changed()
    assert not (window._image_eseq_order_key_edits() if image_source else window._regular_eseq_order_key_edits())
    if user_reordered:
        window.table.setCurrentCell(rows[1], 4)
        window.move_selected_eseq_row(-1)
        assert window._eseq_order_changed()
    _apply(window)
    assert not errors
    output = tmp_path / "export"
    if image_source:
        window._export_image_session_files_to_folder(str(output))
    else:
        failures, *_ = window._export_regular_files_to_folder(str(output))
        assert not failures
    songs = parse_smart_pianosoft_song_catalog((output / "PSONG.MNG").read_bytes())
    assert [song.title for song in songs] == (["Song B", "Song A", "Song C"] if user_reordered else
                                            ["Song C", "Song B", "Song A"])
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("image_source", [False, True], ids=["folder", "image"])
def test_failed_conversion_does_not_reorder_remaining_native_songs(window, tmp_path, monkeypatch, image_source):
    source, originals = _load(window, tmp_path, image_source, count=4)
    converter = main_window.convert_eseq_file_to_midi_path

    def convert(source_path, destination, **kwargs):
        if kwargs.get("filename") in {"B.FIL", "D.FIL"}:
            raise ValueError("Synthetic unreadable song")
        return converter(source_path, destination, **kwargs)

    monkeypatch.setattr(main_window, "convert_eseq_file_to_midi_path", convert)
    monkeypatch.setattr(window, "_show_error_list", lambda *_args, **_kwargs: None)
    _apply(window)
    remaining = window._current_eseq_rows()
    assert [Path(window.table.item(row, 1).text()).name for row in remaining] == ["B.FIL", "D.FIL"]
    assert not window._eseq_order_changed()
    assert not (window._image_eseq_order_key_edits() if image_source else window._regular_eseq_order_key_edits())
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
