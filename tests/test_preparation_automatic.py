"""Destination preparation reached through normal import callbacks and timers."""

import io
import os
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
from aps_midi_prep_tool_app.eseq_pianodir import is_clavinova_mda_file
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profiles import (
    SETTING_MEDIUM, SETTING_PROFILE, get_preparation_medium,
    get_preparation_profile, proposed_settings,
)


def _song(midi_type=1, note=60):
    song = mido.MidiFile(type=midi_type, ticks_per_beat=480)
    conductor = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Automatic preparation"),
        mido.MetaMessage("time_signature", numerator=4, denominator=4),
        mido.MetaMessage("set_tempo", tempo=600001),
        mido.MetaMessage("text", text="Preserve this metadata"),
        mido.MetaMessage("set_tempo", tempo=500003, time=240),
    ])
    notes = mido.MidiTrack([
        mido.Message("program_change", channel=5, program=24),
        mido.Message("note_on", channel=5, note=note, velocity=83, time=127),
        mido.Message("control_change", channel=5, control=66, value=98, time=77),
        mido.Message("note_off", channel=5, note=note, velocity=33, time=480),
        mido.Message("control_change", channel=5, control=66, value=0, time=11),
    ])
    song.tracks = [conductor, notes] if midi_type else [mido.merge_tracks([conductor, notes])]
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _events(data):
    song = mido.MidiFile(file=io.BytesIO(data))
    tick = 0
    result = []
    for message in mido.merge_tracks(song.tracks):
        tick += message.time
        if message.type != "end_of_track":
            result.append((tick, message.copy(time=0).dict()))
    return result


def _settle_import():
    # A disk-load callback can queue preparation while its controls are busy;
    # allow its normal 100 ms retry as well as immediate import callbacks.
    QTest.qWait(150)


@pytest.fixture
def destination_window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    windows = []
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main_window.QMessageBox, "question",
        lambda *_a, **_k: pytest.fail("Import must not ask for a conversion confirmation"),
    )

    def create(profile_key, *, persisted=False):
        settings = QSettings(str(tmp_path / f"settings-{len(windows)}.ini"), QSettings.IniFormat)
        profile = get_preparation_profile(profile_key)
        medium = get_preparation_medium(profile, profile.default_medium)
        if persisted:
            settings.setValue(SETTING_PROFILE, profile.key)
            settings.setValue(SETTING_MEDIUM, medium.key)
            for key, value in proposed_settings(profile, medium).items():
                settings.setValue(key, value)
            settings.sync()
        monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
        window = main_window.MidiTitleWindow()
        windows.append(window)
        monkeypatch.setattr(window, "_show_error_list", lambda *args, **_k: pytest.fail(str(args)))
        if not persisted:
            window._apply_preparation_profile(profile, medium)
        _settle_import()
        return window

    yield create
    for window in windows:
        window._clear_staging_history()
        window._cleanup_midi_scratch_dir()
        if window.image_session is not None:
            window.image_session.cleanup()
        window.deleteLater()
    app.processEvents()


def _browse(window, directory, monkeypatch):
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(directory))
    window.browse_directory()
    _settle_import()


def _open_image(window, image_path):
    session = FloppyImageSession.load(image_path)
    window._set_disk_load_busy(True)
    window._on_disk_load_success(session, session.list_entries())
    window._on_disk_load_finished()
    _settle_import()


def _make_image(tmp_path, files):
    image_path = tmp_path / "source.img"
    create_floppy_images_from_files(
        [(str(path), path.name) for path in files], str(image_path), "img",
        DISK_FORMAT_BY_KEY["ibm.720"],
    )
    return image_path


def _assert_type0(data, original):
    assert mido.MidiFile(file=io.BytesIO(data)).type == 0
    assert _events(data) == _events(original)


def _assert_dos83(filename):
    stem, extension = filename.rsplit(".", 1)
    assert 1 <= len(stem) <= 8
    assert 1 <= len(extension) <= 3
    assert " " not in filename


