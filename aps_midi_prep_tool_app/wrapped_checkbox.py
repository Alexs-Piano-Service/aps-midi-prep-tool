"""A native checkbox with a caption that wraps with the available width."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QStyle, QStyleOptionButton, QStylePainter,
)


class WrappedCheckBox(QCheckBox):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.caption = QLabel(text, self)
        self.caption.setTextFormat(Qt.PlainText)
        self.caption.setWordWrap(True)
        self.caption.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.caption.setBuddy(self)
        layout = QHBoxLayout(self)
        inset = (self.style().pixelMetric(QStyle.PM_IndicatorWidth)
                 + self.style().pixelMetric(QStyle.PM_CheckBoxLabelSpacing))
        layout.setContentsMargins(inset, 0, 0, 0)
        layout.addWidget(self.caption)
        self.setMinimumHeight(self.style().pixelMetric(QStyle.PM_IndicatorHeight))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def setText(self, text):
        super().setText(text)
        if hasattr(self, "caption"):
            self.caption.setText(text)

    def sizeHint(self):
        return self.layout().sizeHint() if self.layout() else super().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize() if self.layout() else super().minimumSizeHint()

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, _event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        # The child label paints the wrapped caption. Keep the native indicator,
        # checked/disabled state, keyboard behavior, and accessible button text.
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_CheckBox, option)
