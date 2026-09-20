"""Ordinary lists can save their staged export payloads directly to Windows floppies."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image, main_window
from test_eseq_output_filtering import window
from test_save_as_overwrite import _song, _title


@pytest.mark.parametrize("accept", [False, True])
def test_regular_save_includes_edits_and_renames_without_an_image(window, tmp_path, monkeypatch, accept):
    source = tmp_path / "original.mid"
    source.write_bytes(_song("Original", 60))
    window._load_regular_files([str(source)], "Songs", prepare_destination=False)
    window.pendingEdits[str(source)] = "Edited"
    row = window._regular_file_rows()[0]
    window._stage_regular_row_pending_rename(row, str(source), "RENAMED.MID")
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    window._refresh_regular_mode_action_state()
    assert window.fileSaveToFloppyAction.isEnabled()
    assert window.image_session is None
    target = SimpleNamespace(path="A:")
    monkeypatch.setattr(window, "_choose_save_to_floppy_drive", lambda: {
        "target": target, "target_name": "Test floppy", "target_kind": "floppy_usb",
    })
    monkeypatch.setattr(floppy_image, "_read_windows_filesystem_drive_listing", lambda *_a, **_k:
        floppy_image.ImageListing([floppy_image.ImageEntry("RENAMED.MID", 3, 1024)], 700000, 1024))
    prompts, jobs, directories = [], [], []
    monkeypatch.setattr(window, "_confirm_save_to_floppy_files", lambda name, **kw: prompts.append(kw) or accept)
    real_stage = window._stage_files_for_image_export
    def stage(directory, **kwargs):
        directories.append(Path(directory))
        return real_stage(directory, **kwargs)
    monkeypatch.setattr(window, "_stage_files_for_image_export", stage)
    def start(_kind, _target, _name, operations, *, session, temporary_directory, file_level):
        assert operations == {} and file_level
        assert set(session.prepared_files) == {"RENAMED.MID"}
        assert _title(Path(session.prepared_files["RENAMED.MID"]).read_bytes()) == "Edited"
        assert session.overwrite_names == {"RENAMED.MID"}
        jobs.append(session)
        temporary_directory.cleanup()
    monkeypatch.setattr(window, "_start_write_image_to_floppy_worker", start)
    monkeypatch.setattr(floppy_image, "create_blank_floppy_image", lambda *_a, **_k: pytest.fail("No image is needed"))

    window.save_to_floppy()

    assert prompts == [{"matching_files": ["RENAMED.MID"]}]
    assert len(jobs) == int(accept)
    assert all(not path.exists() for path in directories)
    assert window.pendingEdits[str(source)] == "Edited"
    assert _title(source.read_bytes()) == "Original"


def test_regular_save_remains_unavailable_without_files(window, monkeypatch):
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    window._refresh_regular_mode_action_state()
    assert not window.fileSaveToFloppyAction.isEnabled()


def test_regular_save_converts_midi_and_builds_the_piano_catalog(window, tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from aps_midi_prep_tool_app.eseq_converter import parse_eseq_bytes
    from test_preparation_delivery import _apply
    from test_eseq_output_filtering import _catalog_tracks

    _apply(window, "mark_ii")
    source = tmp_path / "SOURCE.MID"
    original = _song("Performance", 60)
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Songs")
    QTest.qWait(150)
    assert window.pendingRegularConversions
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(window, "_choose_save_to_floppy_drive", lambda: {
        "target": SimpleNamespace(path="A:"), "target_name": "Test floppy", "target_kind": "floppy_usb",
    })
    monkeypatch.setattr(floppy_image, "_read_windows_filesystem_drive_listing", lambda *_a, **_k:
        floppy_image.ImageListing([], 700000, 1024))
    monkeypatch.setattr(window, "_confirm_save_to_floppy_files", lambda *_a, **_k: True)
    payloads = {}
    def start(*_args, session, temporary_directory, **_kwargs):
        payloads.update({name: Path(path).read_bytes() for name, path in session.prepared_files.items()})
        temporary_directory.cleanup()
    monkeypatch.setattr(window, "_start_write_image_to_floppy_worker", start)
    monkeypatch.setattr(floppy_image, "create_blank_floppy_image", lambda *_a, **_k: pytest.fail("No image is needed"))

    window.save_to_floppy()

    assert set(payloads) == {"SOURCE.FIL", "PIANODIR.FIL"}
    assert parse_eseq_bytes(payloads["SOURCE.FIL"]).title == "Performance"
    assert _catalog_tracks(payloads["PIANODIR.FIL"]) == [("SOURCE.FIL", "Performance")]
    assert source.read_bytes() == original


@pytest.mark.parametrize("outcome", ["complete", "failed", "cancelled"])
def test_regular_save_worker_releases_prepared_files_after_every_outcome(tmp_path, outcome):
    import tempfile
    from aps_midi_prep_tool_app.disk_session_worker import DiskSessionWriteTargetWorker

    directory = tempfile.TemporaryDirectory(dir=tmp_path)
    prepared = Path(directory.name) / "SONG.MID"
    prepared.write_bytes(b"prepared")
    def write(*_args, **_kwargs):
        assert prepared.read_bytes() == b"prepared"
        if outcome == "failed":
            raise OSError("Simulated drive failure")
        if outcome == "cancelled":
            raise floppy_image.FloppyOperationCancelled("Cancelled")
    worker = DiskSessionWriteTargetWorker(
        SimpleNamespace(write_to_floppy_target=write), "floppy_usb", "A:", {},
        file_level=True, temporary_directory=directory,
    )
    results = []
    worker.writeFinished.connect(lambda: results.append("complete"))
    worker.writeFailed.connect(lambda _message: results.append("failed"))
    worker.operationCancelled.connect(lambda _message: results.append("cancelled"))

    worker.run()

    assert results == [outcome]
    assert not prepared.parent.exists()


def test_regular_save_failure_guidance_includes_its_recovery_directory(window):
    window.lastFileSaveSession = SimpleNamespace(last_floppy_save_diagnostics={
        "status": "failed", "target_mutation_attempted": False,
        "recovery_directory": "C:/Recovery/save-test",
    })
    guidance = window._floppy_operation_error_guidance("No staging space", operation="save_files", file_level=True)
    assert "APS has not changed the floppy." in guidance
    assert "C:/Recovery/save-test" in guidance
    assert window._bug_report_context()["floppy_save"]["recovery_directory"] == "C:/Recovery/save-test"
