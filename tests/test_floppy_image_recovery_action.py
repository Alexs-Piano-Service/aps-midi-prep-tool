"""The image recovery action is explicit and waits for failed workers to finish."""

from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from aps_midi_prep_tool_app import floppy_image, main_window
from test_eseq_output_filtering import window
from test_save_as_overwrite import _song


RECOVERY_ACTION = "Save Image and Apply to Floppy..."
WORKERS = (
    ("diskCommitWorker", "_on_floppy_commit_finished"),
    ("diskWriteTargetWorker", "_on_write_image_to_floppy_finished"),
)


def _real_operation_errors(window, monkeypatch):
    monkeypatch.setattr(
        window, "_show_operation_error",
        MethodType(main_window.MidiTitleWindow._show_operation_error, window),
    )


@pytest.mark.parametrize("choice", ["default", "report", "recover"])
def test_failure_dialog_preserves_default_and_report_actions(window, monkeypatch, choice):
    _real_operation_errors(window, monkeypatch)
    recoveries, reports = [], []
    monkeypatch.setattr(window, "_request_floppy_image_recovery", lambda: recoveries.append(True))
    monkeypatch.setattr(window, "show_bug_report_dialog", lambda **kwargs: reports.append(kwargs))

    def choose(box):
        assert box.windowTitle() == "Save To Floppy Failed"
        assert "replaces the entire disk, including its boot sector" in box.text()
        assert "read back and verify" in box.text()
        buttons = {button.text(): button for button in box.buttons()}
        recovery = buttons[RECOVERY_ACTION]
        assert box.buttonRole(recovery) == QMessageBox.ActionRole
        assert box.defaultButton() is box.button(QMessageBox.Ok)
        selected = {
            "default": box.defaultButton(),
            "report": buttons["Report A Bug..."],
            "recover": recovery,
        }[choice]
        selected.click()
        assert box.clickedButton() is selected
        return box.result()

    monkeypatch.setattr(window, "_exec_child_dialog", choose)

    window._show_operation_error(
        "Save To Floppy Failed", "The floppy could not be saved", "Windows rejected the directory",
        offer_floppy_image_recovery=True,
    )

    assert recoveries == ([True] if choice == "recover" else [])
    assert len(reports) == int(choice == "report")
    if reports:
        assert reports[0]["summary"] == "Save To Floppy Failed"
        assert "Windows rejected the directory" in reports[0]["description"]
        assert reports[0]["include_logs"] is True
    assert not getattr(window, "_floppy_image_recovery_pending", False)


def test_repaired_boot_sector_offer_has_explicit_recovery_without_bug_report(window, monkeypatch):
    recoveries = []
    monkeypatch.setattr(window, "_request_floppy_image_recovery", lambda: recoveries.append(True))

    def choose(box):
        assert box.icon() == QMessageBox.Information
        assert "may use Yamaha protection" in box.text()
        assert "replaces the entire disk" in box.text()
        buttons = {button.text(): button for button in box.buttons()}
        assert "Report A Bug..." not in buttons
        assert RECOVERY_ACTION in buttons
        assert box.defaultButton() is box.button(QMessageBox.Ok)
        buttons[RECOVERY_ACTION].click()
        return box.result()

    monkeypatch.setattr(window, "_exec_child_dialog", choose)

    window._offer_floppy_image_recovery()

    assert recoveries == [True]


@pytest.mark.parametrize("worker_attribute, finish_method", WORKERS)
def test_recovery_waits_for_worker_cleanup_then_starts_once(
    window, monkeypatch, worker_attribute, finish_method,
):
    events = []
    setattr(window, worker_attribute, SimpleNamespace(deleteLater=lambda: events.append("deleted")))

    def unlock(busy):
        assert busy is False
        assert getattr(window, worker_attribute) is None
        events.append("unlocked")

    monkeypatch.setattr(window, "_set_disk_write_busy", unlock)
    monkeypatch.setattr(window, "_save_image_and_apply_to_floppy", lambda: events.append("recovery"))

    window._request_floppy_image_recovery()
    QApplication.processEvents()

    assert window._floppy_image_recovery_pending is True
    assert events == []

    getattr(window, finish_method)()
    window._resume_floppy_image_recovery()
    QApplication.processEvents()

    assert events == ["deleted", "unlocked", "recovery"]
    assert window._floppy_image_recovery_pending is False


