"""Dialog actions that stack when their full captions need more room."""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import QDialogButtonBox, QLayout, QSizePolicy


class ResponsiveDialogButtonBox(QDialogButtonBox):
    """Keep native button ordering without imposing a whole row's minimum width.

    The internal layout must not set a horizontal minimum that prevents the
    resize which would trigger stacking. Once assigned a width, the footer
    reserves enough height for its buttons and lets the body use the remainder.
    """

    def __init__(self, *args, spacing=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Some styles use different horizontal and vertical layout spacing.
        # Keep this footer's spacing stable across orientation changes, so the
        # width threshold cannot alternate on successive layout requests.
        self._button_spacing = max(0, self.layout().spacing() if spacing is None else spacing)
        self._set_responsive_policy()
        self._responsive_ready = True

    def _set_responsive_policy(self):
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.layout().setSizeConstraint(QLayout.SetNoConstraint)
        self.layout().setSpacing(self._button_spacing)

    def _button_sizes(self):
        return [
            button.sizeHint().expandedTo(button.minimumSizeHint()).expandedTo(button.minimumSize())
            for button in self.buttons() if not button.isHidden()
        ]

    def _metrics(self):
        sizes = self._button_sizes()
        margins = self.contentsMargins() + self.layout().contentsMargins()
        horizontal_margin = margins.left() + margins.right()
        vertical_margin = margins.top() + margins.bottom()
        if not sizes:
            return horizontal_margin, horizontal_margin, vertical_margin, vertical_margin
        spacing = self._button_spacing * (len(sizes) - 1)
        widest = max(size.width() for size in sizes)
        # Native styles may give every button the widest caption's width.
        row_width = widest * len(sizes) + spacing + horizontal_margin
        row_height = max(size.height() for size in sizes) + vertical_margin
        column_height = sum(size.height() for size in sizes) + spacing + vertical_margin
        return widest + horizontal_margin, row_width, row_height, column_height

    def minimumSizeHint(self):
        minimum_width, _row_width, row_height, column_height = self._metrics()
        height = row_height if self.orientation() == Qt.Horizontal else column_height
        return QSize(minimum_width, height)

    def sizeHint(self):
        _minimum_width, row_width, _row_height, _column_height = self._metrics()
        return QSize(row_width, self.minimumSizeHint().height())

    def resizeEvent(self, event):
        self._fit_width(event.size().width())
        super().resizeEvent(event)

    def event(self, event):
        result = super().event(event)
        if getattr(self, "_responsive_ready", False) and event.type() in (
            QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange, QEvent.Show,
        ):
            self._fit_width(self.width())
        return result

    def _fit_width(self, width):
        self.layout().setSpacing(self._button_spacing)
        _minimum_width, row_width, row_height, column_height = self._metrics()
        orientation = Qt.Horizontal if width >= row_width else Qt.Vertical
        if self.orientation() != orientation:
            self.setOrientation(orientation)
            # Qt resets the size policy whenever the orientation changes.
            self._set_responsive_policy()
        height = row_height if orientation == Qt.Horizontal else column_height
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
