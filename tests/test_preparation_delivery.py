"""Public delivery actions must respect the selected preparation profile."""

import io
import os
import zipfile
from pathlib import Path

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA, convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile


def _midi(kind=1, note=60):
    song = mido.MidiFile(type=kind, ticks_per_beat=480)
    conductor = mido.MidiTrack([
        mido.MetaMessage("track_name", name="A descriptive performance title"),
        mido.MetaMessage("time_signature", numerator=4, denominator=4),
        mido.MetaMessage("set_tempo", tempo=600001),
    ])
    notes = mido.MidiTrack([
        mido.Message("note_on", channel=0, note=note, velocity=73, time=127),
        mido.Message("control_change", channel=0, control=64, value=97, time=31),
        mido.Message("note_off", channel=0, note=note, velocity=34, time=480),
        mido.Message("control_change", channel=0, control=64, value=0, time=45),
    ])
    song.tracks = [conductor, notes] if kind else [mido.merge_tracks([conductor, notes])]
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _apply(window, profile_key):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(profile, get_preparation_medium(profile, profile.default_medium))


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "delivery.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_a: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    errors = []
    monkeypatch.setattr(instance, "_show_error_list", lambda *args, **kwargs: errors.append((args, kwargs)))
    instance.delivery_test_errors = errors
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()


def _forbid_dialogs(window, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("An incomplete preparation reached a destination dialog")

    for name in ("getSaveFileName", "getExistingDirectory", "getOpenFileName"):
        monkeypatch.setattr(main_window.QFileDialog, name, unexpected)
    monkeypatch.setattr(window, "_exec_child_dialog", unexpected)
    monkeypatch.setattr(window, "_prompt_for_save_image_options", unexpected)


@pytest.mark.parametrize("save_action,image_mode", (
    ("save_pending_changes", False),
    ("save_image_changes", True),
    ("save_image_as", True),
    ("save_as_image", False),
    ("save_as_zip", False),
    ("save_as_changes", False),
    ("save_to_floppy", True),
))
def test_type2_rejected_by_automatic_preparation_blocks_every_delivery_entrypoint(
    window, monkeypatch, tmp_path, save_action, image_mode,
):
    _apply(window, "pianodisc_228cfx")
    source = tmp_path / "PATTERNS.MID"
    original = _midi(2)
    source.write_bytes(original)
    image_path = None
    if image_mode:
        image_path = tmp_path / "source.img"
        create_floppy_images_from_files(
            [(str(source), source.name)], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
        )
        original_image = image_path.read_bytes()
        session = FloppyImageSession.load(image_path)
        window._on_disk_load_success(session, session.list_entries())
        window._on_disk_load_finished()
    else:
        window._load_regular_files([str(source)], "Imported patterns")
    QTest.qWait(150)
    assert any("format 2" in str(error).lower() for error in window.delivery_test_errors)
    window.delivery_test_errors.clear()
    _forbid_dialogs(window, monkeypatch)
    before_files = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    getattr(window, save_action)()

    assert len(window.delivery_test_errors) == 1
    args, _kwargs = window.delivery_test_errors[0]
    assert args[0] == "Preparation incomplete"
    assert "PDS-228CFX" in args[1]
    assert "Custom" in args[1]
    assert "PATTERNS.MID" in str(args[2])
    assert source.read_bytes() == original
    if image_path is not None:
        assert image_path.read_bytes() == original_image
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before_files


@pytest.mark.parametrize("source_kind", ("midi", "mda"))
def test_undoing_automatic_conversion_blocks_delivery_without_reapplying_it(
    window, monkeypatch, tmp_path, source_kind,
):
    _apply(window, "mark_ii")
    source = tmp_path / ("SOURCE.MID" if source_kind == "midi" else "SOURCE.MDA")
    original = _midi(0)
    if source_kind == "mda":
        original = convert_midi_bytes_to_eseq_bytes(original, container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported")
    QTest.qWait(150)
    assert window.pendingRegularConversions
    window.undo_last_staged_batch()
    assert not window.pendingRegularConversions
    assert window._preparation_profile().key == "mark_ii"
    window.delivery_test_errors.clear()
    _forbid_dialogs(window, monkeypatch)

    window.save_as_changes()

    assert window.delivery_test_errors[0][0][0] == "Preparation incomplete"
    assert not window.pendingRegularConversions
    assert source.read_bytes() == original


def test_immediate_zip_export_flushes_queued_type0_conversion_before_destination_dialog(
    window, monkeypatch, tmp_path,
):
    _apply(window, "pianodisc_128plus")
    sources = [tmp_path / "FIRST.MID", tmp_path / "SECOND.MID"]
    for index, source in enumerate(sources):
        source.write_bytes(_midi(1, note=60 + index))
    window._load_regular_files([str(path) for path in sources], "Imported")
    assert not window.pendingRegularConversions
    output = tmp_path / "ready.zip"
    dialogs = []

    def choose_output(*_args, **_kwargs):
        for source in sources:
            assert mido.MidiFile(window._regular_source_material_path(str(source))).type == 0
        dialogs.append(True)
        return str(output), "ZIP archive (*.zip)"

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", choose_output)
    window.save_as_zip()

    assert dialogs == [True]
    assert not window.delivery_test_errors
    with zipfile.ZipFile(output) as archive:
        files = [name for name in archive.namelist() if name.upper().endswith(".MID")]
        assert len(files) == 2
        for name in files:
            midi = mido.MidiFile(file=io.BytesIO(archive.read(name)))
            assert midi.type == 0
            assert any(message.type == "control_change" and message.value == 97 for message in midi.tracks[0])
    for index, source in enumerate(sources):
        assert source.read_bytes() == _midi(1, note=60 + index)
    QTest.qWait(150)


def test_custom_allows_ordinary_type2_folder_export(window, monkeypatch, tmp_path):
    _apply(window, "pianodisc_228cfx")
    source = tmp_path / "PATTERNS.MID"
    original = _midi(2)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported")
    QTest.qWait(150)
    assert window.delivery_test_errors
    _apply(window, "custom")
    window.delivery_test_errors.clear()
    output = tmp_path / "ordinary-output"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))

    window.save_as_changes()

    assert not window.delivery_test_errors
    assert mido.MidiFile(output / source.name).type == 2
    assert source.read_bytes() == original


def test_title_filename_utility_keeps_active_floppy_profile_names_dos83(window, monkeypatch, tmp_path):
    _apply(window, "pianodisc_228cfx")
    source = tmp_path / "SONG.MID"
    original = _midi(0)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported")
    QTest.qWait(150)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_a, **_k: main_window.QMessageBox.Yes)

    window.create_long_midi_filenames()

    filename = window._regular_row_output_filename(next(iter(window._regular_file_rows())))
    stem, extension = filename.rsplit(".", 1)
    assert 1 <= len(stem) <= 8
    assert 1 <= len(extension) <= 3
    assert " " not in filename
    assert not window.delivery_test_errors
    assert source.read_bytes() == original
