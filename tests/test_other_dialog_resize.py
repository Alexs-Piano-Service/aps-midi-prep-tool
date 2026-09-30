"""Exercise the remaining tool windows inside live Qt event loops."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, QTimer
from PySide6.QtGui import QFont, QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QPushButton,
)

from aps_midi_prep_tool_app import main_window, onboarding_dialog
from aps_midi_prep_tool_app.boot_sector_dialog import BootSectorRepairDialog
from aps_midi_prep_tool_app.console_log import ConsoleLogBus, ConsoleLogDialog
from aps_midi_prep_tool_app.emulator_image_builder import EmulatorBuildPreview
from aps_midi_prep_tool_app.emulator_preview_dialog import EmulatorPreviewDialog
from aps_midi_prep_tool_app.floppy_image import FloppyDriveInfo, GreaseweazleDeviceInfo
from aps_midi_prep_tool_app.markiv_backup_dialog import MarkIVBackupDialog
from aps_midi_prep_tool_app.pending_changes_dialog import PendingChangesDialog
from aps_midi_prep_tool_app.self_update_ui import SelfUpdateDialog


@pytest.fixture(params=[9, 14], ids=["regular-font", "large-font"])
def window(tmp_path, monkeypatch, request):
    app = QApplication.instance() or QApplication([])
    old_font = QFont(app.font())
    settings = QSettings(str(tmp_path / "other-dialogs.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow(settings=settings)
    font = QFont(app.font())
    font.setPointSize(request.param)
    app.setFont(font)
    instance.setFont(font)
    instance.resize(900, 700)
    instance.show()
    app.processEvents()
    yield instance
    instance.hide()
    instance.deleteLater()
    app.setFont(old_font)
    app.processEvents()


def _exercise_resize(dialog, change=None):
    """Growing, shrinking, moving and relayout must preserve settled geometry."""
    for width, height in ((1200, 900), (740, 520), (1080, 780)):
        dialog.resize(width, height)
        dialog.move(24, 32)
        # Layout minimums can limit a shrink; capture the actual legal size.
        QTest.qWait(30)
        geometry = dialog.geometry()
        QApplication.postEvent(dialog, QEvent(QEvent.LayoutRequest))
        if change is not None:
            change(dialog)
        QTest.qWait(110)
        assert dialog.geometry() == geometry
        assert dialog.isVisible()
        for buttons in dialog.findChildren(QDialogButtonBox):
            if buttons.isVisible():
                for button in buttons.buttons():
                    if button.isVisible():
                        bounds = button.rect().translated(button.mapTo(dialog, QPoint()))
                        assert dialog.rect().contains(bounds), button.text()
        # A full render also exercises the paint path after each geometry change.
        assert dialog.grab().size() == dialog.size()


def _run_live(window, dialog, inspect=None):
    errors = []

    def check():
        try:
            _exercise_resize(dialog, inspect)
        except BaseException as exc:
            errors.append(exc)
        finally:
            QDialog.reject(dialog)

    QTimer.singleShot(150, check)
    result = window._exec_child_dialog(dialog)
    assert result == QDialog.Rejected
    if errors:
        raise errors[0]


@pytest.mark.parametrize("kind,expected", [
    ("bulk", (760, 560)),
    ("overlap-song", (640, 380)),
    ("overlap-settings", (640, 380)),
    ("shortcuts", (800, 560)),
    ("pending-changes", (900, 600)),
])
def test_scrollable_tools_open_compactly_on_large_desktops(window, monkeypatch, kind, expected):
    # A small offscreen display masks accidental font scaling by clipping every
    # oversized default to the same screen bounds.
    monkeypatch.setattr(QScreen, "availableGeometry", lambda _self: QRect(0, 0, 2560, 1440))
    execute = window._exec_child_dialog
    errors = []
    seen = []

    def inspect(dialog, **options):
        seen.append(dialog)

        def check():
            try:
                assert dialog.size().toTuple() == expected
                for buttons in dialog.findChildren(QDialogButtonBox):
                    for button in buttons.buttons():
                        assert button.isVisible()
                        bounds = button.rect().translated(button.mapTo(dialog, QPoint()))
                        assert dialog.rect().contains(bounds), button.text()
                _exercise_resize(dialog)
            except BaseException as exc:
                errors.append(exc)
            finally:
                dialog.reject()

        QTimer.singleShot(150, check)
        return execute(dialog, **options)

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    if kind == "bulk":
        window.show_bulk_extraction_utility()
    elif kind.startswith("overlap"):
        window._piano_overlap_options_dialog(
            filename="SONG.MID" if kind == "overlap-song" else None, count=2,
        )
    elif kind == "shortcuts":
        window.show_keyboard_shortcuts_dialog()
    else:
        window._exec_child_dialog(PendingChangesDialog(window), resize_to_contents=False)
    assert len(seen) == 1
    if errors:
        raise errors[0]


@pytest.mark.parametrize("kind", [
    "boot-repair", "mark-iv", "emulator-preview", "console-log",
    "soundfonts", "render-audio", "file-inspection", "self-update", "bulk-progress",
])
def test_standalone_tool_windows_preserve_user_geometry(window, monkeypatch, kind):
    if kind == "boot-repair":
        dialog = BootSectorRepairDialog(window)
    elif kind == "mark-iv":
        dialog = MarkIVBackupDialog(window.settings, window, refresh_on_open=False)
    elif kind == "emulator-preview":
        preview = EmulatorBuildPreview("/source", "/output", (), (), (), {}, {}, "midi", "flat")
        dialog = EmulatorPreviewDialog(preview, window)
    elif kind == "console-log":
        dialog = ConsoleLogDialog(ConsoleLogBus(), window)
    elif kind == "soundfonts":
        monkeypatch.setattr(main_window.SoundFontManagerDialog, "refresh_catalog", lambda _self: None)
        dialog = main_window.SoundFontManagerDialog(window)
    elif kind == "render-audio":
        dialog = main_window.BatchAudioRenderDialog([], window)
    elif kind == "file-inspection":
        from test_file_inspection_actions import _SilentAudioOutput, _SilentPlayer
        monkeypatch.setattr(main_window, "QMediaPlayer", _SilentPlayer)
        monkeypatch.setattr(main_window, "QAudioOutput", _SilentAudioOutput)
        monkeypatch.setattr(main_window, "_midi_output_ports", lambda: ([], ""))
        dialog = main_window.FileInspectionDialog([], window)
    elif kind == "bulk-progress":
        dialog = window._create_bulk_extraction_progress_dialog()
    else:
        # Keep the progress UI live without downloading or installing anything.
        monkeypatch.setattr(SelfUpdateDialog, "_start_worker", lambda _self: None)
        dialog = SelfUpdateDialog(window, "0.8.8")
    try:
        _run_live(window, dialog)
    finally:
        if kind == "self-update":
            dialog.worker = None
        dialog.close()
        dialog.deleteLater()


FORMS = [
    ("_confirm_eseq_to_midi_conversion", (), {"title": "Convert E-SEQ to MIDI", "message": "Convert 16 songs?"}),
    ("_confirm_type0_conversion", (16,), {}),
    ("_pedal_compatibility_options_dialog", ([],), {}),
    ("_channel_merging_options_dialog", ([],), {}),
    ("_xf_stripping_options_dialog", ([],), {}),
    ("_prompt_for_new_image_options", (), {}),
    ("recover_damaged_image_dialog", (), {}),
    ("_choose_floppy_recovery_disk_format", (), {}),
    ("_prompt_for_image_filename", ("SONG.MID",), {}),
    ("_prompt_for_save_image_options", (), {}),
    ("_choose_greaseweazle_retry_format", ({},), {}),
    ("_show_greaseweazle_sector_report", ({"allow_empty_rows": True},), {}),
    ("show_emulator_image_utility", (), {}),
]


@pytest.mark.parametrize("method,args,kwargs", FORMS, ids=[case[0] for case in FORMS])
def test_other_option_forms_preserve_user_geometry(window, monkeypatch, method, args, kwargs):
    original_exec = window._exec_child_dialog
    errors = []
    seen = []

    def execute(dialog, **options):
        seen.append(dialog.windowTitle())

        def check():
            try:
                _exercise_resize(dialog)
            except BaseException as exc:
                errors.append(exc)
            finally:
                dialog.reject()

        QTimer.singleShot(150, check)
        return original_exec(dialog, **options)

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    getattr(window, method)(*args, **kwargs)
    assert len(seen) == 1
    if errors:
        raise errors[0]


@pytest.mark.parametrize("method", [
    "_choose_floppy_read_options", "_choose_floppy_image_capture_options",
    "_choose_format_floppy_options", "_choose_write_image_floppy_target",
    "_choose_save_to_floppy_drive",
])
def test_drive_choices_and_refresh_keep_resized_window(window, monkeypatch, method):
    drive = FloppyDriveInfo("/dev/test-floppy", 737280, label="Test floppy")
    device = GreaseweazleDeviceInfo("/dev/test-greaseweazle", "Test Greaseweazle")
    monkeypatch.setattr(window, "_discover_floppy_devices", lambda **_: ([drive], [device]))
    original_exec = window._exec_child_dialog
    errors = []
    seen = []

    def execute(dialog, **options):
        seen.append(dialog)

        def check():
            try:
                # Both device pages fit comfortably: a source change should not
                # contract the manually enlarged window to either page's hint.
                dialog.resize(1300, 1100)
                dialog.move(24, 32)
                QTest.qWait(40)
                geometry = dialog.geometry()
                source = next((combo for combo in dialog.findChildren(QComboBox)
                               if combo.findData("floppy_gw") >= 0), None)
                if source is not None:
                    for target in ("floppy_gw", "floppy_usb", "floppy_gw"):
                        source.setCurrentIndex(source.findData(target))
                        QTest.qWait(110)
                        assert dialog.geometry() == geometry
                refresh = dialog.findChild(QPushButton, "refreshFloppyDrives")
                refresh.click()
                QTest.qWait(110)
                assert dialog.geometry() == geometry
                for checkbox in dialog.findChildren(QCheckBox):
                    if "Recovery" in checkbox.text() or "recovery" in checkbox.text():
                        checkbox.click()
                        QTest.qWait(110)
                        assert dialog.geometry() == geometry
                _exercise_resize(dialog)
            except BaseException as exc:
                errors.append(exc)
            finally:
                dialog.reject()

        QTimer.singleShot(150, check)
        return original_exec(dialog, **options)

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    assert getattr(window, method)() is None
    assert len(seen) == 1
    if errors:
        raise errors[0]


def test_modeless_song_list_keeps_user_geometry(window, monkeypatch):
    monkeypatch.setattr(window, "_build_song_list_text", lambda: "1. First song\n2. Second song")
    window.show_song_list_tool()
    dialog = window.songListDialog
    try:
        QTest.qWait(150)
        _exercise_resize(dialog)
    finally:
        dialog.close()


def test_nested_mark_iv_report_preserves_user_geometry(window):
    parent = MarkIVBackupDialog(window.settings, window, refresh_on_open=False)
    errors = []
    seen = []

    def inspect():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            seen.append(dialog)
            _exercise_resize(dialog)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(150, inspect)
    parent._open_text_dialog("Album details", "Song information\n" * 30, object_name="resizeTestReport")
    parent.close()
    parent.deleteLater()
    assert len(seen) == 1
    if errors:
        raise errors[0]


@pytest.mark.parametrize("title_mode", ["midi", "eseq", "smart_pianosoft"])
def test_fixed_song_title_editor_has_stable_geometry(window, monkeypatch, title_mode):
    original_exec = window._exec_child_dialog
    errors = []

    def execute(dialog, **options):
        def check():
            try:
                initial_size = dialog.size()
                _exercise_resize(dialog)
                assert dialog.size() == initial_size
            except BaseException as exc:
                errors.append(exc)
            finally:
                dialog.reject()

        QTimer.singleShot(150, check)
        return original_exec(dialog, **options)

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    window._prompt_for_title("Test song title", title_mode=title_mode)
    if errors:
        raise errors[0]


def test_onboarding_page_switches_preserve_user_geometry(window, monkeypatch):
    monkeypatch.setattr(onboarding_dialog, "QSettings", lambda *_: window.settings)
    errors = []
    seen = []

    def inspect():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            seen.append(dialog)
            dialog.resize(1300, 1100)
            dialog.move(24, 32)
            QTest.qWait(30)
            geometry = dialog.geometry()
            selector = dialog.findChild(QComboBox)
            assert selector.count() > 1
            for index in range(selector.count()):
                selector.setCurrentIndex(index)
                QTest.qWait(40)
                assert dialog.geometry() == geometry
            _exercise_resize(dialog)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(150, inspect)
    onboarding_dialog.show_first_time_dialog(parent=window, force_show=True)
    assert len(seen) == 1
    if errors:
        raise errors[0]
