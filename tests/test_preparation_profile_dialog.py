import os
from dataclasses import replace
from html import escape

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from aps_midi_prep_tool_app import preparation_profile_dialog as dialog_module
from aps_midi_prep_tool_app import preparation_profiles as profiles_module
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    COMPATIBILITY_SOURCE, SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT, SETTING_MEDIUM, SETTING_PROFILE,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _rows(dialog):
    return [tuple(dialog.changes_table.item(row, column).text() for column in range(3))
            for row in range(dialog.changes_table.rowCount())]


@pytest.fixture
def grouped_profiles(monkeypatch):
    profiles = tuple(profiles_module.get_preparation_profile(key) for key in ("unsure", "custom", "mark_ii")) + (
        replace(
            profiles_module.get_preparation_profile("mark_ii_xg"),
            key="test_pianodisc", label="Test controller", category="pianodisc",
            midi_types=(0,),
            source_url='https://example.test/guide?model="A"&language=en',
            source_label="PianoDisc <Guide> & setup",
            preparation_note="Copy the prepared MIDI songs to the controller's music folder.",
        ),
    )
    monkeypatch.setattr(dialog_module, "PIANO_PROFILES", profiles)
    monkeypatch.setattr(profiles_module, "PROFILES_BY_KEY", {profile.key: profile for profile in profiles})
    return profiles


def test_profile_groups_have_disabled_bold_headings_and_keep_stable_item_keys(application, tmp_path, grouped_profiles):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "mark_ii")
    try:
        combo = dialog.profile_combo
        headings = [index for index in range(combo.count()) if combo.itemData(index) is None]
        assert [combo.itemText(index) for index in headings] == ["General", "Disklavier", "PianoDisc"]
        for index in headings:
            item = combo.model().item(index)
            assert not item.flags() & (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            assert item.font().bold()
        for profile in grouped_profiles:
            index = combo.findData(profile.key)
            assert index >= 0
            assert combo.itemData(index) == profile.key
            assert combo.itemText(index) == profile.label
        assert dialog.selection()[0].key == "mark_ii"
    finally:
        dialog.close()


def test_profile_keyboard_and_mouse_navigation_cannot_select_group_headings(application, tmp_path, grouped_profiles):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "custom")
    try:
        dialog.show()
        combo = dialog.profile_combo
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentData() == "mark_ii"
        QTest.keyClick(combo, Qt.Key.Key_Up)
        assert combo.currentData() == "custom"
        QTest.keyClick(combo, Qt.Key.Key_End)
        assert combo.currentData() == "test_pianodisc"
        QTest.keyClick(combo, Qt.Key.Key_Home)
        assert combo.currentData() == "unsure"

        combo.setCurrentIndex(combo.findData("custom"))
        combo.showPopup()
        application.processEvents()
        heading_index = combo.model().index(combo.findText("Disklavier"), 0)
        heading_rect = combo.view().visualRect(heading_index)
        assert not heading_rect.isEmpty()
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=heading_rect.center())
        assert combo.currentData() == "custom"
        assert dialog.selection()[0].key == "custom"
    finally:
        dialog.profile_combo.hidePopup()
        dialog.close()


def test_typing_a_model_name_selects_it_across_disabled_group_headings(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "unsure")
    try:
        dialog.show()
        combo = dialog.profile_combo
        combo.setFocus()
        selected_keys = []
        combo.currentIndexChanged.connect(lambda _index: selected_keys.append(combo.currentData()))

        QTest.keyClicks(combo, "PDS")

        assert combo.currentData() == "pianodisc_128plus"
        assert dialog.selection()[0].key == "pianodisc_128plus"
        assert selected_keys
        assert all(key is not None for key in selected_keys)
        assert combo.currentText() == "PDS-128 Plus"
    finally:
        dialog.close()


