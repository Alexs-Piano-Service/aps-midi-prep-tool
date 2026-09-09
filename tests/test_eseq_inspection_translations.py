"""Original E-SEQ details remain localized without changing technical values."""

import ast
from pathlib import Path
from string import Formatter

import pytest

from aps_midi_prep_tool_app.eseq_inspection_translations import ESEQ_INSPECTION_TRANSLATIONS
from aps_midi_prep_tool_app.message_catalog import COMMON_TEXT_TRANSLATIONS, SUPPORTED_LANGUAGES, translate_text


def _fields(template):
    return {field for _literal, field, _spec, _conversion in Formatter().parse(template) if field}


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_eseq_details_cover_all_languages_with_literal_values_and_placeholders(language):
    expected_languages = {entry.code for entry in SUPPORTED_LANGUAGES if entry.code != "en"}
    values = {
        "variant": "Disklavier FIL", "title": "Album <take & 2>", "offset": "0x53", "ticks": 384,
        "raw": "00 FF", "bpm": "117.000", "mpqn": 512820, "count": 25,
        "tick": 1536, "factor": 1000, "meter": "3/4", "channels": "1, 3",
        "label": translate_text("Detailed pedal flag", language), "value": translate_text("Set", language),
    }
    for source, translations in ESEQ_INSPECTION_TRANSLATIONS.items():
        assert set(translations) == expected_languages
        template = source if language == "en" else translations[language]
        assert template.strip()
        assert _fields(template) == _fields(source)
        assert translate_text(source, language) == template
        rendered = translate_text(source, language, **values)
        assert rendered == template.format(**values)
        for field in _fields(source):
            assert str(values[field]) in rendered
    # These sentences contain user-facing prose, unlike shared MIDI abbreviations
    # and the raw-value layout, which can correctly stay identical in a language.
    if language != "en":
        for source in (
            "E-SEQ file details",
            "Decoded MIDI preview:",
            "Zero tempo byte selects Yamaha's 117 BPM default.",
            "Original header details are unavailable for this E-SEQ variant.",
        ):
            assert translate_text(source, language) != source


def test_eseq_report_literal_templates_and_dynamic_labels_are_in_shared_catalog():
    path = Path(__file__).parents[1] / "aps_midi_prep_tool_app" / "eseq_inspection_report.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sources = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "report":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                sources.add(node.args[0].value)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_FIELD_LABELS" for target in node.targets):
            sources.update(ast.literal_eval(node.value).values())
    assert "E-SEQ file details" in sources
    assert "Detailed pedal flag" in sources
    assert not sources.difference(COMMON_TEXT_TRANSLATIONS)
