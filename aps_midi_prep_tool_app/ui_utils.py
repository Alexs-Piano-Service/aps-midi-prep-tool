import os
import sys

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtCore import QByteArray, QEvent, QObject, QSize, Qt

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
        if self.window_handle is not None:
            self.window_handle.installEventFilter(self)

    def _detach(self):
        dialog = self.parent()
        dialog.removeEventFilter(self)
        if self.window_handle is not None:
            self.window_handle.removeEventFilter(self)
        dialog._aps_center_on_show = None
        self.deleteLater()

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
            self._detach()
            if not self.geometry_changed:
                _position_dialog_on_parent(dialog, self.parent_widget)
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
                target_geometry = parent_window.frameGeometry()
        if screen is None:
            screen = parent_widget.screen()

    if target_geometry is None:
        screen = screen or dialog.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        target_geometry = screen.availableGeometry()

    dialog_geometry = dialog.frameGeometry()
    if dialog_geometry.width() <= 0 or dialog_geometry.height() <= 0:
        dialog_geometry.setSize(dialog.sizeHint())
    dialog_geometry.moveCenter(target_geometry.center())
    if screen is not None:
        available = screen.availableGeometry()
        dialog_geometry.moveLeft(max(available.left(), min(
            dialog_geometry.left(), available.right() - dialog_geometry.width() + 1,
        )))
        dialog_geometry.moveTop(max(available.top(), min(
            dialog_geometry.top(), available.bottom() - dialog_geometry.height() + 1,
        )))
    dialog.move(dialog_geometry.topLeft())


def center_dialog_on_parent(dialog, parent=None, *, adjust_size=True):
    """Place a dialog once, retaining its owner and later user geometry."""
    if getattr(dialog, "_aps_dialog_positioned", False):
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

    on_show = getattr(dialog, "_aps_center_on_show", None)
    if on_show is None:
        on_show = _CenterDialogOnShow(dialog, parent_widget)
        dialog._aps_center_on_show = on_show
        dialog.installEventFilter(on_show)
    else:
        on_show.parent_widget = parent_widget


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
