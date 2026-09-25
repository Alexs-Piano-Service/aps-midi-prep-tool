"""Native E-SEQ piano merging retains the original Yamaha container."""

import os

import pytest

from aps_midi_prep_tool_app.eseq_channel_merger import (
    merge_eseq_channels_to_channel0_bytes,
    merge_eseq_channels_to_channel0_path,
)
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    EseqConversionError,
    _declared_stream_end,
    _detect_eseq_container,
    _eseq_event_stream_start,
    convert_midi_bytes_to_eseq_bytes,
    is_clavinova_mda_eseq_bytes,
    is_eseq_file,
    parse_eseq_bytes,
)


def _eseq(stream, variant="fil", suffix=b"opaque trailing data"):
    track = b"\x00\x94\x3C\x64\x60\x84\x3C\x00\x00\xFF\x2F\x00"
    midi = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\x80MTrk"
    midi += len(track).to_bytes(4, "big") + track
    options = {"filename_hint": "ORIGINAL.FIL", "title_override": "Native title"}
    if variant == "mda":
        options["container_variant"] = ESEQ_CONTAINER_CLAVINOVA_MDA
        options["filename_hint"] = "ORIGINAL.MDA"
    initial = convert_midi_bytes_to_eseq_bytes(midi, **options)
    header = bytearray(initial[:_eseq_event_stream_start(initial)])
    header[0x4F] = 0xA5
    if variant == "q11":
        header[0x0F:0x17] = b"Q11V1.00"
        header.extend(b"opaque Q11 prefix".ljust(0x200 - len(header), b"\xA5"))
    size = len(header) + len(stream)
    if variant != "mda":
        header[3:7] = size.to_bytes(4, "little")
    header[0x1F:0x23] = (len(stream) if variant == "fil" else size).to_bytes(4, "little")
    return bytes(header) + stream + suffix


def _events(data):
    return [(tick, raw) for tick, _order, raw in parse_eseq_bytes(data).events]


@pytest.fixture
def filename_assisted_mda():
    # The first 32 bytes contain channel events, but the remainder is also a
    # valid stream: treating this as FIL would silently leave those untouched.
    stream = (
        b"\xF1\x00\xF9\x04\x02\xB2\x40\x2A\xC5\x29"
        b"\xB5\x00\x01\xB5\x20\x02\xFF\x35\x95\x3C\x64"
        b"\xA5\x3C\x20\xD5\x30\xE5\x00\x50\xF4\x20\x00"
        b"\x94\x3E\x50\xF3\x10\x84\x3E\x20\x85\x3C\x40\xB2\x40\x00\xF2"
    )
    source = bytearray(_eseq(stream, "mda"))
    # Fail both parts of the strong signature. Include FIL-like fields that
    # must remain opaque when the filename resolves this shorter container.
    source[3:7] = (0x57 + len(stream)).to_bytes(4, "little")
    source[0x19] = 0x40
    source[0x1B:0x1F] = (0x50).to_bytes(4, "little")
    source[0x51] = 1
    source[0x54:0x56] = b"\xAA\x55"
    assert not is_clavinova_mda_eseq_bytes(source)
    assert is_clavinova_mda_eseq_bytes(source, "source.MDA")
    assert not is_clavinova_mda_eseq_bytes(source, "source.FIL")
    return bytes(source)


