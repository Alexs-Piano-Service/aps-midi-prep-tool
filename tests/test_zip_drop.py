"""ZIP drops use the same import and save workflows as individual songs."""

import io
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QSettings, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from aps_midi_prep_tool_app import drop_table_widget, floppy_image, main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _archive(tmp_path, name, entries):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, data in entries.items():
            archive.writestr(filename, data)
    return path


def _drop(table, *paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    accepted = []
    table.dropEvent(SimpleNamespace(
        mimeData=lambda: mime, acceptProposedAction=lambda: accepted.append(True),
    ))
    assert accepted == [True]


def _midi_bytes(title):
    midi = mido.MidiFile(type=0)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=96),
    ]))
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "zip.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_a: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_warning_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_schedule_destination_preparation", lambda *_a: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_a, **_k: main_window.QMessageBox.Yes)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.table.cleanup_zip_imports()
    instance.deleteLater()
    application.processEvents()


def test_zip_drop_imports_individual_songs_and_save_exports_persistent_copies(window, tmp_path, monkeypatch):
    first, second, third = (_midi_bytes(title) for title in ("First", "Second", "Third"))
    archive = _archive(tmp_path, "Songs.ZIP", {
        "album/first.mid": first,
        "album/notes.txt": "Companion metadata",
        "album/disc2/second.mid": second,
    })
    original_zip = archive.read_bytes()
    direct = tmp_path / "third.mid"
    direct.write_bytes(third)
    _drop(window.table, archive, direct)

    rows = window._regular_file_rows()
    assert len(rows) == 3
    paths = {Path(window.table.item(row, 1).text()) for row in rows}
    extracted = paths - {direct}
    extraction_roots = [Path(directory.name) for directory, _source in window.table._zip_imports]
    assert {path.name for path in extracted} == {"first.mid", "second.mid"}
    assert {path.read_bytes() for path in extracted} == {first, second}
    assert all(window.table.zip_source_for_path(path) == str(archive) for path in extracted)
    assert window.regularModeContextPath == str(tmp_path)
    window._cleanup_midi_scratch_dir()
    assert all(path.is_file() for path in extracted)

    # Cancelling Save As must leave the imported files available for another try.
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: "")
    window.save_pending_changes()
    assert window._regular_file_count() == 3
    assert all(path.is_file() for path in extracted)

    destination = tmp_path / "saved"
    destination.mkdir()
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(destination))
    window.save_pending_changes()
    assert {path.name for path in destination.glob("*.mid")} == {"first.mid", "second.mid", "third.mid"}
    assert {Path(window.table.item(row, 1).text()).parent for row in window._regular_file_rows()} == {destination}
    assert all(not root.exists() for root in extraction_roots)
    assert window.table._zip_imports == []
    assert window.table._zip_import_usage == {}
    assert all(not path.exists() for path in extracted)
    assert all(path.is_file() for path in destination.glob("*.mid"))
    assert archive.read_bytes() == original_zip
    assert direct.read_bytes() == third


@pytest.mark.parametrize("replacement", ["clear", "loose_files"])
def test_replacing_zip_list_automatically_removes_extraction_tree(window, tmp_path, replacement):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("  Original  ")})
    _drop(window.table, archive)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    window.trim_title_spaces_for_all(show_summary=False)
    assert window._staged_undo_stack

    if replacement == "clear":
        window.clear_list()
        assert window._regular_file_count() == 0
    else:
        durable = tmp_path / "durable.mid"
        durable.write_bytes(_midi_bytes("Replacement"))
        window._load_regular_files([str(durable)], "Replacement", prepare_destination=False)
        assert window.table.item(window._regular_file_rows()[0], 1).text() == str(durable)

    assert not extraction_root.exists()
    assert window.table._zip_imports == []
    assert window.table._zip_import_usage == {}
    assert not window._staged_undo_stack
    assert archive.is_file()


def test_reloading_files_from_zip_keeps_incoming_sources_alive(window, tmp_path):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Reload")})
    _drop(window.table, archive)
    source = Path(window.table.item(window._regular_file_rows()[0], 1).text())

    window._load_regular_files([str(source)], "Reload archive sources", prepare_destination=False)

    assert window._regular_file_count() == 1
    assert source.read_bytes() == _midi_bytes("Reload")
    assert window.table.zip_source_for_path(source) == str(archive)


