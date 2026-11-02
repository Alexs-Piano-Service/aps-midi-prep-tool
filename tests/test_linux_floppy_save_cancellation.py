"""Worker progress cancellation must preserve floppy save transaction boundaries."""

import os
from pathlib import Path
import shutil

import pytest

from aps_midi_prep_tool_app import disk_session_worker
from aps_midi_prep_tool_app import floppy_image as image
from test_linux_floppy_save import _manifest, floppy_save  # noqa: F401
from test_save_as_overwrite import window  # noqa: F401


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


@pytest.mark.parametrize("worker_kind", ["commit", "write_target"])
@pytest.mark.parametrize("verify_copies", [False, True])
def test_cancellation_at_completion_keeps_saved_contents_and_success_state(
    floppy_save, window, tmp_path, worker_kind, verify_copies,
):
    session, _source, target, read_file = floppy_save
    shutil.copyfile(target, session.working_img_path)
    session.source_kind = "floppy_usb"
    session.source_path = target
    session.repair_changed = True
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    replacement = tmp_path / "replacement.mid"
    replacement.write_bytes(b"new recording")
    window.pendingImageReplacements["CHANGE.MID"] = str(replacement)
    operations = {
        "replacements": dict(window.pendingImageReplacements),
        "verify_copies": verify_copies,
    }
    assert Path(session.extract_file("CHANGE.MID")).read_bytes() == b"old recording"

    if worker_kind == "commit":
        worker = disk_session_worker.DiskSessionCommitWorker(session, operations)
        finished_signal, failed_signal = worker.commitFinished, worker.commitFailed
        finished_signal.connect(window._on_floppy_commit_success)
        completion_message = "Finishing floppy save..."
    else:
        worker = disk_session_worker.DiskSessionWriteTargetWorker(
            session, "floppy_usb", image.FloppyDriveInfo(target, session.disk_format.size_bytes),
            operations, file_level=True,
        )
        finished_signal, failed_signal = worker.writeFinished, worker.writeFailed
        finished_signal.connect(
            lambda: window._on_write_image_to_floppy_success("Test floppy", file_level=True)
        )
        completion_message = "Floppy write complete."

    cancellations, failures, completions, completed_progress = [], [], [], []
    worker.operationCancelled.connect(cancellations.append)
    failed_signal.connect(failures.append)
    finished_signal.connect(lambda *_args: completions.append(True))

    def cancel_at_completion(_step, _total, message):
        if message == completion_message:
            completed_progress.append(message)
            assert read_file(target, "CHANGE.MID") == b"new recording"
            worker.cancel()

    worker.progressChanged.connect(cancel_at_completion)
    worker.run()

    assert completed_progress == [completion_message]
    assert worker._cancel_requested()
    assert completions == [True]
    assert not cancellations
    assert not failures
    assert not window._test_errors
    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert session.last_write_verification["confidence"] == (
        "contents_verified" if verify_copies else "written"
    )
    diagnostics = session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "complete"
    assert diagnostics["stage"] == "complete"
    _directory, manifest = _manifest(session)
    assert manifest["status"] == "complete"
    assert manifest["diagnostics"]["status"] == "complete"
    assert manifest["diagnostics"]["stage"] == "complete"
    assert session._pending_windows_save_recovery is None
    if worker_kind == "commit":
        assert read_file(session.working_img_path, "CHANGE.MID") == b"new recording"
        assert Path(session.extract_file("CHANGE.MID")).read_bytes() == b"new recording"
        assert not window.pendingImageReplacements
        assert not session.repair_changed
        assert session.repair_note == "Floppy saved."
    else:
        # Exporting to a target preserves edits to the open source session.
        assert read_file(session.working_img_path, "CHANGE.MID") == b"old recording"
        assert window.pendingImageReplacements == operations["replacements"]


@pytest.mark.parametrize("worker_kind", ["commit", "write_target"])
@pytest.mark.parametrize("cancel_at", ["after_delete", "restore_verification"])
def test_worker_cancellation_preserves_original_files_and_recovery_diagnostics(
    floppy_save, monkeypatch, worker_kind, cancel_at,
):
    session, source, target, read_file = floppy_save
    originals = {
        entry.path: read_file(target, entry.path)
        for entry in image.read_image_listing(target).entries
    }

    if worker_kind == "commit":
        monkeypatch.setattr(
            session, "commit_to_source",
            lambda **kwargs: session._sync_modified_image_files_to_floppy_drive(
                source, target, **kwargs,
            ),
        )
        worker = disk_session_worker.DiskSessionCommitWorker(session, {})
        finished_signal, failed_signal = worker.commitFinished, worker.commitFailed
    else:
        def write_to_target(_kind, _target, *, file_level, verify_after_write, **kwargs):
            assert file_level
            session._sync_modified_image_files_to_floppy_drive(source, target, **kwargs)

        monkeypatch.setattr(session, "write_to_floppy_target", write_to_target)
        worker = disk_session_worker.DiskSessionWriteTargetWorker(
            session, "floppy_usb", target, {}, file_level=True,
        )
        finished_signal, failed_signal = worker.writeFinished, worker.writeFailed

    cancellations, failures, completions = [], [], []
    worker.operationCancelled.connect(cancellations.append)
    failed_signal.connect(failures.append)
    finished_signal.connect(lambda *_args: completions.append(True))
    restore_verification_progress = []

    def cancel_during_restore_verification(_step, _total, message):
        if message.startswith("Restoring original files...\n"):
            restore_verification_progress.append(message)
            worker.cancel()

    if cancel_at == "restore_verification":
        worker.progressChanged.connect(cancel_during_restore_verification)

    run = session._run_mtools
    deleted = []

    def stop_after_deletion(args, message, **kwargs):
        result = run(args, message, **kwargs)
        if os.path.basename(args[0]) == "mdel" and not deleted:
            deleted.append(args[-1])
            if cancel_at == "after_delete":
                worker.cancel()
            else:
                raise image.FloppyImageError("Injected failure after deleting original")
        return result

    monkeypatch.setattr(session, "_run_mtools", stop_after_deletion)
    worker.run()

    assert deleted == ["::/CHANGE.MID"]
    assert {
        entry.path: read_file(target, entry.path)
        for entry in image.read_image_listing(target).entries
    } == originals
    assert worker._cancel_requested()
    assert not failures
    assert not completions
    assert len(cancellations) == 1
    diagnostics = session.last_floppy_save_diagnostics
    expected_status = "cancelled" if cancel_at == "after_delete" else "failed"
    assert diagnostics["status"] == expected_status
    assert diagnostics["target_mutation_attempted"]
    assert diagnostics["restoration"] == {
        "status": "complete", "restored": ["CHANGE.MID"], "unchanged": [],
    }
    directory, manifest = _manifest(session)
    assert str(directory) in cancellations[0]
    assert "recovery files are retained" in cancellations[0]
    assert manifest["status"] == expected_status
    assert manifest["diagnostics"]["restoration"] == diagnostics["restoration"]
    if cancel_at == "restore_verification":
        assert restore_verification_progress
        assert "Injected failure after deleting original" in cancellations[0]
        assert diagnostics["error"]["message"] == "Injected failure after deleting original"
