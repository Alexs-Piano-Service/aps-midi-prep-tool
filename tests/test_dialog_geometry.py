"""Shared dialog centering must not fight interactive window resizing."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QRect, QSettings, QTimer, Qt
from PySide6.QtGui import QFont, QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QProgressDialog, QPushButton, QTextEdit, QVBoxLayout,
)

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.ui_utils import center_dialog_on_parent, scrollable_dialog_layout


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


@pytest.mark.parametrize("font_size", [9, 14, 18])
@pytest.mark.parametrize("screen_size,expected", [
    ((2560, 1440), (640, 380)),
    ((600, 400), (560, 340)),
])
def test_scrollable_dialog_defaults_fit_screen_without_font_scaling(
    window, monkeypatch, font_size, screen_size, expected,
):
    monkeypatch.setattr(
        QScreen, "availableGeometry", lambda _self: QRect(0, 0, *screen_size),
    )
    dialog = _Dialog(window)
    dialog.setFont(QFont(dialog.font().family(), font_size))
    layout, content = scrollable_dialog_layout(dialog, width=640, height=380)
    description = QLabel("Choose the files to prepare. " * 30)
    description.setWordWrap(True)
    content.addWidget(description)
    close = QPushButton("Close")
    layout.addWidget(close)

    def inspect():
        assert dialog.size().toTuple() == expected
        assert dialog.font().pointSize() == font_size
        assert dialog.rect().contains(close.geometry())
        assert dialog.adjustments == 0
        dialog.resize(740, 500)
        geometry = dialog.geometry()
        QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
        QTest.qWait(50)
        assert dialog.geometry() == geometry

    _run_dialog(window, dialog, inspect)


@pytest.fixture
def emulator_progress(window, tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.disk_session_worker import EmulatorImageBuildWorker
    from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY

    monkeypatch.setattr(main_window, "QProgressDialog", _ProgressDialog)
    # Keep the real launch, native dialog, signals, and worker ownership while
    # avoiding disk output and a background thread in this geometry test.
    monkeypatch.setattr(EmulatorImageBuildWorker, "start", lambda _worker: None)
    window._start_emulator_image_build(
        str(tmp_path / "songs"), str(tmp_path / "images"), prefix="DSKA", starting_number=0,
        safety_margin_bytes=0, album_title="", output_content="midi",
        disk_format=DISK_FORMAT_BY_KEY["ibm.1440"], output_ext="img",
        include_subfolders=False, shuffle=False, include_song_lists=False,
    )
    dialog, worker = window.emulatorImageProgressDialog, window.emulatorImageWorker
    try:
        yield dialog
    finally:
        window._close_emulator_image_progress()
        worker.finished.emit()


def test_emulator_launch_progress_is_centered_and_owned_by_main_window(window, emulator_progress):
    dialog = emulator_progress
    assert dialog.isVisible()
    assert dialog.parentWidget() is window
    assert dialog.windowModality() == Qt.WindowModal
    assert dialog.windowHandle().modality() == Qt.WindowModal
    assert dialog.windowHandle().transientParent() is window.windowHandle()
    _assert_centered(dialog, window)
    geometry, adjustments = dialog.geometry(), dialog.adjustments
    window._apply_stage_progress(dialog, 1, 10, "Building reviewed images")
    QTest.qWait(50)
    assert dialog.geometry() == geometry
    assert dialog.adjustments == adjustments


@pytest.mark.parametrize("review", ["preview", "overwrite"])
def test_emulator_review_reshows_progress_without_resetting_user_geometry(
        window, emulator_progress, monkeypatch, tmp_path, review):
    from test_emulator_preview import _preview

    progress = emulator_progress
    progress.resize(progress.width(), 220)
    progress.move(22, 31)
    QTest.qWait(50)
    geometry, adjustments = progress.geometry(), progress.adjustments
    execute = window._exec_child_dialog
    failures = []

    def accept_review(dialog, **kwargs):
        assert not progress.isVisible()

        def finish():
            try:
                assert dialog.isVisible()
                assert dialog.windowHandle().transientParent() is window.windowHandle()
                if review == "preview":
                    dialog.build_button.click()
                else:
                    dialog.button(main_window.QMessageBox.Yes).click()
            except BaseException as exc:
                failures.append(exc)
                dialog.reject()

        QTimer.singleShot(50, finish)
        return execute(dialog, **kwargs)

    monkeypatch.setattr(window, "_exec_child_dialog", accept_review)
    if review == "preview":
        window._on_emulator_preview_requested(_preview())
    else:
        window._on_emulator_overwrite_requested([str(tmp_path / "DSKA0000.img")])
    if failures:
        raise failures[0]
    QTest.qWait(50)
    assert progress.isVisible()
    assert progress.windowHandle().transientParent() is window.windowHandle()
    assert progress.windowModality() == Qt.WindowModal
    assert progress.geometry() == geometry
    assert progress.adjustments == adjustments


@pytest.mark.parametrize("parent_position", [(-400, -250), (500, 320)])
@pytest.mark.parametrize("screen_origin", [(0, 0), (-800, 200)])
def test_progress_initial_position_stays_on_small_screen_with_offscreen_parent(
        window, monkeypatch, parent_position, screen_origin):
    available = QRect(*screen_origin, 600, 400)
    monkeypatch.setattr(QScreen, "availableGeometry", lambda _self: available)
    window.move(*parent_position)
    QApplication.processEvents()
    progress = _ProgressDialog("Working", "Cancel", 0, 100, window)
    try:
        window._prepare_progress_dialog(progress)
        window._stabilize_progress_dialog_width(progress)
        window._show_centered_progress_dialog(progress)
        QTest.qWait(50)
        assert progress.isVisible()
        assert available.contains(progress.frameGeometry())
        assert progress.windowHandle().transientParent() is window.windowHandle()
    finally:
        progress.close()


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


def test_initial_show_size_is_centered_synchronously_and_only_once(window):
    class ShowSizedDialog(_Dialog):
        def showEvent(self, event):
            super().showEvent(event)
            if not getattr(self, "shown_once", False):
                # QMessageBox finalizes its size in showEvent; the difference
                # from its hidden size depends on the platform and font.
                self.resize(self.width() - 34, self.height() + 18)
                self.shown_once = True

    dialog = ShowSizedDialog(window)
    dialog.resize(420, 250)
    window._center_child_dialog(dialog)
    dialog.show()
    _assert_centered(dialog, window)
    assert dialog.adjustments == 0

    dialog.resize(540, 380)
    dialog.move(18, 27)
    geometry = dialog.geometry()
    dialog.hide()
    dialog.show()
    QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
    QTest.qWait(150)
    assert dialog.geometry() == geometry
    assert dialog.adjustments == 0


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


def test_explicit_dialog_owner_is_native_transient_parent_before_first_show(window):
    dialog = _Dialog()
    dialog.resize(360, 180)
    try:
        center_dialog_on_parent(dialog, window, adjust_size=False)
        assert not dialog.isVisible()
        assert dialog.parentWidget() is None
        assert dialog.windowHandle().transientParent() is window.windowHandle()
        dialog.show()
        QTest.qWait(50)
        _assert_centered(dialog, window)
        assert dialog.windowHandle().transientParent() is window.windowHandle()
        assert dialog.adjustments == 0
    finally:
        dialog.close()


@pytest.mark.parametrize("platform_name", ["wayland", "wayland-egl"])
def test_wayland_centering_uses_native_owner_without_manual_coordinates(window, monkeypatch, platform_name):
    class MoveObservedDialog(_Dialog):
        def __init__(self):
            super().__init__()
            self.manual_moves = []

        def move(self, *args):
            self.manual_moves.append(args)
            return super().move(*args)

    # The offscreen backend cannot emulate a compositor's placement. Exercise
    # the Wayland branch while checking the actual Qt transient relationship.
    monkeypatch.setattr(QApplication, "platformName", lambda: platform_name)
    dialog = MoveObservedDialog()
    dialog.resize(360, 180)
    try:
        center_dialog_on_parent(dialog, window, adjust_size=False)
        assert dialog.windowHandle().transientParent() is window.windowHandle()
        assert not dialog.isVisible()
        dialog.show()
        QTest.qWait(50)
        assert dialog.manual_moves == []
        assert dialog.windowHandle().transientParent() is window.windowHandle()
        dialog.hide()
        center_dialog_on_parent(dialog, window)
        dialog.show()
        QTest.qWait(50)
        assert dialog.manual_moves == []
        assert dialog.adjustments == 0
    finally:
        dialog.close()


@pytest.mark.parametrize("change", ["move", "resize"])
def test_pending_native_exposure_preserves_geometry_chosen_after_show(window, change):
    dialog = _Dialog(window)
    dialog.resize(360, 180)
    try:
        center_dialog_on_parent(dialog, window, adjust_size=False)
        dialog.show()
        # Choose geometry before pumping events that deliver the native Expose.
        # An initial frame correction must not undo this explicit user choice.
        if change == "move":
            dialog.move(18, 27)
        else:
            dialog.resize(500, 260)
        geometry = dialog.geometry()
        QTest.qWait(80)
        assert dialog.geometry() == geometry
        assert dialog.windowHandle().transientParent() is window.windowHandle()
        assert dialog.adjustments == 0
    finally:
        dialog.close()
