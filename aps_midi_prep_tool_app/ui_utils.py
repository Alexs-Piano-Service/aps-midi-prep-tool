import os
import sys

from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget
from PySide6.QtGui import QPalette, QPixmap, QTextLayout, QTextOption
from PySide6.QtCore import QByteArray, QEvent, QMargins, QObject, QPoint, QRect, QSize, Qt, QTimer

from .logo_assets import embedded_logo_dt, embedded_logo_lt


class WrappedProgressLabel(QLabel):
    """Give QProgressDialog a size hint that includes every wrapped line.

    QProgressDialog lays out its children itself, so wordWrap alone does not
    reserve the height needed when a short status becomes a detailed error.
    Widths are Qt logical pixels, including on displays with Windows scaling.
    """

    def __init__(self, text, dialog):
        super().__init__(text, dialog)
        screen = dialog.screen() or QApplication.primaryScreen()
        available_width = screen.availableGeometry().width() if screen else 800
        self._wrap_width = max(160, min(600, available_width - 80))
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def sizeHint(self):
        natural_width = max((self.fontMetrics().horizontalAdvance(line) for line in self.text().splitlines()), default=0)
        width = max(1, min(self._wrap_width, natural_width))
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):
        return self.sizeHint()


class FixedProgressLabel(QLabel):
    """Reserve four readable status lines without changing dialog geometry."""

    def __init__(self, dialog, text_width):
        super().__init__(dialog)
        self._text_width = text_width
        self._line_count = 4
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * self._line_count)

    def sizeHint(self):
        return QSize(self._text_width, self.height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def elided_message(self, message):
        lines = []
        paragraphs = message.split('\n')
        for index, paragraph in enumerate(paragraphs):
            # QTextLayout offsets count UTF-16 units, unlike Python's string
            # indices. Preserve supplementary characters in device/file names.
            encoded = paragraph.encode('utf-16-le')
            layout = QTextLayout(paragraph or ' ', self.font())
            options = QTextOption()
            options.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            layout.setTextOption(options)
            layout.beginLayout()
            while (line := layout.createLine()).isValid():
                line.setLineWidth(self._text_width)
                start = line.textStart() * 2
                end = start + line.textLength() * 2
                if len(lines) == self._line_count - 1:
                    remainder = ' '.join([encoded[start:].decode('utf-16-le'), *paragraphs[index + 1:]])
                    lines.append(self.fontMetrics().elidedText(remainder, Qt.ElideMiddle, self._text_width))
                    layout.endLayout()
                    return '\n'.join(lines)
                lines.append(encoded[start:end].decode('utf-16-le').rstrip())
            layout.endLayout()
        return '\n'.join(lines)


def is_dark_theme():
    app = QApplication.instance()
    if app is not None:
        appearance_mode = str(app.property("_aps_appearance_mode") or "").strip().lower()
        if appearance_mode == "dark":
            return True
        if appearance_mode == "light":
            return False
        pal = app.palette()
    else:
        pal = QApplication.palette()
    bg_color = pal.color(QPalette.Window)
    brightness = 0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue()
    return brightness < 128


def pixmap_from_base64(data):
    ba = QByteArray.fromBase64(data)
    pixmap = QPixmap()
    pixmap.loadFromData(ba)
    return pixmap


class _CenterDialogOnShow(QObject):
    def __init__(self, dialog, parent_widget):
        super().__init__(dialog)
        self.parent_widget = parent_widget
        self.window_handle = dialog.windowHandle()
        self.shown = False
        self.geometry_changed = False
        self.frame_attempts = 0
        self.frame_timer = QTimer(self)
        self.frame_timer.setSingleShot(True)
        self.frame_timer.timeout.connect(self._finish_positioning)
        if self.window_handle is not None:
            self.window_handle.installEventFilter(self)

    def _detach(self):
        self.frame_timer.stop()
        dialog = self.parent()
        dialog.removeEventFilter(self)
        if self.window_handle is not None:
            self.window_handle.removeEventFilter(self)
        dialog._aps_center_on_show = None
        self.deleteLater()

    def _finish_positioning(self):
        dialog = self.parent()
        if self.geometry_changed or not dialog.isVisible():
            self._detach()
            return
        margins = self.window_handle.frameMargins()
        if min(margins.left(), margins.top(), margins.right(), margins.bottom()) < 0:
            # X11 can briefly report a 1x0 native frame with negative margins
            # while KWin decorates the window. Never center that bogus frame.
            self.frame_attempts += 1
            if self.frame_attempts < 20:
                self.frame_timer.start(16)
            else:
                self._detach()
            return
        self._detach()
        _position_dialog_on_parent(dialog, self.parent_widget)

    def eventFilter(self, watched, event):
        # Qt owns this filter through the dialog. Keeping a Python reference
        # back to it would delay native destruction past exec() and can leave
        # queued window events targeting a deleted parentless message box.
        dialog = self.parent()
        if watched is dialog and event.type() == QEvent.ShowToParent:
            # This synchronous event follows showEvent and native window
            # creation, so QMessageBox's final size and frame are available.
            _position_dialog_on_parent(dialog, self.parent_widget)
            dialog._aps_dialog_positioned = True
            self.shown = True
        elif watched is dialog and self.shown:
            if event.type() in (QEvent.Move, QEvent.Resize) and not event.spontaneous():
                # A caller or user has chosen geometry after opening. Never
                # override it when the window manager later exposes the window.
                self.geometry_changed = True
            elif event.type() == QEvent.Hide:
                self._detach()
        elif (watched is self.window_handle and event.type() == QEvent.Expose
              and self.shown and self.window_handle.isExposed()):
            # On X11 the window manager supplies the decorated frame after
            # showEvent. Finish positioning once that native frame is known.
            # Process the native event first so frame margins are current.
            if not self.frame_timer.isActive():
                self.frame_timer.start(0)
        return False


def _dialog_parent_window(dialog, parent_widget):
    if parent_widget is None:
        return None
    parent_window = parent_widget.window()
    return parent_window if parent_window is not dialog else None


def _position_dialog_on_parent(dialog, parent_widget):
    # Wayland does not expose global window coordinates. Its compositor places
    # dialogs using the native transient-parent relationship established below.
    if QApplication.platformName().startswith("wayland"):
        return
    screen = None
    target_geometry = None

    if parent_widget is not None:
        parent_window = _dialog_parent_window(dialog, parent_widget)
        if parent_window is not None:
            screen = parent_window.screen()
            if parent_window.isVisible() and not parent_window.isMinimized():
                target_geometry = _window_frame_geometry(parent_window)
        if screen is None:
            screen = parent_widget.screen()

    if target_geometry is None:
        screen = screen or dialog.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        target_geometry = screen.availableGeometry()

    dialog_geometry = _window_frame_geometry(dialog)
    dialog_geometry.moveCenter(target_geometry.center())
    if screen is not None:
        available = screen.availableGeometry()
        dialog_geometry.moveLeft(max(available.left(), min(
            dialog_geometry.left(), available.right() - dialog_geometry.width() + 1,
        )))
        dialog_geometry.moveTop(max(available.top(), min(
            dialog_geometry.top(), available.bottom() - dialog_geometry.height() + 1,
        )))
    # QWidget's cached frame strut can be stale after native window creation
    # on X11, even reporting a 1x0 frame for a fully sized dialog. Position the
    # client rectangle explicitly instead of letting move() use that cache.
    margins = _window_frame_margins(dialog)
    client_position = dialog_geometry.topLeft() + QPoint(margins.left(), margins.top())
    dialog.setGeometry(QRect(client_position, dialog.size()))


def _window_frame_margins(widget):
    handle = widget.windowHandle()
    margins = handle.frameMargins() if handle is not None else QMargins()
    return QMargins(*(max(0, value) for value in (
        margins.left(), margins.top(), margins.right(), margins.bottom(),
    )))


def _window_frame_geometry(widget):
    # Use the current client size: a hidden QWindow may still have its initial
    # 100x30 size until QWidget maps it. Native margins become available when
    # the window manager decorates it, before the first exposed-frame pass.
    return widget.geometry().marginsAdded(_window_frame_margins(widget))


def center_dialog_on_parent(dialog, parent=None, *, adjust_size=True, recenter=False):
    """Place a dialog once, or recenter after an explicit change of form."""
    if getattr(dialog, "_aps_dialog_positioned", False) and not recenter:
        return
    parent_widget = parent or dialog.parentWidget()
    parent_window = _dialog_parent_window(dialog, parent_widget)
    if parent_window is not None:
        # Make the relationship available before the first native map, including
        # dialogs given an owner here rather than through their constructor.
        if dialog.screen() is not parent_window.screen():
            dialog.setScreen(parent_window.screen())
        parent_window.winId()
    dialog.winId()
    if parent_window is not None:
        dialog.windowHandle().setTransientParent(parent_window.windowHandle())
    if adjust_size:
        dialog.adjustSize()
    _position_dialog_on_parent(dialog, parent_widget)

    if dialog.isVisible() and getattr(dialog, "_aps_dialog_positioned", False):
        return

    on_show = getattr(dialog, "_aps_center_on_show", None)
    if on_show is None:
        on_show = _CenterDialogOnShow(dialog, parent_widget)
        dialog._aps_center_on_show = on_show
        dialog.installEventFilter(on_show)
    else:
        on_show.parent_widget = parent_widget


class _DialogPlacementFilter(QObject):
    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Show and isinstance(watched, QDialog)
                and not getattr(watched, "_aps_dialog_positioned", False)
                and getattr(watched, "_aps_center_on_show", None) is None):
            # Cover dialogs opened directly by nested tools as well as those
            # opened through the main window's exec/progress helpers.
            parent = watched.parentWidget()
            owner = parent
            while owner is not None and owner is not self.parent():
                owner = owner.parentWidget()
            if owner is self.parent():
                center_dialog_on_parent(watched, parent, adjust_size=False)
        return False


