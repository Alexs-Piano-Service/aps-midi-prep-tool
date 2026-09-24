"""Guided recovery saves the prepared IMG before a deliberate, verified raw write."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox

from aps_midi_prep_tool_app import floppy_image, main_window
from test_save_as_overwrite import window, _song, _title


def _prepare(window, tmp_path, route):
    song = tmp_path / "SONG.MID"
    song.write_bytes(_song("Original", 60))
    if route == "regular":
        window._load_regular_files([str(song)], "Songs", prepare_destination=False)
        window.pendingEdits[str(song)] = "Edited"
        window._stage_regular_row_pending_rename(0, str(song), "EDITED.MID")
        return song, song.read_bytes()

    source = tmp_path / "protected.img"
    floppy_image.create_floppy_images_from_files(
        [{"host_path": str(song), "image_path": "SONG.MID"}],
        str(source), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"],
    )
    data = bytearray(source.read_bytes())
    data[:512] = bytes(512)
    source.write_bytes(data)
    session = floppy_image.FloppyImageSession.load(str(source))
    assert session.source_boot_sector_repaired
    session.source_kind, session.source_path = "floppy_usb", "A:"
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    window.pendingImageTitleEdits["SONG.MID"] = "Edited"
    window.pendingImageRenames["SONG.MID"] = "EDITED.MID"
    added = tmp_path / "NEW.MID"
    added.write_bytes(_song("Added", 64))
    window.pendingImageAdditions["NEW.MID"] = str(added)
    assert window._bug_report_context()["image"]["source_boot_sector_repaired"] is True
    return source, bytes(data)


@pytest.mark.parametrize("route", ["regular", "protected"])
@pytest.mark.parametrize("decision", ["cancel_target", "decline_overwrite", "write"])
def test_guided_recovery_saves_current_files_before_confirmed_verified_write(
    window, tmp_path, monkeypatch, route, decision,
):
    source, original = _prepare(window, tmp_path, route)
    output = tmp_path / "prepared.img"
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    calls, workers = [], []

    def options(**kwargs):
        assert kwargs["raw_only"]
        return str(output), "img", disk_format

    def choose():
        assert output.is_file()
        assert window.image_session.source_path == str(output)
        assert _title(Path(window.image_session.extract_file("EDITED.MID")).read_bytes()) == "Edited"
        if route == "protected":
            assert _title(Path(window.image_session.extract_file("NEW.MID")).read_bytes()) == "Added"
        assert floppy_image._geometry_from_boot_sector(output.read_bytes()[:512]) is not None
        calls.append("choose")
        if decision == "cancel_target":
            return None
        return {"target_kind": "floppy_usb", "target": floppy_image.FloppyDriveInfo("A:", 0),
                "target_name": "Test floppy", "drive_size_bytes": 0}

    def question(_parent, title, message, _buttons, default):
        assert title == "Write Current Image to Floppy"
        assert "overwrite the floppy" in message
        assert default == QMessageBox.No
        calls.append("confirm")
        return QMessageBox.Yes if decision == "write" else QMessageBox.No

    monkeypatch.setattr(window, "_prompt_for_save_image_options", options)
    monkeypatch.setattr(window, "_choose_write_image_floppy_target", choose)
    monkeypatch.setattr(main_window.QMessageBox, "question", question)
    monkeypatch.setattr(main_window.DiskSessionWriteTargetWorker, "start", lambda worker: workers.append(worker))
    monkeypatch.setattr(floppy_image, "_read_windows_filesystem_drive_listing",
                        lambda *a, **k: pytest.fail("Raw image recovery must not list the target filesystem"))
    window.verifyFloppyWriteAction.setChecked(False)

    window._save_image_and_apply_to_floppy()

    assert not window._test_errors
    assert output.exists() and source.read_bytes() == original
    assert not window.verifyFloppyWriteAction.isChecked()  # Override only this write.
    assert calls == (["choose"] if decision == "cancel_target" else ["choose", "confirm"])
    if decision != "write":
        assert not workers
        return
    assert len(workers) == 1
    worker = workers[0]
    assert not worker.file_level and worker.verify_after_write
    written = []

    def write(image, device, **kwargs):
        assert device == "A:"
        written.append(Path(image).read_bytes())
        return {"confidence": "written"}

    def verify(image, target_kind, target, actual_format, **kwargs):
        assert written == [Path(image).read_bytes()]
        assert target_kind == "floppy_usb" and actual_format == disk_format
        calls.append("verify")
        return {"confidence": "contents_verified", "hardware_tested": False}

    monkeypatch.setattr(floppy_image, "_write_block_device", write)
    monkeypatch.setattr(floppy_image, "_verify_physical_floppy_contents", verify)
    worker.run()
    window._on_write_image_to_floppy_finished()
    assert not window._test_errors
    assert calls[-1] == "verify"
    assert window.image_session.last_write_verification["confidence"] == "contents_verified"
    assert output.exists() and source.read_bytes() == original


@pytest.mark.parametrize("route", ["regular", "protected"])
@pytest.mark.parametrize("failure", ["cancel_export", "export_error", "open_error"])
def test_incomplete_export_never_writes_a_stale_session(window, tmp_path, monkeypatch, route, failure):
    source, original = _prepare(window, tmp_path, route)
    session = window.image_session
    output = tmp_path / "prepared.img"
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **kwargs:
                        None if failure == "cancel_export" else
                        (str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]))
    monkeypatch.setattr(window, "write_image_to_floppy", lambda **kwargs: pytest.fail("Unexpected write"))

    def fail(*args, **kwargs):
        raise OSError("Export unavailable")

    if failure == "export_error":
        if session is None:
            monkeypatch.setattr(main_window, "create_floppy_images_from_files", fail)
        else:
            monkeypatch.setattr(session, "export_to_images", fail)
    elif failure == "open_error":
        monkeypatch.setattr(main_window.FloppyImageSession, "load", fail)

    window._save_image_and_apply_to_floppy()

    assert window.image_session is session
    assert source.read_bytes() == original
    assert bool(window.pendingEdits if route == "regular" else window.pendingImageTitleEdits)
    assert len(window._test_errors) == (failure != "cancel_export")
    if failure == "open_error":
        assert output.exists()


def test_recovery_forces_img_even_when_preparation_prefers_hfe(window, monkeypatch):
    from test_delivery_image_defaults import _choose
    _choose(window, "unsure", "flashfloppy_hfe")

    def inspect(dialog):
        image_type, disk = dialog.findChildren(QComboBox)
        assert image_type.count() == 1
        assert image_type.currentData() == "img" and not image_type.isEnabled()
        assert disk.currentData() is not None
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert window._prompt_for_save_image_options(raw_only=True) is None


def test_multiple_saved_images_do_not_apply_previous_session(window, tmp_path, monkeypatch):
    _prepare(window, tmp_path, "protected")
    paths = [str(tmp_path / f"disk{i}.img") for i in (1, 2)]
    shown = []
    monkeypatch.setattr(window, "save_as_image", lambda **kwargs: paths)
    monkeypatch.setattr(window, "write_image_to_floppy", lambda **kwargs: pytest.fail("Unexpected write"))
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda parent, title, message: shown.append(message))

    window._save_image_and_apply_to_floppy()

    assert len(shown) == 1
    assert all(path in shown[0] for path in paths)
    assert "Several images were saved" in shown[0]


@pytest.mark.parametrize("entrypoint", ["save_image_changes", "save_to_floppy"])
def test_repaired_source_offers_image_recovery_before_filesystem_save(window, tmp_path, monkeypatch, entrypoint):
    _prepare(window, tmp_path, "protected")
    offered = []
    monkeypatch.setattr(window, "_original_write_is_allowed", lambda: True)
    monkeypatch.setattr(window, "_offer_floppy_image_recovery", lambda: offered.append(True))
    monkeypatch.setattr(window, "_choose_save_to_floppy_drive", lambda: {
        "target_kind": "floppy_usb", "target": floppy_image.FloppyDriveInfo("A:", 0), "target_name": "A:",
    })
    monkeypatch.setattr(window, "_start_floppy_commit_worker", lambda *a, **k: pytest.fail("Unexpected file save"))
    monkeypatch.setattr(window, "_start_write_image_to_floppy_worker", lambda *a, **k: pytest.fail("Unexpected file save"))
    getattr(window, entrypoint)()
    assert offered == [True]
