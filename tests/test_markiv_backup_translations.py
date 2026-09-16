"""Mark IV backup messages render in every supported application language."""

from string import Formatter

import pytest

from aps_midi_prep_tool_app.markiv_backup_translations import MARKIV_BACKUP_MESSAGES
from aps_midi_prep_tool_app.message_catalog import MESSAGES, SUPPORTED_LANGUAGES, tr


def _fields(template):
    return {field for _literal, field, _spec, _conversion in Formatter().parse(template) if field}


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_markiv_messages_cover_all_languages_and_preserve_values(language):
    expected_languages = {entry.code for entry in SUPPORTED_LANGUAGES}
    values = {
        "albums": 11,
        "files": 127,
        "size": "12.5 MiB",
        "verified": 126,
        "converted": 37,
        "count": 2,
        "completed": 85,
        "total": 128,
    }
    for message_id, translations in MARKIV_BACKUP_MESSAGES.items():
        assert set(translations) == expected_languages
        assert MESSAGES[message_id] == translations
        template = translations[language]
        assert template.strip()
        assert _fields(template) == _fields(translations["en"])
        rendered = tr(message_id, language, **values)
        assert rendered == template.format(**values)
        for field in _fields(template):
            assert str(values[field]) in rendered


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES if entry.code != "en"])
def test_markiv_descriptive_messages_are_translated(language):
    for message_id in (
        "markiv.action", "markiv.description", "markiv.source_note", "markiv.destination_note",
        "markiv.convert", "markiv.conversion_note", "markiv.complete", "markiv.incomplete", "markiv.tooltip",
    ):
        assert tr(message_id, language) != tr(message_id, "en")
    assert "songs/" in tr("markiv.source_note", language)
    assert "E-SEQ" in tr("markiv.convert", language)
    assert "MIDI" in tr("markiv.convert", language)
