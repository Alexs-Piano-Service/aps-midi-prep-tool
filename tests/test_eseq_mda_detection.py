"""Filename-assisted MDA detection must preserve the complete performance."""

import io
import os
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app.conversion_review import build_conversion_report
from aps_midi_prep_tool_app.eseq_channel_merger import merge_eseq_channels_to_channel0_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    convert_eseq_bytes_to_midi_bytes,
    convert_eseq_file_to_midi_path,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_inspection import inspect_eseq_header
from aps_midi_prep_tool_app.main_window import BatchAudioRenderWorker
from test_eseq_channel_merging import _eseq, filename_assisted_mda
from test_file_inspection_actions import application, dialog_factory, _item
from test_inspection_staging import window, _open_image, _item as _staged_item


@pytest.fixture
def mda(filename_assisted_mda):
    data = bytearray(filename_assisted_mda)
    data[0x24] = 61  # MDA: 90 BPM.
    data[0x33] = 91  # FIL: 120 BPM, but opaque in this container.
    data[0x34:0x36] = bytes((3, 8))  # Not an MDA meter field.
    return bytes(data)


def _midi_events(data):
    song = mido.MidiFile(file=io.BytesIO(data))
    tick = 0
    events = []
    for message in song.tracks[0]:
        tick += message.time
        events.append((tick, message))
    return events


def _notes(data):
    return [(tick, bytes(message.bytes())) for tick, message in _midi_events(data)
            if message.type in ("note_on", "note_off")]


def _expected_notes(merged=False):
    return [
        (0, bytes((0x90 if merged else 0x95, 60, 100))),
        (32, bytes((0x90 if merged else 0x94, 62, 80))),
        (48, bytes((0x80 if merged else 0x84, 62, 32))),
        (48, bytes((0x80 if merged else 0x85, 60, 64))),
    ]


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("metadata_policy", ["clean", "archival"])
def test_parser_and_converter_preserve_early_events_and_mda_header_meanings(mda, merged, metadata_policy):
    if merged:
        mda, changed = merge_eseq_channels_to_channel0_bytes(mda, filename="SONG.MDA")
        assert changed
    parsed = parse_eseq_bytes(mda, filename="SONG.mDa")
    assert [(tick, raw) for tick, _, raw in parsed.events if raw[0] & 0xF0 in (0x80, 0x90)] == _expected_notes(merged)
    assert parsed.base_bpm == 90
    assert parsed.tempo_events == [(0, 666666)]
    assert parsed.time_signature_events == [(0, 4, 2)]
    assert parsed.title == ""
    assert parsed.end_tick == 48

    converted = convert_eseq_bytes_to_midi_bytes(mda, filename="SONG.MDA", midi_metadata_policy=metadata_policy)
    assert _notes(converted) == _expected_notes(merged)
    events = _midi_events(converted)
    assert [(tick, message.tempo) for tick, message in events if message.type == "set_tempo"] == [(0, 666666)]
    assert [(tick, message.numerator, message.denominator) for tick, message in events
            if message.type == "time_signature"] == [(0, 4, 4)]
    assert [message.name for _, message in events if message.type == "track_name"] == ["Yamaha File"]
    assert events[-1][0] == 48
    if metadata_policy == "archival":
        texts = [message.text for _, message in events if message.type == "text"]
        assert any("before=0 after=0 duration=48" in text and "source_title_blank=1" in text for text in texts)
        assert any(text.endswith("prefix00_stream=" + mda[:0x57].hex()) for text in texts)

    info = inspect_eseq_header(mda, filename="SONG.MDA")
    assert (info.variant, info.stream_offset, info.tempo_offset, info.base_bpm) == ("mda", 0x57, 0x24, 90)
    assert info.header_meter is None
    assert not info.fields  # FIL-only flags must remain opaque.


@pytest.mark.parametrize("extension", ["MDA", "mDa"])
@pytest.mark.parametrize("source_type", [str, os.fsencode, Path])
def test_file_conversion_uses_source_name_and_reports_the_complete_performance(tmp_path, mda, extension, source_type):
    source = tmp_path / f"SONG.{extension}"
    destination = tmp_path / "converted.MID"
    source.write_bytes(mda)
    convert_eseq_file_to_midi_path(source_type(source), destination)
    assert _notes(destination.read_bytes()) == _expected_notes()
    report = build_conversion_report(source, destination)
    assert report.before.notes == report.after.notes == 2
    assert not report.notes_changed
    assert not report.channel_events_changed
    assert source.read_bytes() == mda


def test_staged_file_conversion_accepts_logical_source_name(tmp_path, mda):
    staged = tmp_path / "staged.tmp"
    destination = tmp_path / "converted.MID"
    staged.write_bytes(mda)
    convert_eseq_file_to_midi_path(staged, destination, filename="SONG.MDA")
    assert _notes(destination.read_bytes()) == _expected_notes()


