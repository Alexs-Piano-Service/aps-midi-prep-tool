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


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
