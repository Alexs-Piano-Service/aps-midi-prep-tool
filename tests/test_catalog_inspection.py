"""Catalog inspection preserves source bytes and exposes albums and ordered songs."""

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableWidget, QTableWidgetItem

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.catalog_inspection import (
    MAX_CATALOG_BYTES, catalog_hex, inspect_catalog, read_catalog, song_fields,
)
from aps_midi_prep_tool_app.catalog_inspection_widget import CatalogInspectionWidget
from aps_midi_prep_tool_app.catalog_inspection_translations import CATALOG_INSPECTION_TRANSLATIONS
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.smart_pianosoft import build_smart_pianosoft_song_record
from test_zip_drop import window


def disk_catalog(title="An original album"):
    data = bytearray((b" " * 14 + b"\r\n") * 8)
    data[:48] = b"PDISK   MNG   \r\nP.PLAYER      \r\nVer1.01DMV0.53\r\n"
    data[0x30:0x70] = title.encode("cp1252").ljust(64)
    data[0x70:0x72] = b"\r\n"
    data[0x72:0x73] = b"S"
    return bytes(data)


def song_catalog():
    header = bytearray((b" " * 14 + b"\r\n") * 8)
    header[:48] = b"PSONG   MNG   \r\nMAX002        \r\nFILE002       \r\n"
    return bytes(header) + build_smart_pianosoft_song_record("SECOND.MID", "First in catalog") + build_smart_pianosoft_song_record("FIRST.MID", "Second in catalog", midi_format=1) + b" " * 32


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def test_album_fields_and_uninterpreted_flags_preserve_original_bytes():
    data = disk_catalog("Café piano")
    info = inspect_catalog(data)
    fields = {field.label: field for field in info.fields}
    assert info.kind == "PDISK.MNG"
    assert info.title == "Café piano"
    assert fields["Program field"].value == "P.PLAYER"
    assert fields["Version field"].value == "Ver1.01DMV0.53"
    assert fields["Album title"].offset == 0x30
    assert fields["Uninterpreted data"].value == "S"
    assert info.raw == data
    assert not info.songs
    assert not info.normalized_line_endings


def test_song_order_descriptors_and_record_offsets():
    info = inspect_catalog(song_catalog())
    assert [(s.track_number, s.filename, s.title) for s in info.songs] == [
        (1, "SECOND.MID", "First in catalog"), (2, "FIRST.MID", "Second in catalog")]
    fields = {field.label: field for field in song_fields(info.songs[1])}
    assert fields["Playback descriptor"].value == "A,I,P,M,SMF1,0"
    assert fields["Playback descriptor"].offset == 0x80 + 0xb0 + 0x50
    assert fields["Uninterpreted data"].value.startswith("L")
    assert info.fields[-1].label == "Trailing bytes"
    assert info.fields[-1].size == 32


@pytest.mark.parametrize("payload", [disk_catalog(), song_catalog()], ids=["disk", "songs"])
def test_lf_copies_keep_raw_bytes_while_using_canonical_field_offsets(payload):
    lf = payload.replace(b"\r\n", b"\n")
    info = inspect_catalog(lf)
    assert info.normalized_line_endings
    assert info.raw == lf
    assert info.title == inspect_catalog(payload).title
    assert info.songs == inspect_catalog(payload).songs


@pytest.mark.parametrize("payload, message", [(b"hello", "not a PDISK"), (disk_catalog()[:60], "truncated"), (song_catalog()[:150], "truncated"), (b"x" * (MAX_CATALOG_BYTES + 1), "too large")], ids=["invalid-header", "short-disk", "short-songs", "oversized"])
def test_invalid_catalogs_fail_clearly(payload, message):
    with pytest.raises(ValueError, match=message):
        inspect_catalog(payload)


def test_raw_hex_has_original_offsets_and_bytes():
    assert catalog_hex(b"AB\x00\xff") == "00000000  41 42 00 FF                                      AB.."


