"""The primary image action delivers staged songs, in the visible order."""

import io
import os
from pathlib import Path
from types import SimpleNamespace

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import emulator_image_builder, main_window, prepared_delivery
from aps_midi_prep_tool_app.disk_session_worker import EmulatorImageBuildWorker
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_COUNT_OFFSET, PIANODIR_FILENAME, read_pianodir_metadata_from_file,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.midi_metadata import extract_eseq_title_from_file
from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile


def _song(title, note=60):
    song = mido.MidiFile(type=0)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.Message("program_change", channel=4, program=40),
        mido.Message("note_on", channel=4, note=note, velocity=73),
        mido.Message("note_off", channel=4, note=note, velocity=36, time=480),
    ]))
    stream = io.BytesIO()
    song.save(file=stream)
    return stream.getvalue()


def _title(path):
    return next(message.name for track in mido.MidiFile(path).tracks for message in track
                if message.type == "track_name")


class Worker(QObject):
    finished = Signal()

    def __init__(self, already_finished=False):
        super().__init__()
        self.already_finished = already_finished

    def isFinished(self):
        return self.already_finished


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "prepared.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_a: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    errors = []
    monkeypatch.setattr(instance, "_show_operation_error", lambda *a, **k: errors.append((a, k)))
    instance.delivery_errors = errors
    monkeypatch.setattr(prepared_delivery.QFileDialog, "getExistingDirectory", lambda *_a: str(tmp_path / "output"))
    yield instance
    instance.emulatorImageWorker = None
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()


def _apply(window, profile_key="mark_ii_xg", medium_key="flashfloppy_img"):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(profile, get_preparation_medium(profile, medium_key))


def test_stages_current_conversion_edits_and_order_until_worker_finishes(window, monkeypatch, tmp_path):
    sources = [tmp_path / "Z.FIL", tmp_path / "A.FIL"]
    for index, source in enumerate(sources):
        source.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(source.stem, 60 + index)))
    originals = {path: path.read_bytes() for path in sources}
    # A neighboring file must never enter the image set.
    (tmp_path / "NOT_LOADED.MID").write_bytes(_song("Not loaded", 90))
    window._load_regular_files([str(path) for path in sources], "", prepare_destination=False)
    _apply(window)
    rows = tuple(window._preparation_song_rows())
    first_row, first_path, _, _ = rows[0]
    window.pendingEdits[first_path] = "Edited delivery title"
    window.table.item(first_row, 4).setData(window.TITLE_RAW_ROLE, "Edited delivery title")
    worker = Worker()
    calls = []

    def start(source, output, **options):
        window.emulatorImageWorker = worker
        staged = sorted(Path(source).iterdir())
        calls.append((source, output, options, staged))
        assert len(staged) == 2
        assert _title(staged[0]) == "Edited delivery title"
        assert _title(staged[1]) == window._row_raw_title(rows[1][0])
        for path in staged:
            messages = list(mido.MidiFile(path).tracks[0])
            assert any(message.type == "note_on" and message.channel == 4 for message in messages)

    monkeypatch.setattr(window, "_start_emulator_image_build", start)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_PREFIX, "OLD")
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_STARTING_NUMBER, 55)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_OUTPUT_FORMAT, "hfe")

    assert prepared_delivery.create_prepared_disk_images(window) is worker

    source, output, options, staged = calls[0]
    assert Path(source).is_dir()
    assert options["prefix"] == "DSKA"
    assert options["starting_number"] == 55
    assert options["output_ext"] == "img"
    assert options["output_content"] == "midi"
    assert options["include_subfolders"] is False
    assert options["shuffle"] is False
    assert options["disk_layout"] == "fill"
    assert {path: path.read_bytes() for path in sources} == originals
    assert window.pendingRegularConversions
    worker.finished.emit()
    assert not Path(source).exists()
    assert not window.delivery_errors


