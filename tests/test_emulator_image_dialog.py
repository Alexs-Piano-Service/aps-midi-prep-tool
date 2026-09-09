import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QLineEdit,
    QComboBox,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.preparation_profiles import SETTING_PROFILE, get_preparation_profile
from aps_midi_prep_tool_app.ui_utils import center_dialog_on_parent


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, *, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self.values[key] = value


class _EmulatorDialogHarness(QWidget):
    SETTING_EMULATOR_IMAGE_SOURCE = MidiTitleWindow.SETTING_EMULATOR_IMAGE_SOURCE
    SETTING_EMULATOR_IMAGE_OUTPUT = MidiTitleWindow.SETTING_EMULATOR_IMAGE_OUTPUT
    SETTING_EMULATOR_IMAGE_PREFIX = MidiTitleWindow.SETTING_EMULATOR_IMAGE_PREFIX
    SETTING_EMULATOR_IMAGE_STARTING_NUMBER = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_STARTING_NUMBER
    )
    SETTING_EMULATOR_IMAGE_SAFETY_MARGIN_KIB = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_SAFETY_MARGIN_KIB
    )
    SETTING_EMULATOR_IMAGE_ALBUM_TITLE_OVERRIDE = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_ALBUM_TITLE_OVERRIDE
    )
    SETTING_EMULATOR_IMAGE_CONTENT = MidiTitleWindow.SETTING_EMULATOR_IMAGE_CONTENT
    SETTING_EMULATOR_IMAGE_OUTPUT_FORMAT = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_OUTPUT_FORMAT
    )
    SETTING_EMULATOR_IMAGE_DISK_FORMAT = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_DISK_FORMAT
    )
    SETTING_EMULATOR_IMAGE_INCLUDE_SUBFOLDERS = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_INCLUDE_SUBFOLDERS
    )
    SETTING_EMULATOR_IMAGE_SHUFFLE = MidiTitleWindow.SETTING_EMULATOR_IMAGE_SHUFFLE
    SETTING_EMULATOR_IMAGE_DISK_LAYOUT = MidiTitleWindow.SETTING_EMULATOR_IMAGE_DISK_LAYOUT
    SETTING_EMULATOR_IMAGE_INCLUDE_SONG_LISTS = (
        MidiTitleWindow.SETTING_EMULATOR_IMAGE_INCLUDE_SONG_LISTS
    )

    show_emulator_image_utility = MidiTitleWindow.show_emulator_image_utility
    _language_code = MidiTitleWindow._language_code
    _t = MidiTitleWindow._t
    _lt = MidiTitleWindow._lt
    _translate_dialog_tree = MidiTitleWindow._translate_dialog_tree
    _translate_dialog_button_box = MidiTitleWindow._translate_dialog_button_box

    def __init__(self, source_directory):
        super().__init__()
        self.currentLanguage = "en"
        self.source_directory = os.fspath(source_directory)
        self.settings = _Settings(
            {self.SETTING_EMULATOR_IMAGE_INCLUDE_SUBFOLDERS: False}
        )
        self.recursive_option = None
        self.build_call = None
        self.inspect_dialog = None
        self.run_dialog_event_loop = False

    def _disk_worker_busy(self):
        return False

    def _emulator_image_default_source_directory(self):
        return self.source_directory

    def _make_dialog_button_box(self, buttons, parent):
        return QDialogButtonBox(buttons, parent=parent)

    def _exec_child_dialog(self, dialog, *, resize_to_contents=True):
        assert resize_to_contents is False
        if self.run_dialog_event_loop:
            errors = []

            def inspect():
                try:
                    dialog.done(self.inspect_dialog(dialog))
                except BaseException as exc:
                    errors.append(exc)
                    dialog.reject()

            QTimer.singleShot(150, inspect)
            result = MidiTitleWindow._exec_child_dialog(
                self, dialog, resize_to_contents=resize_to_contents,
            )
            if errors:
                raise errors[0]
            return result
        if self.inspect_dialog is not None:
            return self.inspect_dialog(dialog)
        checkbox = dialog.findChild(
            QCheckBox,
            "emulatorIncludeSubfoldersCheckbox",
        )
        self.recursive_option = {
            "text": checkbox.text(),
            "tooltip": checkbox.toolTip(),
            "checked": checkbox.isChecked(),
        }
        checkbox.setChecked(True)
        return QDialog.Accepted

    def _start_emulator_image_build(self, *args, **kwargs):
        self.build_call = (args, kwargs)