@pytest.mark.parametrize("persisted", (False, True))
def test_future_folder_import_prepares_every_required_type0_file(
    destination_window, monkeypatch, tmp_path, persisted,
):
    window = destination_window("pianodisc_128plus", persisted=persisted)
    folder = tmp_path / "music"
    folder.mkdir()
    originals = {}
    for index, name in enumerate(("long first performance.mid", "long second performance.midi", "ALREADY.MID")):
        path = folder / name
        originals[path] = _song(0 if index == 2 else 1, note=60 + index)
        path.write_bytes(originals[path])
    _browse(window, folder, monkeypatch)

    assert window._regular_file_count() == 3
    filenames = []
    for row in window._regular_file_rows():
        source = Path(window.table.item(row, 1).text())
        material = Path(window._regular_source_material_path(str(source)))
        _assert_type0(material.read_bytes(), originals[source])
        filenames.append(window._regular_row_output_filename(row))
        _assert_dos83(filenames[-1])
        assert source.read_bytes() == originals[source]
    assert len(set(name.upper() for name in filenames)) == 3
    assert str(folder / "ALREADY.MID") not in window.pendingRegularConversions


def test_future_regular_drop_prepares_all_type1_files(destination_window, tmp_path):
    window = destination_window("pianodisc_228cfx")
    paths = [tmp_path / "Dropped first performance.mid", tmp_path / "Dropped second performance.mid"]
    for index, path in enumerate(paths):
        path.write_bytes(_song(note=65 + index))
    window.prepare_regular_file_drop([str(path) for path in paths])
    results = [window.add_regular_file_from_drop(str(path)) for path in paths]
    window.finish_regular_file_drop(results)
    _settle_import()

    assert window._regular_file_count() == 2
    for row in window._regular_file_rows():
        path = window.table.item(row, 1).text()
        _assert_type0(Path(window._regular_source_material_path(path)).read_bytes(), Path(path).read_bytes())
        _assert_dos83(window._regular_row_output_filename(row))


def test_future_image_open_prepares_every_type1_file(destination_window, tmp_path):
    window = destination_window("pianodisc_228cfx", persisted=True)
    paths = [tmp_path / name for name in ("FIRST.MID", "SECOND.MID", "ALREADY.MID")]
    for index, path in enumerate(paths):
        path.write_bytes(_song(0 if index == 2 else 1, note=60 + index))
    image_path = _make_image(tmp_path, paths)
    original_image = image_path.read_bytes()
    _open_image(window, image_path)

    assert len(tuple(window._preparation_song_rows())) == 3
    for path in paths:
        material = window._pending_or_extracted_image_path(path.name)
        _assert_type0(Path(material).read_bytes(), path.read_bytes())
    assert "ALREADY.MID" not in window.pendingImageReplacements
    assert image_path.read_bytes() == original_image


