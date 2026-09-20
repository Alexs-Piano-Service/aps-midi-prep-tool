from string import Formatter
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.midi_metadata import validate_legacy_title_input
from aps_midi_prep_tool_app.validation_translations import VALIDATION_TRANSLATIONS


def _fields(text):
    return sorted(field for _, field, _, _ in Formatter().parse(text) if field is not None)


def test_validation_catalog_covers_all_languages_and_preserves_fields():
    codes = {language.code for language in SUPPORTED_LANGUAGES}
    for source, copies in VALIDATION_TRANSLATIONS.items():
        assert set(copies) == codes, source
        for code, copy in copies.items():
            assert copy.strip(), (source, code)
            assert _fields(copy) == _fields(source), (source, code)
            # Encoding identifiers are standardized names in every language.
            if code != "en" and source != "Windows-1252":
                assert copy != source, (source, code)


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_filename_errors_translate_before_being_added_to_other_dialogs(code):
    window = SimpleNamespace(
        _lt=lambda source, **fields: translate_text(source, code).format(**fields),
        _dos83_filenames_enabled=lambda: False,
    )
    cases = (
        ("", "Filename cannot be empty.", {}),
        ("PIANODIR.FIL", "{filename} is managed automatically.", {"filename": "PIANODIR.FIL"}),
        ("bad?.mid", "Filename contains characters that are not valid in FAT filenames.", {}),
        ("song\n.mid", "Filename cannot contain control characters.", {}),
    )
    for filename, source, fields in cases:
        error = MidiTitleWindow._validate_image_filename(window, filename)
        assert error == VALIDATION_TRANSLATIONS[source][code].format(**fields)
        if code != "en":
            assert error != VALIDATION_TRANSLATIONS[source]["en"].format(**fields)


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_legacy_title_errors_preserve_characters_and_translate_the_sentence(code):
    assert validate_legacy_title_input("Song 12 ~", code) is None
    error = validate_legacy_title_input("Café 東京", code)
    for character, codepoint in (("é", "00E9"), ("東", "6771"), ("京", "4EAC")):
        assert f"'{character}' (U+{codepoint})" in error
    if code != "en":
        assert "Unsupported characters" not in error
    assert "{characters}" not in error
