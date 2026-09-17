"""Check the final merged catalogs used by every UI translation boundary."""

import ast
from collections import Counter
from pathlib import Path
from string import Formatter

import pytest

from aps_midi_prep_tool_app.message_catalog import (
    COMMON_TEXT_TRANSLATIONS,
    MESSAGES,
    SUPPORTED_LANGUAGES,
    TEXT_TO_MESSAGE_ID,
    tr,
    translate_text,
)


LANGUAGES = {language.code for language in SUPPORTED_LANGUAGES}


def _fields(template):
    # Counts and formatting matter too: losing a second occurrence or changing
    # a hexadecimal/precision specifier can silently corrupt a diagnostic.
    return Counter(
        (name, specification, conversion)
        for _, name, specification, conversion in Formatter().parse(template)
        if name is not None
    )


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_final_catalogs_cover_every_language_and_preserve_formatting(language):
    for message_id, copies in MESSAGES.items():
        assert set(copies) == LANGUAGES, message_id
        translated = copies[language]
        assert translated.strip(), (message_id, language)
        assert _fields(translated) == _fields(copies["en"]), (message_id, language)
        assert tr(message_id, language) == translated, (message_id, language)

    for source, copies in COMMON_TEXT_TRANSLATIONS.items():
        # Source text is the implicit English translation in older catalogs.
        assert LANGUAGES - {"en"} <= set(copies) <= LANGUAGES, source
        translated = copies.get(language, source)
        assert translated.strip(), (source, language)
        assert _fields(translated) == _fields(source), (source, language)
        if source not in TEXT_TO_MESSAGE_ID:
            assert translate_text(source, language) == translated, (source, language)


def test_message_aliases_resolve_to_complete_catalog_entries():
    for source, message_id in TEXT_TO_MESSAGE_ID.items():
        assert message_id in MESSAGES, (source, message_id)
        assert _fields(source) == _fields(MESSAGES[message_id]["en"]), source
        for language in LANGUAGES:
            assert translate_text(source, language) == tr(message_id, language)


def test_literal_translation_calls_reference_registered_sources():
    """A translated call is still English if its source was never cataloged."""
    application = Path(__file__).resolve().parents[1] / "aps_midi_prep_tool_app"
    missing = []
    for path in sorted(application.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
                # Qt's translate() has a context argument and its own catalog.
                if name == "translate":
                    continue
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            else:
                continue
            if name not in {"_lt", "_t", "translate_text", "translate", "_set_static_tooltip"}:
                continue
            index = 1 if name == "_set_static_tooltip" else 0
            if len(node.args) <= index:
                continue
            argument = node.args[index]
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                continue
            source = argument.value
            if not source:
                continue
            if name == "_t":
                represented = source in MESSAGES
            else:
                represented = source in COMMON_TEXT_TRANSLATIONS or source in TEXT_TO_MESSAGE_ID
                if not represented and source.endswith(("...", ":")):
                    represented = source.removesuffix("...").removesuffix(":") in COMMON_TEXT_TRANSLATIONS
                if not represented and source.endswith(" image"):
                    # Format labels such as HFE image have a shared translator.
                    represented = all(
                        translate_text(source, language) != source
                        for language in LANGUAGES - {"en"}
                    )
            if not represented:
                missing.append((path.relative_to(application).as_posix(), node.lineno, source))
    assert not missing


@pytest.mark.parametrize("language", sorted(LANGUAGES - {"en"}))
def test_dialog_sentences_do_not_silently_copy_english(language):
    # Short labels, product names, and musical terms can correctly match English.
    # Complete prose sentences should not. These are technical display formats
    # whose labels legitimately have the same spelling in the listed languages.
    shared_formats = {
        "Track {track}, tick {tick}: {name}: {value}",
        "Tick {tick}: FB {factor} → {bpm} BPM",
        "{label} [{offset}]: {raw} — {value}",
    }
    for key, copies in (*MESSAGES.items(), *COMMON_TEXT_TRANSLATIONS.items()):
        source = copies.get("en", key)
        if len(source) > 32 and source not in shared_formats:
            assert copies[language] != source, (key, language)
