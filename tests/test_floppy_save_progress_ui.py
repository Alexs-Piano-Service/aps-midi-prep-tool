"""File-save progress describes the active step without implying overall timing."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication, QProgressBar, QProgressDialog, QPushButton

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import translate_text
from test_progress_dialog_translations import _DialogHarness
from test_save_as_overwrite import window


class _ProgressHarness(_DialogHarness):
    _stabilize_progress_dialog_width = main_window.MidiTitleWindow._stabilize_progress_dialog_width
    _prepare_floppy_file_progress_dialog = main_window.MidiTitleWindow._prepare_floppy_file_progress_dialog
    _apply_stage_progress = main_window.MidiTitleWindow._apply_stage_progress

    def _scaled_int(self, value, minimum=0):
        return max(value, minimum)

    def _lt(self, source, **fields):
        return translate_text(source, self.code, **fields)


@pytest.fixture
def progress():
    app = QApplication.instance() or QApplication([])
    parent = _ProgressHarness("en")
    dialog = QProgressDialog("Saving files to floppy...", "Cancel", 0, 5, parent)
    parent._prepare_floppy_file_progress_dialog(dialog)
    dialog.show()
    app.processEvents()
    yield parent, dialog
    dialog.close()
    parent.close()
    parent.deleteLater()
    app.processEvents()


def test_file_progress_is_busy_until_measured_and_stays_open_between_steps(progress):
    parent, dialog = progress
    bar = dialog.findChild(QProgressBar)
    assert (dialog.minimum(), dialog.maximum()) == (0, 0)
    assert not dialog.autoReset() and not dialog.autoClose()

    parent._apply_stage_progress(dialog, 2, 5, "Copying files...\nFile 3 of 5: SONG.MID")
    assert dialog.value() == 2 and dialog.maximum() == 5
    assert bar.text() == "This step: 40%"
    parent._apply_stage_progress(dialog, 5, 5, "Copying files...")
    assert dialog.value() == 5 and dialog.isVisible()

    parent._apply_stage_progress(dialog, 0, 0, "Checking floppy contents...")
    assert (dialog.minimum(), dialog.maximum()) == (0, 0)
    assert dialog.isVisible()
    parent._apply_stage_progress(dialog, 0, 5, "Verifying files...\nFile 1 of 5: SONG.MID")
    assert dialog.maximum() == 5 and dialog.value() == 0
    assert bar.text() == "This step: 0%"


def test_file_progress_preserves_lines_and_literal_filenames_at_a_stable_width(progress):
    parent, dialog = progress
    label = dialog._aps_stable_progress_label
    width = dialog.width()
    literal = "Copying files...\nFile 1 of 2: <b>SONG</b> & {live}.MID"
    parent._apply_stage_progress(dialog, 0, 2, literal)
    assert dialog.labelText() == literal
    assert label.textFormat() == Qt.PlainText
    assert label.alignment() & Qt.AlignLeft

    long_message = "Copying files...\nFile 1 of 2: <b>" + "long-name-" * 80 + "</b> & {live}.MID"
    parent._apply_stage_progress(dialog, 0, 2, long_message)
    assert dialog.width() == width
    assert dialog.labelText().splitlines()[0] == "Copying files..."
    assert len(dialog.labelText().splitlines()) == 2
    assert dialog.labelText() != long_message
    tooltip = QTextDocument()
    tooltip.setHtml(label.toolTip())
    assert tooltip.toPlainText() == long_message
    parent._apply_stage_progress(dialog, 0, 0, "Checking floppy contents...")
    assert label.toolTip() == ""


def test_other_progress_dialogs_keep_their_existing_unknown_stage_behavior(progress):
    parent, _dialog = progress
    other = QProgressDialog("Working...", "Cancel", 0, 5, parent)
    other.setAutoReset(False)
    parent._apply_stage_progress(other, 4, 5, "Working...")
    parent._apply_stage_progress(other, 0, 0, "Waiting...")
    assert other.maximum() == 5 and other.value() == 4
    assert other.findChild(QProgressBar).format() == "%p%"
    other.close()


@pytest.mark.parametrize("operation,file_level", [("target", True), ("target", False), ("commit", True), ("commit", False)])
def test_file_save_entrypoints_use_step_progress_and_keep_cancellation_wired(window, monkeypatch, operation, file_level):
    window.image_session = SimpleNamespace(source_kind="floppy_usb" if file_level else "floppy_gw")
    monkeypatch.setattr(window, "_set_disk_write_busy", lambda _busy: None)
    worker_class = (main_window.DiskSessionWriteTargetWorker if operation == "target"
                    else main_window.DiskSessionCommitWorker)
    monkeypatch.setattr(worker_class, "start", lambda _worker: None)
    if operation == "target":
        window._start_write_image_to_floppy_worker("floppy_usb", "A:", "Test floppy", {}, file_level=file_level)
        worker, dialog = window.diskWriteTargetWorker, window.diskWriteTargetProgressDialog
    else:
        window._start_floppy_commit_worker({}, "Saving files to floppy...")
        worker, dialog = window.diskCommitWorker, window.diskCommitProgressDialog

    assert bool(getattr(dialog, "_aps_floppy_file_progress", False)) == file_level
    assert dialog.maximum() == (0 if file_level else 5)
    if file_level:
        worker.progressChanged.emit(1, 2, "Copying files...\nFile 2 of 2: SONG.MID")
        assert dialog.findChild(QProgressBar).text() == "This step: 50%"
        assert dialog.labelText().splitlines()[-1] == "File 2 of 2: SONG.MID"
    dialog.findChild(QPushButton).click()
    assert worker._cancel_requested()
    assert dialog.wasCanceled()
    assert dialog.labelText() == "Cancelling..."
    # Keep the window fixture's cleanup independent of this stub session.
    window.image_session = None
