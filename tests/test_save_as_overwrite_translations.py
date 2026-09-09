"""Save As overwrite and recovery copy stays complete in every language."""

from string import Formatter

import pytest

from aps_midi_prep_tool_app.message_catalog import MESSAGES, SUPPORTED_LANGUAGES, tr
from aps_midi_prep_tool_app.pending_changes_translations import PENDING_CHANGE_MESSAGES


EXPECTED_ENGLISH = {
    "save_as.overwrite.title": "Overwrite Files?",
    "save_as.overwrite.prompt": "{count} existing file(s) in {folder} will be replaced:\n\n{files}\n\nOverwrite these files?",
    "save_as.overwrite.cancelled": "Save As cancelled. No files were overwritten.",
    "save_as.overwrite.failed": "Save As could not be completed. The previous destination files were restored.",
    "save_as.overwrite.restore_failed": "Some destination files could not be restored. Recovery copies are in {folder}.",
}


def _fields(template):
    return sorted(field for _text, field, _format, _conversion in Formatter().parse(template) if field)


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_save_as_overwrite_messages_are_complete_and_preserve_format_fields(language):
    codes = {entry.code for entry in SUPPORTED_LANGUAGES}
    for key, english in EXPECTED_ENGLISH.items():
        translations = PENDING_CHANGE_MESSAGES[key]
        assert set(translations) == codes
        assert translations["en"] == english
        assert MESSAGES[key] == translations
        assert translations[language].strip()
        assert _fields(translations[language]) == _fields(english)
        assert tr(key, language) == translations[language]
        if language != "en":
            assert translations[language] != english


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_formatted_overwrite_prompt_preserves_file_list_and_recovery_folder(language):
    folder = "/tmp/Album & Piano <destination>"
    files = "Song One.mid\nDuet & Waltz.mid"
    prompt = tr("save_as.overwrite.prompt", language, count=2, folder=folder, files=files)
    paragraphs = prompt.split("\n\n")
    assert len(paragraphs) == 3
    assert "2" in paragraphs[0]
    assert folder in paragraphs[0]
    assert paragraphs[1] == files
    assert paragraphs[2].strip()
    assert "{count}" not in prompt and "{folder}" not in prompt and "{files}" not in prompt
    recovery = tr("save_as.overwrite.restore_failed", language, folder=folder)
    assert folder in recovery
    assert "{folder}" not in recovery
    if language != "en":
        assert "Overwrite these files?" not in prompt
        assert "Recovery copies are in" not in recovery
