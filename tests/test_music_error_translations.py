"""Parser diagnostics are localized only for display, including runtime values."""

import ast
from pathlib import Path
from string import Formatter

import pytest

from aps_midi_prep_tool_app.conversion_review import localize_music_error
from aps_midi_prep_tool_app.eseq_converter import parse_eseq_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.midi_type0_converter import _convert_midi_bytes_to_type0, _parse_track_events
from aps_midi_prep_tool_app.music_error_translations import MUSIC_ERROR_TRANSLATIONS


LANGUAGES = [language.code for language in SUPPORTED_LANGUAGES]
PREFIX = "Save {status}: <A&B>/song.mid: "


def _fields(template):
    return sorted(field for _, field, _, _ in Formatter().parse(template) if field)


def test_parser_error_catalog_covers_every_language_and_preserves_parameters():
    for source, translations in MUSIC_ERROR_TRANSLATIONS.items():
        assert set(translations) == set(LANGUAGES) - {"en"}, source
        for language, translation in translations.items():
            assert translation.strip(), (source, language)
            assert _fields(source) == _fields(translation), (source, language)
            assert translate_text(source, language) == translation


def test_every_static_music_parser_diagnostic_has_a_translation():
    root = Path(__file__).resolve().parents[1] / "aps_midi_prep_tool_app"
    for module in ("midi_type0_converter", "eseq_converter", "eseq_legacy", "eseq_inspection", "conversion_review"):
        for node in ast.walk(ast.parse((root / f"{module}.py").read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args):
                continue
            argument = node.exc.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                for language in LANGUAGES:
                    if language != "en":
                        assert localize_music_error(argument.value, language) != argument.value, (
                            module, node.lineno, language, argument.value,
                        )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("parse,data", (
    (_convert_midi_bytes_to_type0, b"short"),
    (_convert_midi_bytes_to_type0, b"MThd\x00\x00\x00\x06\x00\x07\x00\x00\x00\x60"),
    (_parse_track_events, b"\x00\x90\x3c"),
    (_parse_track_events, b"\x00\xf5"),
    (_parse_track_events, b"\x81"),
    (parse_eseq_bytes, b"short"),
    (parse_eseq_bytes, b"\x00" * 160),
), ids=("short-midi", "unknown-midi-format", "short-note", "unknown-status", "short-vlq", "short-eseq", "missing-eseq-signature"))
def test_real_parser_failures_translate_and_preserve_path_context(language, parse, data):
    with pytest.raises(ValueError) as caught:
        parse(data)
    raw = str(caught.value)
    localized = localize_music_error(caught.value, language)
    assert localize_music_error(PREFIX + raw, language) == PREFIX + localized
    assert str(caught.value) == raw
    if language == "en":
        assert localized == raw
    else:
        assert localized != raw
        assert "{" not in localized


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("source,values", (
    ("Unsupported system status byte: 0x{status}", {"status": "F5"}),
    ("Unsupported E-SEQ opcode 0x{status}.", {"status": "FF"}),
    ("Cannot safely preserve embedded E-SEQ SysEx status 0x{status} in MIDI.", {"status": "90"}),
    ("MIDI format {format} is not a Standard MIDI File type.", {"format": "65535"}),
    ("Unsupported E-SEQ pedal policy '{value}'.", {"value": "Save {filename}: <A&B> 'policy'"}),
))
def test_parameterized_diagnostics_keep_numeric_details(language, source, values):
    raw = source.format(**values)
    expected = translate_text(source, language, **values)
    assert localize_music_error(raw, language) == expected
    assert localize_music_error(PREFIX + raw, language) == PREFIX + expected


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("track_label", ("track 12", "merged tracks"))
def test_sysex_diagnostics_localize_track_and_tick_context(language, track_label):
    source = "Cannot preserve MIDI SysEx packets with intervening MIDI events in E-SEQ ({location})."
    raw = source.format(location=f"{track_label}, tick 12345")
    tracks = (translate_text("track {track}", language, track=12)
              if track_label == "track 12" else translate_text("merged tracks", language))
    location = translate_text("{tracks}, tick {tick}", language, tracks=tracks, tick=12345)
    assert localize_music_error(PREFIX + raw, language) == PREFIX + translate_text(source, language, location=location)
    source = "MIDI SysEx in {track_label} is missing its terminating F7; E-SEQ conversion was not performed."
    raw = source.format(track_label=track_label)
    assert localize_music_error(raw, language) == translate_text(source, language, track_label=tracks)


@pytest.mark.parametrize("language", LANGUAGES)
def test_nested_emulator_error_localizes_parser_details_and_preserves_song_name(language):
    from aps_midi_prep_tool_app.emulator_preview_dialog import localize_emulator_warning

    source = "Could not prepare '{name}' as {format}: {error}"
    name = "Save {format} <title> & original.mid"
    error = "Unsupported system status byte: 0xF5"
    warning = source.format(name=name, format="MIDI", error=error)
    assert localize_emulator_warning(PREFIX + warning, language) == PREFIX + translate_text(
        source, language, name=name, format="MIDI", error=localize_music_error(error, language),
    )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("message", (
    "Unknown diagnostic with Save {filename} <A&B>.mid",
    "Could not decode an unfamiliar image",
    "Unsupported system status byte: 0x{status}",
    "MIDI format Save is not a Standard MIDI File type.",
    "Cannot preserve MIDI SysEx packets with intervening MIDI events in E-SEQ (user-supplied text).",
))
def test_unrecognized_technical_details_remain_verbatim(language, message):
    # An unformatted known template is still valid catalog input.
    if message in MUSIC_ERROR_TRANSLATIONS:
        assert localize_music_error(message, language) == translate_text(message, language)
    else:
        assert localize_music_error(message, language) == message
