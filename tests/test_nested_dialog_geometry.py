"""Nested utility dialogs share initial placement and retain their native owner."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QProgressDialog, QWidget

from aps_midi_prep_tool_app import disk_device_discovery, markiv_backup_dialog, smart_pianosoft_dialog
from aps_midi_prep_tool_app.floppy_image import GreaseweazleDeviceInfo
from aps_midi_prep_tool_app.localized_dialogs import QMessageBox


def test_parentless_message_box_can_close_and_release_its_native_window():
    # Startup recovery can show a message before the main window exists. Run
    # the native destruction path in a subprocess so a Qt crash is a failure,
    # rather than terminating every remaining test in the suite.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from aps_midi_prep_tool_app.localized_dialogs import QMessageBox

            app = QApplication([])
            def show_recovery():
                box = QMessageBox()
                box.setText("Recover the interrupted rename?")
                box.setStandardButtons(QMessageBox.Ok)
                QTimer.singleShot(0, box.accept)
                box.exec()
            show_recovery()
            app.processEvents()
        """)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def owner():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.resize(600, 500)
    window.move(90, 60)
    window.show()
    nested = QDialog(window)
    nested.resize(420, 320)
    nested.move(240, 220)
    nested.show()
    app.processEvents()
    yield app, window, nested
    nested.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def _assert_owned_and_centered(dialog, owner):
    assert dialog.parentWidget() is owner
    assert dialog.windowHandle().transientParent() is owner.windowHandle()
    actual = dialog.frameGeometry()
    expected = dialog.frameGeometry()
    expected.moveCenter(owner.frameGeometry().center())
    available = owner.screen().availableGeometry()
    expected.moveLeft(max(available.left(), min(expected.left(), available.right() - expected.width() + 1)))
    expected.moveTop(max(available.top(), min(expected.top(), available.bottom() - expected.height() + 1)))
    assert (actual.topLeft() - expected.topLeft()).manhattanLength() <= 2


@pytest.mark.parametrize("nested", [False, True], ids=["main-owner", "nested-owner"])
def test_direct_message_box_is_centered_once_and_preserves_owner(owner, nested):
    app, window, parent = owner
    parent = parent if nested else window
    box = QMessageBox(parent)
    box.setText("Save the files created so far?")
    box.setInformativeText("The source files are kept.")
    box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard)
    # Explicit stale positioning must not override first-show preparation.
    box.move(0, 0)
    box.show()
    app.processEvents()
    _assert_owned_and_centered(box, parent)

    box.move(110, 130)
    app.processEvents()
    position = box.pos()
    box.hide()
    box.show()
    app.processEvents()
    assert box.pos() == position
    assert box.windowHandle().transientParent() is parent.windowHandle()
    box.close()


@pytest.mark.parametrize("kind", ["greaseweazle", "markiv-report"])
def test_nested_tool_dialogs_use_initial_placement(owner, tmp_path, monkeypatch, kind):
    app, window, _nested = owner
    settings = QSettings(str(tmp_path / "nested.ini"), QSettings.IniFormat)
    module = smart_pianosoft_dialog if kind == "greaseweazle" else markiv_backup_dialog

    class InitiallyMisplacedDialog(QDialog):
        def __init__(self, parent):
            super().__init__(parent)
            self.move(0, 0)

    monkeypatch.setattr(module, "QDialog", InitiallyMisplacedDialog)
    if kind == "greaseweazle":
        parent = module.SmartPianoSoftDialog(settings, window, discover_on_open=False)
    else:
        parent = module.MarkIVBackupDialog(settings, window, refresh_on_open=False)
    parent.move(80, 90)
    parent.show()
    app.processEvents()
    errors = []
    seen = []

    def inspect():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, InitiallyMisplacedDialog)
            seen.append(dialog)
            _assert_owned_and_centered(dialog, parent)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, inspect)
    if kind == "greaseweazle":
        assert parent._choose_greaseweazle_source(GreaseweazleDeviceInfo("COM42", "Greaseweazle")) is None
    else:
        parent._open_text_dialog("Album details", "Song information", object_name="nestedPlacementReport")
    parent.close()
    parent.deleteLater()
    assert len(seen) == 1
    if errors:
        raise errors[0]


def test_drive_discovery_without_main_window_callback_keeps_nested_owner(owner, monkeypatch):
    _app, _window, parent = owner
    completed = []
    closed = []
    probe = SimpleNamespace(
        poll=lambda: bool(completed), devices=[], error="", close=lambda: closed.append(True),
    )
    monkeypatch.setattr(disk_device_discovery, "_start_probe", lambda _kind: probe)
    progress_type = disk_device_discovery.QProgressDialog

    class InitiallyMisplacedProgress(progress_type):
        def __init__(self, *args):
            super().__init__(*args)
            self.move(0, 0)

    monkeypatch.setattr(disk_device_discovery, "QProgressDialog", InitiallyMisplacedProgress)
    errors = []

    def inspect():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QProgressDialog)
            _assert_owned_and_centered(dialog, parent)
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.append(True)

    QTimer.singleShot(0, inspect)
    assert disk_device_discovery.discover_floppy_devices(parent, include_greaseweazle=False) == ([], [], [])
    assert closed == [True]
    if errors:
        raise errors[0]
