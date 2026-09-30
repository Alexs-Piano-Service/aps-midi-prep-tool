"""Native button styles may reflow without constraining the user's window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from itertools import combinations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QProxyStyle, QStyle, QTextEdit, QVBoxLayout,
)

from aps_midi_prep_tool_app.responsive_button_box import ResponsiveDialogButtonBox


@pytest.fixture
def application():
    app = QApplication.instance() or QApplication([])
    original_style = app.style().objectName()
    yield app
    app.setStyle(original_style)


def _assert_buttons_fit(box):
    buttons = box.buttons()
    for button in buttons:
        assert button.isVisible()
        assert button.width() >= button.sizeHint().width()
        assert button.height() >= button.sizeHint().height()
        assert box.rect().contains(button.geometry())
    for first, second in combinations(buttons, 2):
        assert not first.geometry().intersects(second.geometry())


@pytest.mark.parametrize("style", ["Fusion", "Windows"])
@pytest.mark.parametrize("font_size", [14, 28])
def test_footer_reflows_full_size_buttons_after_repeated_narrowing(application, style, font_size):
    application.setStyle(style)
    dialog = QDialog()
    dialog.setFont(QFont(dialog.font().family(), font_size))
    layout = QVBoxLayout(dialog)
    body = QTextEdit()
    layout.addWidget(body, 1)
    box = ResponsiveDialogButtonBox(QDialogButtonBox.Close, dialog)
    box.addButton("Discard selected changes", QDialogButtonBox.ActionRole)
    box.addButton("Undo last change", QDialogButtonBox.ActionRole)
    # Reproduce large native caption widths on every test platform.
    for button in box.buttons():
        button.setMinimumWidth(300)
    box.setContentsMargins(7, 8, 13, 4)
    layout.addWidget(box)
    dialog.ensurePolished()
    margins = layout.contentsMargins()
    row_width = box.sizeHint().width() + margins.left() + margins.right()
    narrow_width = row_width - box.minimumSizeHint().width()
    dialog.show()
    try:
        # Native font/DPI metrics can make the captions wider than their
        # 300-pixel minimum; size the dialog around the actual row threshold.
        for width, orientation in (
            (row_width + 200, Qt.Horizontal),
            (narrow_width, Qt.Vertical),
            (row_width, Qt.Horizontal),
            (narrow_width, Qt.Vertical),
        ):
            dialog.resize(width, 500)
            dialog.move(20, 30)
            QTest.qWait(20)
            assert dialog.size().toTuple() == (width, 500)
            assert dialog.pos().toTuple() == (20, 30)
            assert box.orientation() == orientation
            assert body.geometry().bottom() < box.geometry().top()
            assert dialog.rect().contains(box.geometry())
            _assert_buttons_fit(box)
    finally:
        dialog.close()


class _UnevenSpacingStyle(QProxyStyle):
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PM_LayoutHorizontalSpacing:
            return 28
        if metric == QStyle.PM_LayoutVerticalSpacing:
            return 2
        return super().pixelMetric(metric, option, widget)


@pytest.mark.parametrize("font_size", [9, 28])
def test_native_spacing_changes_do_not_oscillate_the_footer(application, font_size):
    application.setStyle(_UnevenSpacingStyle("Fusion"))
    dialog = QDialog()
    dialog.setFont(QFont(dialog.font().family(), font_size))
    layout = QVBoxLayout(dialog)
    layout.addWidget(QTextEdit(), 1)
    box = ResponsiveDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Close, dialog,
    )
    layout.addWidget(box)
    dialog.ensurePolished()
    # Each button fits, but the full row needs more horizontal spacing than
    # this width allows. A vertical style's smaller gap must not undo stacking.
    button_width = max(
        button.sizeHint().expandedTo(button.minimumSizeHint()).width()
        for button in box.buttons()
    )
    for button in box.buttons():
        button.setFixedWidth(button_width)
    margins = layout.contentsMargins() + box.contentsMargins() + box.layout().contentsMargins()
    # A 15-pixel gap fits between the style's 2- and 28-pixel gaps, regardless
    # of how wide the platform's native buttons are.
    width = button_width * len(box.buttons()) + 15 * (len(box.buttons()) - 1)
    dialog.resize(width + margins.left() + margins.right(), 400)
    dialog.show()
    try:
        QTest.qWait(20)
        geometry = dialog.geometry()
        footer_geometry = box.geometry()
        for _ in range(5):
            application.processEvents()
            assert dialog.geometry() == geometry
            assert box.geometry() == footer_geometry
            assert box.orientation() == Qt.Vertical
            assert box.layout().spacing() == 28
            _assert_buttons_fit(box)
    finally:
        dialog.close()