def test_widget_displays_album_and_read_only_ordered_song_table(application, tmp_path):
    disk = tmp_path / "PDISK.MNG"
    song = tmp_path / "PSONG.MNG"
    disk.write_bytes(disk_catalog())
    song.write_bytes(song_catalog())
    widget = CatalogInspectionWidget()
    widget.load_path(disk)
    assert widget.heading.text() == "An original album"
    assert widget.songs.isHidden()
    assert widget.fields.topLevelItem(3).text(1) == "An original album"
    widget.load_path(song)
    assert widget.songs.rowCount() == 2
    assert widget.songs.item(0, 2).text() == "SECOND.MID"
    widget.songs.selectRow(1)
    assert widget.song_details.topLevelItem(0).text(1) == "FIRST.MID"
    assert widget.song_details.topLevelItem(4).text(1) == "A,I,P,M,SMF1,0"
    assert widget.songs.editTriggers() == QAbstractItemView.NoEditTriggers
    assert not widget.raw_text.toPlainText()
    widget.tabs.setCurrentIndex(2)
    assert "50 53 4F 4E 47" in widget.hex_text.toPlainText()
    assert widget.raw_text.toPlainText().startswith("PSONG")
    assert disk.read_bytes() == disk_catalog()
    assert song.read_bytes() == song_catalog()


def test_widget_does_not_leave_previous_song_details_after_error(application, tmp_path):
    path = tmp_path / "PSONG.MNG"
    path.write_bytes(song_catalog())
    widget = CatalogInspectionWidget()
    widget.load_path(path)
    path.write_bytes(b"invalid")
    widget.load_path(path)
    assert widget.inspection is None
    assert widget.songs.rowCount() == 0
    assert widget.song_details.topLevelItemCount() == 0
    assert widget.tabs.isHidden()
    assert "not a PDISK" in widget.description.text()


@pytest.mark.parametrize("language", [lang.code for lang in SUPPORTED_LANGUAGES])
def test_catalog_copy_registered_in_every_language(language):
    for source, copies in CATALOG_INSPECTION_TRANSLATIONS.items():
        assert translate_text(source, language) == (source if language == "en" else copies[language])


@pytest.fixture
def inspection_dialog(application, monkeypatch):
    from aps_midi_prep_tool_app import audio_output_routing
    from test_audio_output_routing import _FakeMediaDevices
    from test_file_inspection_actions import _SilentPlayer, _SilentAudioOutput

    monkeypatch.setattr(audio_output_routing, "QMediaDevices", _FakeMediaDevices)
    monkeypatch.setattr(main_window, "QMediaPlayer", _SilentPlayer)
    monkeypatch.setattr(main_window, "QAudioOutput", _SilentAudioOutput)
    monkeypatch.setattr(main_window, "_midi_output_ports", lambda: ([], ""))
    monkeypatch.setattr(main_window, "_available_soundfonts", lambda: [])
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: None)
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(main_window.QMessageBox, name, lambda *args, **kwargs: pytest.fail(f"Unexpected modal: {args[1:]}"))
    dialogs = []

    def create(items=(), **kwargs):
        dialog = main_window.FileInspectionDialog(items, **kwargs)
        dialogs.append(dialog)
        return dialog

    yield create
    for dialog in dialogs:
        dialog.close()


def test_empty_inspection_can_open_catalog_without_loading_main_list(inspection_dialog, monkeypatch, tmp_path):
    source = tmp_path / "PDISK.MNG"
    source.write_bytes(disk_catalog())
    dialog = inspection_dialog(items_callback=lambda: [])
    assert dialog.inspection_stack.currentWidget() is dialog.catalog_view
    assert dialog.open_catalog_button.isEnabled()
    monkeypatch.setattr(main_window.QFileDialog, "getOpenFileName", lambda *_args: (str(source), ""))
    dialog._open_catalog()
    assert dialog.catalog_view.heading.text() == "An original album"
    dialog._refresh_inspection_items()
    assert dialog.file_tree.topLevelItemCount() == 1
    assert not dialog.play_button.isEnabled()
    assert not dialog.convert_type0_button.isEnabled()
    assert source.read_bytes() == disk_catalog()


