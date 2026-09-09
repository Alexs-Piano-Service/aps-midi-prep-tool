"""Disklavier binary/detail pedal conversion contracts, using synthetic music only."""

import struct

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
    convert_midi_file_to_eseq_path,
    parse_eseq_bytes,
)


def _vlq(value):
    encoded = bytearray([value & 127])
    while value >> 7:
        value >>= 7
        encoded.insert(0, 128 | (value & 127))
    return bytes(encoded)


def _midi(events, *, division=384):
    track = bytearray()
    previous = 0
    for tick, raw in events:
        track.extend(_vlq(tick - previous) + raw)
        previous = tick
    track.extend(b"\x00\xff\x2f\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, division) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _events(data):
    return [(tick, raw) for tick, _order, raw in parse_eseq_bytes(data).events]


def _lane(controller=64, *, channel=0):
    return [(tick, bytes([0xB0 | channel, controller, value]))
            for tick, value in ((0, 0), (100, 32), (200, 127), (300, 0))]


def _prepared_lane(controller=64):
    return [
        (100, bytes((0xB2, controller, 32))),
        (200, bytes((0xB0, controller, 127))),
        (200, bytes((0xB2, controller, 127))),
        (300, bytes((0xB0, controller, 0))),
        (300, bytes((0xB2, controller, 0))),
    ]


@pytest.mark.parametrize("controller", (64, 67))
@pytest.mark.parametrize("policy", ("auto", "yamaha"))
def test_continuous_lane_adds_binary_edges_and_detail_without_tick_changes(controller, policy):
    source = _lane(controller)

    output = convert_midi_bytes_to_eseq_bytes(_midi(source), timing_policy="preserve", pedal_policy=policy)

    assert _events(output) == _prepared_lane(controller)


@pytest.mark.parametrize("value", (1, 126))
def test_any_intermediate_value_identifies_the_entire_continuous_lane(value):
    events = [(0, b"\xb0\x40\x00"), (100, bytes([0xB0, 64, value])), (200, b"\xb0\x40\x7f")]

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve")

    expected = [(100, bytes((0xB2, 64, value))), (200, b"\xb2\x40\x7f")]
    expected.insert(0 if value == 126 else 1, (100 if value == 126 else 200, b"\xb0\x40\x7f"))
    assert _events(result) == expected


def test_legacy_binary_companions_and_suppression_do_not_reschedule_original_events():
    # Include a pedal before the first note, simultaneous events, a tempo
    # change, and a long silence so added/omitted messages cannot alter timing.
    source = _midi([
        (0, b"\xc0\x00"), (0, b"\xb0\x40\x00"), (0, b"\xb0\x40\x28"),
        (0, b"\xb0\x07\x00"), (0, b"\x90\x3c\x42"), (0, b"\x90\x40\x43"),
        (384, b"\xff\x51\x03\x0f\x42\x40"), (384, b"\xb0\x40\x7f"),
        (768, b"\x80\x3c\x20"), (768, b"\x80\x40\x21"), (4000, b"\xb0\x40\x00"),
    ])
    preserved = convert_midi_bytes_to_eseq_bytes(source, pedal_policy="preserve")
    routed = convert_midi_bytes_to_eseq_bytes(source)
    old, new = parse_eseq_bytes(preserved), parse_eseq_bytes(routed)

    expected = []
    first_zero = True
    for tick, raw in _events(preserved):
        if raw[:2] != b"\xb0\x40":
            expected.append((tick, raw))
        elif raw[2] == 0 and first_zero:
            first_zero = False
        else:
            if raw[2] in (0, 127):
                expected.append((tick, raw))
            expected.append((tick, b"\xb2" + raw[1:]))
    assert _events(routed) == expected
    assert new.end_tick == old.end_tick
    assert new.tempo_events == old.tempo_events


@pytest.mark.parametrize("timing_policy", ("preserve", "mid2eseq"))
def test_dense_repeated_pedals_preserve_each_original_note_and_pedal_schedule(timing_policy):
    raw_events = [
        b"\xc0\x00", b"\xb0\x40\x00", b"\xb0\x40\x28", b"\xb0\x40\x28",
        b"\x90\x3c\x40", b"\xb0\x40\x54", b"\xb0\x40\x54", b"\x90\x40\x40",
        b"\xb0\x40\x53", b"\xb0\x40\x54", b"\xb0\x40\x52", b"\xb0\x40\x51",
        b"\xb0\x40\x51", b"\x80\x3c\x20", b"\x80\x40\x20",
    ]
    expected_groups = [
        (raw_events[0],), (), (b"\xb2\x40\x28",), (),
        (raw_events[4],), (b"\xb0\x40\x7f", b"\xb2\x40\x54"), (), (raw_events[7],),
        (b"\xb2\x40\x53",), (b"\xb0\x40\x7f", b"\xb2\x40\x54"),
        (b"\xb2\x40\x52",), (b"\xb0\x40\x00", b"\xb2\x40\x51"), (),
        (raw_events[13],), (raw_events[14],),
    ]
    source = _midi([(0, raw) for raw in raw_events])
    preserved = convert_midi_bytes_to_eseq_bytes(
        source, timing_policy=timing_policy, pedal_policy="preserve",
    )
    prepared = convert_midi_bytes_to_eseq_bytes(source, timing_policy=timing_policy)

    assert len(_events(preserved)) == len(expected_groups)
    expected = [(tick, raw) for (tick, _source_raw), group in zip(_events(preserved), expected_groups)
                for raw in group]
    assert _events(prepared) == expected
    assert parse_eseq_bytes(prepared).end_tick == parse_eseq_bytes(preserved).end_tick


@pytest.mark.parametrize("controller", (64, 66, 67))
def test_binary_only_pedal_lanes_stay_on_channel_one(controller):
    events = [(tick, bytes([0xB0, controller, value]))
              for tick, value in ((0, 0), (100, 127), (200, 0), (300, 127))]

    assert _events(convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve")) == events


@pytest.mark.parametrize("policy", ("auto", "yamaha"))
@pytest.mark.parametrize("channel", (0, 1, 2))
def test_continuous_sostenuto_keeps_its_source_channel(channel, policy):
    events = _lane(66, channel=channel)

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve", pedal_policy=policy)

    assert _events(result) == events


@pytest.mark.parametrize("note", (b"\x92\x3c\x40", b"\x92\x3c\x00", b"\x82\x3c\x40"))
@pytest.mark.parametrize("policy", ("auto", "yamaha"))
def test_channel_three_note_events_protect_the_entire_destination(note, policy):
    events = [
        (0, b"\xb0\x40\x00"), (0, b"\xb0\x43\x00"),
        (100, b"\xb0\x40\x32"), (100, b"\xb0\x43\x32"),
        (200, note),
        (300, b"\xb0\x40\x00"), (300, b"\xb0\x43\x00"),
    ]

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve", pedal_policy=policy)

    assert _events(result) == events


@pytest.mark.parametrize("setup", (
    b"\xc2\x00", b"\xb2\x07\x64", b"\xb2\x7b\x00",
    b"\xe2\x00\x40", b"\xd2\x20", b"\xa2\x3c\x20",
))
def test_channel_three_setup_without_notes_does_not_prevent_pedal_routing(setup):
    events = [(0, setup)] + _lane()

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve")

    assert _events(result) == [(0, setup)] + _prepared_lane()


def test_each_controller_lane_is_classified_independently():
    events = [
        (0, b"\xb0\x40\x00"), (0, b"\xb0\x42\x00"), (0, b"\xb0\x43\x00"),
        (100, b"\xb0\x40\x2a"), (100, b"\xb0\x42\x7f"), (100, b"\xb0\x43\x7f"),
        (200, b"\xb0\x40\x00"), (200, b"\xb0\x42\x00"), (200, b"\xb0\x43\x00"),
    ]

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve")

    assert _events(result) == [
        (0, b"\xb0\x42\x00"), (0, b"\xb0\x43\x00"),
        (100, b"\xb2\x40\x2a"), (100, b"\xb0\x42\x7f"), (100, b"\xb0\x43\x7f"),
        (200, b"\xb2\x40\x00"), (200, b"\xb0\x42\x00"), (200, b"\xb0\x43\x00"),
    ]


def test_native_channel_three_continuous_and_channel_one_binary_layers_remain_intact():
    events = [
        (0, b"\xb2\x40\x00"), (0, b"\xb0\x40\x00"),
        (100, b"\xb2\x40\x3f"), (100, b"\xb0\x40\x7f"),
        (200, b"\xb2\x40\x7f"), (300, b"\xb2\x40\x00"), (300, b"\xb0\x40\x00"),
    ]

    assert _events(convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve")) == events


def test_other_channels_and_nonpedal_controllers_are_untouched():
    events = [
        (0, b"\xb0\x01\x32"), (0, b"\xb0\x07\x00"), (0, b"\xb0\x0b\x32"),
        (100, b"\xb1\x40\x32"), (100, b"\xb2\x43\x32"),
        (200, b"\xb3\x42\x32"), (200, b"\xbf\x40\x32"),
        (300, b"\x90\x3c\x40"), (300, b"\x92\x43\x40"),
    ]

    assert _events(convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve")) == events


@pytest.mark.parametrize("destination_value", (0, 32, 127))
def test_existing_destination_lane_prevents_merging_while_other_lanes_still_route(destination_value):
    events = [
        (0, b"\xb0\x40\x00"), (0, b"\xb0\x43\x00"),
        (100, b"\xb0\x40\x3f"), (100, b"\xb0\x43\x3f"),
        (200, bytes([0xB2, 64, destination_value])),
        (300, b"\xb0\x40\x00"), (300, b"\xb0\x43\x00"),
    ]

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve", pedal_policy="yamaha")

    assert _events(result) == [
        (0, b"\xb0\x40\x00"), (100, b"\xb0\x40\x3f"),
        (100, b"\xb2\x43\x3f"), (200, bytes((0xB2, 64, destination_value))),
        (300, b"\xb0\x40\x00"), (300, b"\xb0\x43\x00"), (300, b"\xb2\x43\x00"),
    ]


def test_explicit_preserve_keeps_channel_one_continuous_data():
    events = _lane()

    assert _events(convert_midi_bytes_to_eseq_bytes(_midi(events), timing_policy="preserve", pedal_policy="preserve")) == events


@pytest.mark.parametrize("metadata_policy,include_notice", (("clean", True), ("archival", True), ("archival", False)))
def test_eseq_origin_auto_retains_channels_and_explicit_yamaha_can_override(metadata_policy, include_notice):
    seed = convert_midi_bytes_to_eseq_bytes(_midi(_lane()), timing_policy="preserve", pedal_policy="preserve")
    exported = convert_eseq_bytes_to_midi_bytes(seed, midi_metadata_policy=metadata_policy, include_conversion_text=include_notice)

    restored = convert_midi_bytes_to_eseq_bytes(exported)
    routed = convert_midi_bytes_to_eseq_bytes(exported, pedal_policy="yamaha")

    assert _events(restored) == _events(seed)
    assert _events(routed) == _prepared_lane()
    assert parse_eseq_bytes(routed).end_tick == parse_eseq_bytes(restored).end_tick


def test_mda_auto_preserves_continuous_pedal_channels():
    events = _lane()

    result = convert_midi_bytes_to_eseq_bytes(_midi(events), container_variant="clavinova_mda")

    assert _events(result) == events


def test_explicit_yamaha_policy_cannot_silently_change_mda_pedals():
    with pytest.raises(EseqConversionError, match="Yamaha pedal routing requires the Disklavier FIL container"):
        convert_midi_bytes_to_eseq_bytes(_midi(_lane()), container_variant="clavinova_mda", pedal_policy="yamaha")


@pytest.mark.parametrize("policy", ("unsupported", "legacy", "channel3", 123))
def test_unknown_pedal_policy_is_rejected_even_without_pedals(policy):
    with pytest.raises(EseqConversionError, match="Unsupported E-SEQ pedal policy"):
        convert_midi_bytes_to_eseq_bytes(_midi([(0, b"\xc0\x00")]), pedal_policy=policy)


@pytest.mark.parametrize("policy", ("auto", "yamaha", "preserve"))
def test_public_path_converter_honors_pedal_policy_and_leaves_source_unchanged(tmp_path, policy):
    source = tmp_path / "pedals.mid"
    destination = tmp_path / "pedals.fil"
    original = _midi(_lane())
    source.write_bytes(original)

    convert_midi_file_to_eseq_path(source, destination, timing_policy="preserve", pedal_policy=policy)

    assert _events(destination.read_bytes()) == (_lane() if policy == "preserve" else _prepared_lane())
    assert source.read_bytes() == original
