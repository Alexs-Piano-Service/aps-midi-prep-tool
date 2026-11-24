"""Album folder naming is shared by direct extraction, the worker, and UI."""

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

from aps_midi_prep_tool_app import bulk_extraction
from aps_midi_prep_tool_app.disk_session_worker import BulkExtractionWorker
from aps_midi_prep_tool_app.eseq_pianodir import build_pianodir_bytes
from aps_midi_prep_tool_app.floppy_image import FloppyOperationCancelled
from aps_midi_prep_tool_app.smart_pianosoft import update_smart_pianosoft_disk_title
from test_bulk_extraction import FakeImageSession
from test_bulk_extraction_dialog import window, _controls, _resume_job


@pytest.mark.parametrize("pianodir_title,pdisk_title,expected", [
    (None, "Evening Jazz", "Evening Jazz"),
    ("Piano Classics", None, "Piano Classics"),
    ("  ", "Evening Jazz", "Evening Jazz"),
    (None, "  ", "disk"),
    (None, " . /: ", "disk"),
    ("  ", None, "disk"),
    (None, None, "disk"),
])
def test_default_folders_use_available_album_title(tmp_path, pianodir_title, pdisk_title, expected):
    source = tmp_path / "images"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic floppy image")
    files = {"README.TXT": b"Preserved contents"}
    if pianodir_title is not None:
        files["pianodir.fil"] = build_pianodir_bytes([], disk_title=pianodir_title)
    if pdisk_title is not None:
        files["pdisk.mng"] = update_smart_pianosoft_disk_title(b"", pdisk_title)
    result = bulk_extraction.bulk_extract_images(
        source, tmp_path / "output",
        session_loader=lambda path, **_kwargs: FakeImageSession(path, files),
    )
    assert not result.errors
    folder = tmp_path / "output" / expected
    assert result.output_directories == (str(folder),)
    assert (folder / "README.TXT").read_bytes() == files["README.TXT"]


def test_worker_defaults_to_catalog_album_name(tmp_path, monkeypatch, qt_application):
    source = tmp_path / "images"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic floppy image")
    files = {"PDISK.MNG": update_smart_pianosoft_disk_title(b"", "Worker album")}
    monkeypatch.setattr(bulk_extraction.FloppyImageSession, "load",
                        lambda path, **_kwargs: FakeImageSession(path, files))
    worker = BulkExtractionWorker(str(source), str(tmp_path / "output"))
    completed, failed = [], []
    worker.extractionFinished.connect(completed.append)
    worker.extractionFailed.connect(failed.append)
    worker.run()
    assert not failed and len(completed) == 1
    assert Path(completed[0].output_directories[0]).name == "Worker album"


@pytest.mark.parametrize("saved_preference,expected", [(None, "album"), (True, "album"), (False, "image")])
def test_dialog_defaults_to_albums_and_honors_explicit_preference(window, monkeypatch, saved_preference, expected):
    w, _source, _output = window
    if saved_preference is None:
        w.settings.remove(w.SETTING_BULK_EXTRACTION_USE_ALBUM_NAMES)
    else:
        w.settings.setValue(w.SETTING_BULK_EXTRACTION_USE_ALBUM_NAMES, saved_preference)
    inspected = []

    def inspect(dialog, **_kwargs):
        controls = _controls(w, dialog)
        inspected.append(controls["naming"].currentData())
        assert "PIANODIR.FIL" in controls["naming"].toolTip()
        assert "PDISK.MNG" in controls["naming"].toolTip()
        return QDialog.Rejected

    monkeypatch.setattr(w, "_exec_child_dialog", inspect)
    w.show_bulk_extraction_utility()
    assert inspected == [expected]


def test_resumed_dialog_keeps_saved_filename_naming(window, monkeypatch, tmp_path):
    w, _source, _output = window
    w.settings.remove(w.SETTING_BULK_EXTRACTION_USE_ALBUM_NAMES)
    _job_path, job = _resume_job(w, monkeypatch, tmp_path)
    job["options"]["use_album_names"] = False
    inspected = []

    def inspect(dialog, **_kwargs):
        controls = _controls(w, dialog)
        assert controls["naming"].currentData() == "album"
        controls["resume"].click()
        inspected.append(controls["naming"].currentData())
        assert not controls["naming"].isEnabled()
        return QDialog.Rejected

    monkeypatch.setattr(w, "_exec_child_dialog", inspect)
    w.show_bulk_extraction_utility()
    assert inspected == ["image"]


def test_filename_override_and_resume_keep_original_output_folder(tmp_path):
    source = tmp_path / "images"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic floppy image")
    files = {"PDISK.MNG": update_smart_pianosoft_disk_title(b"", "Album title"),
             "README.TXT": b"Kept after resume"}
    output = tmp_path / "output"
    checkpoint = output / "job.json"
    loader = lambda path, **_kwargs: FakeImageSession(path, files)

    def cancel_after_first(detail):
        if detail["stage"] == "extracting" and detail["file_completed"] == 1:
            raise FloppyOperationCancelled("Pause extraction")

    with pytest.raises(FloppyOperationCancelled):
        bulk_extraction.bulk_extract_images(
            source, output, use_album_names=False, session_loader=loader,
            job_record_path=checkpoint, progress_detail_callback=cancel_after_first,
        )
    saved = json.loads(checkpoint.read_text())
    assert saved["options"]["use_album_names"] is False
    assert saved["images"]["disk.img"]["output_directory"] == "disk"
    result = bulk_extraction.bulk_extract_images(
        source, output, session_loader=loader, job_record_path=checkpoint,
        resume=True, **saved["options"],
    )
    assert not result.errors
    assert result.output_directories == (str(output / "disk"),)
    assert (output / "disk/README.TXT").read_bytes() == files["README.TXT"]
    assert not (output / "Album title").exists()
