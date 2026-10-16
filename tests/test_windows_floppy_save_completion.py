"""Cancellation cannot undo completion bookkeeping after Windows file saves."""

import threading

import pytest

from aps_midi_prep_tool_app import floppy_image
from test_windows_floppy_save import _package, mounted_drive, save_session  # noqa: F401


@pytest.mark.parametrize("finalize_recovery", [False, True])
@pytest.mark.parametrize("verify_copies", [False, True])
def test_final_progress_cancellation_preserves_completed_windows_save(
    save_session, mounted_drive, monkeypatch, finalize_recovery, verify_copies,
):
    cancelled = threading.Event()
    final_progress = []
    backend_finished = False
    sync = save_session._sync_windows_drive_files

    def finish_backend(*args, **kwargs):
        nonlocal backend_finished
        result = sync(*args, **kwargs)
        backend_finished = True
        return result

    def progress(step, total, message):
        if backend_finished and message == "Finishing floppy save...":
            final_progress.append((step, total, message))
            cancelled.set()
            raise floppy_image.FloppyOperationCancelled("Cancelled at final progress")

    monkeypatch.setattr(save_session, "_sync_windows_drive_files", finish_backend)

    save_session._sync_modified_image_files_to_windows_drive(
        "prepared.img", "A:", progress_callback=progress,
        cancel_callback=cancelled.is_set, finalize_recovery=finalize_recovery,
        verify_copies=verify_copies,
    )

    assert cancelled.is_set()
    assert final_progress == [(0, 0, "Finishing floppy save...")]
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == save_session.prepared
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["stage"] == "complete"
    assert diagnostics["status"] == ("complete" if verify_copies else "files_saved")
    assert diagnostics["file_contents_verified"] is verify_copies
    assert "error" not in diagnostics
    assert save_session.last_write_verification == {
        "confidence": "contents_verified" if verify_copies else "written",
        "hardware_tested": False,
    }
    directory, manifest = _package(save_session)
    assert manifest["status"] == (
        "complete" if finalize_recovery else "contents_verified" if verify_copies else "files_saved"
    )
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    assert (directory / manifest["replacements"]["SONG.FIL"]["file"]).read_bytes() == b"new song"
    assert (directory / "prepared.img").read_bytes() == b"prepared image"
    if not finalize_recovery:
        assert save_session._pending_windows_save_recovery.directory == directory
        floppy_image._finish_windows_save_recovery(save_session, "complete")
        assert _package(save_session)[1]["status"] == "complete"
        assert save_session._pending_windows_save_recovery is None


@pytest.mark.parametrize("verify_copies", [False, True])
def test_final_verification_progress_remains_cancellable(
    save_session, mounted_drive, verify_copies,
):
    cancelled = threading.Event()
    final_action = "Verifying saved files..." if verify_copies else "Finishing floppy save..."

    def progress(_step, _total, message):
        diagnostics = save_session.last_floppy_save_diagnostics
        if diagnostics["stage"] == "post_write_listing" and message == final_action:
            cancelled.set()
            raise floppy_image.FloppyOperationCancelled("Cancelled before final verification")

    with pytest.raises(floppy_image.FloppyOperationCancelled, match="before final verification"):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", progress_callback=progress,
            cancel_callback=cancelled.is_set, verify_copies=verify_copies,
        )

    assert cancelled.is_set()
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == save_session.prepared
    assert save_session.last_write_verification["confidence"] == "partial_or_uncertain"
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["stage"] == "post_write_listing"
    assert diagnostics["status"] == "cancelled"
    assert diagnostics["file_contents_verified"] is False
    directory, manifest = _package(save_session)
    assert manifest["status"] == "cancelled"
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