@pytest.mark.parametrize("replace", (False, True), ids=("additions", "replacement"))
def test_future_image_drop_prepares_type1_additions_and_replacements(
    destination_window, monkeypatch, tmp_path, replace,
):
    window = destination_window("pianodisc_228cfx")
    existing = tmp_path / "EXISTING.MID"
    existing.write_bytes(_song(0))
    image_path = _make_image(tmp_path, [existing])
    original_image = image_path.read_bytes()
    _open_image(window, image_path)
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    names = ("EXISTING.MID",) if replace else ("New first performance.mid", "New second performance.mid")
    paths = [incoming / name for name in names]
    for index, path in enumerate(paths):
        path.write_bytes(_song(note=70 + index))
    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", lambda **_k: ("replace", True))
    window.queue_image_additions([str(path) for path in paths])
    _settle_import()

    staged = window.pendingImageReplacements if replace else window.pendingImageAdditions
    assert len(staged) == len(paths)
    expected = {70 + index: path.read_bytes() for index, path in enumerate(paths)}
    for image_name, material in staged.items():
        data = Path(material).read_bytes()
        song = mido.MidiFile(file=io.BytesIO(data))
        assert song.type == 0, f"{image_name} remained Type {song.type} after the drop"
        note = next(message.note for message in mido.merge_tracks(song.tracks) if message.type == "note_on")
        _assert_type0(data, expected[note])
        _assert_dos83(image_name)
    assert image_path.read_bytes() == original_image

    # Exercise the real Save As Image pipeline and independently reopen its
    # files, so a correct temporary conversion cannot hide an export omission.
    output = tmp_path / "prepared.img"
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_k: (
        str(output), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    ))
    monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *_a, **_k: None)
    window.save_image_as()
    _settle_import()
    saved = FloppyImageSession.load(output)
    try:
        actual = []
        for entry in saved.list_entries().entries:
            if entry.path.upper().endswith(".MID"):
                song = mido.MidiFile(saved.extract_file(entry.path))
                assert song.type == 0
                actual.extend(message.note for message in song.tracks[0] if message.type == "note_on")
        assert sorted(actual) == ([70] if replace else [60, 70, 71])
    finally:
        saved.cleanup()
    assert not window.pendingImageAdditions
    assert not window.pendingImageReplacements
    assert image_path.read_bytes() == original_image


def test_future_eseq_folder_import_becomes_midi_without_saving_sources(
    destination_window, monkeypatch, tmp_path,
):
    window = destination_window("pianodisc_228cfx")
    folder = tmp_path / "eseq"
    folder.mkdir()
    source = folder / "SOURCE.FIL"
    original = convert_midi_bytes_to_eseq_bytes(_song(0), title_override="Imported ESEQ")
    source.write_bytes(original)
    _browse(window, folder, monkeypatch)

    assert window._regular_file_count() == 1
    conversion = window.pendingRegularConversions[str(source)]
    midi = mido.MidiFile(conversion["temp_path"])
    assert midi.type == 0
    assert sum(message.type == "note_on" and message.velocity > 0 for message in midi.tracks[0]) == 1
    assert window._regular_row_output_filename(next(iter(window._regular_file_rows()))).upper().endswith(".MID")
    assert source.read_bytes() == original
    assert not list(folder.glob("*.MID"))


def test_future_mark_ii_image_addition_normalizes_clavinova_container(destination_window, tmp_path):
    window = destination_window("mark_ii")
    session = FloppyImageSession.create_blank_session(DISK_FORMAT_BY_KEY["ibm.720"], eseq_disk=False)
    window._on_disk_load_success(session, session.list_entries())
    window._on_disk_load_finished()
    _settle_import()
    source = tmp_path / "Clavinova performance.MDA"
    original = convert_midi_bytes_to_eseq_bytes(
        _song(0), container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA,
    )
    source.write_bytes(original)
    window.queue_image_additions([str(source)])
    _settle_import()

    rows = tuple(window._preparation_song_rows())
    assert len(rows) == 1
    _row, image_path, kind, filename = rows[0]
    assert kind == "eseq"
    assert filename.upper().endswith(".FIL")
    _assert_dos83(filename)
    assert not is_clavinova_mda_file(window._pending_or_extracted_image_path(image_path))
    assert window.pendingGeneratePianodir
    assert window._eseq_directory_filename() == "PIANODIR.FIL"
    assert source.read_bytes() == original


@pytest.mark.parametrize("profile_key", ("custom", "qrs_chili", "pianodisc_prodigy"))
def test_future_import_does_not_force_type0_without_a_type0_requirement(
    destination_window, monkeypatch, tmp_path, profile_key,
):
    window = destination_window(profile_key, persisted=True)
    folder = tmp_path / "music"
    folder.mkdir()
    source = folder / "SONG.MID"
    original = _song()
    source.write_bytes(original)
    _browse(window, folder, monkeypatch)

    assert window._regular_file_count() == 1
    assert not window.pendingRegularConversions
    assert mido.MidiFile(window._regular_source_material_path(str(source))).type == 1
    assert source.read_bytes() == original
