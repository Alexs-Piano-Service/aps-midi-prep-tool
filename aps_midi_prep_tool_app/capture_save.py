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
    ]
    try:
        candidates.append(tempfile.gettempdir())
    except OSError:
        # A full user volume can also make temporary-directory discovery fail.
        pass
    for directory in dict.fromkeys(candidates):
        if _writable_directory(directory):
            return os.path.join(directory, os.path.basename(filename))
    # No writable default exists when every user volume is full or unavailable.
    # Still open the chooser in an absolute user directory so another drive can
    # be selected. In particular, do not interrupt an unsaved capture's retry
    # loop or fall back to the application's working directory.
    for directory in candidates[1:]:
        if directory and os.path.isabs(directory) and os.path.isdir(directory):
            return os.path.join(directory, os.path.basename(filename))
    raise OSError("No writable user directory is available for saving captures.")


def remember_capture_directory(settings, output_path):
    directory = os.path.dirname(os.path.abspath(output_path))
    settings.setValue(SETTING_CAPTURE_DIRECTORY, directory)
    settings.sync()
