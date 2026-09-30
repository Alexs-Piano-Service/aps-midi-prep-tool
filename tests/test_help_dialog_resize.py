"""Help forms remain usable while a live modal window is resized and moved."""

from itertools import combinations

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialogButtonBox, QLabel, QLineEdit,
    QPlainTextEdit, QScrollArea,
)

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from test_window_menu_behavior import window


HELP_DIALOGS = (
    "show_about_dialog", "show_disclaimer_dialog",
    "show_bug_report_dialog", "show_feedback_dialog",
)


@pytest.fixture
def dialog_font(window):
    app = QApplication.instance()
    original = QFont(app.font())

    def set_size(size, *, family=None):
        font = QFont(family or window.font().family(), size)
        app.setFont(font)
        window.setFont(font)

    yield set_size
    app.setFont(original)


def _run_dialog(window, monkeypatch, method, exercise, **values):
    execute = window._exec_child_dialog
    failures = []
    dialogs = []

    def inspect(dialog, **kwargs):
        dialogs.append(dialog)

        def inspect_visible():
            try:
                exercise(dialog)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(130, inspect_visible)
        return execute(dialog, **kwargs)

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    getattr(window, method)(**values)
    if failures:
        raise failures[0]
    assert len(dialogs) == 1


def _rect_in(dialog, widget):
    return QRect(widget.mapTo(dialog, QPoint()), widget.size())


def _assert_accessible(dialog):
    button_box = dialog.findChild(QDialogButtonBox)
    assert button_box is not None
    box_bounds = _rect_in(dialog, button_box)
    assert dialog.rect().contains(box_bounds)
    buttons = button_box.buttons()
    for button in buttons:
        assert button.isVisible()
        assert button.width() >= button.sizeHint().width()
        assert dialog.rect().contains(_rect_in(dialog, button))
    for first, second in combinations(buttons, 2):
        assert not _rect_in(dialog, first).intersects(_rect_in(dialog, second))

    for scroll in dialog.findChildren(QScrollArea):
        assert dialog.rect().contains(_rect_in(dialog, scroll))
        assert _rect_in(dialog, scroll).bottom() < box_bounds.top()
        assert scroll.horizontalScrollBar().maximum() == 0
        button_rects = [_rect_in(dialog, button) for button in buttons]
        bar = scroll.verticalScrollBar()
        for value in (bar.maximum(), 0):
            bar.setValue(value)
            QTest.qWait(10)
            assert [_rect_in(dialog, button) for button in buttons] == button_rects

    for label in dialog.findChildren(QLabel):
        if label.isVisible() and label.wordWrap():
            assert label.height() >= label.heightForWidth(label.width()), label.text()
    return button_box


def _resize(dialog, size):
    dialog.resize(*size)
    dialog.move(20, 30)
    QTest.qWait(130)
    assert dialog.size().toTuple() == size
    assert dialog.pos().toTuple() == (20, 30)
    geometry = dialog.geometry()
    QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
    QTest.qWait(130)
    assert dialog.geometry() == geometry
    _assert_accessible(dialog)


@pytest.mark.parametrize("method", HELP_DIALOGS)
@pytest.mark.parametrize("font_size", [9, 14])
def test_help_dialogs_keep_user_geometry_and_accessible_controls(
    window, monkeypatch, dialog_font, method, font_size,
):
    dialog_font(font_size)

    def exercise(dialog):
        available = dialog.screen().availableGeometry()
        assert dialog.width() <= available.width()
        assert dialog.height() <= available.height()
        for size in ((1150, 800), (700, 480), (950, 650)):
            _resize(dialog, size)
        normal_geometry = dialog.geometry()
        dialog.showMaximized()
        QTest.qWait(130)
        assert dialog.isMaximized()
        assert dialog.normalGeometry() == normal_geometry
        _assert_accessible(dialog)
        dialog.showNormal()
        QTest.qWait(130)
        assert not dialog.isMaximized()
        # The offscreen plugin retains the maximized bounds on showNormal();
        # actual window-manager restoration requires a native platform test.
        if QApplication.platformName() != "offscreen":
            assert dialog.geometry() == normal_geometry
        _assert_accessible(dialog)
        _resize(dialog, normal_geometry.size().toTuple())

    _run_dialog(window, monkeypatch, method, exercise)


@pytest.mark.parametrize("method", HELP_DIALOGS)
@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_translated_help_dialogs_fit_narrow_windows_at_large_font(
    window, monkeypatch, dialog_font, method, language,
):
    window.currentLanguage = language
    dialog_font(14)

    def exercise(dialog):
        for size in ((700, 480), (950, 650)):
            _resize(dialog, size)

    _run_dialog(window, monkeypatch, method, exercise)


def test_bug_report_long_questions_remain_fully_visible_with_wide_font(
    window, monkeypatch, dialog_font,
):
    window.currentLanguage = "fr"
    dialog_font(14, family="DejaVu Sans")

    def exercise(dialog):
        for size in ((700, 480), (950, 650)):
            _resize(dialog, size)

    _run_dialog(window, monkeypatch, "show_bug_report_dialog", exercise)


@pytest.mark.parametrize("method", ["show_bug_report_dialog", "show_feedback_dialog"])
def test_report_forms_preserve_edits_on_resize_and_cancel_without_sending(
    window, monkeypatch, method,
):
    sent = []
    monkeypatch.setattr(window, "_submit_bug_report", lambda payload: sent.append(payload))
    monkeypatch.setattr(window, "_submit_feedback", lambda payload: sent.append(payload))

    def exercise(dialog):
        summary = next(edit for edit in dialog.findChildren(QLineEdit) if edit.text() == "Original summary")
        contact = next(edit for edit in dialog.findChildren(QLineEdit) if edit.text() == "reply@example.invalid")
        details = dialog.findChild(QPlainTextEdit)
        logs = next(check for check in dialog.findChildren(QCheckBox) if "console logs" in check.text())
        summary.setText("Edited summary")
        details.setPlainText("Edited details\nwith another line")
        contact.setText("new@example.invalid")
        logs.setChecked(not logs.isChecked())
        checked = logs.isChecked()
        for size in ((1100, 800), (700, 480)):
            _resize(dialog, size)
            assert summary.text() == "Edited summary"
            assert details.toPlainText() == "Edited details\nwith another line"
            assert contact.text() == "new@example.invalid"
            assert logs.isChecked() == checked
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()

    _run_dialog(
        window, monkeypatch, method, exercise,
        summary="Original summary", description="Original details", contact="reply@example.invalid",
    )
    assert sent == []


@pytest.mark.parametrize("method", ["show_bug_report_dialog", "show_feedback_dialog"])
def test_empty_report_validation_keeps_dialog_and_chosen_geometry(
    window, monkeypatch, method,
):
    warnings = []
    sent = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(window, "_submit_bug_report", lambda payload: sent.append(payload))
    monkeypatch.setattr(window, "_submit_feedback", lambda payload: sent.append(payload))

    def exercise(dialog):
        _resize(dialog, (700, 480))
        geometry = dialog.geometry()
        box = dialog.findChild(QDialogButtonBox)
        send_button = next(button for button in box.buttons() if box.buttonRole(button) == QDialogButtonBox.AcceptRole)
        send_button.click()
        QTest.qWait(130)
        assert dialog.isVisible()
        assert dialog.geometry() == geometry
        _assert_accessible(dialog)
        box.button(QDialogButtonBox.Cancel).click()

    _run_dialog(window, monkeypatch, method, exercise)
    assert len(warnings) == 1
    assert sent == []