@pytest.mark.parametrize("direction", (Qt.LayoutDirection.LeftToRight, Qt.LayoutDirection.RightToLeft))
def test_popup_indents_profile_text_only_on_the_leading_edge(application, tmp_path, direction):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "custom")
    try:
        combo = dialog.profile_combo
        view = combo.view()
        view.setLayoutDirection(direction)
        delegate = view.itemDelegate()
        standard = QStyledItemDelegate(view)
        style = view.style()
        for row in (combo.findData("custom"), combo.findText("General")):
            index = combo.model().index(row, 0)
            base = QStyleOptionViewItem()
            base.initFrom(view)
            base.rect = QRect(0, 0, 400, 30)
            base.direction = direction
            standard.initStyleOption(base, index)
            indented = QStyleOptionViewItem(base)
            delegate.initStyleOption(indented, index)
            base_text = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, base, view)
            popup_text = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, indented, view)
            assert indented.text == base.text
            if combo.itemData(row) is None:
                assert popup_text == base_text
                assert delegate.sizeHint(base, index) == standard.sizeHint(base, index)
            else:
                if direction == Qt.LayoutDirection.LeftToRight:
                    assert popup_text.left() > base_text.left()
                    assert popup_text.right() == base_text.right()
                else:
                    assert popup_text.right() < base_text.right()
                    assert popup_text.left() == base_text.left()
                assert delegate.sizeHint(base, index).width() > standard.sizeHint(base, index).width()
        assert combo.currentText() == "Custom"
        assert combo.itemText(combo.currentIndex()) == "Custom"
    finally:
        dialog.close()


@pytest.mark.parametrize("unknown_key", ("removed_controller", None, ""))
def test_grouped_profiles_restore_saved_keys_and_unknown_keys_fall_back_to_unsure(application, tmp_path, grouped_profiles, unknown_key):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue(SETTING_PROFILE, "test_pianodisc")
    settings.setValue(SETTING_MEDIUM, "flashfloppy_hfe")
    before = {key: settings.value(key) for key in settings.allKeys()}
    dialog = PreparationProfileDialog(settings, settings.value(SETTING_PROFILE), settings.value(SETTING_MEDIUM))
    fallback = PreparationProfileDialog(settings, unknown_key)
    try:
        profile, medium = dialog.selection()
        assert profile.key == "test_pianodisc"
        assert medium.key == "flashfloppy_hfe"
        assert dialog.profile_combo.currentData() == "test_pianodisc"
        assert fallback.profile_combo.currentData() == "unsure"
        assert fallback.selection()[0].key == "unsure"
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii"))
        assert {key: settings.value(key) for key in settings.allKeys()} == before
    finally:
        dialog.close()
        fallback.close()


def test_manufacturer_note_and_escaped_sources_follow_selection(application, tmp_path, grouped_profiles):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    manufacturer = grouped_profiles[-1]
    medium = profiles_module.MEDIA_BY_KEY["flashfloppy_hfe"]
    dialog = PreparationProfileDialog(settings, manufacturer.key, medium.key)
    try:
        assert dialog.preparation_note_label.text() == manufacturer.preparation_note
        assert dialog.preparation_note_label.wordWrap()
        assert dialog.preparation_note_label.textFormat() == Qt.TextFormat.PlainText
        assert not dialog.preparation_note_label.isHidden()
        sources = dialog.source_label.text()
        assert f'href="{escape(manufacturer.source_url, quote=True)}"' in sources
        assert escape(manufacturer.source_label, quote=True) in sources
        assert medium.source_url in sources
        assert "Emulator documentation" in sources
        assert COMPATIBILITY_SOURCE not in sources
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("custom"))
        assert dialog.preparation_note_label.isHidden()
        assert dialog.preparation_note_label.text() == ""
        assert COMPATIBILITY_SOURCE in dialog.source_label.text()
        assert manufacturer.source_url not in dialog.source_label.text()
    finally:
        dialog.close()


@pytest.mark.parametrize("conversion_count", (0, 3))
def test_type_zero_profile_shows_requirement_and_needed_conversions(application, tmp_path, grouped_profiles, conversion_count):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(
        settings, "test_pianodisc", song_counts={"midi_non_type0": conversion_count},
    )
    try:
        rows = _rows(dialog)
        assert ("MIDI type", "Current default", "MIDI Type 0") in rows
        conversions = [row for row in rows if row[0] == "Convert songs"]
        assert conversions == ([("Convert songs", str(conversion_count), "MIDI → SMF0 (staged)")] if conversion_count else [])
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii"))
        assert not any(row[0] == "MIDI type" for row in _rows(dialog))
        assert not any("SMF0" in row[2] for row in _rows(dialog))
    finally:
        dialog.close()


