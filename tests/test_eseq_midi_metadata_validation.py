"""MIDI metadata must be valid and representable by the selected timing writer."""

import pytest

from aps_midi_prep_tool_app import eseq_converter, eseq_legacy


_MALFORMED = [
    pytest.param(0x51, b"", "tempo", id="empty-tempo"),
    pytest.param(0x51, b"\x07", "tempo", id="one-byte-tempo"),
    pytest.param(0x51, b"\x07\xa1", "tempo", id="short-tempo"),
    pytest.param(0x51, b"\x07\xa1\x20\x00", "tempo", id="long-tempo"),
    pytest.param(0x51, b"\x00\x00\x00", "tempo", id="zero-tempo"),
    pytest.param(0x58, b"", "time signature", id="empty-meter"),
    pytest.param(0x58, b"\x04", "time signature", id="one-byte-meter"),
    pytest.param(0x58, b"\x04\x02", "time signature", id="two-byte-meter"),
    pytest.param(0x58, b"\x04\x02\x18", "time signature", id="short-meter"),
    pytest.param(0x58, b"\x04\x02\x18\x08\x00", "time signature", id="long-meter"),
    pytest.param(0x58, b"\x00\x02\x18\x08", "time signature", id="zero-numerator"),
    pytest.param(0x20, b"", "channel prefix", id="empty-prefix"),
    pytest.param(0x20, b"\x00\x01", "channel prefix", id="long-prefix"),
    pytest.param(0x20, b"\x10", "channel prefix", id="out-of-range-prefix"),
    pytest.param(0x20, b"\xff", "channel prefix", id="high-prefix"),
]


def _midi(meta_type, payload, *, late=False, eseq_origin=False):
    metadata = b"\x00\xff" + bytes([meta_type, len(payload)]) + payload
    notes = b"\x00\x90\x3c\x40\x83\x00\x80\x3c\x00"
    track = (notes + metadata if late else metadata + notes) + b"\x00\xff\x2f\x00"
    if eseq_origin:
        notice = eseq_converter.ESEQ_TO_MIDI_CONVERSION_TEXT.encode("ascii")
        track = b"\x00\xff\x01" + bytes([len(notice)]) + notice + track
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\x80"
            + b"MTrk" + len(track).to_bytes(4, "big") + track)


@pytest.mark.parametrize("meta_type,payload,kind", _MALFORMED)
@pytest.mark.parametrize("timing_policy", ["preserve", "mid2eseq", "auto"])
@pytest.mark.parametrize("existing_destination", [False, True])
def test_invalid_metadata_does_not_publish_eseq_output(
    tmp_path, meta_type, payload, kind, timing_policy, existing_destination,
):
    source = tmp_path / "source.mid"
    original = _midi(meta_type, payload, late=True)
    source.write_bytes(original)
    destination = tmp_path / "song.fil"
    if existing_destination:
        destination.write_bytes(b"previous good song")

    with pytest.raises(eseq_converter.EseqConversionError, match=f"Invalid MIDI {kind}"):
        eseq_converter.convert_midi_file_to_eseq_path(source, destination, timing_policy=timing_policy)

    assert source.read_bytes() == original
    if existing_destination:
        assert destination.read_bytes() == b"previous good song"
    else:
        assert not destination.exists()
    expected_files = ["song.fil", "source.mid"] if existing_destination else ["source.mid"]
    assert sorted(path.name for path in tmp_path.iterdir()) == expected_files


@pytest.mark.parametrize("timing_policy", ["preserve", "mid2eseq"])
@pytest.mark.parametrize("meta_type,payload,kind", [
    (0x51, b"\x00\x00\x00", "tempo"),
    (0x58, b"\x04\x02\x18", "time signature"),
    (0x20, b"\x10", "channel prefix"),
])
def test_invalid_metadata_is_rejected_before_timing_generation(monkeypatch, timing_policy, meta_type, payload, kind):
    def unexpected_writer(*_args, **_kwargs):
        pytest.fail("Timing generation started before malformed metadata was rejected")

    monkeypatch.setattr(eseq_converter, "_build_time_signature_markers", unexpected_writer)
    monkeypatch.setattr(eseq_legacy, "build_legacy_eseq_bytes", unexpected_writer)
    with pytest.raises(eseq_converter.EseqConversionError, match=f"Invalid MIDI {kind}"):
        eseq_converter.convert_midi_bytes_to_eseq_bytes(_midi(meta_type, payload), timing_policy=timing_policy)