@pytest.mark.parametrize("profile_key", ("mark_ii", "mark_ii_xg", "mark_iii", "enspire"))
def test_builder_respects_destination_format_despite_conflicting_saved_content(tmp_path, profile_key):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    target = get_preparation_profile(profile_key).song_format
    other = "midi" if target == "eseq" else "eseq"
    window.settings.setValue(SETTING_PROFILE, profile_key)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_CONTENT, other)

    def inspect(dialog):
        content = dialog.findChild(QComboBox, "emulatorContentCombo")
        assert content.currentData() == target
        other_index = content.findData(other)
        assert not content.model().item(other_index).isEnabled()
        assert content.model().item(content.findData(target)).isEnabled()
        reason = MidiTitleWindow._preparation_conversion_restriction(window, other)
        assert content.toolTip() == reason
        assert content.itemData(other_index, Qt.ToolTipRole) == reason
        assert dialog.findChild(QLabel, "emulatorContentRestrictionHint").text() == reason
        content.setCurrentIndex(other_index)
        assert content.currentData() == target
        return QDialog.Accepted

    window.inspect_dialog = inspect
    try:
        window.show_emulator_image_utility()
        assert window.build_call[1]["output_content"] == target
        assert window.settings.value(window.SETTING_EMULATOR_IMAGE_CONTENT) == target
    finally:
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("profile_key", ("custom", "unsure"))
def test_builder_custom_destination_keeps_both_format_choices(tmp_path, profile_key):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    window.settings.setValue(SETTING_PROFILE, profile_key)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_CONTENT, "midi")

    def inspect(dialog):
        content = dialog.findChild(QComboBox, "emulatorContentCombo")
        assert content.currentData() == "midi"
        assert all(content.model().item(index).isEnabled() for index in range(content.count()))
        assert content.toolTip() == ""
        assert dialog.findChild(QLabel, "emulatorContentRestrictionHint") is None
        content.setCurrentIndex(content.findData("eseq"))
        assert content.currentData() == "eseq"
        return QDialog.Accepted

    window.inspect_dialog = inspect
    try:
        window.show_emulator_image_utility()
        assert window.build_call[1]["output_content"] == "eseq"
    finally:
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("include_song_lists", [False, True])
def test_completed_build_shows_all_file_warnings_even_without_song_lists(tmp_path, include_song_lists):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    window.status_label = QLabel(window)
    window._close_emulator_image_progress = lambda: None
    logged = []
    window._log_event = lambda *args, **kwargs: logged.append(kwargs)
    messages = []
    window._exec_child_dialog = lambda message: messages.append(
        (message.icon(), message.text(), message.detailedText())
    )
    warnings = tuple(f"DSKA0001.hfe / SONG{index:02d}.MID: damaged source preserved" for index in range(12))
    result = SimpleNamespace(
        source_directory=str(tmp_path), output_directory=str(tmp_path / "output"),
        files_prepared=12, converted_files=0, images_created=1,
        output_content="midi", shuffled=False, output_paths=("DSKA0001.hfe",),
        song_list_path="song-lists.txt" if include_song_lists else "", warnings=warnings,
    )
    try:
        MidiTitleWindow._on_emulator_image_success(window, result)
        icon, summary, detail = messages[0]
        assert icon == QMessageBox.Warning
        assert "may not play correctly" in summary
        assert "Details" in summary
        assert all(warning in detail for warning in warnings)
        assert all(warning in logged[0]["warnings"] for warning in warnings)
        assert bool("song-lists.txt" in summary) == include_song_lists
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_emulator_disk_set_dialog_persists_and_forwards_recursive_option(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)

    window.show_emulator_image_utility()

    assert window.recursive_option == {
        "text": "Include nested folders",
        "tooltip": (
            "Include supported MIDI and E-SEQ songs from every nested subfolder. "
            "Turn this off to use only songs directly in the selected source folder."
        ),
        "checked": False,
    }
    assert window.settings.values[
        window.SETTING_EMULATOR_IMAGE_INCLUDE_SUBFOLDERS
    ] is True
    assert window.build_call[1]["include_subfolders"] is True
    assert window.build_call[1]["disk_layout"] == "fill"

    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("saved_layout", [None, "folders", "fill"])