@pytest.mark.parametrize("extension", ["MDA", "mDa"])
@pytest.mark.parametrize("bytes_path", [False, True])
def test_path_merge_preserves_filename_assisted_mda(tmp_path, filename_assisted_mda, extension, bytes_path):
    original = filename_assisted_mda
    source = tmp_path / f"source.{extension}"
    destination = tmp_path / "staged.tmp"
    source.write_bytes(original)
    assert is_eseq_file(source)
    container = _detect_eseq_container(original, source.name)
    assert container == ESEQ_CONTAINER_CLAVINOVA_MDA
    assert _eseq_event_stream_start(original, container_variant=container) == 0x57
    assert _declared_stream_end(original, 0x57, container_variant=container) == len(original) - len(b"opaque trailing data")

    source_path = os.fsencode(source) if bytes_path else source
    destination_path = os.fsencode(destination) if bytes_path else destination
    assert merge_eseq_channels_to_channel0_path(source_path, destination_path)
    merged = destination.read_bytes()
    expected_stream = (
        b"\xF1\x00\xF9\x04\x02\xC0\x00\xB0\x40\x2A\xFF\x30"
        b"\x90\x3C\x64\xA0\x3C\x20\xD0\x30\xE0\x00\x50\xF4\x20\x00"
        b"\x90\x3E\x50\xF3\x10\x80\x3E\x20\x80\x3C\x40\xB0\x40\x00\xF2"
    )
    assert merged[0x57:] == expected_stream + b"opaque trailing data"
    assert merged[:0x1F] == original[:0x1F]
    assert merged[0x23:0x57] == original[0x23:0x57]
    assert int.from_bytes(merged[0x1F:0x23], "little") == 0x57 + len(expected_stream)
    assert not is_clavinova_mda_eseq_bytes(merged)
    assert is_clavinova_mda_eseq_bytes(merged, source.name)
    assert source.read_bytes() == original
    assert merge_eseq_channels_to_channel0_bytes(original, filename=source.name) == (merged, True)
    assert merge_eseq_channels_to_channel0_bytes(merged, filename=source.name) == (merged, False)
    # Reopening the staged song under its logical name must remain a no-op.
    staged_song = tmp_path / f"staged.{extension}"
    destination.rename(staged_song)
    assert not merge_eseq_channels_to_channel0_path(staged_song, source)
    assert source.read_bytes() == original


def test_mda_filename_fallback_does_not_apply_to_fil_source(tmp_path, filename_assisted_mda):
    original = filename_assisted_mda
    source = tmp_path / "source.FIL"
    destination = tmp_path / "destination.MDA"
    source.write_bytes(original)
    container = _detect_eseq_container(original, source.name)
    assert _eseq_event_stream_start(original, container_variant=container) == 0x77
    assert merge_eseq_channels_to_channel0_path(source, destination)
    merged = destination.read_bytes()
    assert merged == merge_eseq_channels_to_channel0_bytes(original)[0]
    assert merged[0x57:0x77] == original[0x57:0x77]
    assert not is_clavinova_mda_eseq_bytes(merged, source.name)


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
def test_native_merge_preserves_header_timing_sysex_and_opaque_suffix(variant):
    stream = (
        b"\xF1\x00\xF9\x03\x02\xFB\x64\x07"
        b"\xFF\x35\xC5\x29\xB5\x00\x01\xB5\x20\x02"
        b"\xF4\x00\x03\x95\x3C\x64\xA5\x3C\x20\xD5\x30\xE5\x00\x50"
        b"\xF0\x43\x12\xF3\x03\x34\xF4\x04\x00\x56\xF7"
        b"\xF3\x20\x85\x3C\x40\xF4\x00\x03\xF2"
    )
    source = _eseq(stream, variant)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)

    assert changed
    before = parse_eseq_bytes(source)
    after = parse_eseq_bytes(merged)
    assert after.end_tick == before.end_tick
    assert after.tempo_factors == before.tempo_factors
    assert after.time_signature_events == before.time_signature_events
    assert after.title == before.title
    assert merged.endswith(b"opaque trailing data")
    assert b"\xF0\x43\x12\xF3\x03\x34\xF4\x04\x00\x56\xF7" in merged
    assert b"\xF4\x00\x03" in merged
    assert b"\xFF\x30" in merged
    channels = [(tick, raw) for tick, raw in _events(merged) if 0x80 <= raw[0] <= 0xEF]
    assert all(raw[0] & 0x0F == 0 for _tick, raw in channels)
    assert [(tick, raw) for tick, raw in channels if raw[0] == 0xC0] == [(0, b"\xC0\x00")]
    assert not any(raw[:2] in (b"\xB0\x00", b"\xB0\x20") for _tick, raw in channels)
    assert (384, b"\x90\x3C\x64") in channels
    assert (423, b"\x80\x3C\x40") in channels

    start = _eseq_event_stream_start(source)
    changed_offsets = set(range(0x1F, 0x23))
    if variant != "mda":
        changed_offsets.update(range(3, 7))
    if variant == "fil":
        changed_offsets.update((0x54, 0x55))
        assert merged[0x54:0x56] == b"\x01\x00"
    assert all(source[index] == merged[index] for index in range(start) if index not in changed_offsets)
    delta = len(merged) - len(source)
    assert int.from_bytes(merged[0x1F:0x23], "little") == int.from_bytes(source[0x1F:0x23], "little") + delta
    if variant == "mda":
        assert is_clavinova_mda_eseq_bytes(merged)
        assert merged[0x57:0x5E] == b"\xF1\x00\xF9\x03\x02\xC0\x00"
    again, changed_again = merge_eseq_channels_to_channel0_bytes(merged)
    assert not changed_again
    assert again is merged