def test_failed_zip_save_keeps_sources_for_retry(window, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Retry")})
    _drop(window.table, archive)
    source = Path(window.table.item(window._regular_file_rows()[0], 1).text())
    destination = tmp_path / "failed-export"
    errors = []
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(destination))
    monkeypatch.setattr(window, "_write_listed_file_to_path", lambda *_a, **_k: "Destination unavailable")
    monkeypatch.setattr(window, "_show_error_list", lambda *_a, **_k: errors.append(True))

    window.save_pending_changes()

    assert errors
    assert Path(window.table.item(window._regular_file_rows()[0], 1).text()) == source
    assert source.read_bytes() == _midi_bytes("Retry")
    assert window.table.zip_source_for_path(source) == str(archive)


def test_skipping_zip_duplicate_automatically_removes_unused_archive(window, tmp_path, monkeypatch):
    direct = tmp_path / "song.mid"
    direct.write_bytes(_midi_bytes("Existing"))
    _drop(window.table, direct)
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Skipped")})
    extraction_roots = []
    real_extract = drop_table_widget.extract_zip

    def extract(path, destination, **kwargs):
        extraction_roots.append(Path(destination))
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", extract)
    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", lambda **_kwargs: ("keep", False))
    _drop(window.table, archive)

    assert window._regular_file_count() == 1
    assert window.table.item(window._regular_file_rows()[0], 1).text() == str(direct)
    assert extraction_roots and all(not root.exists() for root in extraction_roots)
    assert window.table._zip_imports == []


@pytest.mark.parametrize("completed_first", [False, True])
def test_cancelled_zip_batch_collects_only_archives_without_added_files(
    window, tmp_path, monkeypatch, completed_first,
):
    existing = tmp_path / "existing.mid"
    existing.write_bytes(_midi_bytes("Existing"))
    _drop(window.table, existing)
    completed = _archive(tmp_path, "completed.zip", {"first.mid": _midi_bytes("First")})
    cancelled = _archive(tmp_path, "cancelled.zip", {"existing.mid": _midi_bytes("Cancel")})
    unused = _archive(tmp_path, "unused.zip", {"unused.mid": _midi_bytes("Never added")})
    roots = {}
    real_extract = drop_table_widget.extract_zip

    def extract(path, destination, **kwargs):
        roots[Path(path)] = Path(destination)
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", extract)
    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", lambda **_kwargs: ("cancel", False))
    _drop(window.table, *([completed] if completed_first else []), cancelled, unused)

    assert window._regular_file_count() == (2 if completed_first else 1)
    assert not roots[cancelled].exists()
    assert not roots[unused].exists()
    if completed_first:
        assert (roots[completed] / "first.mid").read_bytes() == _midi_bytes("First")
        assert [source for _directory, source in window.table._zip_imports] == [str(completed)]
    else:
        assert window.table._zip_imports == []


@pytest.mark.parametrize("finish", ["undo_last", "undo_all", "clear_history"])
def test_zip_sources_needed_by_undo_survive_row_replacement(window, tmp_path, monkeypatch, finish):
    old_data = _midi_bytes("  Original  ")
    old_archive = _archive(tmp_path, "original.zip", {"song.mid": old_data})
    new_archive = _archive(tmp_path, "replacement.zip", {"song.mid": _midi_bytes("Replacement")})
    _drop(window.table, old_archive)
    old_source = Path(window.table.item(window._regular_file_rows()[0], 1).text())
    window.trim_title_spaces_for_all(show_summary=False)
    assert window._staged_undo_stack
    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", lambda **_kwargs: ("replace", False))

    _drop(window.table, new_archive)

    new_source = Path(window.table.item(window._regular_file_rows()[0], 1).text())
    assert new_source != old_source
    assert old_source.read_bytes() == old_data
    if finish == "clear_history":
        window._clear_staging_history()
        assert new_source.is_file()
        assert not old_source.parent.exists()
    else:
        if finish == "undo_last":
            window.undo_last_staged_batch()
        else:
            window.undo_all_staged_changes()
        assert Path(window.table.item(window._regular_file_rows()[0], 1).text()) == old_source
        assert old_source.read_bytes() == old_data
        assert not new_source.parent.exists()
    assert len(window.table._zip_imports) == 1
    assert len(window.table._zip_import_usage) == 1


