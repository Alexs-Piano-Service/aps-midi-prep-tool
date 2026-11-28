"""Fixed-size, palette-aware progress illustrations for disk acquisition."""

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QVariantAnimation, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QLabel, QWidget

from .ui_utils import FixedProgressLabel


class DiskReadStages(QWidget):
    PHASES = ("inspect", "read", "organize")
    TITLES = ("Inspecting Disk", "Reading Disk", "Organizing Files")

    def __init__(self, parent, translate):
        super().__init__(parent)
        self.active_phase = "inspect"
        self.completed = False
        self.stopped = False
        self._pulse = 0.0
        self._unit = max(12, self.fontMetrics().lineSpacing())
        self._icon_size = self._unit * 2.8
        self._top_padding = round(self._unit * .75)
        self._caption_top = self._top_padding + round(self._icon_size + self._unit * .7)
        self.setFixedHeight(self._caption_top + self._unit * 2)
        self.captions = []
        for title in self.TITLES:
            label = QLabel(translate(title), self)
            label.setTextFormat(Qt.PlainText)
            label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            label.setWordWrap(True)
            self.captions.append(label)
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(2600)
        self.animation.setLoopCount(-1)
        self.animation.valueChanged.connect(self._animate)
        self._update_accessibility()

    def _animate(self, value):
        self._pulse = (1 - math.cos(float(value) * 2 * math.pi)) / 2
        self.update()

    @Slot(str)
    def set_phase(self, phase):
        if self.completed or self.stopped or phase not in self.PHASES:
            return
        # A counter reset or a late event cannot unfinish a completed phase.
        if self.PHASES.index(phase) < self.PHASES.index(self.active_phase):
            return
        self.active_phase = phase
        self._update_accessibility()
        self.update()

    @Slot()
    def complete(self):
        if self.stopped:
            return
        self.completed = True
        self.active_phase = "organize"
        self.animation.stop()
        self._update_accessibility()
        self.update()

    @Slot()
    def stop(self):
        self.stopped = True
        self.animation.stop()
        self._pulse = 0.0
        self.update()

    def _update_accessibility(self):
        # The captions stay readable regardless of animation or color vision.
        current = self.PHASES.index(self.active_phase)
        for index, label in enumerate(self.captions):
            label.setEnabled(self.completed or index <= current)
        self.setAccessibleName(" · ".join(label.text() for label in self.captions))
        self.setAccessibleDescription(self.captions[current].text())

    def showEvent(self, event):
        super().showEvent(event)
        if not self.completed and not self.stopped:
            self.animation.start()

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Close and getattr(watched, "_aps_read_result_pending", False):
            # The hardware worker has handed off its result. Closing here
            # cannot cancel the local table update already being applied.
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cell = self.width() / 3
        top = self._caption_top
        for index, label in enumerate(self.captions):
            left = round(index * cell + 4)
            right = round((index + 1) * cell - 4)
            label.setGeometry(left, top, right - left, self.height() - top)

    @staticmethod
    def _color(color, alpha):
        color = QColor(color)
        color.setAlphaF(alpha)
        return color

    @staticmethod
    def _luminance(color):
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))

    @classmethod
    def _illustration_color(cls, palette):
        # Selection backgrounds can be too pale in light themes or too dim in
        # dark themes to use for thin strokes. Keep their hue, with contrast.
        accent = palette.color(QPalette.Highlight)
        background = cls._luminance(palette.color(QPalette.Window))
        hue, saturation, lightness, alpha = accent.getHslF()
        for _ in range(30):
            foreground = cls._luminance(accent)
            if (max(foreground, background) + .05) / (min(foreground, background) + .05) >= 4.5:
                break
            lightness = max(0.0, min(1.0, lightness + (.035 if background < .18 else -.035)))
            accent.setHslF(hue, saturation, lightness, alpha)
        return accent

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        accent = self._illustration_color(palette)
        muted = palette.color(QPalette.Disabled, QPalette.WindowText)
        cell = self.width() / 3
        size = self._icon_size
        center_y = self._top_padding + size / 2 + self._unit * .25
        current = self.PHASES.index(self.active_phase)
        for index in range(2):
            color = accent if self.completed or index < current else muted
            painter.setPen(QPen(self._color(color, .35), 1.5))
            painter.drawLine(QPointF(cell * (index + .5) + size * .7, center_y),
                             QPointF(cell * (index + 1.5) - size * .7, center_y))
        for index in range(3):
            done = self.completed or index < current
            active = not self.completed and index == current
            color = accent if done or active else muted
            painter.save()
            painter.translate(cell * (index + .5), center_y)
            painter.scale(size / 48, size / 48)
            painter.setPen(Qt.NoPen)
            if active:
                painter.setBrush(self._color(accent, .06 + .12 * self._pulse))
                painter.drawEllipse(QRectF(-27, -27, 54, 54))
            painter.setBrush(self._color(color, .14 if done or active else .06))
            painter.drawEllipse(QRectF(-23, -23, 46, 46))
            opacity = 1.0 if done else .65 + .35 * self._pulse if active else .55
            painter.setPen(QPen(self._color(color, opacity), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            if index == 0:
                painter.drawRoundedRect(QRectF(-12, -13, 21, 24), 2, 2)
                painter.drawRect(QRectF(-7, -13, 11, 7))
                painter.drawLine(QPointF(-7, 4), QPointF(-1, 4))
                painter.drawEllipse(QRectF(0, -1, 12, 12))
                painter.drawLine(QPointF(10, 9), QPointF(15, 14))
            elif index == 1:
                painter.drawRoundedRect(QRectF(-13, -14, 26, 28), 2, 2)
                painter.drawRect(QRectF(-7, -14, 14, 8))
                painter.drawLine(QPointF(0, -1), QPointF(0, 10))
                painter.drawLine(QPointF(-5, 5), QPointF(0, 10))
                painter.drawLine(QPointF(0, 10), QPointF(5, 5))
            else:
                path = QPainterPath(QPointF(-14, -8))
                for x, y in ((-14, -13), (-4, -13), (0, -9), (13, -9), (13, 12), (-14, 12), (-14, -8)):
                    path.lineTo(x, y)
                painter.drawPath(path)
                painter.drawLine(QPointF(-9, -4), QPointF(8, -4))
                painter.drawLine(QPointF(-9, 1), QPointF(8, 1))
                painter.drawLine(QPointF(-9, 6), QPointF(3, 6))
            if done:
                painter.setPen(Qt.NoPen)
                painter.setBrush(accent)
                painter.drawEllipse(QRectF(12, 10, 14, 14))
                check_color = Qt.black if self._luminance(accent) > .18 else Qt.white
                painter.setPen(QPen(check_color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawLine(QPointF(15, 17), QPointF(18, 20))
                painter.drawLine(QPointF(18, 20), QPointF(23, 14))
            painter.restore()


class DiskReadProgressLabel(FixedProgressLabel):
    """Keep QProgressDialog's native status label beneath a reserved stage strip."""

    def __init__(self, dialog, text_width, translate):
        super().__init__(dialog, text_width)
        self.stages = DiskReadStages(self, translate)
        strip_height = self.stages.height() + round(self.fontMetrics().lineSpacing() * .25)
        self.setContentsMargins(0, strip_height, 0, 0)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setFixedHeight(self.height() + strip_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.stages.setGeometry(0, 0, self.width(), self.stages.height())
