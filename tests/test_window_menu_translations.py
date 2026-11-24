"""Menu-specific labels stay localized without changing existing dialog text."""

import pytest

from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.window_menu_translations import WINDOW_MENU_TRANSLATIONS


@pytest.fixture
def catalog_menu_window(qt_application, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from aps_midi_prep_tool_app import main_window

    settings = QSettings(str(tmp_path / "menu-translations.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    window = main_window.MidiTitleWindow()
    yield window
    window._set_language("en")
    window.deleteLater()


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
def test_window_menu_labels_are_available_in_every_ui_language(language):
    for source, translations in WINDOW_MENU_TRANSLATIONS.items():
        translated = translate_text(source, language)
        assert translated.strip()
        assert translated == (source if language == "en" else translations[language])


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
def test_review_changes_menu_keeps_existing_dialog_translation(language):
    assert translate_text("Review Changes...", language) == (
        translate_text("Review Changes", language) + "..."
    )


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
def test_catalog_creation_option_keeps_literal_catalog_filenames_in_every_language(catalog_menu_window, language):
    label = "Create Missing Yamaha Catalogs"
    tooltip = (
        "Create PSONG.MNG and PDISK.MNG when missing. Uses DOS 8.3 song filenames. "
        "Existing catalogs and titles converted "
        "from PIANODIR.FIL are preserved when this is off."
    )
    translated_tooltip = translate_text(tooltip, language)
    translated_label = translate_text(label, language)
    assert translated_tooltip == (tooltip if language == "en" else WINDOW_MENU_TRANSLATIONS[tooltip][language])
    if language != "en":
        assert translated_label != label
        assert translated_tooltip != tooltip
    catalog_menu_window._set_language("de" if language == "en" else "en")
    catalog_menu_window._set_language(language)
    action = catalog_menu_window.settingsGenerateYamahaCatalogsAction
    assert action.text().replace("&", "") == translated_label
    assert action.toolTip() == translated_tooltip
    assert "8.3" in translated_tooltip
    for filename in ("PSONG.MNG", "PDISK.MNG", "PIANODIR.FIL"):
        assert translated_tooltip.count(filename) == 1