def test_fil_and_byte_only_detection_do_not_gain_mda_filename_fallback(tmp_path, mda):
    source = tmp_path / "SONG.FIL"
    destination = tmp_path / "output.MDA"
    source.write_bytes(mda)
    assert parse_eseq_bytes(mda, filename=source.name) == parse_eseq_bytes(mda)
    convert_eseq_file_to_midi_path(source, destination)
    assert destination.read_bytes() == convert_eseq_bytes_to_midi_bytes(mda)
    assert len(_notes(destination.read_bytes())) == 3  # Earlier note-on is outside the FIL stream.


@pytest.mark.parametrize("variant", ["fil", "q11", "mda"])
def test_unambiguous_containers_keep_their_byte_detected_layout(variant):
    data = _eseq(bytes.fromhex("F1 00 94 3C 64 F3 30 84 3C 00 F2"), variant)
    if variant == "q11":
        data = data[:0x57] + bytes.fromhex("F1 00 F9") + data[0x5A:]
    assert parse_eseq_bytes(data, filename="SONG.MDA") == parse_eseq_bytes(data)
    assert convert_eseq_bytes_to_midi_bytes(data, filename="SONG.MDA") == convert_eseq_bytes_to_midi_bytes(data)


@pytest.mark.parametrize("truncated", [False, True])
def test_short_filename_assisted_mda_converts_or_rejects_incomplete_events(tmp_path, truncated):
    stream = bytes.fromhex("F1 00 F9 00 00 95 3C 64 F3 30 85 3C 00 F2")
    if truncated:
        stream = stream[:-2]
    data = bytearray(_eseq(stream, "mda", suffix=b""))
    data[3:7] = len(data).to_bytes(4, "little")
    data[0x34:0x36] = bytes((3, 8))
    assert len(data) < 0x77
    source = tmp_path / "SHORT.MDA"
    destination = tmp_path / "existing.MID"
    source.write_bytes(data)
    destination.write_bytes(b"existing destination")
    if truncated:
        with pytest.raises(EseqConversionError, match="incomplete E-SEQ event"):
            convert_eseq_file_to_midi_path(source, destination)
        assert destination.read_bytes() == b"existing destination"
    else:
        parsed = parse_eseq_bytes(data, filename=source.name)
        assert parsed.time_signature_events == []
        convert_eseq_file_to_midi_path(source, destination)
        assert _notes(destination.read_bytes()) == [(0, b"\x95\x3C\x64"), (48, b"\x85\x3C\x00")]


@pytest.mark.parametrize("merged", [False, True])
def test_inspection_preview_uses_logical_name_for_staged_mda(dialog_factory, tmp_path, mda, merged):
    if merged:
        mda, _ = merge_eseq_channels_to_channel0_bytes(mda, filename="SONG.MDA")
    staged = tmp_path / "staged.tmp"
    staged.write_bytes(mda)
    item = _item(staged)
    item.update(display_name="SONG.MDA", label="SONG.MDA - Album title")
    dialog = dialog_factory([item])
    assert _notes(dialog.current_midi_bytes) == _expected_notes(merged)
    assert "0x24" in dialog.details_box.toPlainText()
    assert "0x33" not in dialog.details_box.toPlainText()
    assert staged.read_bytes() == mda


def test_audio_render_uses_logical_name_for_staged_mda(application, tmp_path, mda):
    staged = tmp_path / "audio.tmp"
    staged.write_bytes(mda)
    item = _item(staged)
    item["display_name"] = "SONG.MDA"
    worker = BatchAudioRenderWorker([item], "", str(tmp_path), "wav")
    assert _notes(worker._midi_bytes_for_item(item)) == _expected_notes()


@pytest.mark.parametrize("image_mode", [False, True])
@pytest.mark.parametrize("merged", [False, True])
def test_staged_mda_to_midi_preserves_notes_with_or_without_prior_merge(
    window, tmp_path, monkeypatch, mda, image_mode, merged,
):
    source = tmp_path / "SONG.MDA"
    source.write_bytes(mda)
    monkeypatch.setattr(window, "_show_error_list", lambda *a, **k: pytest.fail(str(a)))
    if image_mode:
        _open_image(window, tmp_path, [(source, source.name)])
        source_key = source.name
    else:
        window._load_regular_files([str(source)], "MDA conversion test", prepare_destination=False)
        source_key = str(source)
    item = _staged_item(window, source_key)
    if merged:
        assert window._stage_inspected_midi_action(item, "piano")["changed"]
    if image_mode:
        window._queue_image_format_conversion(item["row"], "midi")
        material = window._pending_or_extracted_image_path(source_key)
    else:
        window._stage_regular_row_conversion(item["row"], source_key, "midi")
        material = window._regular_source_material_path(source_key)
    converted = Path(material).read_bytes()
    assert _notes(converted) == _expected_notes(merged)
    assert source.read_bytes() == mda