def install_dialog_placement(window):
    """Center all dialogs belonging to this window when they first open."""
    if getattr(window, "_aps_dialog_placement_filter", None) is None:
        placement = _DialogPlacementFilter(window)
        window._aps_dialog_placement_filter = placement
        QApplication.instance().installEventFilter(placement)


def resize_dialog_to_screen(dialog, *, width, height):
    """Set an initial logical-pixel size without multiplying it by font size."""
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, max(1, available.width() - 40))
        height = min(height, max(1, available.height() - 60))
    dialog.resize(width, height)


def scrollable_dialog_layout(dialog, *, width, height, spacing=10):
    """Give a form a scrollable body and room for an always-visible footer.

    Use the requested logical-pixel size, bounded by the available screen.
    Larger fonts reflow or scroll the body instead of enlarging the window.
    Later resizing is owned by Qt's layouts and the user, without queued fits.
    """
    dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
    dialog.setSizeGripEnabled(True)
    resize_dialog_to_screen(dialog, width=width, height=height)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(spacing)
    scroll_area = QScrollArea(dialog)
    scroll_area.setObjectName("dialogScrollArea")
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.NoFrame)
    body = QWidget(scroll_area)
    content = QVBoxLayout(body)
    content.setContentsMargins(0, 0, 0, 0)
    content.setSpacing(spacing)
    scroll_area.setWidget(body)
    layout.addWidget(scroll_area, 1)
    return layout, content


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
