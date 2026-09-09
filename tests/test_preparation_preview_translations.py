import os
from html import escape
from string import Formatter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QWidget

from aps_midi_prep_tool_app.emulator_image_builder import EmulatorAlbumPreview, EmulatorBuildPreview
from aps_midi_prep_tool_app.emulator_preview_dialog import EmulatorPreviewDialog
from aps_midi_prep_tool_app.message_catalog import COMMON_TEXT_TRANSLATIONS, SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import (
    COMPATIBILITY_SOURCE, NALBANTOV_SLIM_FIT_GUIDANCE, PIANO_PROFILES,
    PIANO_PROFILE_CATEGORIES, MEDIA, SETTING_LABELS, get_preparation_profile,
)
from aps_midi_prep_tool_app.preparation_preview_translations import PREPARATION_PREVIEW_TRANSLATIONS


NON_ENGLISH = [language.code for language in SUPPORTED_LANGUAGES if language.code != "en"]


def test_preparation_preview_catalog_covers_all_languages_and_preserves_placeholders():
    formatter = Formatter()
    for source, translations in PREPARATION_PREVIEW_TRANSLATIONS.items():
        assert set(translations) == set(NON_ENGLISH), source
        fields = {field for _literal, field, _format, _conversion in formatter.parse(source) if field}
        for language, translation in translations.items():
            assert translation.strip(), (source, language)
            assert {field for _literal, field, _format, _conversion in formatter.parse(translation) if field} == fields
            assert translate_text(source, language) == translation


def test_profile_guidance_and_setting_labels_are_localized():
    labels = [
        NALBANTOV_SLIM_FIT_GUIDANCE, *SETTING_LABELS.values(), "MIDI type", "MIDI Type 0",
        *(label for _category, label in PIANO_PROFILE_CATEGORIES),
        *(entry.caution for entry in (*PIANO_PROFILES, *MEDIA) if entry.caution),
        *(profile.label for profile in PIANO_PROFILES if profile.category != "disklavier"),
        *(profile.source_label for profile in PIANO_PROFILES if profile.source_label),
        *(profile.preparation_note for profile in PIANO_PROFILES if profile.preparation_note),
        *(medium.label for medium in MEDIA),
    ]
    for source in labels:
        assert set(COMMON_TEXT_TRANSLATIONS[source]) >= set(NON_ENGLISH), source
    for profile in PIANO_PROFILES:
        if profile.preparation_note:
            for language in NON_ENGLISH:
                assert translate_text(profile.preparation_note, language) != profile.preparation_note


@pytest.mark.parametrize("language", NON_ENGLISH)
def test_profile_dialog_uses_selected_language_for_controls_and_compact_summary(tmp_path, language):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._language_code = lambda: language
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, "mark_i", "original", parent, song_counts={"midi": 2, "dos83_midi": 1})
    try:
        assert dialog.windowTitle() == translate_text("Preparing for...", language)
        assert dialog.buttons.button(QDialogButtonBox.Apply).text() == translate_text("Apply and Prepare", language)
        assert dialog.medium_combo.currentText() == translate_text("Original floppy drive", language)
        labels = [dialog.changes_table.item(row, 0).text() for row in range(dialog.changes_table.rowCount())]
        assert translate_text("Filenames", language) in labels
        assert translate_text("Song format", language) in labels
        assert translate_text("Convert songs", language) in labels
        assert dialog.changes_table.item(1, 2).text() == translate_text("DOS 8.3 · {count} to rename", language, count=1)
        assert not hasattr(dialog, "guidance_label")
        assert not hasattr(dialog, "evidence_label")
        assert dialog.changes_table.rowCount() == 5
        assert "2026-09-07" not in dialog.source_label.text()
        assert translate_text("APS Disklavier Compatibility Table", language) in dialog.source_label.text()
        assert settings.allKeys() == []
    finally:
        dialog.close()
        parent.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("language", NON_ENGLISH)