def test_overlapping_source_notes_defer_release_and_controller_resets_stay_local():
    source = _eseq(
        b"\xF1\x00\xB4\x01\x50\x94\x3C\x64\xF3\x0A"
        b"\xB5\x01\x30\x95\x3C\x64\xF3\x0A"
        b"\xB4\x79\x00\xB4\x7B\x00\xF3\x0A\x85\x3C\x40\xF2"
    )
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert _events(merged) == [
        (0, b"\xC0\x00"),
        (0, b"\xB0\x01\x50"),
        (0, b"\x90\x3C\x64"),
        (10, b"\xB0\x01\x30"),
        (10, b"\x90\x3C\x64"),
        (30, b"\x80\x3C\x00"),
        (30, b"\x80\x3C\x40"),
    ]


def test_factory_length_control_word_is_preserved_when_it_is_not_a_length():
    source = bytearray(_eseq(
        b"\xF1\x00\xC5\x29\xB5\x00\x01\xB5\x20\x02"
        b"\x95\x3C\x64\xF3\x60\x85\x3C\x00\xF2",
    ))
    source[3:7] = (1).to_bytes(4, "little")
    merged, changed = merge_eseq_channels_to_channel0_bytes(bytes(source))
    assert changed
    assert merged[3:7] == source[3:7]
    delta = len(merged) - len(source)
    assert int.from_bytes(merged[0x1F:0x23], "little") == int.from_bytes(source[0x1F:0x23], "little") + delta
    assert parse_eseq_bytes(merged).end_tick == parse_eseq_bytes(source).end_tick


@pytest.mark.parametrize("unfamiliar_layout", [False, True])
def test_unestablished_header_bits_are_preserved(unfamiliar_layout):
    source = bytearray(_eseq(b"\xF1\x00\x92\x3C\x64\xF3\x60\x82\x3C\x00\xF2"))
    source[0x51] = 0x80  # Historical value with unknown semantics.
    source[0x54:0x56] = b"\xAA\x55"
    if unfamiliar_layout:
        source[0x19] = 0x41
    merged, changed = merge_eseq_channels_to_channel0_bytes(bytes(source))
    assert changed
    assert merged[0x51] == 0x80
    assert merged[0x54:0x56] == (b"\xAA\x55" if unfamiliar_layout else b"\x01\x00")
    assert all(raw[0] & 0x0F == 0 for _, raw in _events(merged) if 0x80 <= raw[0] <= 0xEF)


def test_controller_reset_emits_replacement_in_original_stream_position():
    source = _eseq(b"\xF1\x00\xB4\x01\x50\xF3\x0A\xB4\x79\x00\xF2")
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert _events(merged) == [(0, b"\xB0\x01\x50"), (10, b"\xB0\x01\x00")]


def test_yamaha_dedicated_pedal_lane_survives_instrument_channel_merge():
    source = bytearray(_eseq(
        b"\xF1\x00\xB0\x40\x7F\xB2\x40\x2A\xB2\x43\x14"
        b"\x94\x3C\x64\xF3\x60\x84\x3C\x00\xB0\x40\x00\xB2\x40\x00\xF2"
    ))
    source[0x51] = 0x80
    merged, changed = merge_eseq_channels_to_channel0_bytes(bytes(source))
    assert changed
    assert merged[0x51] == 0x80
    assert merged[0x54:0x56] == b"\x01\x00"
    assert [(tick, raw) for tick, raw in _events(merged) if raw[0] == 0xB2] == [
        (0, b"\xB2\x40\x2A"), (0, b"\xB2\x43\x14"), (96, b"\xB2\x40\x00"),
    ]
    assert merge_eseq_channels_to_channel0_bytes(merged) == (merged, False)