def test_apply_catalog_refreshes_loaded_titles_and_keeps_catalog_selected(inspection_dialog, tmp_path):
    source = tmp_path / "PSONG.MNG"
    original = song_catalog()
    source.write_bytes(original)
    items = [{"kind": "catalog", "path": str(source), "source_path": str(source)}]
    calls = []

    def apply(item):
        calls.append(item)
        assert dialog._editing_file
        assert not dialog.catalog_view.apply_button.isEnabled()
        assert not dialog.file_tree.isEnabled()
        assert not dialog.open_catalog_button.isEnabled()
        items.append({"path": str(tmp_path / "SECOND.MID"), "title": "First in catalog"})
        return {"applied": True, "message": "Applied 1 title. Skipped 1 unmatched record."}

    dialog = inspection_dialog(items, items_callback=lambda: items, catalog_action_callback=apply)
    assert dialog.catalog_view.apply_button.text() == "Apply song titles"
    assert dialog.catalog_view.apply_button.isEnabled()
    dialog.catalog_view.apply_button.click()
    assert calls == [items[0]]
    assert dialog.file_tree.topLevelItemCount() == 2
    assert dialog._current_item()["path"] == str(source)
    assert dialog.catalog_view.apply_status.text() == "Applied 1 title. Skipped 1 unmatched record."
    assert dialog.catalog_view.apply_button.isEnabled()
    assert dialog.file_tree.isEnabled()
    assert dialog.open_catalog_button.isEnabled()
    assert source.read_bytes() == original


def test_external_album_catalog_can_be_applied_and_errors_remain_inline(inspection_dialog, monkeypatch, tmp_path):
    source = tmp_path / "PDISK.MNG"
    source.write_bytes(disk_catalog())
    calls = []

    def apply(item):
        calls.append(item["path"])
        if len(calls) == 1:
            raise ValueError("The destination is busy.")
        return {"applied": True, "message": "Album title applied."}

    dialog = inspection_dialog(items_callback=lambda: [], catalog_action_callback=apply)
    assert not dialog.catalog_view.apply_button.isEnabled()
    monkeypatch.setattr(main_window.QFileDialog, "getOpenFileName", lambda *_args: (str(source), ""))
    dialog._open_catalog()
    assert dialog.catalog_view.apply_button.text() == "Apply album title"
    dialog.catalog_view.apply_button.click()
    assert dialog.catalog_view.apply_status.text() == "Could not apply this catalog: The destination is busy."
    assert dialog.catalog_view.apply_button.isEnabled()
    dialog.catalog_view.apply_button.click()
    assert calls == [str(source), str(source)]
    assert dialog.catalog_view.apply_status.text() == "Album title applied."
    assert dialog.catalog_view.inspection.title == "An original album"
    assert source.read_bytes() == disk_catalog()


def test_apply_disabled_for_invalid_empty_busy_or_readonly_catalog(inspection_dialog, tmp_path):
    source = tmp_path / "PDISK.MNG"
    source.write_bytes(disk_catalog())
    calls = []
    dialog = inspection_dialog([{"path": str(source)}])
    assert not dialog.catalog_view.apply_button.isEnabled()
    dialog._catalog_action_callback = lambda item: calls.append(item)
    dialog._refresh_inspection_edit_buttons()
    assert dialog.catalog_view.apply_button.isEnabled()
    dialog._inspection_rendering = True
    dialog._apply_current_catalog()
    assert not dialog.catalog_view.apply_button.isEnabled()
    assert calls == []
    dialog._inspection_rendering = False
    source.write_bytes(b"invalid")
    dialog._load_current_file()
    dialog._apply_current_catalog()
    assert not dialog.catalog_view.apply_button.isEnabled()
    assert calls == []
    source.write_bytes(disk_catalog(""))
    dialog._load_current_file()
    assert not dialog.catalog_view.apply_button.isEnabled()


def test_dropped_catalog_rows_are_inspectable_without_entering_render_items(application, tmp_path):
    source = tmp_path / "PDISK.MNG"
    source.write_bytes(disk_catalog())
    table = QTableWidget(1, 4)
    table.setItem(0, 1, QTableWidgetItem(str(source)))
    table.setItem(0, 3, QTableWidgetItem(source.name))
    window = SimpleNamespace(table=table, _current_song_rows_for_listing=lambda: [0],
                             _song_title_for_row=lambda _row: "Untitled",
                             is_image_mode=lambda: False,
                             _regular_source_material_path=lambda path: path,
                             regularModeContextPath="")
    window._inspection_catalog_items = lambda items: main_window.MidiTitleWindow._inspection_catalog_items(window, items)
    assert main_window.MidiTitleWindow._inspection_items(window) == []
    items = main_window.MidiTitleWindow._inspection_items(window, include_catalogs=True)
    assert len(items) == 1
    assert items[0]["kind"] == "catalog"
    assert items[0]["row"] == 0
    assert items[0]["path"] == str(source)


