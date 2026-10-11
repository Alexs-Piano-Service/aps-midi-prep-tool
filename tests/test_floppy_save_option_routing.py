"""Persisted file-save choices reach both workers and physical-save backends."""

from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image, main_window
from test_save_as_overwrite import window  # noqa: F401


@pytest.mark.parametrize("operation", ["target", "commit"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("file_level", [False, True])
def test_file_save_worker_receives_settings_without_full_image_readback(window, monkeypatch, operation, enabled, file_level):
    window.image_session = SimpleNamespace(source_kind="floppy_usb" if file_level else "floppy_gw")
    for key in (window.SETTING_FLOPPY_BACKUP_ORIGINALS, window.SETTING_FLOPPY_CHECK_ORIGINALS,
                window.SETTING_FLOPPY_VERIFY_COPIES):
        window.settings.setValue(key, enabled)
    window.verifyFloppyWriteAction.setChecked(True)
    monkeypatch.setattr(window, "_set_disk_write_busy", lambda _busy: None)
    worker_class = (main_window.DiskSessionWriteTargetWorker if operation == "target"
                    else main_window.DiskSessionCommitWorker)
    monkeypatch.setattr(worker_class, "start", lambda _worker: None)
    if operation == "target":
        window._start_write_image_to_floppy_worker("floppy_usb", "A:", "Test floppy", {}, file_level=file_level)
        worker = window.diskWriteTargetWorker
        dialog = window.diskWriteTargetProgressDialog
        assert worker.verify_after_write is not file_level
    else:
        window._start_floppy_commit_worker({}, "Saving files to floppy...")
        worker = window.diskCommitWorker
        dialog = window.diskCommitProgressDialog
        assert worker.operations["verify_after_write"] is not file_level
    if file_level:
        assert {key: worker.operations[key] for key in ("backup_originals", "check_originals", "verify_copies")} == {
            "backup_originals": enabled, "check_originals": enabled, "verify_copies": enabled,
        }
    else:
        assert not {"backup_originals", "check_originals", "verify_copies"} & worker.operations.keys()
    dialog.close()
    window.image_session = None


@pytest.mark.parametrize("operation", ["commit", "target", "prepared_windows"])
@pytest.mark.parametrize("options", [
    {"backup_originals": False, "check_originals": False, "verify_copies": False},
    {"backup_originals": True, "check_originals": False, "verify_copies": False},
    {"backup_originals": False, "check_originals": True, "verify_copies": True},
])
def test_file_save_options_reach_backend_without_becoming_image_edits(tmp_path, operation, options):
    prepared = tmp_path / "prepared.img"
    prepared.write_bytes(b"prepared output")
    calls = []

    def sync(*_args, **kwargs):
        calls.append(kwargs)
        owner = job if operation == "prepared_windows" else session
        owner.last_write_verification = {
            "confidence": "contents_verified" if options["verify_copies"] else "written",
            "hardware_tested": False,
        }

    def create_modified_image(**kwargs):
        assert not set(options) & kwargs.keys()
        return str(prepared)

    session = SimpleNamespace(
        source_kind="floppy_usb", source_path=str(tmp_path / "drive.img"),
        working_img_path=str(tmp_path / "working.img"), _extracted_files={},
        create_modified_image=create_modified_image,
        _sync_modified_image_files_to_floppy_drive=sync,
    )
    target = floppy_image.FloppyDriveInfo(session.source_path, 720 * 1024)
    if operation == "commit":
        floppy_image.FloppyImageSession.commit_to_source(session, **options)
    elif operation == "target":
        floppy_image.FloppyImageSession.write_to_floppy_target(session, "floppy_usb", target, file_level=True, **options)
    else:
        job = floppy_image.PreparedWindowsFileSave([{"image_path": "SONG.MID", "host_path": str(prepared)}])
        job._sync_modified_image_files_to_windows_drive = sync
        job.write_to_floppy_target("floppy_usb", target, **options)
    assert len(calls) == 1
    assert {key: calls[0][key] for key in options} == options
    owner = job if operation == "prepared_windows" else session
    assert owner.last_write_verification["confidence"] == (
        "contents_verified" if options["verify_copies"] else "written"
    )