@pytest.mark.parametrize("profile_key", ("pianodisc_128plus", "pianodisc_prodigy", "qrs_chili", "qrs_pno4"))
def test_manufacturer_dialog_localizes_subgroup_guidance_delivery_and_source(tmp_path, language, profile_key):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._language_code = lambda: language
    settings = QSettings(str(tmp_path / "manufacturer.ini"), QSettings.IniFormat)
    profile = get_preparation_profile(profile_key)
    dialog = PreparationProfileDialog(settings, profile_key, profile.default_medium, parent, song_counts={"midi_non_type0": 2})
    try:
        selected_profile, medium = dialog.selection()
        assert selected_profile.key == profile_key
        assert dialog.profile_combo.currentData() == profile_key
        assert dialog.profile_combo.currentText().strip() == translate_text(profile.label, language)
        category_row = max(
            row for row in range(dialog.profile_combo.currentIndex())
            if dialog.profile_combo.itemData(row) is None
        )
        category_label = dict(PIANO_PROFILE_CATEGORIES)[profile.category]
        assert dialog.profile_combo.itemText(category_row) == translate_text(category_label, language)
        assert dialog.profile_combo.itemText(0) == translate_text("General", language)
        assert dialog.medium_combo.currentText() == translate_text(medium.label, language)
        assert dialog.preparation_note_label.text() == translate_text(profile.preparation_note, language)
        assert not dialog.preparation_note_label.isHidden()
        assert escape(translate_text(profile.source_label, language), quote=True) in dialog.source_label.text()
        assert profile.source_url in dialog.source_label.text()
        assert COMPATIBILITY_SOURCE not in dialog.source_label.text()
        rows = [tuple(dialog.changes_table.item(row, column).text() for column in range(3))
                for row in range(dialog.changes_table.rowCount())]
        type_label = translate_text("MIDI type", language)
        if profile.midi_types == (0,):
            assert (type_label, translate_text("Current default", language), translate_text("MIDI Type 0", language)) in rows
            assert (
                translate_text("Convert songs", language), "2",
                translate_text("{source} → {target} (staged)", language, source="MIDI", target="SMF0"),
            ) in rows
        else:
            assert not any(row[0] == type_label for row in rows)
        assert settings.allKeys() == []
    finally:
        dialog.close()
        parent.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_rendered_preparation_dialog_covers_every_profile_and_delivery_option(tmp_path, language):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._language_code = lambda: language
    settings = QSettings(str(tmp_path / "all-profiles.ini"), QSettings.IniFormat)
    dialog = PreparationProfileDialog(settings, parent=parent)
    try:
        dialog.show()
        app.processEvents()
        visible_labels = {label.text() for label in dialog.findChildren(QLabel) if label.isVisible()}
        for source in ("Piano / controller:", "Drive / delivery:", "Keep current settings"):
            assert translate_text(source, language) in visible_labels
        assert dialog.buttons.button(QDialogButtonBox.Apply).text() == translate_text("Apply and Prepare", language)
        assert dialog.buttons.button(QDialogButtonBox.Cancel).text() == translate_text("Cancel", language)
        assert [dialog.changes_table.horizontalHeaderItem(index).text() for index in range(3)] == [
            translate_text(source, language) for source in ("Setting", "Current", "Proposed")
        ]
        for profile in PIANO_PROFILES:
            dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData(profile.key))
            app.processEvents()
            assert dialog.selection()[0].key == profile.key
            assert dialog.profile_combo.currentText() == translate_text(profile.label, language)
            assert dialog.preparation_note_label.text() == translate_text(profile.preparation_note, language)
            assert dialog.preparation_note_label.isVisible() == bool(profile.preparation_note)
            for medium_key in profile.media:
                dialog.medium_combo.setCurrentIndex(dialog.medium_combo.findData(medium_key))
                app.processEvents()
                selected_profile, selected_medium = dialog.selection()
                assert selected_profile.key == profile.key
                assert selected_medium.key == medium_key
                assert dialog.medium_combo.currentText() == translate_text(selected_medium.label, language)
                if profile.source_url:
                    assert escape(translate_text(profile.source_label, language), quote=True) in dialog.source_label.text()
                if selected_medium.source_url:
                    assert escape(translate_text("Emulator documentation", language), quote=True) in dialog.source_label.text()
                for row in range(dialog.changes_table.rowCount()):
                    for column in range(dialog.changes_table.columnCount()):
                        item = dialog.changes_table.item(row, column)
                        assert item.toolTip() == item.text()
                        assert "{" not in item.text()
        assert settings.allKeys() == []
    finally:
        dialog.close()
        parent.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
@pytest.mark.parametrize("has_parent", (False, True))
def test_preparation_dialog_uses_saved_language_without_a_main_window(tmp_path, language, has_parent):
    app = QApplication.instance() or QApplication([])
    parent = QWidget() if has_parent else None
    settings = QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    dialog = PreparationProfileDialog(settings, "pianodisc_128plus", parent=parent)
    try:
        dialog.show()
        app.processEvents()
        assert dialog.language_code == language
        assert dialog.windowTitle() == translate_text("Preparing for...", language)
        assert dialog.buttons.button(QDialogButtonBox.Apply).text() == translate_text("Apply and Prepare", language)
        profile, _medium = dialog.selection()
        assert dialog.preparation_note_label.text() == translate_text(profile.preparation_note, language)
    finally:
        dialog.close()
        if parent is not None:
            parent.deleteLater()
        app.processEvents()


def test_preparation_dialog_prefers_the_active_parent_language(tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._language_code = lambda: "fr-CA"
    settings = QSettings(str(tmp_path / "active-language.ini"), QSettings.IniFormat)
    settings.setValue("language", "de")
    dialog = PreparationProfileDialog(settings, parent=parent)
    try:
        assert dialog.language_code == "fr"
        assert dialog.windowTitle() == translate_text("Preparing for...", "fr")
        assert settings.value("language") == "de"
    finally:
        dialog.close()
        parent.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("language", NON_ENGLISH)
def test_collection_preview_uses_selected_language_for_actions_and_provenance(language):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._language_code = lambda: language
    preview = EmulatorBuildPreview(
        "/source", "/output", (),
        (EmulatorAlbumPreview("/source/album", "My album", 0, True, "Folder name"),),
        (), {}, {}, "midi", "folders",
    )
    dialog = EmulatorPreviewDialog(preview, parent)
    try:
        assert dialog.windowTitle() == translate_text("Review emulator disk set", language)
        assert dialog.update_button.text() == translate_text("Update Preview", language)
        assert dialog.build_button.text() == translate_text("Build Reviewed Output", language)
        assert dialog.albums_table.item(0, 4).text() == translate_text("Folder name", language)
        assert dialog.albums_table.item(0, 1).text() == "My album"
    finally:
        dialog.close()
        parent.deleteLater()
        app.processEvents()
