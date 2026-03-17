import sys

from PySide6.QtWidgets import QApplication

from .app_info import APP_VERSION, SETTINGS_APP, SETTINGS_ORG
from .onboarding_dialog import show_first_time_dialog
from .main_window import MidiTitleWindow


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationName(SETTINGS_APP)
    app.setApplicationVersion(APP_VERSION)

    show_first_time_dialog()
    window = MidiTitleWindow()
    window.show()
    sys.exit(app.exec())
