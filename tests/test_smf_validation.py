"""Discovery and conversion agree on the structure of Standard MIDI Files."""

from contextlib import contextmanager
from types import SimpleNamespace
import struct

import pytest

from aps_midi_prep_tool_app import emulator_image_builder, midi_metadata
from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    convert_midi_bytes_to_eseq_bytes,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import FloppyImageError
from aps_midi_prep_tool_app.midi_channel_merger import merge_midi_channels_to_channel0_bytes
from aps_midi_prep_tool_app.midi_type0_converter import _convert_midi_bytes_to_type0


_TRACK = b"\x00\x92\x3c\x40\x60\x82\x3c\x00\x00\xff\x2f\x00"


def _chunk(chunk_id, payload):
    return chunk_id + len(payload).to_bytes(4, "big") + payload


def _midi(*, format_type=1, declared_tracks=1, division=96, tracks=(_TRACK,), extra=b""):
    header = struct.pack(">HHH", format_type, declared_tracks, division) + extra
    return _chunk(b"MThd", header) + b"".join(_chunk(b"MTrk", track) for track in tracks)


_INVALID_FILES = [
    pytest.param(_midi(format_type=3), "format 3", id="unknown-format"),
    pytest.param(_midi(format_type=65535), "format 65535", id="maximum-format"),
    pytest.param(
        _midi(format_type=0, declared_tracks=2, tracks=(_TRACK, _TRACK)),
        "invalid track count", id="type0-two-tracks",
    ),
    pytest.param(_midi(format_type=0, declared_tracks=0, tracks=()), "invalid track count", id="type0-zero-tracks"),
    pytest.param(_midi(declared_tracks=0, tracks=()), "invalid track count", id="type1-zero-tracks"),
    pytest.param(_midi(format_type=2, declared_tracks=0, tracks=()), "invalid track count", id="type2-zero-tracks"),
    pytest.param(_midi(division=0), "time division of zero", id="zero-ppqn"),
    pytest.param(_midi(division=0xE428), "invalid SMPTE", id="invalid-smpte-frame-rate"),
    pytest.param(_midi(division=0xE700), "invalid SMPTE", id="zero-smpte-ticks"),
    pytest.param(_midi(declared_tracks=2), "declared MIDI track is missing", id="missing-declared-track"),
    pytest.param(_midi(tracks=()), "declared MIDI track is missing", id="missing-only-track"),
    pytest.param(_midi()[:-1], "Corrupt MIDI chunk length", id="truncated-track-payload"),
    pytest.param(_midi(tracks=()) + b"MTrk\x00", "declared MIDI track is missing", id="truncated-track-header"),
    pytest.param(
        _midi(tracks=()) + _chunk(b"JUNK", _TRACK),
        "declared MIDI track is missing", id="unknown-chunk-is-not-track",
    ),
]


@pytest.mark.parametrize("payload, message", _INVALID_FILES)
@pytest.mark.parametrize("filename", ["Broken.mid", "Broken"])
def test_discovery_rejects_invalid_smf_structure(tmp_path, payload, message, filename):
    song = tmp_path / filename
    song.write_bytes(payload)

    with pytest.raises(midi_metadata.MidiTitleFormatError, match=message):
        midi_metadata.probe_midi_file_type(song)
    assert not midi_metadata.is_midi_file(song)
    with pytest.raises(FloppyImageError, match=message):
        emulator_image_builder.discover_song_files(tmp_path)
    assert song.read_bytes() == payload


@pytest.mark.parametrize("payload, message", _INVALID_FILES)
@pytest.mark.parametrize("convert", [
    _convert_midi_bytes_to_type0,
    convert_midi_bytes_to_eseq_bytes,
    merge_midi_channels_to_channel0_bytes,
])
def test_converters_reject_the_same_invalid_smf_structure(payload, message, convert):
    with pytest.raises(ValueError, match=message):
        convert(payload)


