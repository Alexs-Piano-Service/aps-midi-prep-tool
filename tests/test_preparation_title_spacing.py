"""Modern Disklavier and standard MIDI preparation clean up song titles."""

import io
import os

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import (
    CC7_POLICY_PRESERVE,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageSession,
    create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium,
    get_preparation_profile,
)


MODERN_PROFILES = ("midi_export", "e3_850", "enspire")
SPACED_TITLE = "  Moon    River  "


def _midi_bytes(title=SPACED_TITLE):
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    midi.tracks = [mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.MetaMessage("text", text="  Keep   metadata spacing  "),
        mido.MetaMessage("time_signature", numerator=3, denominator=4),
        mido.MetaMessage("set_tempo", tempo=600001),
        mido.Message("program_change", channel=0, program=3),
        mido.Message("control_change", channel=0, control=7, value=91),
        mido.Message("note_on", channel=0, note=60, velocity=73, time=127),
        mido.Message("control_change", channel=0, control=64, value=97, time=31),
        mido.Message("note_off", channel=0, note=60, velocity=34, time=480),
        mido.Message("control_change", channel=0, control=64, value=0, time=45),
    ])]
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _apply(window, profile_key):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, profile.default_medium),
    )


def _without_title(midi):
    return [
        [message for message in track if message.type != "track_name"]
        for track in midi.tracks
    ]


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "title-spacing.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    errors = []
    monkeypatch.setattr(instance, "_show_error_list", lambda *a, **k: errors.append((a, k)))
    instance.title_spacing_test_errors = errors
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("profile_key", MODERN_PROFILES)
@pytest.mark.parametrize("prepare_before_import", (False, True))
@pytest.mark.parametrize("source_kind", ("midi", "eseq"))
def test_preparation_stages_and_exports_clean_title_preserving_other_events(
    window, monkeypatch, tmp_path, profile_key, prepare_before_import, source_kind,
):
    original = _midi_bytes()
    expected_midi = original
    source = tmp_path / ("SOURCE.MID" if source_kind == "midi" else "SOURCE.FIL")
    if source_kind == "eseq":
        original = convert_midi_bytes_to_eseq_bytes(original, cc7_policy=CC7_POLICY_PRESERVE)
        expected_midi = convert_eseq_bytes_to_midi_bytes(original, cc7_policy=CC7_POLICY_PRESERVE)
    source.write_bytes(original)
    if prepare_before_import:
        _apply(window, profile_key)

    window._load_regular_files([str(source)], "Imported song")
    if not prepare_before_import:
        _apply(window, profile_key)
    QTest.qWait(20)

    row = next(iter(window._regular_file_rows()))
    assert window._row_raw_title(row) == "Moon River"
    assert source.read_bytes() == original
    filename = window._regular_row_output_filename(row)
    output = tmp_path / "export"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))

    window.save_as_changes()

    assert not window.title_spacing_test_errors
    exported = mido.MidiFile(output / filename)
    expected = mido.MidiFile(file=io.BytesIO(expected_midi))
    assert [message.name for track in exported.tracks for message in track if message.type == "track_name"] == ["Moon River"]
    assert exported.type == expected.type
    assert exported.ticks_per_beat == expected.ticks_per_beat
    assert _without_title(exported) == _without_title(expected)
    assert source.read_bytes() == original


@pytest.mark.parametrize("profile_key", MODERN_PROFILES)
def test_preparation_title_cleanup_is_undoable(window, tmp_path, profile_key):
    source = tmp_path / "SOURCE.MID"
    original = _midi_bytes()
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported song")

    _apply(window, profile_key)

    assert window.pendingEdits[str(source)] == "Moon River"
    window.undo_last_staged_batch()

    assert not window.pendingEdits
    assert window._row_raw_title(next(iter(window._regular_file_rows()))) == SPACED_TITLE
    assert source.read_bytes() == original


@pytest.mark.parametrize("profile_key", ("custom", "mark_iv", "pianodisc_228cfx"))
def test_other_profiles_keep_title_spacing(window, tmp_path, profile_key):
    source = tmp_path / "SOURCE.MID"
    original = _midi_bytes()
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported song")

    _apply(window, profile_key)
    QTest.qWait(20)

    assert not window.pendingEdits
    assert window._row_raw_title(next(iter(window._regular_file_rows()))) == SPACED_TITLE
    assert source.read_bytes() == original
    assert not window.title_spacing_test_errors


def test_preparation_cleans_all_titles_in_sorted_image_table_and_undo_restores_them(
    window, tmp_path,
):
    originals = {
        "ONE.MID": "  Zulu    Song ",
        "TWO.MID": "Bravo    Song   ",
        "THREE.MID": " Alpha   Song",
    }
    files = []
    for filename, title in originals.items():
        source = tmp_path / filename
        source.write_bytes(_midi_bytes(title))
        files.append((str(source), filename))
    image_path = tmp_path / "songs.img"
    create_floppy_images_from_files(files, str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"])
    original_image = image_path.read_bytes()
    session = FloppyImageSession.load(image_path)
    window._on_disk_load_success(session, session.list_entries())
    window._on_disk_load_finished()
    window.table.setSortingEnabled(True)
    window.table.sortItems(4, Qt.AscendingOrder)

    _apply(window, "enspire")

    assert window.table.isSortingEnabled()
    assert sorted(window.pendingImageTitleEdits.values()) == ["Alpha Song", "Bravo Song", "Zulu Song"]
    assert {filename: window._row_raw_title(row) for row, _, _, filename in window._preparation_song_rows()} == {
        filename: " ".join(title.split()) for filename, title in originals.items()
    }
    assert image_path.read_bytes() == original_image
    window.undo_last_staged_batch()

    assert not window.pendingImageTitleEdits
    assert {filename: window._row_raw_title(row) for row, _, _, filename in window._preparation_song_rows()} == originals
    assert image_path.read_bytes() == original_image
    assert not window.title_spacing_test_errors


@pytest.mark.parametrize("profile_key", MODERN_PROFILES)
def test_preparation_preview_discloses_automatic_title_cleanup(window, profile_key):
    dialog = PreparationProfileDialog(window.settings, profile_key, "usb", window)
    try:
        rows = [
            tuple(dialog.changes_table.item(row, column).text() for column in range(3))
            for row in range(dialog.changes_table.rowCount())
        ]
        assert ("Trim Title Spaces", "Off", "On") in rows
    finally:
        dialog.close()
