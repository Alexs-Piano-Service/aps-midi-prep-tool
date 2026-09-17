import ast
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from string import Formatter

from aps_midi_prep_tool_app.app_info import APP_VERSION
from aps_midi_prep_tool_app.message_catalog import (
    COMMON_TEXT_TRANSLATIONS,
    MESSAGES,
    SUPPORTED_LANGUAGES,
    TEXT_TO_MESSAGE_ID,
    normalize_language_code,
    translate_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _format_fields(text):
    return sorted(
        field_name
        for _, field_name, _, _ in Formatter().parse(text)
        if field_name is not None
    )


def test_release_version_is_consistent_across_app_and_documentation():
    from scripts.release_metadata import validate_release_metadata

    assert validate_release_metadata(PROJECT_ROOT)["version"] == APP_VERSION


def test_message_catalog_has_complete_language_and_placeholder_coverage():
    supported_codes = {language.code for language in SUPPORTED_LANGUAGES}

    for message_id, translations in MESSAGES.items():
        assert supported_codes <= set(translations), message_id
        expected_fields = _format_fields(translations["en"])
        for language_code in supported_codes:
            assert translations[language_code], (message_id, language_code)
            assert _format_fields(translations[language_code]) == expected_fields, (
                message_id,
                language_code,
            )


def test_common_text_catalog_has_complete_translation_and_placeholder_coverage():
    translated_codes = {
        language.code for language in SUPPORTED_LANGUAGES if language.code != "en"
    }

    for source, translations in COMMON_TEXT_TRANSLATIONS.items():
        assert translated_codes <= set(translations), source
        expected_fields = _format_fields(source)
        for language_code in translated_codes:
            assert translations[language_code], (source, language_code)
            assert _format_fields(translations[language_code]) == expected_fields, (
                source,
                language_code,
            )


def test_supported_language_metadata_and_regional_aliases_are_consistent():
    for attribute in ("code", "english_name", "native_name"):
        values = [str(getattr(language, attribute)).strip() for language in SUPPORTED_LANGUAGES]
        assert all(values), attribute
        assert len(values) == len(set(values)), attribute

    expected_aliases = {
        "en_US": "en",
        "en_GB": "en",
        "es_MX": "es",
        "fr_FR": "fr",
        "de_DE": "de",
        "it_IT": "it",
        "pt_PT": "pt-BR",
        "pt_BR": "pt-BR",
        "bg_BG": "bg",
        "nl_NL": "nl",
        "pl_PL": "pl",
        "ja_JP": "ja",
        "ko_KR": "ko",
        "zh_CN": "zh-Hans",
        "zh_SG": "zh-Hans",
        "zh_Hans": "zh-Hans",
    }
    for regional_code, expected_code in expected_aliases.items():
        assert normalize_language_code(regional_code) == expected_code, regional_code


def test_bulgarian_catalog_never_silently_falls_back_to_english():
    for message_id, translations in MESSAGES.items():
        assert translations["bg"] != translations["en"], message_id

    for source, translations in COMMON_TEXT_TRANSLATIONS.items():
        # A layout containing only placeholders and punctuation has no English
        # prose to translate. Its inserted labels/values are localized separately.
        literal_text = "".join(part for part, _field, _spec, _conversion in Formatter().parse(source))
        if any(character.isalpha() for character in literal_text):
            assert translations["bg"] != source, source


def test_literal_translation_calls_are_represented_in_the_catalog():
    missing = []
    ignored_modules = {
        "bulgarian_translations.py",
        "file_inspection_translations.py",
        "message_catalog.py",
        "translation_supplements.py",
        "workflow_translations.py",
    }

    for path in (PROJECT_ROOT / "aps_midi_prep_tool_app").rglob("*.py"):
        if path.name in ignored_modules:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            source_node = node.args[0]
            if not isinstance(source_node, ast.Constant) or not isinstance(source_node.value, str):
                continue
            if isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                call_name = node.func.id
            else:
                continue
            if call_name not in {"_lt", "t", "translate_text", "_menu_action_text"}:
                continue

            source = source_node.value
            represented = source in COMMON_TEXT_TRANSLATIONS or source in TEXT_TO_MESSAGE_ID
            if source.endswith("..."):
                represented = represented or source[:-3] in COMMON_TEXT_TRANSLATIONS
            if source.endswith(":"):
                represented = represented or source[:-1] in COMMON_TEXT_TRANSLATIONS
            if source.endswith(" image") or source.endswith(" raw sector image"):
                represented = True
            if not represented:
                missing.append((str(path.relative_to(PROJECT_ROOT)), node.lineno, source))

    assert not missing


def test_primary_utility_menu_labels_translate_in_every_supported_language():
    translated_codes = {
        language.code for language in SUPPORTED_LANGUAGES if language.code != "en"
    }
    sources = (
        "Name MIDI Files from Song Titles",
        "Strip XF Data",
    )

    for source in sources:
        expected_fields = _format_fields(source)
        for language_code in translated_codes:
            translation = translate_text(source, language_code)
            assert translation != source, (source, language_code)
            assert _format_fields(translation) == expected_fields, (
                source,
                language_code,
            )

    for language_code in translated_codes:
        assert translate_text("Strip XF Data...", language_code) == (
            f"{translate_text('Strip XF Data', language_code)}..."
        )


def test_recover_damaged_image_guidance_translates_in_every_supported_language():
    translated_codes = {
        language.code for language in SUPPORTED_LANGUAGES if language.code != "en"
    }
    sources = (
        "Recover song data from a damaged floppy image and open the result as a new editable image copy.",
        "Recover song data from a damaged floppy image.",
        "Please wait for the current operation to finish before recovering a damaged image.",
    )

    for source in sources:
        expected_fields = _format_fields(source)
        for language_code in translated_codes:
            translation = translate_text(source, language_code)
            assert translation != source, (source, language_code)
            assert _format_fields(translation) == expected_fields, (
                source,
                language_code,
            )


def test_emulator_disk_set_onboarding_title_translates_in_every_supported_language():
    source = "Build Emulator Disk Sets"
    translated_codes = {
        language.code for language in SUPPORTED_LANGUAGES if language.code != "en"
    }

    for language_code in translated_codes:
        assert translate_text(source, language_code) != source, language_code
