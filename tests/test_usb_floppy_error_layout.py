"""Long floppy errors remain readable after a progress dialog is shown."""

import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QProgressBar, QProgressDialog, QPushButton

from aps_midi_prep_tool_app.floppy_image import USB_FLOPPY_720K_READ_GUIDANCE
from aps_midi_prep_tool_app.ui_utils import WrappedProgressLabel
from test_progress_dialog_translations import _DialogHarness


def _check_progress_layout(points, direct_update):
    app = QApplication.instance() or QApplication([])
    parent = _DialogHarness("en")
    dialog = QProgressDialog("Reading floppy...", "Cancel", 0, 100, parent)
    font = QFont(dialog.font())
    font.setPointSize(points)
    dialog.setFont(font)
    parent._prepare_progress_dialog(dialog)
    dialog.show()
    app.processEvents()
    initial_height = dialog.height()
    message = (
        "Fast floppy read unavailable: The disk could not be read well enough to determine its format.\n\n"
        "Details: Windows refused <disk> & sector data.\n\n"
        + USB_FLOPPY_720K_READ_GUIDANCE
        + "\n\nReading full floppy image from A:..."
    )
    try:
        update = dialog.setLabelText if direct_update else lambda text: parent._set_progress_dialog_message(dialog, text)
        update(message)
        app.processEvents()
        label = dialog.findChild(WrappedProgressLabel)
        assert dialog.labelText() == message
        assert label.wordWrap() and label.textFormat() == Qt.PlainText
        assert dialog.height() > initial_height
        assert label.height() >= label.heightForWidth(label.width())
        assert dialog.width() <= dialog.screen().availableGeometry().width() - 40
        assert dialog.height() <= dialog.screen().availableGeometry().height()
        bar = dialog.findChild(QProgressBar)
        cancel = dialog.findChild(QPushButton)
        assert label.geometry().bottom() < bar.geometry().top()
        assert bar.geometry().bottom() < cancel.geometry().top()
        assert dialog.rect().contains(cancel.geometry())
        assert cancel.isVisible()

        # A device path without spaces must not widen the window off screen.
        width = dialog.width()
        update(message + "\nDevice: " + "disk-path-" * 15)
        app.processEvents()
        assert dialog.width() == width
        assert label.height() >= label.heightForWidth(label.width())
        dialog.setLabelText("Cancelling disk read...")
        app.processEvents()
        assert dialog.labelText() == "Cancelling disk read..."
    finally:
        dialog.close()
        parent.close()
        parent.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("points", [9, 14, 18])
@pytest.mark.parametrize("direct_update", [False, True])
def test_progress_error_wraps_and_grows_at_larger_fonts(points, direct_update):
    _check_progress_layout(points, direct_update)


def test_progress_error_at_200_percent_display_scaling():
    # Qt reads its display scaling at startup, so this needs a fresh process.
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": "2"}
    script = (
        "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
        "runpy.run_path(sys.argv[2])['_check_progress_layout'](9, True)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent), __file__],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
