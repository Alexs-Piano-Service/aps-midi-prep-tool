"""PIANODIR inspection decodes source records without treating them as songs."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.catalog_inspection import (
    CATALOG_NAMES, inspect_catalog, song_fields, song_playback_descriptor,
)
from aps_midi_prep_tool_app.catalog_inspection_widget import CatalogInspectionWidget
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_HEADER, PIANODIR_COUNT_OFFSET, PIANODIR_DISK_METADATA_OFFSET,
    PIANODIR_ROW_PATH, PIANODIR_TARGET_FILE_SIZE,
)
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_catalog_inspection import application, inspection_dialog, song_catalog


def pianodir_catalog(count=2):
    data = bytearray(PIANODIR_TARGET_FILE_SIZE)
    data[:16] = PIANODIR_HEADER
    for index in range(count):
        record = bytearray(0x50)
        record[:11] = f"SONG{count - index:04d}FIL".encode("ascii")
        record[0x10:0x14] = (1000 + index).to_bytes(4, "little")
        record[0x14:0x1a] = b"\r\n\x1a\xff\x00\x80"
        record[0x28] = 0x81
        record[0x29] = 0x82
        record[0x30:0x50] = f"Title {index + 1}".encode("ascii").ljust(32)
        start = 16 + index * 0x50
        data[start:start + 0x50] = record
    label = b"ABC-1234        An example album"
    data[0x12d0:0x1310] = label.ljust(64, b"\x00")
    data[0x1310:0x1314] = sum(1000 + i for i in range(count)).to_bytes(4, "little")
    data[0x1314:0x1316] = b"\xab\xcd"
    data[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2] = (count + 1).to_bytes(2, "little")
    return bytes(data)


def test_pianodir_preserves_binary_bytes_and_decodes_ordered_records():
    raw = pianodir_catalog()
    info = inspect_catalog(raw)
    assert info.kind == "PIANODIR.FIL"
    assert info.title == "An example album"
    assert info.raw == raw
    assert not info.normalized_line_endings
    assert [(song.track_number, song.filename, song.title) for song in info.songs] == [
        (1, "SONG0002.FIL", "Title 1"), (2, "SONG0001.FIL", "Title 2"),
    ]
    assert info.songs[0].raw_record == raw[0x10:0x60]
    fields = {field.label: field for field in info.fields}
    assert fields["Catalog number"].value == "ABC-1234"
    assert fields["Album title"].offset == PIANODIR_DISK_METADATA_OFFSET
    assert fields["Declared records"].value == "2"
    assert fields["Stored record count (songs + 1)"].value == "3"
    assert fields["Total declared duration (ticks)"].value == "2001"
    assert fields["Secondary aggregate (raw)"].value == "AB CD"
    song = info.songs[1]
    fields = {field.label: field for field in song_fields(song)}
    assert fields["Filename"].offset == 0x60
    assert fields["Title"].offset == 0x90
    assert fields["Declared duration (ticks)"].value == "1001"
    assert fields["Timing bytes (album-specific)"].value == "0D 0A 1A FF 00 80"
    assert fields["Write protection"].value == "Set"
    assert fields["Write protection"].offset == 0x88
    assert fields["Arrangement/type code"].value == "Ensemble"
    assert song_playback_descriptor(song) == "Ensemble"


@pytest.mark.parametrize("count", [0, 1, 60])
def test_pianodir_count_boundaries(count):
    info = inspect_catalog(pianodir_catalog(count))
    assert len(info.songs) == count
    assert info.title == "An example album"
    if count:
        assert info.songs[-1].filename == "SONG0001.FIL"


def test_header_only_pianodir_is_an_empty_catalog():
    info = inspect_catalog(PIANODIR_HEADER)
    assert info.kind == "PIANODIR.FIL"
    assert not info.songs
    assert not info.title
    assert info.raw == PIANODIR_HEADER


@pytest.mark.parametrize("length", [17, 0x12d0, 0x1317])
def test_truncated_pianodir_fails_clearly(length):
    with pytest.raises(ValueError, match="too small"):
        inspect_catalog(pianodir_catalog()[:length])


@pytest.mark.parametrize("count", [0, 62, 65535])
def test_invalid_pianodir_counts_are_rejected(count):
    raw = bytearray(pianodir_catalog())
    raw[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2] = count.to_bytes(2, "little")
    with pytest.raises(ValueError, match="invalid track count"):
        inspect_catalog(raw)


def test_pianodir_marker_is_validated():
    raw = bytearray(pianodir_catalog())
    raw[0] = 0
    with pytest.raises(ValueError, match="not a valid PIANODIR"):
        inspect_catalog(raw)


@pytest.mark.parametrize("language", [lang.code for lang in SUPPORTED_LANGUAGES])
def test_pianodir_widget_shows_fields_raw_views_and_no_midi_apply_action(application, tmp_path, language):
    path = tmp_path / "PIANODIR.FIL"
    path.write_bytes(pianodir_catalog())
    widget = CatalogInspectionWidget(language=language)
    widget.set_apply_enabled(True)
    widget.load_path(path)
    assert widget.heading.text() == "An example album"
    assert widget.songs.rowCount() == 2
    assert not widget.songs.isHidden()
    assert widget.songs.item(0, 2).text() == "SONG0002.FIL"
    assert widget.songs.item(0, 3).text() == translate_text("Ensemble", language)
    assert widget.song_details.topLevelItem(0).text(1) == "SONG0002.FIL"
    assert widget.song_details.topLevelItem(4).text(1) == translate_text("Set", language)
    assert widget.apply_row.isHidden()
    assert not widget.apply_button.isEnabled()
    assert widget.offset_note.isHidden()
    widget.tabs.setCurrentIndex(2)
    assert "FE 00 00 00 14 00 00 50" in widget.hex_text.toPlainText()
    assert "PIANODIR" in widget.raw_text.toPlainText()
    assert path.read_bytes() == pianodir_catalog()
    # Switching back restores the MNG-specific descriptor and apply action.
    path.write_bytes(song_catalog())
    widget.load_path(path)
    assert widget.songs.item(1, 3).text() == "A,I,P,M,SMF1,0"
    assert not widget.apply_row.isHidden()
    assert widget.apply_button.isEnabled()
    path.write_bytes(pianodir_catalog()[:17])
    widget.load_path(path)
    assert widget.inspection is None
    assert widget.tabs.isHidden()
    assert widget.songs.rowCount() == 0
    assert widget.song_details.topLevelItemCount() == 0


def test_open_pianodir_directly_keeps_it_out_of_playback(inspection_dialog, tmp_path, monkeypatch):
    path = tmp_path / "PIANODIR.FIL"
    path.write_bytes(pianodir_catalog())
    dialog = inspection_dialog(items_callback=lambda: [], catalog_action_callback=lambda item: pytest.fail("Read-only"))
    filters = []

    def choose(*args):
        filters.append(args[-1])
        return str(path), ""

    monkeypatch.setattr(main_window.QFileDialog, "getOpenFileName", choose)
    dialog._open_catalog()
    dialog._refresh_inspection_items()
    assert "*.fil *.FIL" in filters[0]
    assert dialog.file_tree.topLevelItemCount() == 1
    assert dialog.inspection_stack.currentWidget() is dialog.catalog_view
    assert dialog.catalog_view.inspection.kind == "PIANODIR.FIL"
    assert dialog.current_midi_bytes == b""
    assert not dialog.play_button.isEnabled()
    assert not dialog.render_song_button.isEnabled()
    assert not dialog.convert_type0_button.isEnabled()
    assert not dialog.catalog_view.apply_button.isEnabled()
    assert path.read_bytes() == pianodir_catalog()


def test_pianodir_identity_survives_temporary_material_path():
    assert main_window.FileInspectionDialog._is_catalog_item({
        "path": "/tmp/1234", "display_name": "pianodir.fil",
    })
    assert main_window.FileInspectionDialog._is_catalog_item({
        "path": "/tmp/1234", "source_path": "FOLDER/PIANODIR.FIL",
    })
    # Inspection must not change format-specific MIDI import/delivery handling.
    assert "PIANODIR.FIL" not in CATALOG_NAMES


def test_folder_discovery_includes_pianodir_with_only_special_row(application, tmp_path):
    path = tmp_path / "pianodir.fil"
    path.write_bytes(pianodir_catalog())
    table = QTableWidget(1, 4)
    table.setItem(0, 1, QTableWidgetItem(PIANODIR_ROW_PATH))
    window = SimpleNamespace(
        table=table, is_image_mode=lambda: False,
        regularModeContextPath="", regularPianodirSourcePath=str(path),
        _regular_source_material_path=lambda path: path,
    )
    items = main_window.MidiTitleWindow._inspection_catalog_items(window, [])
    assert len(items) == 1
    assert items[0]["kind"] == "catalog"
    assert items[0]["path"] == str(path)


def test_image_discovery_uses_staged_pianodir_and_omits_deleted_catalog(tmp_path):
    staged = tmp_path / "staged-pianodir"
    staged.write_bytes(pianodir_catalog())
    window = SimpleNamespace(
        is_image_mode=lambda: True,
        imageEntriesByPath={"PIANODIR.FIL": object()},
        pendingImageAdditions={}, pendingImageDeletes=set(),
        pendingDeletePianodir=False, image_session=object(),
        _pending_or_extracted_image_path=lambda path: str(staged),
    )
    items = main_window.MidiTitleWindow._inspection_catalog_items(window, [])
    assert len(items) == 1
    assert items[0]["path"] == str(staged)
    assert items[0]["source_path"] == "PIANODIR.FIL"
    window.pendingDeletePianodir = True
    assert main_window.MidiTitleWindow._inspection_catalog_items(window, []) == []
    window.pendingDeletePianodir = False
    window.pendingImageDeletes.add("PIANODIR.FIL")
    assert main_window.MidiTitleWindow._inspection_catalog_items(window, []) == []
