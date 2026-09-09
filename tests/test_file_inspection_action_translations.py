"""Every new inspection action and channel-merge message has localized copy."""

from string import Formatter

import pytest

from aps_midi_prep_tool_app.file_inspection_translations import FILE_INSPECTION_TEXT_TRANSLATIONS
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


SOURCES = (
    "FluidSynth did not report its startup position.",
    "Combine all tracks into MIDI Type 0 while keeping channels and instruments.",
    "Convert to Type 0",
    "Merge Channels to Piano",
    "Merge Channels to Piano...",
    "Editing…",
    "Could not update this file: {error}",
    "Merge all channels into MIDI channel 1 using Acoustic Grand Piano.",
    "Merge all channels into MIDI channel 1 using Acoustic Grand Piano for one song or all listed MIDI songs.",
    "Channel events now use MIDI channel 1 with Acoustic Grand Piano.",
    "This song is no longer available in the current file list.",
    "These actions require a MIDI file.",
    "Unsupported inspection action.",
    "Type 0 conversion staged. Use Save to write it, or Undo to revert.",
    "Piano channel merge staged. Use Save to write it, or Undo to revert.",
    "This file is already MIDI Type 0.",
    "This file is already merged to piano.",
    "Piano channel merge staged for {count} MIDI file(s).",
    "Already merged: {count} MIDI file(s).",
    "Failed: {count} MIDI file(s).",
)


def _fields(template):
    return {field for _text, field, _format, _conversion in Formatter().parse(template) if field}


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_inspection_action_and_channel_merge_copy_is_localized_with_intact_placeholders(language):
    non_english = {entry.code for entry in SUPPORTED_LANGUAGES if entry.code != "en"}
    for source in SOURCES:
        assert set(FILE_INSPECTION_TEXT_TRANSLATIONS[source]) == non_english
        expected = source if language == "en" else FILE_INSPECTION_TEXT_TRANSLATIONS[source][language]
        assert _fields(expected) == _fields(source)
        assert translate_text(source, language) == expected
        if language != "en":
            assert expected != source
        if "{error}" in source:
            detail = translate_text("These actions require a MIDI file.", language)
            rendered = translate_text(source, language, error=detail)
            assert detail in rendered
            assert "{error}" not in rendered
            if language != "en":
                assert "Could not update this file" not in rendered
                assert "These actions require a MIDI file" not in rendered
        if "{count}" in source:
            rendered = translate_text(source, language, count=3)
            assert "3" in rendered
            assert "{count}" not in rendered
    assert translate_text("Merge Channels to Piano...", language) == translate_text("Merge Channels to Piano", language) + "..."
    # Both busy and one-click inspection paths use the same existing message.
    busy = translate_text("Please wait for MIDI processing to finish.", language)
    if language != "en":
        assert busy != "Please wait for MIDI processing to finish."
