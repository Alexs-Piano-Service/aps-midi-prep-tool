"""Independent full Yamaha pedal report fixtures and damaged-output checks."""

import json
import struct
from string import Formatter

import pytest

from aps_midi_prep_tool_app.conversion_review import ConversionReport, compare_music_bytes
from aps_midi_prep_tool_app.conversion_report_translations import CONVERSION_REPORT_TRANSLATIONS
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_legacy import is_legacy_eseq_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


EXPLANATION = "Yamaha pedals on {controllers}: {binary} on/off events added on channel 1; {duplicates} redundant continuous events omitted on channel 3."
MATCH = "MIDI channel events match the Yamaha pedal conversion and expected timing."
MISMATCH = "Other MIDI channel events or timing differ from the expected Yamaha pedal conversion."


def _vlq(value):
    result = bytearray((value & 127,))
    while value > 127:
        value >>= 7
        result.insert(0, 128 | (value & 127))
    return bytes(result)


def _midi(events):
    track = bytearray()
    previous = 0
    for tick, raw in sorted(events, key=lambda event: event[0]):
        track.extend(_vlq(tick - previous) + raw)
        previous = tick
    track.extend(b"\x00\xff\x2f\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 384) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _fil(events, **options):
    # A plain event writer packages the explicit expected fixture. It never
    # invokes the Yamaha transform or derives expected events from the source.
    return convert_midi_bytes_to_eseq_bytes(
        _midi(events), pedal_policy="preserve", timing_policy="preserve", **options,
    )


def _source_and_expected():
    common = [(0, b"\xc0\x00"), (0, b"\x90\x3c\x40"),
              (75, b"\xb0\x42\x7f"), (120, b"\x80\x3c\x20"), (120, b"\xb0\x42\x00")]
    source = list(common)
    expected = list(common)
    # Explicit expectations include both non-latched repeated crossings and
    # duplicate detail values. No production threshold/state helper is used.
    for controller, values, detail in (
        (64, (0, 83, 84, 84, 83, 84, 82, 81, 81, 83, 81, 0),
         ((10, 83), (20, 84), (40, 83), (50, 84), (60, 82), (70, 81), (90, 83), (100, 81), (110, 0))),
        (67, (0, 63, 64, 64, 63, 64, 61, 60, 60, 63, 60, 0),
         ((10, 63), (20, 64), (40, 63), (50, 64), (60, 61), (70, 60), (90, 63), (100, 60), (110, 0))),
    ):
        source.extend((tick * 10, bytes((0xB0, controller, value))) for tick, value in enumerate(values))
        for tick, value in detail:
            if tick in (20, 50):
                expected.append((tick, bytes((0xB0, controller, 127))))
            if tick in (70, 100):
                expected.append((tick, bytes((0xB0, controller, 0))))
            expected.append((tick, bytes((0xB2, controller, value))))
    return sorted(source, key=lambda event: event[0]), sorted(expected, key=lambda event: event[0])


def test_complete_layers_explain_counts_and_preserve_exact_change_flags():
    source, expected = _source_and_expected()
    report = compare_music_bytes(_midi(source), _fil(expected))

    assert report.yamaha_pedal_controllers == (64, 67)
    assert report.pedal_channels_routed == (64, 67)
    assert report.pedal_binary_events_added == 8
    assert report.pedal_duplicate_events_removed == 6
    assert report.before.pedals == {64: 12, 67: 12, 66: 2}
    assert report.after.pedals == {64: 13, 67: 13, 66: 2}
    assert report.pedals_changed and report.channel_events_changed and report.channel_payload_changed
    assert not report.notes_changed
    assert report.other_channel_payload_changed is False
    assert report.expected_channel_events_changed is False
    assert MATCH in report.to_text()
    assert MISMATCH not in report.to_text()
    assert "message values are unchanged" not in report.to_text()


@pytest.mark.parametrize("damage", (
    "missing_binary", "extra_binary", "binary_value", "detail_value", "missing_detail",
    "redundant_zero", "reversed_pair", "detail_time", "entire_pedal_time", "note_time",
    "note_velocity", "other_controller", "sostenuto", "extra_channel",
    "same_tick_message_order",
))
def test_arbitrary_messages_missing_companions_and_timing_damage_never_earn_full_match(damage):
    source, expected = _source_and_expected()
    bad = list(expected)
    target = bad.index((20, b"\xb0\x40\x7f"))
    detail = bad.index((20, b"\xb2\x40\x54"))
    if damage == "missing_binary":
        bad.pop(target)
    elif damage == "extra_binary":
        bad.insert(target, bad[target])
    elif damage == "binary_value":
        bad[target] = (20, b"\xb0\x40\x7e")
    elif damage == "detail_value":
        bad[detail] = (20, b"\xb2\x40\x55")
    elif damage == "missing_detail":
        bad.pop(detail)
    elif damage == "redundant_zero":
        bad.insert(0, (0, b"\xb2\x40\x00"))
    elif damage == "reversed_pair":
        bad[target], bad[detail] = bad[detail], bad[target]
    elif damage == "detail_time":
        bad[detail] = (25, bad[detail][1])
    elif damage == "entire_pedal_time":
        bad = [(tick + 10 if raw[0] in (0xB0, 0xB2) and raw[1] == 64 else tick, raw) for tick, raw in bad]
    elif damage == "note_time":
        bad = [(tick + 10 if raw[0] == 0x80 else tick, raw) for tick, raw in bad]
    elif damage == "note_velocity":
        bad[bad.index((0, b"\x90\x3c\x40"))] = (0, b"\x90\x3c\x41")
    elif damage == "other_controller":
        bad.append((55, b"\xb0\x01\x7f"))
    elif damage == "sostenuto":
        bad[bad.index((75, b"\xb0\x42\x7f"))] = (75, b"\xb0\x42\x7e")
    elif damage == "extra_channel":
        bad.append((20, b"\xb4\x40\x7f"))
    elif damage == "same_tick_message_order":
        first, second = bad.index((0, b"\xc0\x00")), bad.index((0, b"\x90\x3c\x40"))
        bad[first], bad[second] = bad[second], bad[first]
    report = compare_music_bytes(_midi(source), _fil(bad))

    assert report.expected_channel_events_changed is True
    assert MISMATCH in report.to_text()
    assert MATCH not in report.to_text()
    assert 67 in report.yamaha_pedal_controllers  # independently intact lane
    if damage in {"missing_binary", "extra_binary", "binary_value", "detail_value", "missing_detail",
                  "redundant_zero", "reversed_pair", "detail_time", "entire_pedal_time"}:
        assert 64 not in report.yamaha_pedal_controllers
    if damage in {"note_time", "note_velocity"}:
        assert report.notes_changed


def test_only_relocation_cannot_earn_complete_yamaha_claim():
    source, _expected = _source_and_expected()
    relocated = [(tick, b"\xb2" + raw[1:] if raw[0] == 0xB0 and raw[1] in (64, 67) else raw)
                 for tick, raw in source]
    report = compare_music_bytes(_midi(source), _fil(relocated))
    assert report.pedal_channels_routed == (64, 67)
    assert report.yamaha_pedal_controllers == ()
    assert report.expected_channel_events_changed is True
    assert MISMATCH in report.to_text()
    assert MATCH not in report.to_text()


@pytest.mark.parametrize("mode", ("preserve", "native", "occupied", "mda", "midi"))
def test_preserved_native_or_non_disklavier_output_is_not_claimed_as_a_new_yamaha_transform(mode):
    source, expected = _source_and_expected()
    if mode == "native":
        source = expected
    elif mode == "occupied":
        source = sorted([*source, (0, b"\x92\x40\x30"), (120, b"\x82\x40\x00")])
    before = _midi(source)
    after = (_midi(expected) if mode == "midi" else
             _fil(expected, container_variant="clavinova_mda") if mode == "mda" else _fil(source))
    report = compare_music_bytes(before, after)
    assert report.yamaha_pedal_controllers == ()
    assert report.expected_channel_events_changed is None
    assert MATCH not in report.to_text()


def test_report_new_fields_round_trip_and_older_saved_reports_remain_unknown():
    source, expected = _source_and_expected()
    report = compare_music_bytes(_midi(source), _fil(expected))
    data = json.loads(json.dumps(report.as_dict()))
    restored = ConversionReport.from_dict(data)
    assert restored.yamaha_pedal_controllers == (64, 67)
    assert restored.pedal_binary_events_added == 8
    assert restored.pedal_duplicate_events_removed == 6
    assert restored.expected_channel_events_changed is False
    assert MATCH in restored.to_text()
    assert restored == report
    assert restored.to_text() == report.to_text()
    for field in ("yamaha_pedal_controllers", "pedal_binary_events_added", "pedal_duplicate_events_removed", "expected_channel_events_changed"):
        data.pop(field)
    old = ConversionReport.from_dict(data)
    assert old.yamaha_pedal_controllers == ()
    assert old.pedal_binary_events_added == old.pedal_duplicate_events_removed == 0
    assert old.expected_channel_events_changed is None
    assert MATCH not in old.to_text()


@pytest.mark.parametrize("timing_policy", ("preserve", "auto"))
def test_actual_conversion_with_duplicates_and_repeated_edges_matches_independent_source_reference(timing_policy):
    source, _expected = _source_and_expected()
    before = _midi(source)
    after = convert_midi_bytes_to_eseq_bytes(before, timing_policy=timing_policy)
    report = compare_music_bytes(before, after)
    assert report.yamaha_pedal_controllers == (64, 67)
    assert report.pedal_binary_events_added == 8
    assert report.pedal_duplicate_events_removed == 6
    assert report.expected_channel_events_changed is False
    assert report.other_channel_payload_changed is False
    assert report.legacy_timing == (timing_policy == "auto")
    assert MATCH in report.to_text()


def test_valid_legacy_envelope_does_not_hide_a_delayed_binary_pair_or_later_notes():
    source, _expected = _source_and_expected()
    before = _midi(source)
    converted = convert_midi_bytes_to_eseq_bytes(before)
    insertion = converted.index(b"\xb0\x40\x7f", 0x77)
    damaged = bytearray(converted[:insertion] + b"\xf3\x0c" + converted[insertion:])
    damaged[3:7] = len(damaged).to_bytes(4, "little")
    damaged[0x1F:0x23] = (len(damaged) - 0x77).to_bytes(4, "little")
    damaged[0x37:0x3B] = (int.from_bytes(damaged[0x37:0x3B], "little") + 12).to_bytes(4, "little")
    damaged[0x41:0x43] = ((len(damaged) - 1) & 0x7FF).to_bytes(2, "big")
    assert is_legacy_eseq_bytes(damaged)
    report = compare_music_bytes(before, bytes(damaged))
    assert report.legacy_timing
    assert report.expected_channel_events_changed is True
    assert MATCH not in report.to_text()
    assert MISMATCH in report.to_text()


def test_an_existing_destination_reserves_only_its_controller_and_is_fully_checked():
    source, expected = _source_and_expected()
    source.append((0, b"\xb2\x40\x17"))
    # Keep every original CC64 event and the occupied native channel-3 lane;
    # only CC67 gets the independently specified two-layer arrangement.
    output = []
    for tick, raw in sorted(source, key=lambda event: event[0]):
        if raw[:2] == b"\xb0\x43":
            output.extend((time, value) for time, value in expected
                          if time == tick and value[0] in (0xB0, 0xB2) and value[1] == 67)
        else:
            output.append((tick, raw))
    report = compare_music_bytes(_midi(source), _fil(output))
    assert report.yamaha_pedal_controllers == (67,)
    assert report.pedal_binary_events_added == 4
    assert report.pedal_duplicate_events_removed == 3
    assert report.expected_channel_events_changed is False
    assert MATCH in report.to_text()


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_complete_and_damaged_report_explanations_render_in_every_language(language):
    source, expected = _source_and_expected()
    report = compare_music_bytes(_midi(source), _fil(expected))
    actual = report.to_text(language)
    assert translate_text(EXPLANATION, language, controllers="CC64, CC67", binary=8, duplicates=6) in actual
    assert translate_text(MATCH, language) in actual
    changed = expected + [(55, b"\xb0\x01\x7f")]
    damaged = compare_music_bytes(_midi(source), _fil(changed)).to_text(language)
    assert translate_text(MISMATCH, language) in damaged
    if language != "en":
        for template in (EXPLANATION, MATCH, MISMATCH):
            translated = CONVERSION_REPORT_TRANSLATIONS[template][language]
            assert translated != template
            assert {field for _, field, _, _ in Formatter().parse(translated) if field} == {
                field for _, field, _, _ in Formatter().parse(template) if field}
        assert MATCH not in actual
        assert MISMATCH not in damaged