def test_dialog_restores_and_remembers_disk_layout(tmp_path, saved_layout):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    window.settings = _Settings()
    if saved_layout is not None:
        window.settings.setValue(window.SETTING_EMULATOR_IMAGE_DISK_LAYOUT, saved_layout)

    def inspect(dialog):
        folders = dialog.findChild(QRadioButton, "emulatorFoldersRadio")
        fill = dialog.findChild(QRadioButton, "emulatorFillRadio")
        assert folders.isChecked() is (saved_layout != "fill")
        assert fill.isChecked() is (saved_layout == "fill")
        assert dialog.findChild(QCheckBox, "emulatorIncludeSubfoldersCheckbox").isChecked()
        (folders if saved_layout == "fill" else fill).setChecked(True)
        return QDialog.Accepted

    window.inspect_dialog = inspect
    window.show_emulator_image_utility()

    expected = "folders" if saved_layout == "fill" else "fill"
    assert window.build_call[1]["disk_layout"] == expected
    assert window.settings.values[window.SETTING_EMULATOR_IMAGE_DISK_LAYOUT] == expected
    assert window.build_call[1]["include_subfolders"] is True
    window.deleteLater()
    app.processEvents()


def test_folder_mode_guidance_naming_and_advanced_options(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)

    def inspect(dialog):
        folders = dialog.findChild(QRadioButton, "emulatorFoldersRadio")
        folders.setChecked(True)
        recursive = dialog.findChild(QCheckBox, "emulatorIncludeSubfoldersCheckbox")
        shuffle = dialog.findChild(QCheckBox, "emulatorShuffleCheckbox")
        album = dialog.findChild(QLineEdit, "emulatorAlbumTitleEdit")
        hints = dialog.findChild(QStackedWidget, "emulatorLayoutHints")
        preview = dialog.findChild(QLabel, "emulatorNamingExample")
        assert "large albums continue on extra disks" in hints.currentWidget().text()
        assert shuffle.text() == "Shuffle songs within each folder"
        assert album.placeholderText() == "Defaults to the catalog title or folder name"
        assert recursive.isChecked()
        assert not recursive.isEnabled()
        assert "DSKA001/ → DSKA0000.hfe" in preview.text()

        advanced = dialog.findChild(QWidget, "emulatorAdvancedOptions")
        assert advanced.isHidden()
        dialog.findChild(QToolButton, "emulatorAdvancedToggle").setChecked(True)
        assert not advanced.isHidden()
        dialog.findChild(QLineEdit, "emulatorPrefixEdit").setText("DSKB")
        dialog.findChild(QSpinBox, "emulatorStartingNumberSpin").setValue(12)
        output = dialog.findChild(QComboBox, "emulatorOutputFormatCombo")
        output.setCurrentIndex(output.findData("img"))
        assert "DSKB0012.img" in preview.text()
        content = dialog.findChild(QComboBox, "emulatorContentCombo")
        content.setCurrentIndex(content.findData("midi"))
        assert not album.isEnabled()
        content.setCurrentIndex(content.findData("eseq"))
        assert album.isEnabled()
        album.setText("Shared title")
        shuffle.setChecked(True)
        dialog.findChild(QRadioButton, "emulatorFillRadio").setChecked(True)
        assert shuffle.isChecked()
        assert album.text() == "Shared title"
        assert album.placeholderText() == "Defaults to each disk's catalog ID"
        assert preview.text() == "Disk names start at DSKB0012.img and count up."
        assert "Combine all selected songs" in hints.currentWidget().text()
        folders.setChecked(True)
        return QDialog.Accepted

    window.inspect_dialog = inspect
    window.show_emulator_image_utility()
    options = window.build_call[1]
    assert options["disk_layout"] == "folders"
    assert options["shuffle"] is True
    assert options["prefix"] == "DSKB"
    assert options["starting_number"] == 12
    assert options["album_title"] == "Shared title"
    assert options["output_ext"] == "img"
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("saved_scan", [False, True])
@pytest.mark.parametrize("saved_layout", ["fill", "folders"])
def test_folder_scan_is_required_and_fill_preference_survives_switching_and_reopening(
    tmp_path, saved_scan, saved_layout,
):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_INCLUDE_SUBFOLDERS, saved_scan)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_DISK_LAYOUT, saved_layout)

    def inspect(dialog):
        folders = dialog.findChild(QRadioButton, "emulatorFoldersRadio")
        fill = dialog.findChild(QRadioButton, "emulatorFillRadio")
        scan = dialog.findChild(QCheckBox, "emulatorIncludeSubfoldersCheckbox")
        assert scan.isChecked() is (saved_layout == "folders" or saved_scan)
        assert scan.isEnabled() is (saved_layout == "fill")
        folders.setChecked(True)
        assert scan.isChecked() and not scan.isEnabled()
        assert "Required for one album per folder" in scan.toolTip()
        scan.click()
        assert scan.isChecked()
        fill.setChecked(True)
        assert scan.isEnabled() and scan.isChecked() is saved_scan
        scan.setChecked(not saved_scan)
        folders.setChecked(True)
        assert scan.isChecked() and not scan.isEnabled()
        return QDialog.Accepted

    window.inspect_dialog = inspect
    window.show_emulator_image_utility()
    assert window.build_call[1]["disk_layout"] == "folders"
    assert window.build_call[1]["include_subfolders"] is True
    assert window.settings.values[window.SETTING_EMULATOR_IMAGE_INCLUDE_SUBFOLDERS] is not saved_scan

    def inspect_reopened(dialog):
        scan = dialog.findChild(QCheckBox, "emulatorIncludeSubfoldersCheckbox")
        assert dialog.findChild(QRadioButton, "emulatorFoldersRadio").isChecked()
        assert scan.isChecked() and not scan.isEnabled()
        dialog.findChild(QRadioButton, "emulatorFillRadio").setChecked(True)
        assert scan.isEnabled() and scan.isChecked() is not saved_scan
        return QDialog.Accepted

    window.inspect_dialog = inspect_reopened
    window.show_emulator_image_utility()
    assert window.build_call[1]["disk_layout"] == "fill"
    assert window.build_call[1]["include_subfolders"] is not saved_scan
    window.deleteLater()
    app.processEvents()