@pytest.mark.parametrize("reference_kind", ["conversion", "addition", "replacement"])
def test_zip_collector_preserves_pending_materials_without_source_rows(window, tmp_path, reference_kind):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Pending material")})
    _drop(window.table, archive)
    source = window.table.item(window._regular_file_rows()[0], 1).text()
    extraction_root = Path(window.table._zip_imports[0][0].name)
    durable = tmp_path / "durable.mid"
    durable.write_bytes(_midi_bytes("Durable source"))
    _drop(window.table, durable)
    if reference_kind == "conversion":
        pending = window.pendingRegularConversions
        pending[str(durable)] = {"temp_path": source, "target_kind": "midi_type0"}
    else:
        pending = (window.pendingImageAdditions if reference_kind == "addition"
                   else window.pendingImageReplacements)
        pending["SONG.MID"] = source
    window._remove_regular_row_for_path(source)

    window.table.collect_unused_zip_imports()

    assert Path(source).read_bytes() == _midi_bytes("Pending material")
    pending.clear()
    window.table.collect_unused_zip_imports()
    assert not extraction_root.exists()


def test_zip_image_additions_release_extraction_after_copying_to_session(window, tmp_path, monkeypatch):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    session = floppy_image.FloppyImageSession.create_blank_session(disk_format)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Image addition")})
    extraction_roots = []
    real_extract = drop_table_widget.extract_zip

    def extract(path, destination, **kwargs):
        extraction_roots.append(Path(destination))
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", extract)
    _drop(window.table, archive)

    assert len(window.pendingImageAdditions) == 1
    staged = Path(next(iter(window.pendingImageAdditions.values())))
    assert staged.read_bytes() == _midi_bytes("Image addition")
    assert extraction_roots and all(not root.exists() for root in extraction_roots)
    assert window.table._zip_imports == []


@pytest.mark.parametrize("release_action", ["clear", "save"])
def test_zip_image_source_survives_loading_and_is_collected_after_release(
    window, tmp_path, monkeypatch, release_action,
):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    original = tmp_path / "blank.img"
    floppy_image.create_blank_floppy_image(original, disk_format)
    archive = _archive(tmp_path, "disk.zip", {"disk.img": original.read_bytes()})

    def start_load(**kwargs):
        # Hold the asynchronous worker before it opens the extracted source.
        window.diskLoadWorker = SimpleNamespace(source=kwargs["source"])
        window.diskLoadContext = {"load_kind": "image", "source": kwargs["source"]}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_load)
    _drop(window.table, archive)
    source = Path(window.diskLoadWorker.source)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    assert source.read_bytes() == original.read_bytes()

    session = floppy_image.FloppyImageSession.load(source)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    window.diskLoadWorker = None
    window.diskLoadContext = {}
    window.table.collect_unused_zip_imports()
    assert source.is_file()

    if release_action == "save":
        destination = tmp_path / "saved.img"
        monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
            str(destination), "img", disk_format,
        ))
        window.save_image_changes()
        assert Path(window.image_session.source_path) == destination
        assert destination.is_file()
    else:
        window.clear_list()
        assert window.image_session is None

    assert not extraction_root.exists()
    assert window.table._zip_imports == []
    assert archive.is_file()


def test_cancelled_zip_image_load_releases_extraction_when_worker_finishes(window, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "disk.zip", {"disk.img": b"unfinished image load"})

    def start_load(**kwargs):
        window.diskLoadWorker = SimpleNamespace(source=kwargs["source"], deleteLater=lambda: None)
        window.diskLoadContext = {"load_kind": "image", "source": kwargs["source"]}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_load)
    _drop(window.table, archive)
    source = Path(window.diskLoadWorker.source)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    assert source.is_file()

    window._on_disk_load_cancelled("Cancelled")
    assert source.is_file()
    window._on_disk_load_finished()

    assert window.image_session is None
    assert not extraction_root.exists()
    assert window.table._zip_imports == []


