"""Drive choosers can rediscover devices without discarding the open dialog."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QPushButton,
    QSpinBox,
)

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyDriveInfo, GreaseweazleDeviceInfo,
)


CHOOSERS = (
    "_choose_floppy_read_options",
    "_choose_floppy_image_capture_options",
    "_choose_format_floppy_options",
    "_choose_save_to_floppy_drive",
    "_choose_write_image_floppy_target",
)


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "floppy-refresh.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow(settings=settings)
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    instance.deleteLater()
    app.processEvents()


def _floppy_combo(dialog):
    return next(
        combo for combo in dialog.findChildren(QComboBox)
        if isinstance(combo.itemData(0), FloppyDriveInfo)
        or combo.itemText(0) == "No supported floppy drive detected"
    )


def _ok_button(dialog):
    return dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)


def _refresh_button(dialog):
    button = dialog.findChild(QPushButton, "refreshFloppyDrives")
    assert button is not None
    assert button.isEnabled()
    return button


def _discovery_results(window, monkeypatch, *results):
    remaining = iter(results)
    calls = []

    def discover(**kwargs):
        calls.append(kwargs)
        return next(remaining)

    monkeypatch.setattr(window, "_discover_floppy_devices", discover)
    return calls


@pytest.mark.parametrize("method_name", CHOOSERS)
def test_refresh_finds_new_drive_and_allows_using_it(window, monkeypatch, method_name):
    drive = FloppyDriveInfo("/dev/fd0", 737280, label="Inserted disk")
    calls = _discovery_results(window, monkeypatch, ([], []), ([drive], []))

    def inspect(dialog):
        combo = _floppy_combo(dialog)
        assert not combo.isEnabled()
        assert combo.currentData() is None
        assert not _ok_button(dialog).isEnabled()
        _refresh_button(dialog).click()
        assert len(calls) == 2
        assert combo.isEnabled()
        assert combo.count() == 1
        assert combo.currentData() == drive
        assert _ok_button(dialog).isEnabled()
        assert dialog.result() == QDialog.Rejected
        _ok_button(dialog).click()
        return dialog.result()

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = getattr(window, method_name)()
    assert result.get("source", result.get("target")) == drive
    if method_name == "_choose_save_to_floppy_drive":
        assert all(call.get("include_greaseweazle") is False for call in calls)


@pytest.mark.parametrize("method_name", CHOOSERS)
def test_cancelling_refresh_preserves_choices_and_options(window, monkeypatch, method_name):
    drives = [FloppyDriveInfo("/dev/fd0", 737280), FloppyDriveInfo("/dev/fd1", 1474560)]
    calls = _discovery_results(window, monkeypatch, (drives, []), None)

    def inspect(dialog):
        drive_combo = _floppy_combo(dialog)
        drive_combo.setCurrentIndex(1)
        for combo in dialog.findChildren(QComboBox):
            if combo is not drive_combo and combo.findData("floppy_usb") < 0:
                combo.setCurrentIndex(combo.count() - 1)
        for checkbox in dialog.findChildren(QCheckBox):
            if checkbox.isEnabled():
                checkbox.setChecked(not checkbox.isChecked())
        for spin in dialog.findChildren(QSpinBox):
            spin.setValue(spin.maximum())
        combos = [(combo, combo.currentIndex(), combo.currentData())
                  for combo in dialog.findChildren(QComboBox)]
        checks = [(checkbox, checkbox.isChecked()) for checkbox in dialog.findChildren(QCheckBox)]
        spins = [(spin, spin.value()) for spin in dialog.findChildren(QSpinBox)]

        _refresh_button(dialog).click()
        assert len(calls) == 2
        assert drive_combo.isEnabled()
        assert drive_combo.currentData() == drives[1]
        assert _ok_button(dialog).isEnabled()
        assert _refresh_button(dialog).isEnabled()
        for combo, index, data in combos:
            assert (combo.currentIndex(), combo.currentData()) == (index, data)
        assert all(checkbox.isChecked() == checked for checkbox, checked in checks)
        assert all(spin.value() == value for spin, value in spins)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert getattr(window, method_name)() is None


@pytest.mark.parametrize("method_name", CHOOSERS)
def test_refresh_disables_removed_drive_until_it_returns(window, monkeypatch, method_name):
    drive = FloppyDriveInfo("/dev/fd0", 737280)
    _discovery_results(window, monkeypatch, ([drive], []), ([], []), ([drive], []))

    def inspect(dialog):
        combo = _floppy_combo(dialog)
        refresh = _refresh_button(dialog)
        assert _ok_button(dialog).isEnabled()
        refresh.click()
        assert combo.count() == 1
        assert combo.currentData() is None
        assert combo.currentText() == "No supported floppy drive detected"
        assert not combo.isEnabled()
        assert not _ok_button(dialog).isEnabled()
        assert refresh.isEnabled()
        refresh.click()
        assert combo.currentData() == drive
        assert combo.isEnabled()
        assert _ok_button(dialog).isEnabled()
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    assert getattr(window, method_name)() is None


@pytest.mark.parametrize("method_name", CHOOSERS)
def test_refresh_preserves_selected_path_when_drives_reorder(window, monkeypatch, method_name):
    first = FloppyDriveInfo("/dev/fd0", 737280)
    selected = FloppyDriveInfo("/dev/fd1", 737280, label="Old disk")
    updated = FloppyDriveInfo("/dev/fd1", 1474560, label="New disk")
    _discovery_results(window, monkeypatch, ([first, selected], []), ([updated, first], []))

    def inspect(dialog):
        combo = _floppy_combo(dialog)
        combo.setCurrentIndex(1)
        _refresh_button(dialog).click()
        assert combo.currentIndex() == 0
        assert combo.currentData() == updated
        assert combo.currentText() == updated.display_name
        assert _ok_button(dialog).isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = getattr(window, method_name)()
    assert result.get("source", result.get("target")) == updated


def test_refresh_finds_greaseweazle_for_selected_read_source(window, monkeypatch):
    device = GreaseweazleDeviceInfo("COM4", "Greaseweazle")
    _discovery_results(window, monkeypatch, ([], []), ([], [device]))

    def inspect(dialog):
        source_combo = next(combo for combo in dialog.findChildren(QComboBox)
                            if combo.findData("floppy_gw") >= 0)
        source_combo.setCurrentIndex(source_combo.findData("floppy_gw"))
        device_combo = next(combo for combo in dialog.findChildren(QComboBox)
                            if combo.currentText() == "No Greaseweazle device detected")
        assert not device_combo.isEnabled()
        assert not _ok_button(dialog).isEnabled()
        _refresh_button(dialog).click()
        assert source_combo.currentData() == "floppy_gw"
        assert device_combo.currentData() == device
        assert device_combo.isEnabled()
        assert _ok_button(dialog).isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = window._choose_floppy_read_options()
    assert result["source"].device_path == device.path


@pytest.mark.parametrize("refreshed_size, expected_format", (
    (737280, "ibm.1440"),
    (368640, "ibm.360"),
))
def test_image_refresh_retains_manual_size_unless_disk_geometry_changes(
    window, monkeypatch, refreshed_size, expected_format,
):
    drive = FloppyDriveInfo("/dev/fd0", 737280, label="Original label")
    refreshed = FloppyDriveInfo("/dev/fd0", refreshed_size, label="Updated label")
    _discovery_results(window, monkeypatch, ([drive], []), ([refreshed], []))

    def inspect(dialog):
        format_combo = next(combo for combo in dialog.findChildren(QComboBox)
                            if combo.findData(DISK_FORMAT_BY_KEY["ibm.720"]) >= 0)
        format_combo.setCurrentIndex(format_combo.findData(DISK_FORMAT_BY_KEY["ibm.1440"]))
        _refresh_button(dialog).click()
        assert format_combo.currentData().key == expected_format
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    result = window._choose_floppy_image_capture_options()
    assert result["source"] == refreshed
    assert result["disk_format"].key == expected_format
