"""PIANODIR rows retranslate live without translating the user's metadata."""

import io
import os

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_ROW_PATH, PianodirMetadata, PianodirTrackEntry, build_pianodir_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


LANGUAGES = [language.code for language in SUPPORTED_LANGUAGES]
STATES = (
    (True, False, "Present", "{filename} is present and will be left unchanged unless E-SEQ metadata changes."),
    (True, True, "Present - will refresh on save", "{filename} will be refreshed on save because related E-SEQ metadata has changed."),
    (False, False, "Missing - will generate on save", "{filename} will be generated automatically on save."),
    (False, False, "Missing - add E-SEQ files to generate", "{filename} will be generated automatically on save after E-SEQ files are listed."),
)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "languages.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_a: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()


def _load_album(window, tmp_path, image_mode, write_protected):
    midi = mido.MidiFile(type=0)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Present"),
        mido.Message("note_on", note=60, velocity=70),
        mido.Message("note_off", note=60, velocity=0, time=480),
    ]))
    buffer = io.BytesIO()
    midi.save(file=buffer)
    song = tmp_path / "PRESENT.FIL"
    song_bytes = bytearray(convert_midi_bytes_to_eseq_bytes(buffer.getvalue(), title_override="Present"))
    song_bytes[0x4F] = 0x80 if write_protected else 0
    song.write_bytes(song_bytes)
    catalog = tmp_path / "PIANODIR.FIL"
    catalog.write_bytes(build_pianodir_bytes(
        [PianodirTrackEntry(song.name, str(song), "Present")],
        PianodirMetadata(catalog_number="RAW-001", disk_title="Cancel"),
    ))
    paths = [song, catalog]
    if image_mode:
        image_path = tmp_path / "original.img"
        create_floppy_images_from_files(
            [{"host_path": str(path), "image_path": path.name} for path in paths],
            image_path, "img", DISK_FORMAT_BY_KEY["ibm.720"],
        )
        session = FloppyImageSession.load(image_path)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
        paths.append(image_path)
    else:
        window._load_regular_files([str(path) for path in paths], "Test album", prepare_destination=False)
    return {path: path.read_bytes() for path in paths}


def _assert_row(window, language, image_mode, state, state_tooltip):
    row = window._find_pianodir_row()
    assert row >= 0
    cells = [window.table.item(row, column) for column in range(7)]
    assert cells[1].text() == PIANODIR_ROW_PATH
    assert cells[3].text() == "PIANODIR.FIL"
    assert cells[6].text() == "DIR"
    assert cells[4].text() == translate_text(state, language)
    expected_tooltips = {
        0: "{filename} is maintained automatically.",
        2: "{filename} is maintained automatically.",
        3: "Directory file for Yamaha E-SEQ disks." if image_mode else "Directory file for Yamaha E-SEQ folders.",
        4: state_tooltip,
        5: "Not applicable.",
        6: "Special Yamaha E-SEQ directory file.",
    }
    for column, source in expected_tooltips.items():
        expected = translate_text(source, language, filename="PIANODIR.FIL")
        assert cells[column].toolTip() == expected
        if language != "en":
            assert expected != source.format(filename="PIANODIR.FIL")
    if language != "en":
        assert cells[4].text() != state
    # These values intentionally match English UI words: they are user data.
    assert window.imagePianodirTitleEdit.text() == "Cancel"
    assert window.imagePianodirCatalogEdit.text() == "RAW-001"
    for song_row in range(window.table.rowCount()):
        if not window._is_special_pianodir_row(song_row):
            assert window.table.item(song_row, 3).text() == "PRESENT.FIL"
            assert window._row_raw_title(song_row) == "Present"
            kind_item = window.table.item(song_row, 6)
            assert kind_item.text() == "FIL (Solo)"
            tooltip = translate_text("Yamaha E-SEQ type and arrangement information.", language)
            if not image_mode:
                tooltip += " " + translate_text("Double-click to inspect this song.", language)
            assert kind_item.toolTip() == tooltip


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_pianodir_states_retranslate_on_language_change_and_preserve_metadata(window, tmp_path, language, image_mode):
    # Exercise protected and unprotected songs in both folder and image views.
    originals = _load_album(window, tmp_path, image_mode, write_protected=LANGUAGES.index(language) % 2 == 0)
    for state_index, (present, refresh, state, tooltip) in enumerate(STATES):
        if image_mode:
            window.imageHasPianodir = present
            window.imagePianodirPopulated = present
            window.imageEseqMode = True
        else:
            window.regularHasPianodir = present
            window.regularPianodirPopulated = present
            window.regularEseqMode = True
        window.pendingGeneratePianodir = refresh
        if state_index == 3:
            # An empty destination has no E-SEQ song from which to generate a
            # catalog; keep the special row to exercise its missing state.
            for row in reversed(range(window.table.rowCount())):
                if not window._is_special_pianodir_row(row):
                    window.table.removeRow(row)
        window._set_language("de" if language == "en" else "en")
        window._set_language(language)
        _assert_row(window, language, image_mode, state, tooltip)
    assert {path: path.read_bytes() for path in originals} == originals
