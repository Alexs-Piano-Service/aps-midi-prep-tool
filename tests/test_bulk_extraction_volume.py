"""Bulk conversion and resume honor an intentional muted introduction."""

import io
import json
import mido
import pytest

from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from aps_midi_prep_tool_app.bulk_extraction_job import read_extraction_job
from aps_midi_prep_tool_app.disk_session_worker import BulkExtractionWorker
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes, parse_eseq_bytes
from aps_midi_prep_tool_app.floppy_image import FloppyOperationCancelled
from test_bulk_extraction import FakeImageSession


@pytest.fixture
def album(tmp_path):
    midi = mido.MidiFile(type=0, ticks_per_beat=384)
    midi.tracks.append(mido.MidiTrack([
        mido.Message("control_change", control=7, value=0),
        mido.Message("note_on", note=60, velocity=64),
        mido.Message("note_off", note=60, time=96),
        mido.Message("control_change", control=7, value=96),
        mido.Message("note_on", note=64, velocity=64),
        mido.Message("note_off", note=64, time=96),
        mido.Message("control_change", control=7, value=0),
        mido.Message("note_on", note=67, velocity=64),
        mido.Message("note_off", note=67, time=96),
        mido.Message("control_change", control=7, value=100),
    ]))
    stream = io.BytesIO()
    midi.save(file=stream)
    payload = convert_midi_bytes_to_eseq_bytes(stream.getvalue())
    expected = [(tick, raw) for tick, _order, raw in parse_eseq_bytes(payload).events]
    assert expected[0] == (0, b"\xb0\x07\x00")
    source = tmp_path / "images"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic image; content provided by the test session")
    files = {"FIRST.FIL": payload, "SECOND.FIL": payload}
    return source, tmp_path / "output", files, expected


def _events(path):
    tick = 0
    result = []
    for message in mido.MidiFile(path).tracks[0]:
        tick += message.time
        if not message.is_meta:
            result.append((tick, bytes(message.bytes())))
    return result


def _volumes_at_notes(events):
    volume = 100
    result = []
    for _tick, raw in events:
        if raw[:2] == b"\xb0\x07":
            volume = raw[2]
        if raw[0] == 0x90 and raw[2]:
            result.append((raw[1], volume))
    return result


@pytest.mark.parametrize("preserve", [False, True])
def test_bulk_conversion_volume_choice_preserves_event_order_and_timing(album, preserve):
    source, output, files, expected = album
    original_image = (source / "disk.img").read_bytes()
    kwargs = {"preserve_volume_controls": True} if preserve else {}
    result = bulk_extract_images(
        source, output, convert_eseq=True,
        session_loader=lambda path, **_kwargs: FakeImageSession(path, files), **kwargs,
    )
    assert not result.errors and result.files_converted == 2
    for path in (output / "disk").glob("*.mid"):
        events = _events(path)
        assert events == (expected if preserve else expected[1:])
        assert _volumes_at_notes(events) == [(60, 0 if preserve else 100), (64, 96), (67, 0)]
    assert (source / "disk.img").read_bytes() == original_image


def _cancel_after_first(detail):
    if detail["stage"] == "extracting" and detail["file_completed"] == 1:
        raise FloppyOperationCancelled("Cancelled after the verified first song")


def test_resume_preserves_muted_intro_and_rejects_a_changed_volume_policy(album):
    source, output, files, expected = album
    checkpoint = output / "job.json"
    loader = lambda path, **_kwargs: FakeImageSession(path, files)
    options = dict(convert_eseq=True, preserve_volume_controls=True,
                   job_record_path=checkpoint, session_loader=loader)
    with pytest.raises(FloppyOperationCancelled):
        bulk_extract_images(source, output, **options, progress_detail_callback=_cancel_after_first)
    saved = checkpoint.read_bytes()
    record = json.loads(saved)
    assert record["version"] == 2
    assert record["options"]["preserve_volume_controls"] is True
    first = output / "disk" / "FIRST.mid"
    original_first = first.read_bytes()
    assert _events(first) == expected
    with pytest.raises(ValueError, match="different.*options"):
        bulk_extract_images(source, output, **{**options, "preserve_volume_controls": False}, resume=True)
    assert checkpoint.read_bytes() == saved
    assert first.read_bytes() == original_first
    resumed = bulk_extract_images(source, output, **options, resume=True)
    assert resumed.files_reused == 1 and resumed.files_converted == 1
    assert not resumed.errors
    assert first.read_bytes() == original_first
    assert _events(output / "disk" / "SECOND.mid") == expected
    assert not checkpoint.exists()


def test_legacy_job_keeps_its_startup_mute_removal_policy(album):
    source, output, files, expected = album
    checkpoint = output / "legacy-job.json"
    loader = lambda path, **_kwargs: FakeImageSession(path, files)
    with pytest.raises(FloppyOperationCancelled):
        bulk_extract_images(source, output, convert_eseq=True, job_record_path=checkpoint,
                            session_loader=loader, progress_detail_callback=_cancel_after_first)
    legacy = json.loads(checkpoint.read_bytes())
    legacy["version"] = 1
    del legacy["options"]["preserve_volume_controls"]
    checkpoint.write_text(json.dumps(legacy), encoding="utf-8")
    unchanged = checkpoint.read_bytes()
    normalized = read_extraction_job(checkpoint)
    assert normalized["version"] == 2
    assert normalized["options"]["preserve_volume_controls"] is False
    assert checkpoint.read_bytes() == unchanged
    result = bulk_extract_images(source, output, **normalized["options"],
                                 job_record_path=checkpoint, resume=True, session_loader=loader)
    assert not result.errors and result.files_reused == 1
    assert _events(output / "disk" / "FIRST.mid") == expected[1:]
    assert _events(output / "disk" / "SECOND.mid") == expected[1:]


@pytest.mark.parametrize("invalid", [None, 1, "true", "missing"])
def test_saved_volume_policy_must_be_an_explicit_boolean(album, invalid):
    source, output, files, _expected = album
    checkpoint = output / "job.json"
    with pytest.raises(FloppyOperationCancelled):
        bulk_extract_images(source, output, convert_eseq=True, job_record_path=checkpoint,
                            session_loader=lambda path, **_kwargs: FakeImageSession(path, files),
                            progress_detail_callback=_cancel_after_first)
    job = json.loads(checkpoint.read_bytes())
    if invalid == "missing":
        del job["options"]["preserve_volume_controls"]
    else:
        job["options"]["preserve_volume_controls"] = invalid
    checkpoint.write_text(json.dumps(job), encoding="utf-8")
    with pytest.raises(ValueError, match="malformed options"):
        read_extraction_job(checkpoint)


def test_bulk_worker_applies_preservation_to_real_conversion(album, qt_application, monkeypatch):
    from aps_midi_prep_tool_app import bulk_extraction

    source, output, files, expected = album
    monkeypatch.setattr(bulk_extraction.FloppyImageSession, "load",
                        lambda path, **_kwargs: FakeImageSession(path, files))
    worker = BulkExtractionWorker(str(source), str(output), convert_eseq=True,
                                  preserve_volume_controls=True)
    completed, failed = [], []
    worker.extractionFinished.connect(completed.append)
    worker.extractionFailed.connect(failed.append)
    worker.run()
    assert not failed and len(completed) == 1
    assert _events(output / "disk" / "FIRST.mid") == expected
