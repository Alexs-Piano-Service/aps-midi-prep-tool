"""Compatibility timing must be explained without masking changed performances."""

import json
import struct

import pytest

from aps_midi_prep_tool_app.conversion_report_translations import CONVERSION_REPORT_TRANSLATIONS
from aps_midi_prep_tool_app.conversion_review import ConversionReport, compare_music_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_legacy import is_legacy_eseq_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq


_LEGACY_EXPLANATION = (
    "Timing follows MID2ESEQ: a 117 BPM clock, preparation delay, spacing of "
    "crowded events, and two seconds of ending silence."
)
_TIMING_ONLY = "MIDI channel message timing changed; message values are unchanged."
_PAYLOAD_WARNING = "MIDI channel messages changed (notes, controllers, or instruments)."


def _source_midi():
    events = [
        (0, b"\xff\x02\x07Fixture"),
        (0, b"\xff\x51\x03\x07\xa1\x20"),
        (0, b"\xff\x58\x04\x03\x02\x18\x08"),
        (0, b"\x90\x3c\x40"),
        (0, b"\xb0\x40\x7f"),
        (0, b"\xb0\x01\x20"),
        (384, b"\x80\x3c\x00"),
        (384, b"\xb0\x40\x00"),
    ]
    track = bytearray()
    last_tick = 0
    for tick, raw in events:
        track.extend(_encode_vlq(tick - last_tick) + raw)
        last_tick = tick
    track.extend(b"\x00\xff\x2f\x00")
    return (
        struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 384)
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


@pytest.fixture
def legacy_conversion():
    source = _source_midi()
    output = convert_midi_bytes_to_eseq_bytes(source, timing_policy="mid2eseq")
    assert is_legacy_eseq_bytes(output)
    return source, output


def test_legacy_report_explains_timing_but_keeps_tight_timing_and_metadata_checks(legacy_conversion):
    report = compare_music_bytes(*legacy_conversion)

    assert report.legacy_timing
    assert report.channel_payload_changed is False
    assert report.notes_changed
    assert report.pedals_changed
    assert report.channel_events_changed
    assert report.after.duration_seconds > report.before.duration_seconds + 2
    assert report.removed_metadata["Copyright"] == 1
    assert report.removed_metadata["Tempo"] == 1
    assert report.removed_metadata["Time Signature"] == 1
    text = report.to_text("en")
    assert _LEGACY_EXPLANATION in text
    assert _TIMING_ONLY in text
    assert _PAYLOAD_WARNING not in text
    assert "Note values, channels, or timing changed." in text
    assert "Pedal values, channels, or timing changed." in text


@pytest.mark.parametrize("original,replacement,affected", [
    (b"\x90\x3c\x40", b"\x90\x3c\x41", "notes_changed"),
    (b"\x90\x3c\x40", b"\x90\x3d\x40", "notes_changed"),
    (b"\xb0\x40\x7f", b"\xb0\x40\x7e", "pedals_changed"),
    (b"\xb0\x01\x20", b"\xb0\x01\x21", "channel_events_changed"),
])
def test_legacy_envelope_does_not_hide_changed_message_values(legacy_conversion, original, replacement, affected):
    source, output = legacy_conversion
    assert output.count(original) == 1
    damaged = output.replace(original, replacement, 1)
    assert is_legacy_eseq_bytes(damaged)  # Header and trailer are not content verification.

    report = compare_music_bytes(source, damaged)
    assert report.legacy_timing
    assert report.channel_payload_changed is True
    assert getattr(report, affected)
    assert _LEGACY_EXPLANATION in report.to_text("en")
    assert _PAYLOAD_WARNING in report.to_text("en")
    assert _TIMING_ONLY not in report.to_text("en")

    # Comparing the two E-SEQ files removes the intentional conversion delay
    # from the comparison, proving the payload mutation itself triggers checks.
    damage_only = compare_music_bytes(output, damaged)
    assert getattr(damage_only, affected)
    assert damage_only.channel_payload_changed is True
    assert not damage_only.legacy_timing


def test_legacy_report_fields_survive_json_serialization(legacy_conversion):
    report = compare_music_bytes(*legacy_conversion)
    saved = json.loads(json.dumps(report.as_dict()))
    restored = ConversionReport.from_dict(saved)

    assert restored.legacy_timing is True
    assert restored.channel_payload_changed is False
    assert restored.notes_changed == report.notes_changed
    assert restored.pedals_changed == report.pedals_changed
    assert restored.removed_metadata == report.removed_metadata
    assert _LEGACY_EXPLANATION in restored.to_text("en")
    assert _TIMING_ONLY in restored.to_text("en")


def test_old_saved_reports_keep_unknown_payload_status_and_original_warning(legacy_conversion):
    saved = compare_music_bytes(*legacy_conversion).as_dict()
    saved.pop("legacy_timing")
    saved.pop("channel_payload_changed")
    restored = ConversionReport.from_dict(saved)

    assert restored.legacy_timing is False
    assert restored.channel_payload_changed is None
    assert _LEGACY_EXPLANATION not in restored.to_text("en")
    assert _PAYLOAD_WARNING in restored.to_text("en")


def test_preserved_timing_clavinova_and_reverse_conversion_do_not_claim_legacy_timing(legacy_conversion):
    source, legacy = legacy_conversion
    preserved = convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve")
    clavinova = convert_midi_bytes_to_eseq_bytes(source, container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA)
    reversed_midi = convert_eseq_bytes_to_midi_bytes(legacy)

    for before, after in ((source, preserved), (source, clavinova), (legacy, reversed_midi)):
        report = compare_music_bytes(before, after)
        assert not report.legacy_timing
        assert _LEGACY_EXPLANATION not in report.to_text("en")


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES if language.code != "en"])
def test_legacy_report_explanations_are_localized_in_every_supported_language(legacy_conversion, language):
    report = compare_music_bytes(*legacy_conversion)
    translated = report.to_text(language)
    for source in (_LEGACY_EXPLANATION, _TIMING_ONLY):
        assert source not in translated
        assert CONVERSION_REPORT_TRANSLATIONS[source][language] in translated
