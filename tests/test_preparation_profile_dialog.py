import io
import os
from dataclasses import replace
from html import escape

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mido
import pytest
from PySide6.QtCore import QEvent, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem,
)

from aps_midi_prep_tool_app import preparation_profile_dialog as dialog_module
from aps_midi_prep_tool_app import preparation_profiles as profiles_module
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
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
        assert dialog.source_label.isHidden()
        assert dialog.source_label.text() == ""
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
        assert conversions == ([("Convert songs", "—", f"{conversion_count} · MIDI → SMF0 (staged)")] if conversion_count else [])
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii"))
        assert not any(row[0] == "MIDI type" for row in _rows(dialog))
        assert not any("SMF0" in row[2] for row in _rows(dialog))
    finally:
        dialog.close()


def test_details_combine_related_defaults_and_keep_required_counts(application, tmp_path):
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
    dialog = PreparationProfileDialog(settings, "mark_ii", "nalbantov", song_counts={"midi": 3, "dos83_midi": 2})
    try:
        assert _rows(dialog) == [
            ("Song format", "MIDI", "Yamaha E-SEQ + PIANODIR.FIL"),
            ("Filenames", "Descriptive filenames", "DOS 8.3 · 2 to rename"),
            ("Disk size", "1.44 MB (2HD)", "720 KB (2DD)"),
            ("Image type", "IMG", "HFE"),
            ("Image prefix", "MINE", "DSKA"),
            ("Starting disk number", "12", "0"),
            ("Convert songs", "—", "3 · MIDI → E-SEQ (staged)"),
            ("Format for Disklavier screen", "Current default", "On"),
        ]
        assert {key: settings.value(key) for key in settings.allKeys()} == before
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert all(len(text) < 400 for text in labels)
        assert not any("MIDI IN" in text or "Piano requirements" in text or "not written" in text for text in labels)
        assert "reviewed" not in dialog.source_label.text()
    finally:
        dialog.close()


@pytest.mark.parametrize("medium_key", ("nalbantov", "flashfloppy_img", "flashfloppy_hfe"))
def test_emulator_numbering_is_reviewable_in_collapsed_details(application, tmp_path, medium_key):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue("emulator_image_starting_number", 12)
    dialog = PreparationProfileDialog(settings, "mark_ii", medium_key)
    try:
        assert [row[0] for row in _rows(dialog)] == [
            "Song format", "Filenames", "Disk size", "Image type", "Image prefix",
            "Starting disk number", "Format for Disklavier screen",
        ]
        assert ("Image prefix", "Current default", "DSKA") in _rows(dialog)
        assert ("Starting disk number", "12", "0") in _rows(dialog)
        assert dialog.details_widget.isHidden()
        assert "DSKA" not in dialog.outcome_label.text()
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
        assert len(rows) == 5
    finally:
        dialog.close()


def test_usb_summary_omits_disk_defaults_but_shows_required_eseq_conversion(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "enspire", "usb", song_counts={"eseq": 2})
    try:
        assert [row[0] for row in _rows(dialog)] == ["Song format", "Filenames", "Convert songs", "Format for Disklavier screen", "Trim Title Spaces"]
        assert _rows(dialog)[-1] == ("Trim Title Spaces", "Off", "On")
        assert ("Convert songs", "—", "2 · E-SEQ → MIDI (staged)") in _rows(dialog)
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("custom"))
        assert dialog.changes_table.isHidden()
        assert _rows(dialog) == []
        assert not dialog.manual_label.isHidden()
        assert dialog.source_label.isHidden()
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("midi_export"))
        assert not dialog.source_label.isHidden()
        assert COMPATIBILITY_SOURCE in dialog.source_label.text()
    finally:
        dialog.close()


def test_summary_retains_separate_required_clavinova_container_conversion(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "mark_ii", "original", song_counts={"clavinova": 1})
    try:
        assert _rows(dialog)[-2] == ("Convert songs", "—", "1 · Clavinova MDA → Disklavier E-SEQ (staged)")
        assert dialog.changes_table.rowCount() == 6
    finally:
        dialog.close()