@pytest.mark.parametrize("extension", ["scp", "hfe"])
def test_zip_conversion_failure_keeps_source_until_queued_retry_prompt_finishes(
    window, tmp_path, monkeypatch, extension,
):
    archive = _archive(tmp_path, "capture.zip", {f"capture.{extension}": b"retry capture"})

    def start_load(**kwargs):
        window.diskLoadWorker = SimpleNamespace(source=kwargs["source"], deleteLater=lambda: None)
        window.diskLoadContext = {"load_kind": "image", "source": kwargs["source"]}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_load)
    _drop(window.table, archive)
    source = Path(window.diskLoadWorker.source)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    queued, prompted = [], []
    monkeypatch.setattr(main_window.QTimer, "singleShot", lambda _delay, callback: queued.append(callback))
    monkeypatch.setattr(window, "_show_operation_error", lambda *_a, **_k: pytest.fail("Retry source was removed"))

    def decline_retry(details):
        window.table.collect_unused_zip_imports()
        assert Path(details["capture_path"]).read_bytes() == b"retry capture"
        prompted.append(True)
        return None

    monkeypatch.setattr(window, "_choose_greaseweazle_retry_format", decline_retry)
    window.pendingGwConversionDetails = {
        "capture_path": str(source), "message": "Try a different disk format",
    }
    window._on_disk_load_finished()

    assert window.diskLoadWorker is None
    assert len(queued) == 1
    window.table.collect_unused_zip_imports()
    assert source.read_bytes() == b"retry capture"
    queued.pop()()

    assert prompted == [True]
    assert str(source) not in window.status_label.text()
    assert not extraction_root.exists()
    assert window.table._zip_imports == []


def test_zip_retry_image_load_source_keeps_archive_alive_until_worker_finishes(window, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "capture.zip", {"capture.scp": b"retry capture"})
    typed_sources = []

    def start_retry(**kwargs):
        source = floppy_image.ImageLoadSource(kwargs["source"], floppy_image.DISK_FORMAT_BY_KEY["ibm.720"])
        typed_sources.append(source)
        window.diskLoadWorker = SimpleNamespace(source=source, deleteLater=lambda: None)
        window.diskLoadContext = {"load_kind": "image", "source": source}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_retry)
    _drop(window.table, archive)
    assert len(typed_sources) == 1
    source = Path(typed_sources[0].path)
    window.table.collect_unused_zip_imports()
    assert source.read_bytes() == b"retry capture"
    extraction_root = Path(window.table._zip_imports[0][0].name)

    window._on_disk_load_cancelled("Cancelled")
    window._on_disk_load_finished()

    assert not extraction_root.exists()
    assert window.table._zip_imports == []


def test_zip_source_survives_worker_finish_inside_success_dialog(window, tmp_path, monkeypatch):
    original = tmp_path / "blank.img"
    floppy_image.create_blank_floppy_image(original, floppy_image.DISK_FORMAT_BY_KEY["ibm.720"])
    archive = _archive(tmp_path, "disk.zip", {"disk.img": original.read_bytes()})

    def start_load(**kwargs):
        window.diskLoadWorker = SimpleNamespace(source=kwargs["source"], deleteLater=lambda: None)
        window.diskLoadContext = {"load_kind": "image", "source": kwargs["source"]}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_load)
    _drop(window.table, archive)
    source = Path(window.diskLoadWorker.source)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    session = floppy_image.FloppyImageSession.load(source)
    reported = []

    def report_sectors(_reports):
        # The finished signal can arrive while a modal report runs its event loop.
        window._on_disk_load_finished()
        window.table.collect_unused_zip_imports()
        assert source.is_file()
        reported.append(True)

    monkeypatch.setattr(window, "_show_greaseweazle_sector_reports", report_sectors)
    monkeypatch.setattr(window, "_offer_post_load_sequence_conversions", lambda: None)
    window._on_disk_load_success(session, session.list_entries())

    assert reported == [True]
    assert window.image_session is session
    assert source.is_file()
    window.clear_list()
    assert not extraction_root.exists()


def test_zip_source_survives_worker_finish_inside_failure_conversion_prompt(window, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "disk.zip", {"disk.img": b"convertible sequence image"})

    def start_load(**kwargs):
        window.diskLoadWorker = SimpleNamespace(source=kwargs["source"], deleteLater=lambda: None)
        window.diskLoadContext = {"load_kind": "image", "source": kwargs["source"]}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_load)
    monkeypatch.setattr(window, "_log_error_event", lambda *_a, **_k: None)
    _drop(window.table, archive)
    source = Path(window.diskLoadWorker.source)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    converted = []

    def choose_conversion(_summary):
        window._on_disk_load_finished()
        window.table.collect_unused_zip_imports()
        assert source.is_file()
        return True

    def convert(path, *_args, **_kwargs):
        assert Path(path).read_bytes() == b"convertible sequence image"
        converted.append(path)

    monkeypatch.setattr(window, "_v50_nseq_sequence_summary_for_image", lambda _path: {"slot_count": 1})
    monkeypatch.setattr(window, "_prompt_for_v50_nseq_conversion", choose_conversion)
    monkeypatch.setattr(window, "_convert_v50_nseq_image_to_midi_mode", convert)
    window._on_disk_load_failure("Unsupported filesystem")

    assert converted == [str(source)]
    assert not extraction_root.exists()
    assert window.table._zip_imports == []


