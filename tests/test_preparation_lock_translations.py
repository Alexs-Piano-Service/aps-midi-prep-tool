from string import Formatter

import pytest

from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.preparation_preview_translations import PREPARATION_PREVIEW_TRANSLATIONS


LOCK_REASON = (
    "Preparing for {profile} sets {option} to {value}. "
    "Change the target system or choose Custom to change this option."
)
LOCK_HINT = (
    "Some options are set by Preparing for {profile}. "
    "Change the target system or choose Custom to change them."
)
LANGUAGES = tuple(language.code for language in SUPPORTED_LANGUAGES)


@pytest.mark.parametrize("source", (LOCK_REASON, LOCK_HINT))
def test_preparation_lock_catalog_covers_all_supported_languages(source):
    translations = PREPARATION_PREVIEW_TRANSLATIONS[source]
    assert set(translations) == set(LANGUAGES) - {"en"}
    fields = {
        field for _literal, field, _format, _conversion in Formatter().parse(source)
        if field is not None
    }
    for language, translation in translations.items():
        assert translation.strip(), language
        assert translation != source, language
        assert {
            field for _literal, field, _format, _conversion in Formatter().parse(translation)
            if field is not None
        } == fields, language
        assert translate_text(source, language) == translation


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("value", ("On", "Off", "MIDI", "E-SEQ"))
def test_preparation_lock_reason_renders_localized_setting_and_value(language, value):
    profile = translate_text("Standard MIDI export / modern playback", language)
    option = translate_text("Use 8.3 filenames", language)
    translated_value = translate_text(value, language)
    reason = translate_text(
        LOCK_REASON, language, profile=profile, option=option, value=translated_value,
    )
    assert profile in reason
    assert option in reason
    assert translated_value in reason
    assert translate_text("Custom", language) in reason
    assert "{" not in reason
    hint = translate_text(LOCK_HINT, language, profile=profile)
    assert profile in hint
    assert translate_text("Custom", language) in hint
    assert "{" not in hint
