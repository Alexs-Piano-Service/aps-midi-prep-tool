"""Exercise translated controls and batch results around real, unchanged MIDI data."""

import io
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mido
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.localized_dialogs import install_qt_translations
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text

LANGUAGES = [language.code for language in SUPPORTED_LANGUAGES]


@pytest.fixture(scope="module")
def application():
    application = QApplication.instance() or QApplication([])
    yield application
    install_qt_translations("en", application)
    application.processEvents()


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "languages.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_warning_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_a, **_k: main_window.QMessageBox.Yes)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    instance.deleteLater()
    application.processEvents()


def _load_song(window, tmp_path):
    midi = mido.MidiFile(type=0)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Save {original} & title"),
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=96),
    ]))
    output = io.BytesIO()
    midi.save(file=output)
    song = tmp_path / "Cancel {original}.mid"
    song.write_bytes(output.getvalue())
    window._load_regular_files([str(song)], "", prepare_destination=False)
    return song, output.getvalue()


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_switch_refreshes_fixed_and_song_tooltips_without_editing_data(window, tmp_path, language):
    song, original = _load_song(window, tmp_path)
    row_before = [window.table.item(0, column).text() for column in range(7)]
    window._set_language("de" if language == "en" else "en")
    window._set_language(language)
    expected = lambda source, **fields: translate_text(source, language, **fields)
    assert window.statusClearButton.toolTip() == expected("Clear status message.")
    assert window.backup_checkbox.toolTip() == expected(
        "Before overwriting, back up images beside the image and individual files into a backup folder."
    )
    assert window.fileImageFloppyAction.toolTip() == expected(
        "Copy a physical floppy to an image file without opening or scanning its contents."
    )
    assert window.table.horizontalHeaderItem(3).toolTip() == expected("Filename on disk. Double-click to rename.")
    assert window.table.item(0, 0).toolTip() == expected("Remove this file from the list.")
    assert window.table.item(0, 2).toolTip() == expected("Copy filename to clipboard.")
    assert window.table.item(0, 3).toolTip() == expected("Double-click to rename this file.")
    assert window.table.item(0, 4).toolTip() == expected("Click to edit this MIDI title.")
    assert window.table.item(0, 5).toolTip() == expected(
        "Title length is within the {limit}-character compatibility limit.", limit=32,
    )
    assert [window.table.item(0, column).text() for column in range(7)] == row_before
    assert window.pendingEdits == {}
    assert window.pendingRegularRenames == {}
    assert song.read_bytes() == original


@pytest.mark.parametrize("language", LANGUAGES)
def test_copy_and_rename_statuses_localize_around_original_filenames(window, tmp_path, language):
    song, original = _load_song(window, tmp_path)
    window._set_language(language)
    window.handle_cell_clicked(0, 2)
    assert window.status_label.text() == translate_text(
        "'{filename}' copied to clipboard.", language, filename=song.name,
    )
    assert QApplication.clipboard().text() == song.name
    window._confirm_with_optional_skip = lambda **_kwargs: True
    window.rename_all_for_disk()
    assert window.status_label.text().splitlines()[0] == translate_text(
        "Staged {staged_count} DOS 8.3 filename change(s).", language, staged_count=1,
    )
    assert song.read_bytes() == original
    window._set_language("de" if language == "en" else "en")
    window._set_language(language)
    assert window.table.item(0, 3).toolTip() == translate_text(
        "Pending filename. Use Save to rename the original file, or Save As to write a renamed copy.", language,
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_instrument_picker_localizes_search_results_and_keeps_program_numbers(application, language):
    combo = main_window.InstrumentComboBox(translate=lambda source: translate_text(source, language))
    try:
        for program, source in enumerate(main_window.GM_PROGRAM_NAMES):
            index = combo.findData(program)
            assert index >= 0
            assert combo.itemText(index) == f"{program + 1}: {translate_text(source, language)}"
        assert combo.lineEdit().placeholderText() == translate_text("Type an instrument name", language)
        index = combo.findData(40)
        combo._select_completion(combo.itemText(index))
        assert combo.currentData() == 40
    finally:
        combo.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
def test_write_protection_text_and_image_banner_localize_without_changing_paths(window, language):
    window.currentLanguage = language
    toggle = window.writeProtectToggle
    toggle.set_target_label("floppy disk")
    for enabled in (False, True):
        toggle.setChecked(enabled)
        source = (
            "Write enabled for this {target}. Save will modify the original." if enabled else
            "Write protected for this {target}. Use Save As or Save As Image instead."
        )
        assert toggle.toolTip() == translate_text(source, language, target=translate_text("floppy disk", language))
    assert toggle.accessibleName() == translate_text("Allow saving to original media", language)
    window.image_session = SimpleNamespace(mode_name="Image Mode")
    try:
        assert window._disk_mode_banner_headline() == translate_text(
            "{mode} ({content})", language, mode=translate_text("Image Mode", language), content="MIDI",
        )
    finally:
        window.image_session = None


def test_switching_language_with_sorted_long_titles_updates_every_row(window, tmp_path):
    originals = {}
    titles = {}
    for index in range(3):
        path = tmp_path / f"{index}.mid"
        title = f"Save {index} " + "original title " * 4
        midi = mido.MidiFile(type=0)
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage("track_name", name=title),
            mido.Message("note_on", note=60, velocity=80),
            mido.Message("note_off", note=60, time=96),
        ]))
        midi.save(path)
        originals[path] = path.read_bytes()
        titles[str(path)] = title
    window._load_regular_files([str(path) for path in originals], "", prepare_destination=False)
    window.table.setSortingEnabled(True)
    window.table.sortItems(5, Qt.AscendingOrder)
    for language in ("ja", "bg", "ko", "en"):
        window._set_language(language)
        assert window.table.isSortingEnabled()
        for row in range(3):
            assert window.table.item(row, 5).text() == translate_text("Long", language).upper()
            assert window.table.item(row, 2).toolTip() == translate_text("Copy filename to clipboard.", language)
            assert window._row_raw_title(row) == titles[window.table.item(row, 1).text()]
    assert {path: path.read_bytes() for path in originals} == originals


def test_instrument_picker_accepts_localized_names_without_numbers(application):
    def translate(source):
        return "Viool" if source == "Violin" else source

    combo = main_window.InstrumentComboBox(translate=translate)
    try:
        combo.lineEdit().setText("Viool")
        combo._commit_typed_text()
        assert combo.currentData() == 40
        combo.setCurrentIndex(0)
        combo.lineEdit().setText("Violin")
        combo._commit_typed_text()
        assert combo.currentData() == 40
    finally:
        combo.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("language", LANGUAGES)
def test_operation_errors_translate_diagnostics_and_keep_original_logs(window, language):
    window.currentLanguage = language
    shown = []
    logs = []
    window._show_reportable_error_message = lambda _icon, _title, message: shown.append(message)
    window._log_error_event = lambda *_args, **fields: logs.append(fields)
    diagnostic = "Invalid MIDI header."
    window._show_operation_error("Conversion Failed", "Conversion Failed", ValueError(diagnostic), guidance="")
    assert translate_text(diagnostic, language) in shown[-1]
    assert logs[-1]["detail"] == diagnostic
    filename = "Save: {original} <song>.mid"
    error = filename + ": " + diagnostic
    window._show_error_list("Conversion Failed", "Conversion Failed", [error])
    assert filename + ": " + translate_text(diagnostic, language) in shown[-1]
    assert logs[-1]["detail"] == error
    if language != "en":
        assert diagnostic not in shown[-1]
