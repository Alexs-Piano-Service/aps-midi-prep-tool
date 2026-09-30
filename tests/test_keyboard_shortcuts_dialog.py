"""Shortcut editing stays usable while the live modal window is resized."""

from itertools import combinations

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, QTimer
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialogButtonBox, QKeySequenceEdit, QPushButton, QTableWidget,
)

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_window_menu_behavior import window


@pytest.fixture
def dialog_font(window):
    app = QApplication.instance()
    original = QFont(app.font())

    def set_size(size):
        font = QFont(window.font().family(), size)
        app.setFont(font)
        window.setFont(font)

    yield set_size
    app.setFont(original)


def _run_dialog(window, monkeypatch, exercise):
    execute = window._exec_child_dialog
    failures = []
    calls = []

    def inspect(dialog, **kwargs):
        assert kwargs.get("resize_to_contents") is False
        calls.append(dialog)

        def inspect_visible():
            try:
                exercise(dialog, len(calls))
            except BaseException as exc:
                failures.append(exc)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(100, inspect_visible)
        return execute(dialog, **kwargs)

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window.show_keyboard_shortcuts_dialog()
    if failures:
        raise failures[0]
    return calls


def _button(dialog, caption, language="en"):
    return next(
        button for button in dialog.findChildren(QPushButton)
        if button.text().replace("&", "") == translate_text(caption, language)
    )


def _dialog_rect(dialog, widget):
    return QRect(widget.mapTo(dialog, QPoint()), widget.size())


def _assert_controls_accessible(dialog, language="en"):
    table = dialog.findChild(QTableWidget, "keyboardShortcutsTable")
    box = dialog.findChild(QDialogButtonBox, "keyboardShortcutsButtons")
    assert table is not None and box is not None
    # A size-measurement editor must not linger above the header during exec().
    editors = [table.cellWidget(row, 2) for row in range(table.rowCount())]
    assert set(dialog.findChildren(QKeySequenceEdit)) == set(editors)
    assert all(editor.parentWidget() is table.viewport() for editor in editors)
    assert dialog.rect().contains(_dialog_rect(dialog, table))
    assert table.columnWidth(1) >= 100
    assert table.horizontalScrollBar().maximum() == 0
    buttons = [
        box.button(QDialogButtonBox.Ok), box.button(QDialogButtonBox.Cancel),
        _button(dialog, "Restore Defaults", language),
        _button(dialog, "Clear Selected", language),
    ]
    for button in buttons:
        assert button.isVisible()
        assert button.width() >= button.sizeHint().width()
        bounds = _dialog_rect(dialog, button)
        assert dialog.rect().contains(bounds)
        assert bounds.top() > _dialog_rect(dialog, table).bottom()
    for first, second in combinations(buttons, 2):
        assert not _dialog_rect(dialog, first).intersects(_dialog_rect(dialog, second))
    assert table.verticalScrollBar().maximum() > 0
    for row in (0, table.rowCount() - 1):
        table.scrollToItem(table.item(row, 1), QTableWidget.PositionAtCenter)
        QTest.qWait(20)
        editor = table.cellWidget(row, 2)
        assert table.viewport().rect().contains(editor.geometry())
        assert editor.height() >= editor.minimumSizeHint().height()
    return table, box


def _editors(window, table):
    return {
        spec["id"]: table.cellWidget(row, 2)
        for row, spec in enumerate(window._keyboard_shortcut_specs())
    }


@pytest.mark.parametrize("font_size", [9, 14])
def test_live_shortcuts_resize_and_edit_without_moving_or_saving_cancelled_changes(
    window, monkeypatch, dialog_font, font_size,
):
    dialog_font(font_size)
    window.settings.setValue("keyboard_shortcuts/file.save", "Ctrl+Alt+9")
    window._setup_keyboard_shortcuts()

    def exercise(dialog, _call):
        assert dialog.font().pointSize() == font_size
        available = dialog.screen().availableGeometry()
        assert dialog.width() <= available.width()
        assert dialog.height() <= available.height()
        table = dialog.findChild(QTableWidget, "keyboardShortcutsTable")
        editors = _editors(window, table)
        save_row = next(
            row for row, spec in enumerate(window._keyboard_shortcut_specs())
            if spec["id"] == "file.save"
        )
        for size in ((1150, 760), (700, 480), (950, 650)):
            dialog.resize(*size)
            dialog.move(20, 30)
            QTest.qWait(130)
            assert dialog.size().toTuple() == size
            assert dialog.pos().toTuple() == (20, 30)
            geometry = dialog.geometry()
            table, box = _assert_controls_accessible(dialog)
            table.selectRow(save_row)
            editors["file.save"].setKeySequence(QKeySequence("Ctrl+Alt+Shift+9"))
            QTest.qWait(130)
            assert dialog.geometry() == geometry
            _button(dialog, "Clear Selected").click()
            assert editors["file.save"].keySequence().isEmpty()
            _button(dialog, "Restore Defaults").click()
            for spec in window._keyboard_shortcut_specs():
                assert editors[spec["id"]].keySequence() == QKeySequence(spec["default"])
            QTest.qWait(130)
            _assert_controls_accessible(dialog)
            assert dialog.geometry() == geometry
        box.button(QDialogButtonBox.Cancel).click()

    assert len(_run_dialog(window, monkeypatch, exercise)) == 1
    assert window.settings.value("keyboard_shortcuts/file.save") == "Ctrl+Alt+9"
    assert window.keyboardShortcutObjects["file.save"].key().toString() == "Ctrl+Alt+9"


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_translated_shortcuts_stay_accessible_at_large_font_and_narrow_width(
    window, monkeypatch, dialog_font, language,
):
    window.currentLanguage = language
    dialog_font(14)

    def exercise(dialog, _call):
        assert dialog.windowTitle() == translate_text("Keyboard Shortcuts", language)
        for size in ((700, 480), (950, 650)):
            dialog.resize(*size)
            dialog.move(20, 30)
            QTest.qWait(130)
            assert dialog.size().toTuple() == size
            geometry = dialog.geometry()
            _assert_controls_accessible(dialog, language)
            assert dialog.geometry() == geometry

    assert len(_run_dialog(window, monkeypatch, exercise)) == 1


