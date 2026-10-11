"""Worker progress cancellation must not interrupt restoration of floppy files."""

import os

import pytest

from aps_midi_prep_tool_app import disk_session_worker
from aps_midi_prep_tool_app import floppy_image as image
from test_linux_floppy_save import _manifest, floppy_save  # noqa: F401


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


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
    assert diagnostics["restoration"] == {"status": "complete", "restored": ["CHANGE.MID"]}
    directory, manifest = _manifest(session)
    assert str(directory) in cancellations[0]
    assert "recovery files are retained" in cancellations[0]
    assert manifest["status"] == expected_status
    assert manifest["diagnostics"]["restoration"] == diagnostics["restoration"]
    if cancel_at == "restore_verification":
        assert restore_verification_progress
        assert "Injected failure after deleting original" in cancellations[0]
        assert diagnostics["error"]["message"] == "Injected failure after deleting original"