@pytest.mark.parametrize(
    "profile,medium,counts,expected",
    [
        ("mark_ii", "nalbantov", {"midi": 20, "eseq": 4}, "24 songs · 720 KB disk images · Originals unchanged"),
        ("mark_ii_xg", "flashfloppy_img", {"total": 5, "midi": 3, "eseq": 1}, "5 songs · 1.44 MB disk images · Originals unchanged"),
        ("pianodisc_128plus", "original", {"midi": 1}, "1 song · 720 KB floppy disks · Originals unchanged"),
        ("enspire", "usb", {"eseq": 2}, "2 songs · MIDI files · Originals unchanged"),
        ("custom", "custom", {}, "No songs loaded · Current work and settings kept"),
    ],
)
def test_outcome_summary_uses_loaded_counts_and_selected_delivery(application, tmp_path, profile, medium, counts, expected):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue("emulator_image_content", "midi")
    settings.setValue("emulator_image_disk_format", "ibm.1440")
    dialog = PreparationProfileDialog(settings, profile, medium, song_counts=counts)
    try:
        assert dialog.outcome_label.text() == expected
        assert dialog.attention_label.isHidden()
        assert dialog.buttons.button(QDialogButtonBox.Apply).text() == "Apply"
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("enspire"))
        assert "MIDI files" in dialog.outcome_label.text()
        assert "disk" not in dialog.outcome_label.text()
    finally:
        dialog.close()


@pytest.mark.parametrize("saved_format", ("midi", "eseq"))
def test_destination_preview_uses_actual_mixed_songs_despite_saved_image_preferences(
    application, modal_parent, tmp_path, monkeypatch, saved_format,
):
    parent = modal_parent
    parent.settings.setValue("emulator_image_content", saved_format)
    parent.settings.setValue("emulator_image_disk_format", "ibm.1440")
    parent.settings.setValue(SETTING_DISK_FORMAT, "ibm.1440")
    parent.settings.setValue("emulator_image_starting_number", 12)
    song = mido.MidiFile(type=0)
    song.tracks.append(mido.MidiTrack([
        mido.Message("note_on", note=60, velocity=64),
        mido.Message("note_off", note=60, time=480),
    ]))
    output = io.BytesIO()
    song.save(file=output)
    originals = {
        tmp_path / "SONG.MID": output.getvalue(),
        tmp_path / "SONG.FIL": convert_midi_bytes_to_eseq_bytes(output.getvalue()),
    }
    for path, data in originals.items():
        path.write_bytes(data)
    parent._load_regular_files([str(path) for path in originals], "Loaded", prepare_destination=False)
    before = {key: parent.settings.value(key) for key in parent.settings.allKeys()}
    observed = []

    def inspect_preview(dialog):
        observed.append(dialog)
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii"))
        dialog.medium_combo.setCurrentIndex(dialog.medium_combo.findData("nalbantov"))
        dialog.show()
        application.processEvents()
        assert dialog.song_counts["midi"] == dialog.song_counts["eseq"] == 1
        assert dialog.outcome_label.text() == "2 songs · 720 KB disk images · Originals unchanged"
        assert dialog.attention_label.isHidden()
        assert dialog.details_widget.isHidden()
        assert not dialog.changes_table.isVisible()
        assert not dialog.source_label.isVisible()
        assert dialog.height() < 300
        dialog.details_button.click()
        application.processEvents()
        assert dialog.changes_table.isVisible()
        assert dialog.changes_table.horizontalHeaderItem(1).text() == "Saved settings"
        assert ("Song format", profiles_module.display_setting(saved_format), "Yamaha E-SEQ + PIANODIR.FIL") in _rows(dialog)
        assert ("Convert songs", "—", "1 · MIDI → E-SEQ (staged)") in _rows(dialog)
        assert ("Starting disk number", "12", "0") in _rows(dialog)
        dialog.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(parent, "_exec_child_dialog", inspect_preview)
    parent.choose_preparation_profile()
    assert len(observed) == 1
    assert {key: parent.settings.value(key) for key in parent.settings.allKeys()} == before
    assert not parent.pendingRegularConversions
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_custom_preview_keeps_current_work_and_preferences(application, tmp_path):
    settings = QSettings(str(tmp_path / "manual.ini"), QSettings.IniFormat)
    for key, value in {
        "preparation_profile": "mark_ii", "use_dos83_filenames": True,
        "long_midi_filenames": False, "format_disklavier_screen": True,
        "emulator_image_starting_number": 42,
    }.items():
        settings.setValue(key, value)
    before = {key: settings.value(key) for key in settings.allKeys()}
    dialog = PreparationProfileDialog(settings, "mark_ii", song_counts={"eseq": 2})
    try:
        dialog.show()
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("custom"))
        assert dialog.selection()[1].key == "custom"
        assert dialog.outcome_label.text() == "2 songs · Current work and settings kept"
        assert dialog.attention_label.isHidden()
        dialog.details_button.click()
        application.processEvents()
        assert dialog.changes_table.isHidden()
        assert dialog.changes_table.rowCount() == 0
        assert dialog.manual_label.isVisible()
        assert dialog.manual_label.text() == "Stop automatic preparation and keep current work and settings."
        assert dialog.source_label.isHidden()
        assert dialog.preparation_note_label.isHidden()
        assert {key: settings.value(key) for key in settings.allKeys()} == before
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii"))
        assert dialog.changes_table.isVisible()
        assert dialog.changes_table.rowCount() > 0
        assert dialog.manual_label.isHidden()
        dialog.reject()
        assert {key: settings.value(key) for key in settings.allKeys()} == before
    finally:
        dialog.close()


