import os
import sys

from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QVBoxLayout, QWidget
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtCore import QByteArray, Qt

from .logo_assets import embedded_logo_dt, embedded_logo_lt


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


def center_dialog_on_parent(dialog, parent=None, *, adjust_size=True):
    parent_widget = parent or dialog.parentWidget()
    screen = None
    target_geometry = None

    if parent_widget is not None:
        parent_window = parent_widget.window()
        if parent_window is not None:
            screen = parent_window.screen()
            if parent_window.isVisible():
                target_geometry = parent_window.frameGeometry()
        if screen is None:
            screen = parent_widget.screen()

    if target_geometry is None:
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        target_geometry = screen.availableGeometry()

    if adjust_size:
        dialog.adjustSize()
    dialog_geometry = dialog.frameGeometry()
    if dialog_geometry.width() <= 0 or dialog_geometry.height() <= 0:
        dialog_geometry.setSize(dialog.sizeHint())
    dialog_geometry.moveCenter(target_geometry.center())
    dialog.move(dialog_geometry.topLeft())


def scrollable_dialog_layout(dialog, *, width, height, spacing=10):
    """Give a form a scrollable body and room for an always-visible footer.

    Size once, using the current font and available screen. Later resizing is
    owned by Qt's layouts and the user, without queued fits to the size hint.
    """
    dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
    dialog.setSizeGripEnabled(True)
    scale = max(1.0, dialog.fontMetrics().height() / 16.0)
    width, height = round(width * scale), round(height * scale)
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, max(1, available.width() - 40))
        height = min(height, max(1, available.height() - 60))
    dialog.resize(width, height)

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
