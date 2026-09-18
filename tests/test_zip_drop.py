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

from aps_midi_prep_tool_app import drop_table_widget, main_window
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
    window.table.cleanup_zip_imports()
    assert all(not path.exists() for path in extracted)
    assert all(path.is_file() for path in destination.glob("*.mid"))
    assert archive.read_bytes() == original_zip
    assert direct.read_bytes() == third


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
