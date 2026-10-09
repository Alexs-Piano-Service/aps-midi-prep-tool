"""Small MIDI inputs must not expand into unbounded E-SEQ data."""

import os
import struct
import subprocess
import sys

import pytest

from aps_midi_prep_tool_app import eseq_converter as converter
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq


def _midi(*, end_delta=0, meter=True, hint=None, division=384):
    track = bytearray()
    if meter:
        track.extend(b"\x00\xFF\x58\x04\x01\x07\x18\x08")
    if hint:
        payload = hint.encode("ascii")
        track.extend(b"\x00\xFF\x01" + _encode_vlq(len(payload)) + payload)
    track.extend(b"\x00\x90\x3C\x40\x0C\x80\x3C\x00")
    track.extend(_encode_vlq(end_delta) + b"\xFF\x2F\x00")
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, division) + b"MTrk" + len(track).to_bytes(4, "big") + track


@pytest.mark.parametrize("variant", ["disklavier", "clavinova_mda"])
@pytest.mark.parametrize("case", ["barlines", "long_delay", "before_hint", "after_hint"])
def test_tiny_input_expansion_is_rejected_without_touching_destination(tmp_path, variant, case):
    source = tmp_path / "source.mid"
    destination = tmp_path / "existing.fil"
    if case == "barlines":
        midi = _midi(end_delta=0x0FFFFFFF)
    elif case == "long_delay":
        midi = _midi(end_delta=0x0FFFFFFF, meter=False, division=1)
    elif case == "before_hint":
        midi = _midi(meter=False, hint="APS-ESEQ-TIMING before=999999999999 visible_before=0")
    else:
        midi = _midi(meter=False, hint="APS-ESEQ-TIMING after=999999999999 visible_after=0")
    assert len(midi) < 100
    source.write_bytes(midi)
    destination.write_bytes(b"keep the original song")
    # Bound even a regressed implementation: it must raise the conversion
    # error, not exhaust memory or hang the test worker on millions of bars.
    script = """
import sys
if sys.platform == 'linux':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
from aps_midi_prep_tool_app.eseq_converter import EseqConversionError, convert_midi_file_to_eseq_path
try:
    convert_midi_file_to_eseq_path(sys.argv[1], sys.argv[2], timing_policy='preserve', container_variant=sys.argv[3])
except EseqConversionError as exc:
    assert 'limit' in str(exc).lower(), str(exc)
    print(str(exc))
else:
    raise AssertionError('Oversized conversion unexpectedly succeeded')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, os.fspath(source), os.fspath(destination), variant],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert destination.read_bytes() == b"keep the original song"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.fil", "source.mid"]


def test_automatic_eseq_return_uses_the_same_resource_limits():
    midi = _midi(hint="APS-ESEQ-TIMING after=999999999999")
    with pytest.raises(converter.EseqConversionError, match="marker.*limit"):
        converter.convert_midi_bytes_to_eseq_bytes(midi)


def test_ordinary_dense_meter_preserves_barlines_and_end_time():
    output = converter.convert_midi_bytes_to_eseq_bytes(_midi(end_delta=1188), timing_policy="preserve")
    parsed = converter.parse_eseq_bytes(output)
    assert parsed.end_tick == 1200
    assert parsed.time_signature_events == [(0, 1, 7)]
    assert output[converter.ESEQ_HEADER_SIZE:].count(b"\xF9\x01\x07") == 100
    assert [(tick, raw) for tick, _, raw in parsed.events] == [(0, b"\x90\x3C\x40"), (12, b"\x80\x3C\x00")]


def test_marker_budget_counts_all_segments_and_last_shared_tick_wins(monkeypatch):
    monkeypatch.setattr(converter, "MAX_ESEQ_GENERATED_MARKERS", 4)
    signatures = [(0, 4, 2), (0, 1, 7), (24, 1, 7)]
    assert converter._build_time_signature_markers(signatures, 48) == [
        (0, 1, 7), (12, 1, 7), (24, 1, 7), (36, 1, 7),
    ]
    with pytest.raises(converter.EseqConversionError, match="marker.*limit"):
        converter._build_time_signature_markers(signatures, 49)


@pytest.mark.parametrize("variant,padded", [("disklavier", True), ("clavinova_mda", False)])
def test_total_output_budget_includes_header_and_padding(monkeypatch, variant, padded):
    midi = _midi(end_delta=24)
    expected = converter.convert_midi_bytes_to_eseq_bytes(midi, timing_policy="preserve", container_variant=variant)
    if padded:
        assert len(expected) == 2048
    monkeypatch.setattr(converter, "MAX_ESEQ_OUTPUT_BYTES", len(expected))
    assert converter.convert_midi_bytes_to_eseq_bytes(midi, timing_policy="preserve", container_variant=variant) == expected
    monkeypatch.setattr(converter, "MAX_ESEQ_OUTPUT_BYTES", len(expected) - 1)
    with pytest.raises(converter.EseqConversionError, match="output.*limit"):
        converter.convert_midi_bytes_to_eseq_bytes(midi, timing_policy="preserve", container_variant=variant)


def test_archival_q11_output_budget_counts_the_larger_header(monkeypatch):
    header = bytearray(512)
    header[0] = 0xFE
    header[7:15] = b"COM-ESEQ"
    header[15:23] = b"Q11V1.00"
    header[0x24] = 117 - 29
    stream = b"\xF1\x00\xF0" + b"\x01" * 1600 + b"\xF7\xF2"
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    midi = converter.convert_eseq_bytes_to_midi_bytes(bytes(header) + stream, midi_metadata_policy="archival")
    output = converter.convert_midi_bytes_to_eseq_bytes(midi)
    assert output[15:23] == b"Q11V1.00"
    assert len(output) == 4096
    # A normal 119-byte header would fit into 2048 bytes with this stream.
    monkeypatch.setattr(converter, "MAX_ESEQ_OUTPUT_BYTES", 2048)
    with pytest.raises(converter.EseqConversionError, match="output.*limit"):
        converter.convert_midi_bytes_to_eseq_bytes(midi)


@pytest.mark.parametrize("prefer_long,avoid_long", [(False, False), (True, False), (False, True), (True, True)])
def test_delay_preflight_matches_encoded_lengths(prefer_long, avoid_long):
    for ticks in (0, 1, 127, 128, 16382, 16383, 16384, 16510, 16511, 32766, 32767):
        assert converter._eseq_delta_size(ticks, prefer_long=prefer_long, avoid_long=avoid_long) == len(
            converter._encode_eseq_delta(ticks, prefer_long=prefer_long, avoid_long=avoid_long)
        )