def test_cancelling_dialog_keeps_settings_and_does_not_build(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    saved = dict(window.settings.values)

    def inspect(dialog):
        dialog.findChild(QRadioButton, "emulatorFoldersRadio").setChecked(True)
        return QDialog.Rejected

    window.inspect_dialog = inspect
    window.show_emulator_image_utility()
    assert window.settings.values == saved
    assert window.build_call is None
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("saved_song_lists", [False, True])
def test_song_lists_is_a_remembered_global_build_option(tmp_path, saved_song_lists):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    window.settings.setValue(window.SETTING_EMULATOR_IMAGE_INCLUDE_SONG_LISTS, saved_song_lists)

    def inspect(dialog):
        song_lists = dialog.findChild(QCheckBox, "emulatorSongListsCheckbox")
        assert song_lists.parentWidget() is dialog
        assert dialog.layout().indexOf(song_lists) >= 0
        assert "entire set" in song_lists.toolTip()
        for mode in ["emulatorFoldersRadio", "emulatorFillRadio"]:
            dialog.findChild(QRadioButton, mode).setChecked(True)
            content = dialog.findChild(QComboBox, "emulatorContentCombo")
            for output_content in ["midi", "eseq"]:
                content.setCurrentIndex(content.findData(output_content))
                assert song_lists.isEnabled()
                assert song_lists.isChecked() is saved_song_lists
        song_lists.setChecked(not saved_song_lists)
        return QDialog.Accepted

    window.inspect_dialog = inspect
    window.show_emulator_image_utility()
    assert window.build_call[1]["include_song_lists"] is not saved_song_lists
    assert window.settings.values[window.SETTING_EMULATOR_IMAGE_INCLUDE_SONG_LISTS] is not saved_song_lists

    def inspect_reopened(dialog):
        assert dialog.findChild(QCheckBox, "emulatorSongListsCheckbox").isChecked() is not saved_song_lists
        return QDialog.Rejected

    window.inspect_dialog = inspect_reopened
    window.show_emulator_image_utility()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_translated_folder_dialog_keeps_controls_accessible_on_small_screens(tmp_path, language):
    app = QApplication.instance() or QApplication([])
    window = _EmulatorDialogHarness(tmp_path)
    window.currentLanguage = language
    window.settings = _Settings()
    window.run_dialog_event_loop = True

    def inspect(dialog):
        dialog.setMaximumHeight(460)
        dialog.findChild(QToolButton, "emulatorAdvancedToggle").setChecked(True)
        center_dialog_on_parent(dialog, window, adjust_size=False)
        dialog.show()
        app.processEvents()
        preview = dialog.findChild(QLabel, "emulatorNamingExample").text()
        assert "DSKA0000.hfe" in preview
        assert "{folder}" not in preview and "{image}" not in preview
        assert dialog.height() <= 460
        assert dialog.width() >= 600
        scroll = dialog.findChild(QScrollArea, "emulatorOptionsScroll")
        assert scroll.verticalScrollBar().maximum() > 0
        buttons = dialog.findChild(QDialogButtonBox)
        assert not scroll.isAncestorOf(buttons)
        assert dialog.rect().contains(buttons.geometry())
        song_lists = dialog.findChild(QCheckBox, "emulatorSongListsCheckbox")
        assert not scroll.isAncestorOf(song_lists)
        assert dialog.rect().contains(song_lists.geometry())
        scroll.ensureWidgetVisible(dialog.findChild(QLineEdit, "emulatorAlbumTitleEdit"))
        assert scroll.verticalScrollBar().value() > 0
        dialog.close()
        return QDialog.Rejected

    window.inspect_dialog = inspect
    window.show_emulator_image_utility()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("font_size", [9, 14])
def test_live_dialog_keeps_user_geometry_through_mode_and_advanced_changes(tmp_path, font_size):
    app = QApplication.instance() or QApplication([])
    original_font = QFont(app.font())
    app.setFont(QFont(original_font.family(), font_size))
    window = _EmulatorDialogHarness(tmp_path)
    window.settings = _Settings()
    window.run_dialog_event_loop = True

    def inspect(dialog):
        assert dialog.height() <= (540 if font_size == 9 else 640)
        assert not dialog.property("_aps_recenter_on_content_change")
        assert len(dialog.findChildren(QDialogButtonBox)) == 1
        assert len(dialog.findChildren(QScrollArea)) == 1
        buttons = dialog.findChild(QDialogButtonBox)
        assert [button.text() for button in buttons.buttons()
                if buttons.buttonRole(button) == QDialogButtonBox.AcceptRole] == ["Build Disk Set"]
        scroll = dialog.findChild(QScrollArea)
        folders = dialog.findChild(QRadioButton, "emulatorFoldersRadio")
        fill = dialog.findChild(QRadioButton, "emulatorFillRadio")
        advanced = dialog.findChild(QToolButton, "emulatorAdvancedToggle")
        recursive = dialog.findChild(QCheckBox, "emulatorIncludeSubfoldersCheckbox")
        assert recursive.font().pointSize() == font_size
        for width, height in [(740, 520), (610, 440), (750, 590)]:
            dialog.resize(width, height)
            dialog.move(20, 30)
            QTest.qWait(120)
            assert dialog.size().toTuple() == (width, height)
            geometry = dialog.geometry()
            for mode in [fill, folders]:
                hint_height = dialog.findChild(QStackedWidget, "emulatorLayoutHints").height()
                recursive_position = recursive.pos()
                mode.setChecked(True)
                QTest.qWait(60)
                assert dialog.geometry() == geometry
                assert dialog.findChild(QStackedWidget, "emulatorLayoutHints").height() == hint_height
                assert recursive.pos() == recursive_position
            for expanded in [True, False]:
                advanced.setChecked(expanded)
                QTest.qWait(60)
                assert dialog.geometry() == geometry
                assert dialog.rect().contains(buttons.geometry())
            assert not scroll.isAncestorOf(buttons)
        return QDialog.Rejected

    window.inspect_dialog = inspect
    try:
        window.show_emulator_image_utility()
    finally:
        window.deleteLater()
        app.processEvents()
        app.setFont(original_font)
