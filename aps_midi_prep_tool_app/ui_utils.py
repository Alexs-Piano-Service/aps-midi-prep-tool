import os
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtCore import QByteArray

from .logo_assets import embedded_logo_dt, embedded_logo_lt


def is_dark_theme():
    pal = QApplication.palette()
    bg_color = pal.color(QPalette.Window)
    brightness = 0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue()
    return brightness < 128


def pixmap_from_base64(data):
    ba = QByteArray.fromBase64(data)
    pixmap = QPixmap()
    pixmap.loadFromData(ba)
    return pixmap


def center_dialog_on_parent(dialog, parent=None):
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

    dialog.adjustSize()
    dialog_geometry = dialog.frameGeometry()
    if dialog_geometry.width() <= 0 or dialog_geometry.height() <= 0:
        dialog_geometry.setSize(dialog.sizeHint())
    dialog_geometry.moveCenter(target_geometry.center())
    dialog.move(dialog_geometry.topLeft())


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