@pytest.mark.parametrize("catalog_name", ["PSONG.MNG", "PDISK.MNG"])
def test_apply_button_stages_real_loaded_catalog_metadata(
        window, inspection_dialog, tmp_path, catalog_name):
    from test_catalog_imports import _album

    files = _album(tmp_path)
    originals = {path: path.read_bytes() for path in files.values()}
    window._load_regular_files([str(path) for path in files.values()], "Loaded",
                               prepare_destination=False)
    window.imagePianodirTitleEdit.setText("Previous album")
    catalog_path = str(files[catalog_name])
    catalog_row = next(row for row in range(window.table.rowCount())
                       if window.table.item(row, 1).text() == catalog_path)
    # Exercise the actual menu/context-opening wiring as well as the button;
    # only the audio endpoints are replaced by the inspection fixture.
    window.show_file_inspection_tool(selected_row=catalog_row)
    dialog = window.fileInspectionDialog
    try:
        assert dialog._current_item()["source_path"] == catalog_path
        assert dialog.catalog_view.apply_button.isEnabled()
        dialog.catalog_view.apply_button.click()
        assert dialog._current_item()["source_path"] == catalog_path
        assert dialog.inspection_stack.currentWidget() is dialog.catalog_view
        assert dialog.catalog_view.apply_button.isEnabled()
        if catalog_name == "PSONG.MNG":
            expected = {str(files["FIRST.MID"]): "Second in catalog",
                        str(files["SECOND.MID"]): "First in catalog"}
            assert window.pendingEdits == expected
            assert "Applied 2 song title(s)" in dialog.catalog_view.apply_status.text()
            assert "Use Save" in dialog.catalog_view.apply_status.text()
            inspected_titles = {item["source_path"]: item.get("title") for item in dialog.items}
            assert {path: inspected_titles[path] for path in expected} == expected
            assert window.imagePianodirTitleEdit.text() == "Previous album"
        else:
            assert window.imagePianodirTitleEdit.text() == "An original album"
            assert "Album title applied: An original album" in dialog.catalog_view.apply_status.text()
            assert not window.pendingEdits
        assert "Undo" in dialog.catalog_view.apply_status.text()
        assert all(path.read_bytes() == original for path, original in originals.items())
        window.undo_last_staged_batch()
        dialog._refresh_inspection_items()
        assert not window.pendingEdits
        assert window.imagePianodirTitleEdit.text() == "Previous album"
        assert dialog._current_item()["source_path"] == catalog_path
    finally:
        dialog.close()


def test_catalog_selection_stops_midi_preview_and_switches_back(inspection_dialog, tmp_path):
    import mido
    song = mido.MidiFile()
    song.tracks.append(mido.MidiTrack([
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=480),
    ]))
    midi_path = tmp_path / "SONG.MID"
    song.save(midi_path)
    catalog_path = tmp_path / "PDISK.MNG"
    catalog_path.write_bytes(disk_catalog())
    dialog = inspection_dialog([{"path": str(midi_path)}, {"path": str(catalog_path), "kind": "catalog"}])
    assert dialog.current_midi_bytes.startswith(b"MThd")
    assert dialog.play_button.isEnabled()
    dialog.file_tree.setCurrentItem(dialog.file_tree.topLevelItem(1))
    assert dialog.inspection_stack.currentWidget() is dialog.catalog_view
    assert dialog.current_midi_bytes == b""
    assert not dialog.play_button.isEnabled()
    assert not dialog.render_song_button.isEnabled()
    dialog.file_tree.setCurrentItem(dialog.file_tree.topLevelItem(0))
    assert dialog.inspection_stack.currentWidget() is dialog.midi_inspection_panel
    assert dialog.current_midi_bytes.startswith(b"MThd")
    assert dialog.play_button.isEnabled()