def test_summary_combines_related_defaults_and_keeps_required_counts(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue("emulator_image_content", "midi")
    settings.setValue("use_dos83_filenames", False)
    settings.setValue("emulator_image_disk_format", "ibm.1440")
    settings.setValue(SETTING_DISK_FORMAT, "ibm.1440")
    settings.setValue("emulator_image_output_format", "img")
    settings.setValue(SETTING_IMAGE_FORMAT, "img")
    settings.setValue("emulator_image_prefix", "MINE")
    settings.setValue("emulator_image_starting_number", 12)
    before = {key: settings.value(key) for key in settings.allKeys()}
    dialog = PreparationProfileDialog(settings, "mark_ii", "nalbantov_slim", song_counts={"midi": 3, "dos83_midi": 2})
    try:
        assert _rows(dialog) == [
            ("Song format", "MIDI", "Yamaha E-SEQ + PIANODIR.FIL"),
            ("Filenames", "Descriptive filenames", "DOS 8.3 · 2 to rename"),
            ("Disk size", "1.44 MB (2HD)", "720 KB (2DD)"),
            ("Image type", "IMG", "HFE"),
            ("Convert songs", "3", "MIDI → E-SEQ (staged)"),
        ]
        assert {key: settings.value(key) for key in settings.allKeys()} == before
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert all(len(text) < 400 for text in labels)
        assert not any("MIDI IN" in text or "Piano requirements" in text or "not written" in text for text in labels)
        assert "reviewed" not in dialog.source_label.text()
    finally:
        dialog.close()


@pytest.mark.parametrize("medium_key", ("nalbantov_slim", "flashfloppy_img", "flashfloppy_hfe"))
def test_emulator_summary_omits_numbering_row(application, tmp_path, medium_key):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue("emulator_image_starting_number", 12)
    dialog = PreparationProfileDialog(settings, "mark_ii", medium_key)
    try:
        assert [row[0] for row in _rows(dialog)] == ["Song format", "Filenames", "Disk size", "Image type"]
        assert not any("First image" in cell or "DSKA" in cell for row in _rows(dialog) for cell in row)
        assert settings.value("emulator_image_starting_number", type=int) == 12
    finally:
        dialog.close()


def test_summary_represents_mixed_existing_defaults_once(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue("emulator_image_disk_format", "ibm.1440")
    settings.setValue(SETTING_DISK_FORMAT, "ibm.720")
    settings.setValue("emulator_image_output_format", "img")
    settings.setValue(SETTING_IMAGE_FORMAT, "hfe")
    settings.setValue("use_dos83_filenames", True)
    settings.setValue("long_midi_filenames", True)
    dialog = PreparationProfileDialog(settings, "mark_ii_xg", "original")
    try:
        rows = {label: (current, proposed) for label, current, proposed in _rows(dialog)}
        assert rows["Disk size"] == ("Mixed", "1.44 MB (2HD)")
        assert rows["Image type"] == ("Mixed", "IMG")
        assert rows["Filenames"] == ("Mixed", "DOS 8.3")
        assert len(rows) == 4
    finally:
        dialog.close()


def test_usb_summary_omits_disk_defaults_but_shows_required_eseq_conversion(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "enspire", "usb", song_counts={"eseq": 2})
    try:
        assert [row[0] for row in _rows(dialog)] == ["Song format", "Filenames", "Convert songs"]
        assert _rows(dialog)[-1] == ("Convert songs", "2", "E-SEQ → MIDI (staged)")
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("custom"))
        assert dialog.changes_table.isHidden()
        assert not dialog.manual_label.isHidden()
        assert dialog.manual_label.text() == "Keep current settings"
        assert not dialog.source_label.isHidden()
        assert COMPATIBILITY_SOURCE in dialog.source_label.text()
        assert "APS Disklavier Compatibility Table" in dialog.source_label.text()
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("midi_export"))
        assert not dialog.source_label.isHidden()
        assert COMPATIBILITY_SOURCE in dialog.source_label.text()
    finally:
        dialog.close()


def test_summary_retains_separate_required_clavinova_container_conversion(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "mark_ii", "original", song_counts={"clavinova": 1})
    try:
        assert _rows(dialog)[-1] == ("Convert songs", "1", "Clavinova MDA → Disklavier E-SEQ (staged)")
        assert dialog.changes_table.rowCount() == 5
    finally:
        dialog.close()
