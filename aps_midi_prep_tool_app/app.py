import os
import sys


def _prefer_xcb_for_appimage() -> None:
    if not sys.platform.startswith("linux") or not os.environ.get("APPIMAGE"):
        return
    # Qt Wayland can resize AppImage windows unpredictably while they are moved
    # on some GNOME/PopOS desktops. Let users override this when needed.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


_prefer_xcb_for_appimage()

from .app_info import APP_NAME, APP_VERSION, LEGACY_SETTINGS_APP, SETTINGS_APP, SETTINGS_ORG


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
    from PySide6.QtCore import QSettings

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
    from .package_smoke import run_package_smoke_from_argv

    smoke_exit_code = run_package_smoke_from_argv(sys.argv)
    if smoke_exit_code is not None:
        sys.exit(smoke_exit_code)

    from .disk_device_probe import run_device_probe_from_argv

    probe_exit_code = run_device_probe_from_argv(sys.argv)
    if probe_exit_code is not None:
        sys.exit(probe_exit_code)

    from .floppy_image import run_windows_raw_write_helper_from_argv

    helper_exit_code = run_windows_raw_write_helper_from_argv(sys.argv)
    if helper_exit_code is not None:
        sys.exit(helper_exit_code)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .onboarding_dialog import show_first_time_dialog
    from .rename_recovery_dialog import show_rename_recovery_dialogs
    from .console_log import install_console_capture
    from .main_window import MidiTitleWindow, install_tooltip_delay_style
    from .icon_utils import apply_window_icon, load_app_icon
    from .startup_config import StartupConfigError, load_startup_config
    from .floppy_save_recovery import prune_completed_packages

    _set_windows_app_id()
    app = QApplication(sys.argv)
    install_tooltip_delay_style(app)
    install_console_capture()
    prune_completed_packages()
    _migrate_legacy_settings()
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    config_error = None
    try:
        initial_settings = load_startup_config()
    except StartupConfigError as exc:
        initial_settings = {}
        config_error = exc
        print(f"Startup configuration ignored: {exc}", file=sys.stderr)

    window = MidiTitleWindow(initial_settings=initial_settings)
    apply_window_icon(window)
    window.show()

    def run_startup_dialogs(attempt=0):
        window_handle = window.windowHandle()
        if attempt < 20 and (
            not window.isVisible()
            or (window_handle is not None and not window_handle.isExposed())
        ):
            QTimer.singleShot(50, lambda: run_startup_dialogs(attempt + 1))
            return
        if config_error is not None:
            from PySide6.QtCore import Qt
            from .localized_dialogs import QMessageBox

            message = QMessageBox(window)
            message.setIcon(QMessageBox.Warning)
            message.setWindowTitle(APP_NAME)
            message.setTextFormat(Qt.PlainText)
            message.setText(window._t("startup_config.error"))
            message.setInformativeText(str(config_error))
            message.exec()
        show_rename_recovery_dialogs(window)
        show_first_time_dialog(app_icon, parent=window)
        window.schedule_startup_update_check()

    QTimer.singleShot(0, run_startup_dialogs)
    sys.exit(app.exec())