@pytest.mark.parametrize("worker_attribute, finish_method", WORKERS)
def test_recovery_choice_after_worker_finished_inside_error_dialog_is_not_lost(
    window, monkeypatch, worker_attribute, finish_method,
):
    _real_operation_errors(window, monkeypatch)
    events = []
    setattr(window, worker_attribute, SimpleNamespace(deleteLater=lambda: events.append("deleted")))
    monkeypatch.setattr(window, "_set_disk_write_busy", lambda _busy: None)
    monkeypatch.setattr(window, "_save_image_and_apply_to_floppy", lambda: events.append("recovery"))

    def choose_after_finished(box):
        # QMessageBox.exec() dispatches queued QThread.finished signals before
        # the user necessarily chooses one of the displayed actions.
        getattr(window, finish_method)()
        QApplication.processEvents()
        assert events == ["deleted"]
        next(button for button in box.buttons() if button.text() == RECOVERY_ACTION).click()
        return box.result()

    monkeypatch.setattr(window, "_exec_child_dialog", choose_after_finished)

    window._show_operation_error(
        "Save To Floppy Failed", "Could not save", "Directory unavailable",
        offer_floppy_image_recovery=True,
    )
    QApplication.processEvents()

    assert events == ["deleted", "recovery"]
    assert window._floppy_image_recovery_pending is False


@pytest.mark.parametrize("file_level", [False, True])
def test_file_and_raw_write_failures_offer_image_recovery(window, monkeypatch, file_level):
    errors = []
    monkeypatch.setattr(window, "_show_operation_error", lambda *args, **kwargs: errors.append((args, kwargs)))

    window._on_write_image_to_floppy_failure("The drive rejected the write", file_level=file_level)

    assert len(errors) == 1
    assert errors[0][1]["offer_floppy_image_recovery"] is True
    assert not getattr(window, "_floppy_image_recovery_pending", False)


@pytest.mark.parametrize("source_kind, offer_recovery", [
    ("floppy_usb", True), ("floppy_gw", True), ("image", False),
])
def test_commit_failure_offers_image_recovery_for_physical_media(
    window, monkeypatch, source_kind, offer_recovery,
):
    window.image_session = SimpleNamespace(source_kind=source_kind, cleanup=lambda: None)
    errors = []
    monkeypatch.setattr(window, "_show_operation_error", lambda *args, **kwargs: errors.append((args, kwargs)))

    window._on_floppy_commit_failure("The save failed")

    assert len(errors) == 1
    assert errors[0][1]["offer_floppy_image_recovery"] is offer_recovery
    assert not getattr(window, "_floppy_image_recovery_pending", False)


@pytest.mark.parametrize("operation, file_level", [
    ("commit", False), ("target", False), ("target", True),
])
@pytest.mark.parametrize("confidence", ["not_written", "written"])
def test_cancelled_save_or_verification_never_queues_image_recovery(
    window, monkeypatch, operation, file_level, confidence,
):
    window.image_session = SimpleNamespace(
        last_write_verification={"confidence": confidence}, cleanup=lambda: None,
    )
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    monkeypatch.setattr(window, "_set_disk_write_busy", lambda _busy: None)
    monkeypatch.setattr(window, "_request_floppy_image_recovery", lambda: pytest.fail("Cancellation must not request recovery"))
    monkeypatch.setattr(window, "_save_image_and_apply_to_floppy", lambda: pytest.fail("Cancellation must not start recovery"))

    if operation == "commit":
        window._on_floppy_commit_cancelled("Cancelled")
        window._on_floppy_commit_finished()
    else:
        window._on_write_image_to_floppy_cancelled("Cancelled", file_level=file_level)
        window._on_write_image_to_floppy_finished()
    QApplication.processEvents()

    assert len(warnings) == 1
    assert not getattr(window, "_floppy_image_recovery_pending", False)


def test_regular_file_preflight_directory_failure_offers_recovery_and_cleans_staging(
    window, tmp_path, monkeypatch,
):
    source = tmp_path / "SONG.MID"
    source.write_bytes(_song("Keep this song", 60))
    window._load_regular_files([str(source)], "Songs", prepare_destination=False)
    monkeypatch.setattr(window, "_choose_save_to_floppy_drive", lambda: {
        "target": SimpleNamespace(path="A:"), "target_name": "Test floppy", "target_kind": "floppy_usb",
    })
    directories, errors = [], []
    real_stage = window._stage_files_for_image_export

    def stage(directory, **kwargs):
        directories.append(Path(directory))
        return real_stage(directory, **kwargs)

    def reject_directory(*_args, **_kwargs):
        raise floppy_image.FloppyImageError("Windows rejected the directory listing")

    monkeypatch.setattr(window, "_stage_files_for_image_export", stage)
    monkeypatch.setattr(floppy_image, "_read_windows_filesystem_drive_listing", reject_directory)
    monkeypatch.setattr(window, "_show_operation_error", lambda *args, **kwargs: errors.append((args, kwargs)))
    monkeypatch.setattr(window, "_start_write_image_to_floppy_worker", lambda *args, **kwargs: pytest.fail("Preflight must not write"))

    window._save_regular_files_to_floppy()

    assert len(errors) == 1
    assert errors[0][0][0] == "Save To Floppy Failed"
    assert errors[0][1]["offer_floppy_image_recovery"] is True
    assert directories and all(not directory.exists() for directory in directories)
    assert source.read_bytes() == _song("Keep this song", 60)
    assert window.image_session is None
    assert not getattr(window, "_floppy_image_recovery_pending", False)
