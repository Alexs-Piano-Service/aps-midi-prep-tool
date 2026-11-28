"""Error dialogs keep actionable copy visible and diagnostics expandable."""

import errno

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit

from aps_midi_prep_tool_app import disk_session_worker, floppy_image, main_window
from aps_midi_prep_tool_app.error_presentation import brief_error_detail
from aps_midi_prep_tool_app.message_catalog import translate_text
from test_inspection_staging import window


TRACEBACK = (
    'Traceback (most recent call last):\n'
    + '  File "/a/long/application/path/floppy_image.py", line 4258, in read_disk\n' * 90
    + "PermissionError: [Errno 13] Permission denied: '/dev/test-floppy'"
)


@pytest.mark.parametrize("detail", [TRACEBACK, "x" * 2000, "Stage: acquisition\nValueError: failed"])
def test_technical_or_oversized_detail_is_not_a_summary(detail):
    assert brief_error_detail(detail) == ""
    assert brief_error_detail("The drive stopped responding.\n\n" + detail) == "The drive stopped responding."


def _permission_failure(monkeypatch, *, local_copy=False, windows=False):
    drive = floppy_image.FloppyDriveInfo("/dev/test-floppy", 737280)

    def fail(*_args, **_kwargs):
        try:
            if windows:
                raise floppy_image.FloppyImageError("Access is denied", winerror=5)
            raise PermissionError(errno.EACCES, "Permission denied", "/tmp/working.img" if local_copy else drive.path)
        except Exception as exc:
            if local_copy:
                raise floppy_image.FloppyWorkingCopyError("/tmp/working.img", exc) from exc
            raise floppy_image.FloppyImageError(
                "The disk could not be read well enough to determine its format.\n\n"
                f"Details: {exc}\n\nUse Start in recovery mode.\n\n"
                + floppy_image.USB_FLOPPY_720K_READ_GUIDANCE,
            ) from exc

    monkeypatch.setattr(floppy_image.FloppyImageSession, "load_floppy", fail)
    worker = disk_session_worker.DiskSessionLoadWorker("floppy_usb", drive)
    failures = []
    worker.loadFailedWithDetails.connect(failures.append)
    worker.run()
    return drive, failures[0]


@pytest.mark.parametrize("local_copy,windows", [(False, False), (False, True), (True, False), (True, True)])
def test_read_permission_failure_distinguishes_source_from_working_copy(monkeypatch, local_copy, windows):
    _drive, failure = _permission_failure(monkeypatch, local_copy=local_copy, windows=windows)
    assert failure["type"] == ("local_storage" if local_copy else "source_access")
    assert failure["recoverable"] is False
    assert "Traceback" in failure["traceback"]


@pytest.mark.parametrize("points,language", [(9, "en"), (14, "en"), (18, "en"), (14, "de")])
def test_permission_error_is_compact_with_complete_expandable_diagnostics(window, monkeypatch, points, language):
    drive, failure = _permission_failure(monkeypatch)
    failure["traceback"] = TRACEBACK
    window.diskLoadContext = {"load_kind": "floppy_usb", "source": drive}
    window.diskLoadFailureTitle = "Floppy Load Failed"
    window.currentLanguage = language
    seen = []

    def inspect(box):
        box.setFont(QFont(box.font().family(), points))
        box.show()
        QTest.qWait(40)
        seen.append(box)
        assert box.windowTitle() == translate_text("Floppy Load Failed", language)
        assert translate_text("APS does not have permission to read this disk or image.", language) in box.text()
        assert "Traceback" not in box.text()
        assert "720" not in box.text() and "recovery mode" not in box.text()
        assert len(box.text()) < 300
        assert box.textFormat() == Qt.PlainText
        assert box.height() < box.screen().availableGeometry().height() * .75
        editor = box.findChild(QTextEdit)
        assert editor is not None and not editor.isVisible()
        assert TRACEBACK in box.detailedText()
        for button in box.findChildren(QPushButton):
            if button.isVisible():
                assert box.rect().contains(button.geometry())
        details = next(button for button in box.buttons()
                       if button.text() == translate_text("Show Details...", language))
        details.click()
        QTest.qWait(20)
        assert editor.isVisible() and editor.verticalScrollBar().maximum() > 0
        assert TRACEBACK in editor.toPlainText()
        assert box.height() <= box.screen().availableGeometry().height()
        box.accept()
        return box.result()

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window._on_disk_load_failure_with_details(failure)
    assert len(seen) == 1
    assert window.pendingDiskRecoveryRequest is None


@pytest.mark.parametrize("kind", ["recovery", "conversion", "save"])
def test_operation_errors_keep_diagnostics_in_reports_and_recovery_advice_visible(window, monkeypatch, kind):
    reports, logs = [], []
    monkeypatch.setattr(window, "show_bug_report_dialog", lambda **values: reports.append(values))
    monkeypatch.setattr(window, "_log_error_event", lambda *args, **values: logs.append(values))
    guidance = "The floppy may be partially written. Keep the recovery copy before attempting another write."

    def inspect(box):
        assert TRACEBACK not in box.text()
        assert TRACEBACK in box.detailedText()
        assert guidance in box.text()
        if kind == "save":
            assert "replaces the entire disk" in box.text()
        next(button for button in box.buttons() if button.text() == "Report A Bug...").click()
        return box.result()

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window._show_operation_error(
        "Operation Failed", "The operation could not finish", TRACEBACK,
        guidance=guidance, offer_floppy_image_recovery=kind == "save",
    )
    assert logs[0]["detail"] == TRACEBACK
    assert TRACEBACK in reports[0]["description"]


@pytest.mark.parametrize("warning", [False, True])
def test_batch_errors_preview_only_a_few_rows_and_keep_the_complete_list(window, monkeypatch, warning):
    errors = [f"SONG{i:03}.MID: Invalid MIDI header." for i in range(80)]
    seen, logs = [], []
    monkeypatch.setattr(window, "_log_error_event", lambda *args, **values: logs.append(values))
    monkeypatch.setattr(window, "_log_warning_event", lambda *args, **values: logs.append(values))

    def inspect(box):
        seen.append(box)
        assert errors[0] in box.text()
        assert errors[3] not in box.text()
        assert "77" in box.text()
        assert all(error in box.detailedText() for error in errors)
        assert box.textFormat() == Qt.PlainText
        box.accept()
        return box.result()

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window._show_error_list("Conversion Failed", "Some files could not be converted", errors, warning=warning)
    assert len(seen) == 1
    assert errors[-1] in logs[0]["detail"]


def test_nested_audio_warning_uses_the_same_compact_details_pattern(window, monkeypatch):
    def inspect(box):
        assert box.text() == "The files could not be rendered."
        assert box.detailedText() == TRACEBACK
        assert box.textFormat() == Qt.PlainText
        return main_window.QMessageBox.Ok

    monkeypatch.setattr(main_window.QMessageBox, "exec", inspect)
    main_window.QMessageBox.warning(
        window, "Audio Render Failed", "The files could not be rendered.", detailed_text=TRACEBACK,
    )


def test_short_error_with_markup_characters_stays_literal(window, monkeypatch):
    def inspect(box):
        assert "<song> & filename.mid" in box.text()
        assert box.textFormat() == Qt.PlainText
        assert not box.detailedText()
        return main_window.QMessageBox.Ok

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window._show_operation_error("Save Failed", "The file could not be saved", "<song> & filename.mid", guidance="")