def test_image_staging_omits_deleted_songs_and_keeps_pending_title_and_replacement(window, monkeypatch, tmp_path):
    sources = [tmp_path / f"SONG{index}.MID" for index in range(3)]
    for index, source in enumerate(sources):
        source.write_bytes(_song(f"Song {index}", 60 + index))
    image = tmp_path / "source.img"
    create_floppy_images_from_files(
        [(str(path), path.name) for path in sources], str(image), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original = image.read_bytes()
    session = FloppyImageSession.load(image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    _apply(window)
    rows = tuple(window._preparation_song_rows())
    window.pendingImageDeletes.add(rows[0][1])
    window.pendingImageTitleEdits[rows[1][1]] = "Edited on image"
    replacement = Path(session.patched_dir) / "replacement.mid"
    replacement.write_bytes(_song("Replacement", 88))
    window.pendingImageReplacements[rows[2][1]] = str(replacement)
    observed = []

    def start(source, _output, **_options):
        staged = sorted(Path(source).iterdir())
        observed.extend((_title(path), path.read_bytes()) for path in staged)

    monkeypatch.setattr(window, "_start_emulator_image_build", start)
    prepared_delivery.create_prepared_disk_images(window)

    assert [title for title, _data in observed] == ["Edited on image", "Replacement"]
    assert observed[1][1] == replacement.read_bytes()
    assert image.read_bytes() == original
    assert not window.delivery_errors


def test_prepared_eseq_set_splits_with_a_matching_catalog_on_every_disk(window, monkeypatch, tmp_path):
    sources = [tmp_path / name for name in ("Z.FIL", "A.FIL", "M.FIL")]
    for index, path in enumerate(sources):
        path.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(f"Song {index}")))
    original = {path: path.read_bytes() for path in sources}
    window._load_regular_files([str(path) for path in sources], "", prepare_destination=False)
    _apply(window, "mark_ii")
    expected = [window._row_raw_title(row) for row, *_rest in window._preparation_song_rows()]
    monkeypatch.setattr(window, "_current_album_metadata_for_preservation",
                        lambda: SimpleNamespace(disk_title="Prepared album", catalog_number="APS-123"))
    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 2)
    results = []
    staging_paths = []

    def build(source, output, **options):
        staging_paths.append(source)
        assert not (Path(source) / PIANODIR_FILENAME).exists()
        result = emulator_image_builder.build_emulator_disk_images(source, output, **options)
        results.append(result)

    monkeypatch.setattr(window, "_start_emulator_image_build", build)
    prepared_delivery.create_prepared_disk_images(window)

    assert not window.delivery_errors
    result, = results
    assert result.contents_verified
    assert result.converted_files == 0
    assert [Path(path).name for path in result.output_paths] == ["DSKA0000.img", "DSKA0001.img"]
    delivered = []
    counts = []
    for image in result.output_paths:
        session = FloppyImageSession.load(image)
        try:
            songs = [entry.path for entry in session.list_entries().entries if entry.path != PIANODIR_FILENAME]
            counts.append(len(songs))
            catalog_path = session.extract_file(PIANODIR_FILENAME)
            catalog = Path(catalog_path).read_bytes()
            assert int.from_bytes(catalog[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") == len(songs) + 1
            metadata = read_pianodir_metadata_from_file(catalog_path)
            assert metadata.disk_title == "Prepared album"
            assert metadata.catalog_number == "APS-123"
            delivered.extend(extract_eseq_title_from_file(session.extract_file(path)) for path in songs)
        finally:
            session.cleanup()
    assert counts == [2, 1]
    assert delivered == expected
    assert {path: path.read_bytes() for path in sources} == original
    assert not Path(staging_paths[0]).exists()


@pytest.mark.parametrize("outcome", ["no_worker", "startup_failure", "already_finished"])
def test_temporary_staging_is_cleaned_when_start_does_not_leave_a_running_worker(window, monkeypatch, tmp_path, outcome):
    source = tmp_path / "SONG.MID"
    source.write_bytes(_song("Song"))
    window._load_regular_files([str(source)], "", prepare_destination=False)
    _apply(window)
    directories = []

    def start(directory, _output, **_options):
        directories.append(directory)
        if outcome == "startup_failure":
            raise RuntimeError("Cannot start")
        if outcome == "already_finished":
            window.emulatorImageWorker = Worker(already_finished=True)

    monkeypatch.setattr(window, "_start_emulator_image_build", start)
    prepared_delivery.create_prepared_disk_images(window)

    assert len(directories) == 1
    assert not Path(directories[0]).exists()
    assert bool(window.delivery_errors) == (outcome == "startup_failure")


def test_cancelled_output_picker_does_not_stage_or_start(window, monkeypatch, tmp_path):
    source = tmp_path / "SONG.MID"
    source.write_bytes(_song("Song"))
    window._load_regular_files([str(source)], "", prepare_destination=False)
    _apply(window)
    monkeypatch.setattr(prepared_delivery.QFileDialog, "getExistingDirectory", lambda *_a: "")
    monkeypatch.setattr(window, "_write_listed_file_to_path", lambda *_a, **_k: pytest.fail("Cancelled before staging"))
    prepared_delivery.create_prepared_disk_images(window)
    assert window.choose_button.isEnabled()
    assert not window.delivery_errors


@pytest.mark.parametrize("eseq", [False, True])
def test_real_worker_reviews_staged_songs_and_releases_temporary_input(window, monkeypatch, tmp_path, eseq):
    source = tmp_path / ("SONG.FIL" if eseq else "SONG.MID")
    original = _song("Ready for review")
    if eseq:
        original = convert_midi_bytes_to_eseq_bytes(original)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "", prepare_destination=False)
    _apply(window, "mark_ii" if eseq else "mark_ii_xg")
    monkeypatch.setattr(window, "_current_album_metadata_for_preservation",
                        lambda: SimpleNamespace(disk_title="Prepared album", catalog_number="APS-123"))
    reviewed = []
    staging_paths = []

    def run_synchronously(worker):
        staging_paths.append(Path(worker.source_directory))
        assert staging_paths[-1].is_dir()
        worker.run()
        worker.finished.emit()

    def review(preview):
        reviewed.extend(song.title for disk in preview.disks for song in disk.songs)
        window.emulatorImageWorker.resolve_preview_request({"action": "build"})

    monkeypatch.setattr(window, "_on_emulator_preview_requested", review)
    monkeypatch.setattr(EmulatorImageBuildWorker, "start", run_synchronously)
    prepared_delivery.create_prepared_disk_images(window)
    assert window.emulatorImageWorker is None
    assert not staging_paths[0].exists()
    assert reviewed == ["Ready for review"]
    assert window.choose_button.isEnabled()
    session = FloppyImageSession.load(tmp_path / "output" / "DSKA0000.img")
    try:
        songs = [entry for entry in session.list_entries().entries if entry.path != PIANODIR_FILENAME]
        assert len(songs) == 1
        if eseq:
            metadata = read_pianodir_metadata_from_file(session.extract_file(PIANODIR_FILENAME))
            assert metadata.disk_title == "Prepared album"
            assert metadata.catalog_number == "APS-123"
            assert extract_eseq_title_from_file(session.extract_file(songs[0].path)) == "Ready for review"
        else:
            assert Path(session.extract_file(songs[0].path)).read_bytes() == original
    finally:
        session.cleanup()
    assert source.read_bytes() == original