def test_zip_non_fat_export_retains_source_until_capture_worker_finishes(window, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "capture.zip", {"capture.scp": b"non-FAT capture"})

    def start_load(**kwargs):
        window.diskLoadWorker = SimpleNamespace(source=kwargs["source"], deleteLater=lambda: None)
        window.diskLoadContext = {"load_kind": "image", "source": kwargs["source"]}
        return True

    monkeypatch.setattr(window, "_start_disk_load_worker", start_load)
    _drop(window.table, archive)
    source = Path(window.diskLoadWorker.source)
    extraction_root = Path(window.table._zip_imports[0][0].name)
    destination = tmp_path / "converted.img"
    queued, captures = [], []
    monkeypatch.setattr(main_window.QTimer, "singleShot", lambda _delay, callback: queued.append(callback))
    monkeypatch.setattr(window, "_exec_child_dialog", lambda dialog: dialog.defaultButton().click())

    def choose_destination(_parent, _title, default_path, *_args):
        assert Path(default_path).parent == tmp_path
        return str(destination), ""

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", choose_destination)

    def start_capture(source_kind, capture_source, output_path, **_kwargs):
        captures.append((source_kind, capture_source, output_path))
        window.diskImageCaptureWorker = SimpleNamespace(source=capture_source, deleteLater=lambda: None)
        window.diskImageCaptureContext = {"source_kind": source_kind, "output_path": output_path}

    monkeypatch.setattr(window, "_start_floppy_image_capture_worker", start_capture)
    window.pendingGwConversionDetails = {
        "reason": "non_fat_format", "capture_path": str(source),
        "disk_format": floppy_image.DISK_FORMAT_BY_KEY["ibm.720"],
    }
    window._on_disk_load_finished()
    assert len(queued) == 1
    queued.pop()()

    assert captures == [("image_convert", str(source), str(destination))]
    window.table.collect_unused_zip_imports()
    assert source.read_bytes() == b"non-FAT capture"
    window._on_floppy_image_capture_finished()

    assert not extraction_root.exists()
    assert window.table._zip_imports == []


@pytest.mark.parametrize("choice", ["keep", "replace"])
def test_zip_duplicates_use_existing_filename_conflict_flow(window, tmp_path, monkeypatch, choice):
    old, new = _midi_bytes("Old"), _midi_bytes("New")
    first_zip = _archive(tmp_path, "one.zip", {"first/song.mid": old})
    second_zip = _archive(tmp_path, "two.zip", {"second/song.mid": new})
    seen = []

    def conflict(**kwargs):
        seen.append(kwargs)
        return choice, False

    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", conflict)
    _drop(window.table, first_zip)
    _drop(window.table, second_zip)
    assert window._regular_file_count() == 1
    row = window._regular_file_rows()[0]
    assert Path(window.table.item(row, 1).text()).read_bytes() == (new if choice == "replace" else old)
    assert len(seen) == 1
    assert seen[0]["filename"] == "song.mid"


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_zip_progress_and_validation_errors_are_localized(application, tmp_path, monkeypatch, code):
    parent = QWidget()
    parent._lt = lambda source: translate_text(source, code)
    parent.can_accept_regular_drop_path = lambda path: path.endswith(".mid")
    added, errors, progress = [], [], []
    parent._zip_import_references = lambda: added
    parent.add_regular_file_from_drop = lambda path: added.append(path) or {"status": "added", "path": path}
    parent._show_error_list = lambda _title, _summary, details, **_k: errors.extend(details)
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    real_progress = drop_table_widget.QProgressDialog

    def capture(*args):
        dialog = real_progress(*args)
        progress.append(dialog)
        return dialog

    monkeypatch.setattr(drop_table_widget, "QProgressDialog", capture)
    unsafe = _archive(tmp_path, "unsafe.zip", {"../outside.mid": b"unsafe"})
    valid = _archive(tmp_path, "valid.zip", {"nested/song.mid": b"music"})
    _drop(table, unsafe, valid)
    assert [Path(path).read_bytes() for path in added] == [b"music"]
    assert not (tmp_path / "outside.mid").exists()
    assert errors == ["unsafe.zip: " + translate_text(
        "Could not extract ZIP file: {error}", code,
        error=translate_text("The ZIP file contains unsafe paths or unsupported entries.", code),
    )]
    assert progress[0].windowTitle() == translate_text("Extracting ZIP Files", code)
    assert progress[0].labelText() == translate_text("Extracting ZIP files...", code)
    assert any(button.text() == translate_text("Cancel", code) for button in progress[0].findChildren(QPushButton))
    table.cleanup_zip_imports()
    parent.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("contents", [None, {}, {"readme.txt": "Not a song"}])