def test_folder_catalogs_are_added_only_when_requested(application, tmp_path):
    midi = tmp_path / "SONG.MID"
    midi.write_bytes(b"MThd")
    (tmp_path / "pdisk.mng").write_bytes(disk_catalog())
    (tmp_path / "PSONG.MNG").write_bytes(song_catalog())
    table = QTableWidget(1, 4)
    table.setItem(0, 1, QTableWidgetItem(str(midi)))
    table.setItem(0, 3, QTableWidgetItem("SONG.MID"))
    window = SimpleNamespace(table=table, _current_song_rows_for_listing=lambda: [0],
                             _song_title_for_row=lambda _row: "Untitled",
                             is_image_mode=lambda: False,
                             _regular_source_material_path=lambda path: path,
                             regularModeContextPath=str(tmp_path))
    window._inspection_catalog_items = lambda items: main_window.MidiTitleWindow._inspection_catalog_items(window, items)
    music = main_window.MidiTitleWindow._inspection_items(window)
    all_items = main_window.MidiTitleWindow._inspection_items(window, include_catalogs=True)
    assert len(music) == 1
    assert len(all_items) == 3
    assert {Path(item["path"]).name for item in all_items[1:]} == {"pdisk.mng", "PSONG.MNG"}


def test_image_catalogs_inspect_staged_psong_and_skip_deleted_catalog(tmp_path):
    original = tmp_path / "original"
    staged = tmp_path / "staged"
    original.write_bytes(song_catalog())
    staged.write_bytes(song_catalog().replace(b"First in catalog", b"Edited title    "))
    window = SimpleNamespace(is_image_mode=lambda: True,
                             imageEntriesByPath={"PSONG.MNG": object(), "PDISK.MNG": object()},
                             pendingImageAdditions={}, pendingImageDeletes={"PDISK.MNG"},
                             pendingSmartPianoSoftCatalogReplacement=str(original),
                             _pending_or_extracted_image_path=lambda _: str(staged),
                             image_session=object())
    items = main_window.MidiTitleWindow._inspection_catalog_items(window, [])
    assert len(items) == 1
    assert items[0]["path"] == str(staged)
    assert read_catalog(items[0]["path"]).songs[0].title == "Edited title"


def test_removing_selected_session_catalog_clears_old_contents(inspection_dialog, tmp_path):
    source = tmp_path / "PDISK.MNG"
    source.write_bytes(disk_catalog())
    items = [{"path": str(source), "kind": "catalog"}]
    dialog = inspection_dialog(items, items_callback=lambda: items)
    assert dialog.catalog_view.inspection is not None
    items.clear()
    dialog._refresh_inspection_items()
    assert dialog.file_tree.topLevelItemCount() == 0
    assert dialog.catalog_view.inspection is None
    assert dialog.catalog_view.tabs.isHidden()
    assert dialog.catalog_view.heading.text() == "Select a file to inspect."


def test_all_song_render_excludes_catalog_items(inspection_dialog, tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal

    source = tmp_path / "PDISK.MNG"
    source.write_bytes(disk_catalog())
    soundfont = tmp_path / "test.sf2"
    soundfont.write_bytes(b"test font placeholder")
    song_item = {"path": str(tmp_path / "song.mid")}
    catalog_item = {"path": str(source), "kind": "catalog"}
    dialog = inspection_dialog([catalog_item, song_item])
    dialog.soundfont_combo.addItem("Test", str(soundfont))
    dialog.soundfont_combo.setCurrentIndex(dialog.soundfont_combo.count() - 1)
    received = []

    class Worker(QObject):
        renderProgress = Signal(int, int, str)
        renderFinished = Signal(object)
        renderFailed = Signal(str)
        finished = Signal()

        def __init__(self, items, *args, **kwargs):
            super().__init__(dialog)
            received.extend(items)

        def start(self):
            pass

    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: "fluidsynth")
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    monkeypatch.setattr(main_window, "BatchAudioRenderWorker", Worker)
    dialog._render_all_inspected_songs("wav")
    assert received == [song_item]
    dialog.inspection_render_worker = None
    dialog._set_inspection_rendering(False)
