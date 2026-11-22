"""Dialog actions that stack when their full captions need more room."""

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
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
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_current_width)
        self._fitting = False
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
        super().resizeEvent(event)
        self._schedule_fit()

    def event(self, event):
        event_type = event.type()
        result = super().event(event)
        # DeferredDelete can destroy the native widget in super().event().
        # Only inspect this wrapper for events that require a layout update.
        if event_type in (
            QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange, QEvent.Show,
        ):
            self._schedule_fit()
        return result

    def _schedule_fit(self):
        if getattr(self, "_responsive_ready", False) and not self._fit_timer.isActive():
            # Orientation changes rebuild Qt's button layout. Let the current
            # layout/resize event finish first and combine repeated requests.
            # The parented timer also cancels pending work on destruction.
            self._fit_timer.start(0)

    def _fit_current_width(self):
        if self._fitting:
            return
        self._fitting = True
        try:
            self._fit_width(self.width())
            parent = self.parentWidget()
            if parent is not None and parent.layout() is not None:
                # Apply the footer's new height in this deferred pass, so its
                # buttons do not extend beyond the dialog for another frame.
                parent.layout().activate()
        finally:
            self._fitting = False

    def _fit_width(self, width):
        _minimum_width, row_width, row_height, column_height = self._metrics()
        orientation = Qt.Horizontal if width >= row_width else Qt.Vertical
        if self.orientation() != orientation:
            self.setOrientation(orientation)
            # Qt resets the size policy whenever the orientation changes.
            self._set_responsive_policy()
        height = row_height if orientation == Qt.Horizontal else column_height
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
