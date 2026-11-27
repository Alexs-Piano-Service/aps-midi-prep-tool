"""Writable, user-owned defaults shared by capture save dialogs."""

import os
import tempfile

from PySide6.QtCore import QStandardPaths


SETTING_CAPTURE_DIRECTORY = "capture_save_location"


def _writable_directory(directory):
    if not directory or not os.path.isabs(directory) or not os.path.isdir(directory):
        return False
    try:
        # A real create/delete probe also checks Windows ACLs and unavailable
        # redirected Documents folders, which os.access alone cannot establish.
        with tempfile.TemporaryFile(dir=directory):
            pass
        return True
    except OSError:
        return False


def capture_save_path(settings, filename):
    candidates = [
        str(settings.value(SETTING_CAPTURE_DIRECTORY, "") or ""),
        QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation),
        QStandardPaths.writableLocation(QStandardPaths.HomeLocation),
        os.path.expanduser("~"),
        QStandardPaths.writableLocation(QStandardPaths.DesktopLocation),
        QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation),
        tempfile.gettempdir(),
    ]
    for directory in dict.fromkeys(candidates):
        if _writable_directory(directory):
            return os.path.join(directory, os.path.basename(filename))
    raise OSError("No writable user directory is available for saving captures.")


def remember_capture_directory(settings, output_path):
    directory = os.path.dirname(os.path.abspath(output_path))
    settings.setValue(SETTING_CAPTURE_DIRECTORY, directory)
    settings.sync()
