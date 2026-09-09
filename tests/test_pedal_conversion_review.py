"""Only verified pedal relocation may receive a value-preservation claim."""

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
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq


_ROUTING_EXPLANATION = "Continuous pedals moved from channel 1 to channel 3: {controllers}."
_ROUTING_ONLY = "MIDI channel messages changed only in pedal routing or timing; message values are unchanged."
_PAYLOAD_WARNING = "MIDI channel messages changed (notes, controllers, or instruments)."
_INCOMPLETE_YAMAHA = "Other MIDI channel events or timing differ from the expected Yamaha pedal conversion."


def _source(*, binary=False, destination=False):
    events = [(0, b"\x90\x3c\x40"), (0, b"\xc0\x00"), (0, b"\xb0\x01\x20")]
    for controller in (64, 66, 67):
        for tick, value in ((0, 0), (96, 0 if binary else 48), (192, 127), (384, 0)):
            events.append((tick, bytes((0xB0, controller, value))))
    if destination:
        events.append((0, b"\xb2\x40\x00"))
    events.append((384, b"\x80\x3c\x00"))
    track = bytearray()
    previous = 0
    for tick, raw in sorted(events, key=lambda event: event[0]):
        track.extend(_encode_vlq(tick - previous) + raw)
        previous = tick
    track.extend(b"\x00\xff\x2f\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 384) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _relocate_pedals(data):
    # Build an independent expected result instead of reusing the production
    # routing helper whose effect the report must verify.
    for controller in (64, 66, 67):
        data = data.replace(bytes((0xB0, controller)), bytes((0xB2, controller)))
    return data


@pytest.fixture
def routed_conversion():
    source = _source()
    unchanged = convert_midi_bytes_to_eseq_bytes(source, pedal_policy="preserve")
    return source, _relocate_pedals(unchanged)


def test_verified_routing_is_explained_without_clearing_exact_change_flags(routed_conversion):
    report = compare_music_bytes(*routed_conversion)

    assert report.pedal_channels_routed == (64, 66, 67)
    assert report.channel_payload_changed is True
    assert report.other_channel_payload_changed is False
    assert report.pedals_changed
    assert report.channel_events_changed
    assert report.before.pedals == report.after.pedals == {64: 4, 66: 4, 67: 4}
    assert report.before.channels == (1,)
    assert report.after.channels == (1, 3)
    text = report.to_text("en")
    assert _ROUTING_EXPLANATION.format(controllers="CC64, CC66, CC67") in text
    assert report.yamaha_pedal_controllers == ()
    assert report.expected_channel_events_changed is True
    assert _INCOMPLETE_YAMAHA in text
    assert _ROUTING_ONLY not in text
    assert _PAYLOAD_WARNING not in text


@pytest.mark.parametrize("old,new", [
    (b"\x90\x3c\x40", b"\x90\x3c\x41"),  # velocity
    (b"\x90\x3c\x40", b"\x91\x3c\x40"),  # note channel
    (b"\xc0\x00", b"\xc0\x01"),  # instrument
    (b"\xb0\x01\x20", b"\xb0\x01\x21"),  # other controller
    (b"\xb2\x40\x30", b"\xb2\x40\x31"),  # routed value
    (b"\xb2\x40\x30", b"\xb0\x40\x30"),  # partial route
    (b"\xb2\x40\x30", b"\xb3\x40\x30"),  # wrong destination
])
def test_verified_routing_never_hides_other_message_changes(routed_conversion, old, new):
    source, output = routed_conversion
    assert output.count(old) == 1
    report = compare_music_bytes(source, output.replace(old, new, 1))

    assert report.pedal_channels_routed  # unaffected lanes are still described
    assert report.channel_payload_changed is True
    assert report.other_channel_payload_changed is True
    assert _PAYLOAD_WARNING in report.to_text("en")
    assert _ROUTING_ONLY not in report.to_text("en")
    if old.startswith(b"\xb2"):
        assert 64 not in report.pedal_channels_routed


def test_reordered_pedal_values_are_not_described_as_an_intact_lane(routed_conversion):
    source, output = routed_conversion
    first = output.index(b"\xb2\x40\x30") + 2
    second = output.index(b"\xb2\x40\x7f") + 2
    damaged = bytearray(output)
    damaged[first], damaged[second] = damaged[second], damaged[first]

    report = compare_music_bytes(source, bytes(damaged))

    assert report.pedal_channels_routed == (66, 67)
    assert report.other_channel_payload_changed is True
    assert _ROUTING_ONLY not in report.to_text("en")
    assert _PAYLOAD_WARNING in report.to_text("en")


@pytest.mark.parametrize("binary,destination", [(True, False), (False, True)])
def test_binary_lanes_and_occupied_destinations_are_not_claimed_as_expected_routing(binary, destination):
    source = _source(binary=binary, destination=destination)
    output = _relocate_pedals(convert_midi_bytes_to_eseq_bytes(source, pedal_policy="preserve"))
    report = compare_music_bytes(source, output)

    assert 64 not in report.pedal_channels_routed
    assert report.other_channel_payload_changed is True
    assert _PAYLOAD_WARNING in report.to_text("en")
    assert _ROUTING_ONLY not in report.to_text("en")


def test_report_routing_fields_survive_saved_state_and_are_unknown_for_older_reports(routed_conversion):
    report = compare_music_bytes(*routed_conversion)
    saved = json.loads(json.dumps(report.as_dict()))
    restored = ConversionReport.from_dict(saved)

    assert restored.pedal_channels_routed == (64, 66, 67)
    assert restored.other_channel_payload_changed is False
    assert _INCOMPLETE_YAMAHA in restored.to_text("en")
    assert restored.to_text("en") == report.to_text("en")

    saved.pop("pedal_channels_routed")
    saved.pop("other_channel_payload_changed")
    for field in ("yamaha_pedal_controllers", "pedal_binary_events_added", "pedal_duplicate_events_removed", "expected_channel_events_changed"):
        saved.pop(field)
    previous = ConversionReport.from_dict(saved)
    assert previous.pedal_channels_routed == ()
    assert previous.other_channel_payload_changed is None
    assert _ROUTING_EXPLANATION.split(":")[0] not in previous.to_text("en")
    assert _ROUTING_ONLY not in previous.to_text("en")
    assert _PAYLOAD_WARNING in previous.to_text("en")


def test_non_disklavier_conversions_do_not_claim_yamaha_routing(routed_conversion):
    source, output = routed_conversion
    midi = convert_eseq_bytes_to_midi_bytes(output)
    mda = convert_midi_bytes_to_eseq_bytes(source, container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA)
    mda = _relocate_pedals(mda)
    for before, after in ((source, midi), (output, midi), (source, mda)):
        report = compare_music_bytes(before, after)
        assert report.pedal_channels_routed == ()
        assert _ROUTING_EXPLANATION.split(":")[0] not in report.to_text("en")


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES if language.code != "en"])
def test_routing_explanations_are_localized_in_every_supported_language(routed_conversion, language):
    translated = compare_music_bytes(*routed_conversion).to_text(language)
    for source in (_ROUTING_EXPLANATION, _INCOMPLETE_YAMAHA):
        assert source.split(":")[0] not in translated
        assert CONVERSION_REPORT_TRANSLATIONS[source][language].format(controllers="CC64, CC66, CC67") in translated