def test_details_hide_settings_notes_sources_until_requested(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "pianodisc_128plus", song_counts={"midi": 4})
    try:
        dialog.show()
        application.processEvents()
        geometry = dialog.geometry()
        assert dialog.width() <= 700
        assert dialog.height() < 300
        _assert_preparation_controls_fit(dialog)
        assert dialog.outcome_label.isVisible()
        assert not dialog.details_button.isChecked()
        assert dialog.details_button.arrowType() == Qt.ArrowType.RightArrow
        assert all(not widget.isVisible() for widget in (
            dialog.changes_table, dialog.preparation_note_label, dialog.source_label,
        ))
        dialog.details_button.click()
        application.processEvents()
        assert dialog.details_button.arrowType() == Qt.ArrowType.DownArrow
        assert all(widget.isVisible() for widget in (
            dialog.changes_table, dialog.preparation_note_label, dialog.source_label,
        ))
        assert [dialog.changes_table.horizontalHeaderItem(column).text() for column in range(3)] == [
            "Setting", "Saved settings", "On apply",
        ]
        assert dialog.height() > geometry.height()
        assert dialog.geometry().topLeft() == geometry.topLeft()
        dialog.details_button.click()
        application.processEvents()
        assert not dialog.changes_table.isVisible()
        assert dialog.geometry() == geometry
        assert settings.allKeys() == []
    finally:
        dialog.close()


@pytest.mark.parametrize("attention", (0, 1, 3))
def test_attention_only_reports_known_exceptions_not_normal_conversions(application, tmp_path, attention):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "pianodisc_128plus", song_counts={
        "total": 8, "midi": 5, "eseq": 3, "midi_non_type0": 5,
        "dos83_midi": 5, "dos83_eseq": 3, "attention": attention,
        "attention_profile": "pianodisc_128plus", "attention_medium": "original",
    })
    try:
        assert dialog.attention_label.isHidden() == (attention == 0)
        expected = "1 song needs attention" if attention == 1 else f"{attention} songs need attention"
        assert dialog.attention_label.text() == (expected if attention else "")
        dialog.medium_combo.setCurrentIndex(dialog.medium_combo.findData("emulator_custom"))
        assert dialog.attention_label.isHidden()
        dialog.medium_combo.setCurrentIndex(dialog.medium_combo.findData("original"))
        assert dialog.attention_label.isHidden() == (attention == 0)
        dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("mark_ii"))
        assert dialog.attention_label.isHidden()
        assert dialog.attention_label.text() == ""
    finally:
        dialog.close()