def test_shortcut_columns_reserve_command_space_for_wide_native_editors(
    window, monkeypatch, dialog_font,
):
    # Windows' offscreen font metrics can make the category and editor much
    # wider than on Linux, even at the same point size. Supply a large native
    # editor hint so every platform exercises the narrow-column fallback.
    class WideEditor(QKeySequenceEdit):
        def sizeHint(self):
            hint = super().sizeHint()
            hint.setWidth(450)
            return hint

    monkeypatch.setattr(main_window, "QKeySequenceEdit", WideEditor)
    window.currentLanguage = "es"
    dialog_font(14)

    def exercise(dialog, _call):
        table = dialog.findChild(QTableWidget, "keyboardShortcutsTable")
        # The expanded window must accommodate the platform's full category
        # hint as well as the editor and the command column.
        wide_width = max(
            950,
            table.sizeHintForColumn(0) + table.columnWidth(2)
            + max(100, table.horizontalHeader().minimumSectionSize())
            + dialog.width() - table.viewport().width(),
        )
        for size in ((700, 480), (wide_width, 650), (700, 480)):
            dialog.resize(*size)
            QTest.qWait(130)
            assert dialog.size().toTuple() == size
            table, _box = _assert_controls_accessible(dialog, "es")
            assert table.columnWidth(2) >= 450
            if size[0] == wide_width:
                assert table.columnWidth(0) >= table.sizeHintForColumn(0)

    assert len(_run_dialog(window, monkeypatch, exercise)) == 1


def test_shortcut_dialog_saves_accepted_edits_and_clears_selected_command(window, monkeypatch):
    def exercise(dialog, _call):
        table = dialog.findChild(QTableWidget, "keyboardShortcutsTable")
        editors = _editors(window, table)
        editors["file.save"].setKeySequence(QKeySequence("Ctrl+Alt+9"))
        quit_row = next(
            row for row, spec in enumerate(window._keyboard_shortcut_specs())
            if spec["id"] == "file.quit"
        )
        table.selectRow(quit_row)
        _button(dialog, "Clear Selected").click()
        assert editors["file.quit"].keySequence().isEmpty()
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    assert len(_run_dialog(window, monkeypatch, exercise)) == 1
    restored = QSettings(window.settings.fileName(), QSettings.IniFormat)
    assert restored.value("keyboard_shortcuts/file.save") == "Ctrl+Alt+9"
    assert restored.value("keyboard_shortcuts/file.quit") == ""
    assert window.keyboardShortcutObjects["file.save"].key().toString() == "Ctrl+Alt+9"
    assert "file.quit" not in window.keyboardShortcutObjects


def test_shortcut_dialog_rejects_duplicates_without_saving_and_retains_edits(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args))

    def exercise(dialog, call):
        assert call == 1
        table = dialog.findChild(QTableWidget, "keyboardShortcutsTable")
        editors = _editors(window, table)
        dialog.resize(950, 650)
        dialog.move(20, 30)
        QTest.qWait(130)
        geometry = dialog.geometry()
        editors["file.save"].setKeySequence(QKeySequence("Ctrl+O"))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        QTest.qWait(130)
        assert dialog.isVisible()
        assert dialog.geometry() == geometry
        assert editors["file.save"].keySequence() == QKeySequence("Ctrl+O")
        assert not window.settings.contains("keyboard_shortcuts/file.save")
        assert window.keyboardShortcutObjects["file.save"].key().toString() == "Ctrl+S"
        _assert_controls_accessible(dialog)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()

    calls = _run_dialog(window, monkeypatch, exercise)
    assert len(calls) == 1
    assert len(warnings) == 1
    assert warnings[0][1] == "Duplicate Shortcut"
    assert "Ctrl+O" in warnings[0][2]
    assert not window.settings.contains("keyboard_shortcuts/file.save")
