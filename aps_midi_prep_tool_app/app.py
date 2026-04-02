import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .app_info import APP_VERSION, LEGACY_SETTINGS_APP, SETTINGS_APP, SETTINGS_ORG
from .onboarding_dialog import show_first_time_dialog
from .main_window import MidiTitleWindow
from .icon_utils import apply_window_icon, load_app_icon


def _set_windows_app_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{SETTINGS_ORG}.{SETTINGS_APP}"
        )
    except Exception:
        pass


def _migrate_legacy_settings() -> None:
    if SETTINGS_APP == LEGACY_SETTINGS_APP:
        return

    current_settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    if current_settings.allKeys():
        return

    legacy_settings = QSettings(SETTINGS_ORG, LEGACY_SETTINGS_APP)
    legacy_keys = legacy_settings.allKeys()
    if not legacy_keys:
        return

    for key in legacy_keys:
        current_settings.setValue(key, legacy_settings.value(key))
    current_settings.sync()


def main():
    _set_windows_app_id()
    app = QApplication(sys.argv)
    _migrate_legacy_settings()
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationName(SETTINGS_APP)
    app.setApplicationVersion(APP_VERSION)
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    show_first_time_dialog(app_icon)
    window = MidiTitleWindow()
    apply_window_icon(window)
    window.show()
    sys.exit(app.exec())
