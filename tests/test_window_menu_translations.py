"""Menu-specific labels stay localized without changing existing dialog text."""

import pytest

from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.window_menu_translations import WINDOW_MENU_TRANSLATIONS


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