def test_bad_or_empty_zip_does_not_block_other_dropped_files(application, tmp_path, contents):
    parent = QWidget()
    parent.can_accept_regular_drop_path = lambda path: path.endswith(".mid")
    added, errors = [], []
    parent.add_regular_file_from_drop = lambda path: added.append(path) or {"status": "added"}
    parent._show_error_list = lambda _title, _summary, details, **_k: errors.extend(details)
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    archive = _archive(tmp_path, "songs.zip", contents or {})
    if contents is None:
        archive.write_bytes(b"not a zip")
    direct = tmp_path / "direct.mid"
    _drop(table, archive, direct)
    # Qt uses forward slashes for local URLs, including on Windows.
    assert [Path(path) for path in added] == [direct]
    assert len(errors) == 1
    assert table._zip_imports == []
    parent.deleteLater()
    application.processEvents()


def test_zip_drop_in_image_mode_queues_members_not_archive(application, tmp_path):
    parent = QWidget()
    parent.is_image_mode = lambda: True
    queued = []
    parent._zip_import_references = lambda: queued
    parent.queue_image_additions = queued.extend
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    archive = _archive(tmp_path, "songs.zip", {"album/one.mid": b"one", "album/two.fil": b"two"})
    _drop(table, archive)
    assert {Path(path).name for path in queued} == {"one.mid", "two.fil"}
    assert all(Path(path).is_file() for path in queued)
    table.cleanup_zip_imports()
    assert all(not Path(path).exists() for path in queued)
    parent.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("names", [
    ("disk1.img", "disk2.img"),
    ("disk.img", "song.mid"),
    ("disk.hfe", "song.fil"),
])
@pytest.mark.parametrize("image_mode", [False, True])
@pytest.mark.parametrize("packaging", ["one_zip", "two_zips", "zip_and_file", "files"])
def test_ambiguous_image_drop_warns_before_importing(
    application, tmp_path, monkeypatch, names, image_mode, packaging,
):
    parent = QWidget()
    parent.is_image_mode = lambda: image_mode
    parent.can_accept_regular_drop_path = lambda path: path.endswith((".mid", ".fil"))
    imported, warnings, extracted_roots, retained_paths = [], [], [], []
    parent._zip_import_references = lambda: retained_paths

    def keep_import(paths):
        retained_paths.extend(paths if isinstance(paths, list) else [paths])
        imported.append(paths)

    parent.load_image_file = imported.append
    parent.prepare_regular_file_drop = imported.append
    parent.add_regular_file_from_drop = keep_import
    parent.finish_regular_file_drop = imported.append
    parent.queue_image_additions = keep_import
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    previous = _archive(tmp_path, "previous.zip", {"existing.mid": b"existing"})
    _drop(table, previous)
    imported.clear()
    previous_imports = list(table._zip_imports)
    previous_song = Path(previous_imports[0][0].name) / "existing.mid"
    real_extract = drop_table_widget.extract_zip

    def capture(path, destination, **kwargs):
        extracted_roots.append(Path(destination))
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", capture)
    monkeypatch.setattr(
        drop_table_widget.QMessageBox, "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    if packaging == "one_zip":
        paths = [_archive(tmp_path, "disks.zip", {name: b"data" for name in names})]
    else:
        paths = []
        for index, name in enumerate(names):
            if packaging == "two_zips" or (packaging == "zip_and_file" and index == 0):
                paths.append(_archive(tmp_path, f"disk{index}.zip", {name: b"data"}))
            else:
                path = tmp_path / name
                path.write_bytes(b"data")
                paths.append(path)
    originals = {path: path.read_bytes() for path in paths}

    try:
        _drop(table, *paths)
        assert imported == []
        assert len(warnings) == 1
        title, message = warnings[0]
        assert title == "Drop Failed"
        assert "Only one disk image can be opened at a time" in message
        assert "Nothing from this drop was imported." in message
        assert "Extract ZIP files first" in message
        assert table._zip_imports == previous_imports
        assert previous_song.read_bytes() == b"existing"
        assert table.zip_source_for_path(previous_song) == str(previous)
        assert all(not root.exists() for root in extracted_roots)
        assert {path: path.read_bytes() for path in paths} == originals
    finally:
        table.cleanup_zip_imports()
        parent.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("extension", ["IMG", "hfe", "scp"])
def test_single_image_zip_still_opens_and_retains_source(application, tmp_path, extension):
    parent = QWidget()
    opened = []
    parent._zip_import_references = lambda: opened
    parent.load_image_file = opened.append
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    archive = _archive(tmp_path, "disk.zip", {
        f"album/disk.{extension}": b"image data", "album/readme.txt": "Notes",
    })
    try:
        _drop(table, archive)
        assert len(opened) == 1
        source = Path(opened[0])
        assert source.name == f"disk.{extension}"
        assert source.read_bytes() == b"image data"
        assert table.zip_source_for_path(source) == str(archive)
    finally:
        table.cleanup_zip_imports()
        parent.deleteLater()
        application.processEvents()
    assert not source.exists()


def test_zip_sources_are_cleaned_when_window_closes(window, tmp_path):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Close")})
    _drop(window.table, archive)
    paths = [Path(window.table.item(row, 1).text()) for row in window._regular_file_rows()]
    assert paths and all(path.exists() for path in paths)
    assert window.close()
    assert all(not path.exists() for path in paths)
    assert archive.is_file()


def test_closing_window_during_extraction_cancels_and_cleans_current_batch(window, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": _midi_bytes("Close during extraction")})
    extracted_roots = []
    real_extract = drop_table_widget.extract_zip

    def capture(path, destination, **kwargs):
        extracted_roots.append(Path(destination))
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", capture)
    QTimer.singleShot(0, window.close)
    _drop(window.table, archive)
    assert window._regular_file_count() == 0
    assert window.table._zip_imports == []
    assert extracted_roots and all(not path.exists() for path in extracted_roots)
    assert archive.is_file()


def test_cancel_adding_extracted_files_keeps_only_completed_imports(application, tmp_path, monkeypatch):
    parent = QWidget()
    parent.can_accept_regular_drop_path = lambda path: path.endswith(".mid")
    added, results, dialogs = [], [], []
    parent._zip_import_references = lambda: added
    parent.finish_regular_file_drop = results.extend
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    real_progress = drop_table_widget.QProgressDialog

    def capture(*args):
        dialog = real_progress(*args)
        dialogs.append(dialog)
        return dialog

    def add(path):
        added.append(path)
        dialogs[-1].cancel()
        return {"status": "added", "path": path}

    parent.add_regular_file_from_drop = add
    monkeypatch.setattr(drop_table_widget, "QProgressDialog", capture)
    archive = _archive(tmp_path, "songs.zip", {"one.mid": b"one", "two.mid": b"two"})
    _drop(table, archive)
    assert len(added) == 1
    assert [result["status"] for result in results] == ["added", "cancelled"]
    assert Path(added[0]).read_bytes() == b"one"
    table.cleanup_zip_imports()
    parent.deleteLater()
    application.processEvents()


def test_cancel_zip_extraction_discards_current_batch(application, tmp_path, monkeypatch):
    parent = QWidget()
    parent.can_accept_regular_drop_path = lambda path: path.endswith(".mid")
    added, extracted_roots = [], []
    parent.add_regular_file_from_drop = lambda path: added.append(path)
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    archive = _archive(tmp_path, "songs.zip", {"song.mid": b"song"})

    def cancel(_path, destination, **_kwargs):
        extracted_roots.append(Path(destination))
        (Path(destination) / "partial.mid").write_bytes(b"partial")
        raise drop_table_widget.ZipImportCancelled()

    monkeypatch.setattr(drop_table_widget, "extract_zip", cancel)
    _drop(table, archive)
    assert added == []
    assert table._zip_imports == []
    assert all(not path.exists() for path in extracted_roots)
    parent.deleteLater()
    application.processEvents()