def test_channel_three_notes_mean_its_controllers_are_ordinary_instrument_events():
    source = _eseq(b"\xF1\x00\xB2\x40\x2A\x92\x3C\x64\xF3\x60\x82\x3C\x00\xF2")
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert (0, b"\xB0\x40\x2A") in _events(merged)
    assert merged[0x51] == 0
    assert not any(raw[0] == 0xB2 for _tick, raw in _events(merged))


def test_native_pedal_reset_survives_while_ordinary_control_reset_moves_to_piano():
    source = _eseq(
        b"\xF1\x00\xB2\x40\x2A\xB2\x01\x50\x94\x3C\x64"
        b"\xF3\x60\x84\x3C\x00\xB2\x79\x00\xF2"
    )
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert _events(merged) == [
        (0, b"\xC0\x00"), (0, b"\xB2\x40\x2A"),
        (0, b"\xB0\x01\x50"), (0, b"\x90\x3C\x64"),
        (96, b"\x80\x3C\x00"), (96, b"\xB0\x01\x00"), (96, b"\xB2\x79\x00"),
    ]
    assert merge_eseq_channels_to_channel0_bytes(merged) == (merged, False)


@pytest.mark.parametrize("variant", ["mda", "q11"])
def test_other_containers_do_not_assume_disklavier_pedal_channel(variant):
    source = _eseq(b"\xF1\x00\xB2\x40\x2A\x94\x3C\x64\xF3\x60\x84\x3C\x00\xF2", variant)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert (0, b"\xB0\x40\x2A") in _events(merged)
    assert not any(raw[0] == 0xB2 for _tick, raw in _events(merged))


def test_sysex_reset_is_recognized_without_rewriting_its_bytes():
    source = _eseq(
        b"\xF1\x00\xB4\x01\x50\xF0\x7E\x7F\x09\x01\xF7"
        b"\xB4\x79\x00\xF2"
    )
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert _events(merged) == [(0, b"\xB0\x01\x50"), (0, b"\xF0\x7E\x7F\x09\x01\xF7")]


def test_aligned_padding_stays_aligned_and_canonical_file_is_unchanged():
    source = _eseq(b"\xF1\x00\x94\x3C\x64\xF3\x60\x84\x3C\x00\xF2", suffix=b"")
    source += b"\xF6" * (-len(source) % 2048)
    merged, changed = merge_eseq_channels_to_channel0_bytes(source)
    assert changed
    assert len(merged) % 2048 == 0
    assert merge_eseq_channels_to_channel0_bytes(merged) == (merged, False)
    empty = _eseq(b"\xF1\x00\xF9\x04\x02\xF4\x00\x03\xF2")
    assert merge_eseq_channels_to_channel0_bytes(empty) == (empty, False)


@pytest.mark.parametrize("stream", [b"\xF1\x00\x94\x3C", b"\xF1\x00\x94\xF2\x40", b"\xF1\x00\xF0\x43", b"\xF1\x00\xF5"])
def test_malformed_stream_does_not_overwrite_destination(tmp_path, stream):
    source = tmp_path / "invalid.FIL"
    destination = tmp_path / "existing.FIL"
    source.write_bytes(_eseq(stream, suffix=b""))
    destination.write_bytes(b"keep this destination")
    with pytest.raises(EseqConversionError):
        merge_eseq_channels_to_channel0_path(source, destination)
    assert destination.read_bytes() == b"keep this destination"
    assert not list(tmp_path.glob("*.tmp"))


def test_path_api_is_atomic_supports_in_place_and_skips_noop(tmp_path, monkeypatch):
    source = tmp_path / "song.MDA"
    original = _eseq(b"\xF1\x00\x94\x3C\x64\xF3\x60\x84\x3C\x00\xF2", "mda")
    source.write_bytes(original)
    calls = []
    replace = os.replace

    def record_replace(src, dst):
        calls.append((src, dst))
        replace(src, dst)

    monkeypatch.setattr(os, "replace", record_replace)
    assert merge_eseq_channels_to_channel0_path(source, source)
    assert len(calls) == 1
    assert source.read_bytes() != original
    assert not list(tmp_path.glob("*.tmp"))
    destination = tmp_path / "noop.MDA"
    assert not merge_eseq_channels_to_channel0_path(source, destination)
    assert not destination.exists()