@pytest.mark.parametrize("meta_type,payload,kind", _MALFORMED)
def test_direct_legacy_writer_uses_the_same_metadata_validation(meta_type, payload, kind):
    raw = b"\xff" + bytes([meta_type, len(payload)]) + payload
    with pytest.raises(eseq_converter.EseqConversionError, match=f"Invalid MIDI {kind}"):
        eseq_legacy.build_legacy_eseq_bytes([(0, 0, 0, raw)], 384, title="Malformed metadata")


@pytest.mark.parametrize("timing_policy", ["preserve", "mid2eseq"])
@pytest.mark.parametrize("meta_type,payload", [
    (0x51, b"\x07\xa1\x20"),
    (0x58, b"\x03\x02\x18\x08"),
    (0x58, b"\x07\x03\x00\x00"),
    (0x20, b"\x00"),
    (0x20, b"\x0f"),
    (0x7f, b"\x00\x01\x02\x03\xff"),
])
def test_valid_metadata_and_opaque_meta_payloads_still_convert(timing_policy, meta_type, payload):
    output = eseq_converter.convert_midi_bytes_to_eseq_bytes(
        _midi(meta_type, payload), timing_policy=timing_policy,
    )
    parsed = eseq_converter.parse_eseq_bytes(output)
    assert any(raw == b"\x90\x3c\x40" for _tick, _order, raw in parsed.events)
    if meta_type == 0x20:
        assert any(raw == b"\xff\x20\x01" + payload for _tick, _order, raw in parsed.events)


@pytest.mark.parametrize("timing_policy,variant,eseq_origin", [
    ("preserve", "disklavier", False),
    ("preserve", "clavinova_mda", False),
    ("auto", "disklavier", True),
    ("auto", "clavinova_mda", False),
])
@pytest.mark.parametrize("denominator_power", [8, 255])
@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("existing_destination", [False, True])
def test_unrepresentable_meter_is_rejected_without_publishing_output(
    tmp_path, timing_policy, variant, eseq_origin, denominator_power, late, existing_destination,
):
    source = tmp_path / "source.mid"
    original = _midi(0x58, bytes([4, denominator_power, 24, 8]), late=late, eseq_origin=eseq_origin)
    source.write_bytes(original)
    destination = tmp_path / ("song.mda" if variant == "clavinova_mda" else "song.fil")
    if existing_destination:
        destination.write_bytes(b"previous good song")

    with pytest.raises(
        eseq_converter.EseqConversionError,
        match="Unsupported MIDI time signature.*denominator exponent.*0 to 7",
    ):
        eseq_converter.convert_midi_file_to_eseq_path(
            source, destination, timing_policy=timing_policy, container_variant=variant,
        )

    assert source.read_bytes() == original
    if existing_destination:
        assert destination.read_bytes() == b"previous good song"
    else:
        assert not destination.exists()
    expected_files = {source, destination} if existing_destination else {source}
    assert set(tmp_path.iterdir()) == expected_files


@pytest.mark.parametrize("variant", ["disklavier", "clavinova_mda"])
@pytest.mark.parametrize("denominator_power", [0, 7])
def test_supported_meter_boundaries_are_preserved_in_header_and_markers(variant, denominator_power):
    output = eseq_converter.convert_midi_bytes_to_eseq_bytes(
        _midi(0x58, bytes([4, denominator_power, 24, 8])), timing_policy="preserve",
        container_variant=variant,
    )

    parsed = eseq_converter.parse_eseq_bytes(output)
    assert parsed.time_signature_events == [(0, 4, denominator_power)]
    assert parsed.end_tick == 384
    if variant == "disklavier":
        assert output[0x34:0x36] == bytes([4, 1 << denominator_power])


@pytest.mark.parametrize("timing_policy", ["mid2eseq", "auto"])
@pytest.mark.parametrize("denominator_power", [8, 255])
def test_legacy_timing_keeps_its_deliberate_meter_simplification(timing_policy, denominator_power):
    expected = eseq_converter.convert_midi_bytes_to_eseq_bytes(
        _midi(0x58, bytes([4, 2, 24, 8])), timing_policy=timing_policy,
    )
    output = eseq_converter.convert_midi_bytes_to_eseq_bytes(
        _midi(0x58, bytes([4, denominator_power, 24, 8])), timing_policy=timing_policy,
    )

    assert output == expected