@pytest.mark.parametrize("format_type", [0, 1])
def test_extended_headers_and_unknown_chunks_survive_discovery_and_conversion(tmp_path, format_type):
    extra = b"Extended header data"
    unknown = _chunk(b"JUNK", b"Unknown MIDI chunk")
    source = (
        _midi(format_type=format_type, tracks=(), extra=extra)
        + unknown + _chunk(b"MTrk", _TRACK) + unknown
    )
    song = tmp_path / "Extended.mid"
    song.write_bytes(source)

    assert emulator_image_builder.discover_song_files(tmp_path) == [str(song)]
    assert midi_metadata.probe_midi_file_type(song) == format_type
    converted, _ = _convert_midi_bytes_to_type0(source)
    merged, _ = merge_midi_channels_to_channel0_bytes(source)
    for result in (converted, merged):
        assert result[14:14 + len(extra)] == extra
        assert result.count(unknown) == 2
    eseq = convert_midi_bytes_to_eseq_bytes(source)
    assert parse_eseq_bytes(eseq).events


def test_valid_type2_is_discovered_but_not_flattened_by_converters(tmp_path):
    source = _midi(format_type=2, declared_tracks=2, tracks=(_TRACK, _TRACK))
    song = tmp_path / "Independent tracks.mid"
    song.write_bytes(source)

    assert midi_metadata.probe_midi_file_type(song) == 2
    assert emulator_image_builder.discover_song_files(tmp_path) == [str(song)]
    for convert in (_convert_midi_bytes_to_type0, convert_midi_bytes_to_eseq_bytes):
        with pytest.raises(ValueError, match="format 2 files are not supported"):
            convert(source)
    merged, _ = merge_midi_channels_to_channel0_bytes(source)
    assert int.from_bytes(merged[8:10], "big") == 2


def test_converters_retain_support_for_headers_beyond_the_discovery_limit():
    extra = b"\x00" * (64 * 1024)
    source = _midi(extra=extra)

    for convert in (_convert_midi_bytes_to_type0, merge_midi_channels_to_channel0_bytes):
        converted, _ = convert(source)
        assert converted[14:14 + len(extra)] == extra
    assert parse_eseq_bytes(convert_midi_bytes_to_eseq_bytes(source)).events


@pytest.mark.parametrize("frame_rate", [24, 25, 29, 30])
def test_valid_smpte_is_discovered_and_preserved_by_midi_converters(tmp_path, frame_rate):
    division = ((256 - frame_rate) << 8) | 40
    source = _midi(division=division)
    song = tmp_path / "SMPTE.mid"
    song.write_bytes(source)

    assert midi_metadata.probe_midi_file_type(song) == 1
    assert emulator_image_builder.discover_song_files(tmp_path) == [str(song)]
    for convert in (_convert_midi_bytes_to_type0, merge_midi_channels_to_channel0_bytes):
        converted, _ = convert(source)
        assert int.from_bytes(converted[12:14], "big") == division
    with pytest.raises(EseqConversionError, match="SMPTE MIDI timebases are not supported"):
        convert_midi_bytes_to_eseq_bytes(source)


def test_discovery_skips_extended_header_and_track_payload_allocations(tmp_path, monkeypatch):
    song = tmp_path / "Large.mid"
    song.write_bytes(_midi(
        tracks=(b"\x00\xc0\x00" * (64 * 1024) + b"\x00\xff\x2f\x00",),
        extra=b"\x00" * (64 * 1024 - 6),
    ))
    real_open = open
    read_sizes = []

    @contextmanager
    def guarded_open(path, mode):
        with real_open(path, mode) as handle:
            def guarded_read(size=-1):
                assert 0 <= size <= 8, "Discovery must only read fixed-size chunk headers"
                read_sizes.append(size)
                return handle.read(size)
            yield SimpleNamespace(read=guarded_read, seek=handle.seek, fileno=handle.fileno)

    monkeypatch.setattr(midi_metadata, "open", guarded_open, raising=False)
    assert midi_metadata.probe_midi_file_type(song) == 1
    assert read_sizes == [8, 6, 8]
