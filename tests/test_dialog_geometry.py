"""Shared dialog centering must not fight interactive window resizing."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QProgressDialog, QPushButton, QTextEdit, QVBoxLayout,
)

from aps_midi_prep_tool_app import main_window


class _CountAdjustments:
    adjustments = 0

    def adjustSize(self):
        self.adjustments += 1
        super().adjustSize()


class _Dialog(_CountAdjustments, QDialog):
    pass


class _MessageBox(_CountAdjustments, main_window.QMessageBox):
    pass


class _ProgressDialog(_CountAdjustments, QProgressDialog):
    pass


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "geometry.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    parent = main_window.MidiTitleWindow()
    parent.resize(680, 480)
    parent.move(30, 40)
    parent.show()
    app.processEvents()
    yield parent
    parent.hide()
    parent.deleteLater()
    app.processEvents()


def _run_dialog(window, dialog, inspect, *, direct=False):
    errors = []

    def check():
        try:
            inspect()
        except BaseException as exc:
            errors.append(exc)
        finally:
            dialog.reject()

    QTimer.singleShot(150, check)
    if direct:
        window._center_child_dialog(dialog)
        dialog.exec()
    else:
        window._exec_child_dialog(dialog)
    if errors:
        raise errors[0]


def _form(window):
    dialog = _Dialog(window)
    layout = QVBoxLayout(dialog)
    label = QLabel("Choose the files to prepare.")
    label.setWordWrap(True)
    layout.addWidget(label)
    layout.addWidget(QPushButton("Close"))
    return dialog, label


def _assert_centered(dialog, parent):
    distance = dialog.frameGeometry().center() - parent.frameGeometry().center()
    assert abs(distance.x()) <= 2
    assert abs(distance.y()) <= 2


@pytest.mark.parametrize("direct", [False, True], ids=["exec", "modeless-helper"])
def test_explicit_size_and_user_geometry_survive_layout_changes(window, direct):
    dialog, label = _form(window)
    dialog.resize(620, 430)

    def inspect():
        assert dialog.size().toTuple() == (620, 430)
        assert dialog.adjustments == 0
        for width, height in ((540, 380), (700, 460)):
            dialog.resize(width, height)
            dialog.move(18, 27)
            geometry = dialog.geometry()
            label.setText("Choose the songs to prepare and save.")
            QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
            QTest.qWait(150)
            assert dialog.geometry() == geometry
            assert dialog.adjustments == 0

    _run_dialog(window, dialog, inspect, direct=direct)


@pytest.mark.parametrize("direct", [False, True], ids=["exec", "modeless-helper"])
def test_unsized_dialog_fits_once_and_then_preserves_user_geometry(window, direct):
    dialog, label = _form(window)

    def inspect():
        assert dialog.adjustments == 1
        assert dialog.height() < 300
        assert dialog.rect().contains(label.geometry())
        dialog.resize(580, 390)
        dialog.move(22, 31)
        geometry = dialog.geometry()
        QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
        QTest.qWait(150)
        assert dialog.geometry() == geometry
        assert dialog.adjustments == 1

    _run_dialog(window, dialog, inspect, direct=direct)


def test_message_details_resize_without_moving_the_users_window_or_refitting(window):
    dialog = _MessageBox(window)
    dialog.setText("Some files could not be prepared.")
    dialog.setDetailedText("File could not be read.\n" * 20)
    dialog.setStandardButtons(main_window.QMessageBox.Ok)

    def inspect():
        collapsed_size = dialog.size()
        initial_adjustments = dialog.adjustments
        _assert_centered(dialog, window)
        dialog.move(18, 27)
        position = dialog.pos()
        details_button = next(
            button for button in dialog.findChildren(QPushButton)
            if "Details" in button.text()
        )
        details_button.click()
        QTest.qWait(150)
        assert dialog.height() > collapsed_size.height()
        assert dialog.findChild(QTextEdit).isVisible()
        assert dialog.pos() == position
        details_button.click()
        QTest.qWait(150)
        assert dialog.size() == collapsed_size
        assert dialog.pos() == position
        assert dialog.adjustments == initial_adjustments

    _run_dialog(window, dialog, inspect)


def test_progress_label_can_grow_without_moving_the_users_window_or_refitting(window):
    dialog = _ProgressDialog("Working", "Cancel", 0, 100, window)
    window._prepare_progress_dialog(dialog)

    def inspect():
        initial_width = dialog.width()
        initial_adjustments = dialog.adjustments
        _assert_centered(dialog, window)
        dialog.move(18, 27)
        position = dialog.pos()
        dialog.setLabelText("Preparing song number 123 from the selected disk image folder.")
        QTest.qWait(150)
        assert dialog.width() > initial_width
        assert dialog.pos() == position
        geometry = dialog.geometry()
        QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
        QTest.qWait(150)
        assert dialog.geometry() == geometry
        assert dialog.adjustments == initial_adjustments

    _run_dialog(window, dialog, inspect)


def test_progress_stages_and_resizing_preserve_user_geometry(window):
    dialog = _ProgressDialog("Working", "Cancel", 0, 20, window)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    window._prepare_progress_dialog(dialog)

    def inspect():
        _assert_centered(dialog, window)
        initial_adjustments = dialog.adjustments
        dialog.move(18, 27)
        geometry = dialog.geometry()
        # Early progress updates used to queue four extra centering passes.
        for step in range(1, 6):
            window._apply_stage_progress(dialog, step, 20, "Working")
            QTest.qWait(50)
            assert dialog.value() == step
            assert dialog.geometry() == geometry

        for size in ((360, 180), (620, 280), (400, 200)):
            dialog.resize(*size)
            dialog.move(22, 31)
            geometry = dialog.geometry()
            QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
            QTest.qWait(150)
            assert dialog.size().toTuple() == size
            assert dialog.geometry() == geometry
            # Re-showing an existing progress window must also respect it.
            window._center_child_dialog(dialog)
            window._show_centered_progress_dialog(dialog)
            window._apply_stage_progress(dialog, 6, 20, "Working")
            QTest.qWait(150)
            assert dialog.geometry() == geometry
            assert dialog.adjustments == initial_adjustments

    _run_dialog(window, dialog, inspect)
