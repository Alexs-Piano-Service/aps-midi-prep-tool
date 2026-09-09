"""Software integration only: these fixtures make no physical-player claims."""

import struct
from pathlib import Path

import pytest

from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from aps_midi_prep_tool_app.conversion_review import compare_music_bytes
from aps_midi_prep_tool_app.emulator_image_builder import build_emulator_disk_images
from aps_midi_prep_tool_app.floppy_image import DISK_FORMATS, FloppyImageSession
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq, _parse_track_events


def _musical_fixture():
    events = [
        (0, b"\xff\x03\x12Preservation Study"),
        (0, b"\xff\x51\x03\x07\xa1\x20"),
        (0, b"\xff\x58\x04\x04\x02\x18\x08"),
        (0, b"\xb0\x07\x00"),
        (0, b"\x90\x3c\x50"),
        (120, b"\xb0\x40\x24"),
        (240, b"\xb0\x40\x58"),
        (240, b"\x91\x40\x60"),
        (360, b"\xb0\x42\x7f"),
        (480, b"\xff\x51\x03\x0a\x2c\x2a"),  # 90 BPM (rounded MIDI microseconds)
        (480, b"\x80\x3c\x00"),
        (720, b"\x81\x40\x00"),
        (720, b"\xb0\x40\x00"),
        (840, b"\xb0\x42\x00"),
        (960, b"\xff\x2f\x00"),
    ]
    track = bytearray()
    previous = 0
    for tick, raw in events:
        track.extend(_encode_vlq(tick - previous))
        track.extend(raw)
        previous = tick
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 480) + b"MTrk" + len(track).to_bytes(4, "big") + track


def test_midi_to_eseq_image_extraction_roundtrip_preserves_values_and_legacy_timing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = bytes(_musical_fixture())
    source_path = source / "Study.mid"
    source_path.write_bytes(original)
    image_directory = tmp_path / "images"

    prepared = build_emulator_disk_images(
        source, image_directory, output_content="eseq", output_ext="img",
        disk_format=next(item for item in DISK_FORMATS if item.key == "ibm.720"),
    )

    assert prepared.contents_verified
    assert prepared.converted_files == 1
    assert prepared.images_created == 1
    session = FloppyImageSession.load(prepared.output_paths[0])
    try:
        entries = session.list_entries().entries
        assert "PIANODIR.FIL" in {entry.path for entry in entries}
        assert len([entry for entry in entries if entry.path != "PIANODIR.FIL"]) == 1
        song_entry = next(entry for entry in entries if entry.path != "PIANODIR.FIL")
        prepared_song = Path(session.extract_file(song_entry.path)).read_bytes()
    finally:
        session.cleanup()

    output = tmp_path / "roundtrip"
    extracted = bulk_extract_images(
        image_directory, output, convert_eseq=True, job_record_path=output / "job.json",
    )
    assert extracted.errors == ()
    assert extracted.files_converted == 1
    midi_paths = list(output.rglob("*.mid"))
    assert len(midi_paths) == 1
    restored = midi_paths[0].read_bytes()
    report = compare_music_bytes(original, restored, tolerance_seconds=0.005)

    assert report.before.notes == report.after.notes == 2
    assert report.before.channels == (1, 2)
    assert report.after.channels == (1, 2, 3)
    assert report.before.pedals == {64: 3, 66: 2}
    assert report.after.pedals == {64: 5, 66: 2}
    assert report.before.zero_volume_events == report.after.zero_volume_events == 1
    assert report.before.duration_seconds == pytest.approx(1.166666, abs=0.00001)
    # Independent expected clock positions: legacy onset preparation adds
    # one second here, later tempo changes are integrated, and the last pedal
    # release at tick 1497 is followed by the fixed 1498-tick trailer.
    events, end_tick = _parse_track_events(restored[22:])
    assert [(tick, raw) for tick, _order, raw in events if 0x80 <= raw[0] < 0xF0] == [
        (0, b"\xb0\x07\x00"), (748, b"\x90\x3c\x50"),
        (842, b"\xb2\x40\x24"), (936, b"\xb0\x40\x7f"), (936, b"\xb2\x40\x58"),
        (936, b"\x91\x40\x60"), (1029, b"\xb0\x42\x7f"),
        (1123, b"\x80\x3c\x00"), (1372, b"\x81\x40\x00"),
        (1372, b"\xb0\x40\x00"), (1372, b"\xb2\x40\x00"), (1497, b"\xb0\x42\x00"),
    ]
    assert end_tick == 2995
    assert report.after.duration_seconds == pytest.approx(2995 * 512820 / 384_000_000)
    assert report.after.titles == ("Preservation Study",)
    assert report.notes_changed and report.pedals_changed  # intentional legacy timing
    assert report.channel_payload_changed is True
    preparation_report = compare_music_bytes(original, prepared_song)
    assert preparation_report.pedal_channels_routed == (64,)
    assert preparation_report.other_channel_payload_changed is False
    assert preparation_report.yamaha_pedal_controllers == (64,)
    assert preparation_report.pedal_binary_events_added == 2
    assert preparation_report.expected_channel_events_changed is False
    assert source_path.read_bytes() == original

    resumed = bulk_extract_images(
        image_directory, output, convert_eseq=True, job_record_path=output / "job.json", resume=True,
    )
    assert resumed.errors == ()
    assert resumed.images_skipped == 1
    assert resumed.files_reused == 1
    assert [Path(path) for path in resumed.output_directories] == [midi_paths[0].parent]