@pytest.fixture
def modal_parent(application, tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import main_window

    settings = QSettings(str(tmp_path / "resize.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    original_font = QFont(application.font())
    parent = main_window.MidiTitleWindow()
    parent.resize(680, 480)
    parent.show()
    application.processEvents()
    yield parent
    parent.hide()
    parent.deleteLater()
    application.setFont(original_font)
    application.processEvents()


def _assert_preparation_controls_fit(dialog):
    scroll = dialog.scroll_area
    assert dialog.rect().contains(scroll.geometry())
    assert dialog.rect().contains(dialog.buttons.geometry())
    assert scroll.geometry().bottom() < dialog.buttons.geometry().top()
    assert scroll.horizontalScrollBar().maximum() == 0
    for button in dialog.buttons.buttons():
        assert button.isVisible()
        assert dialog.buttons.rect().contains(button.geometry())
        assert button.width() >= button.sizeHint().width()
    content = scroll.widget()
    for widget in (dialog.profile_combo, dialog.medium_combo, dialog.outcome_label, dialog.details_button):
        assert content.rect().contains(widget.geometry())
    if not dialog.details_widget.isVisible():
        return
    assert content.rect().contains(dialog.details_widget.geometry())
    if dialog.changes_table.isVisible():
        assert dialog.details_widget.rect().contains(dialog.changes_table.geometry())
        assert dialog.changes_table.horizontalScrollBar().maximum() == 0
        for row in range(dialog.changes_table.rowCount()):
            assert dialog.changes_table.rowHeight(row) >= dialog.changes_table.sizeHintForRow(row)
    for label in (dialog.manual_label, dialog.preparation_note_label, dialog.source_label):
        if label.isHidden():
            continue
        scroll.ensureWidgetVisible(label)
        QTest.qWait(10)
        assert not label.visibleRegion().isEmpty()
        assert label.height() >= label.heightForWidth(label.width())


def test_preparation_details_reflow_saved_values_after_columns_shrink(application, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    settings.setValue(
        "emulator_image_prefix",
        "A long saved disk image prefix with enough words to wrap over several lines in a narrow window",
    )
    dialog = PreparationProfileDialog(settings, "mark_ii", "nalbantov")
    try:
        dialog.resize(1000, 700)
        dialog.show()
        dialog.details_button.setChecked(True)
        QTest.qWait(30)
        table = dialog.changes_table
        prefix_row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == "Image prefix")
        wide_height = table.rowHeight(prefix_row)
        for width in (600, 1000, 600):
            dialog.resize(width, 700)
            QTest.qWait(30)
            assert dialog.size().toTuple() == (width, 700)
            assert table.rowHeight(prefix_row) >= table.sizeHintForRow(prefix_row)
            if width == 600:
                assert table.rowHeight(prefix_row) > wide_height
            else:
                assert table.rowHeight(prefix_row) == wide_height
    finally:
        dialog.close()


@pytest.mark.parametrize("font_size,language", [(9, "en")] + [(14, item.code) for item in SUPPORTED_LANGUAGES])
def test_preparation_dialog_preserves_user_geometry_and_reflows_during_modal_resize(
    application, modal_parent, font_size, language,
):
    parent = modal_parent
    font = QFont(parent.font().family(), font_size)
    application.setFont(font)
    parent.setFont(font)
    parent.currentLanguage = language
    before = {key: parent.settings.value(key) for key in parent.settings.allKeys()}
    dialog = PreparationProfileDialog(
        parent.settings, "mark_ii", "nalbantov", parent,
        song_counts={"midi": 3, "eseq": 2, "midi_non_type0": 3, "clavinova": 1},
    )
    failures = []

    def exercise():
        try:
            available = dialog.screen().availableGeometry()
            assert dialog.width() <= available.width()
            assert dialog.height() < min(360, available.height())
            _assert_preparation_controls_fit(dialog)
            for size in ((1100, 760), (600, 360), (950, 640)):
                dialog.resize(*size)
                dialog.move(20, 30)
                QTest.qWait(130)
                assert dialog.size().toTuple() == size
                assert dialog.pos().toTuple() == (20, 30)
                geometry = dialog.geometry()
                for profile_key in ("mark_ii", "enspire", "pianodisc_128plus", "custom"):
                    combo = dialog.profile_combo
                    combo.setCurrentIndex(combo.findData(profile_key))
                    application.postEvent(dialog, QEvent(QEvent.Type.LayoutRequest))
                    QTest.qWait(30)
                    assert dialog.geometry() == geometry
                    assert dialog.selection()[0].key == profile_key
                    for details_visible in (False, True):
                        dialog.details_button.setChecked(details_visible)
                        QTest.qWait(20)
                        _assert_preparation_controls_fit(dialog)
                        assert dialog.geometry() == geometry
            dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        except BaseException as exc:
            failures.append(exc)
        finally:
            if dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(150, exercise)
    assert parent._exec_child_dialog(dialog) == QDialog.DialogCode.Rejected
    try:
        if failures:
            raise failures[0]
        assert {key: parent.settings.value(key) for key in parent.settings.allKeys()} == before
    finally:
        dialog.deleteLater()
