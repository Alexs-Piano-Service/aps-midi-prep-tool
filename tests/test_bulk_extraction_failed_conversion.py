"""Failed conversions retain requested sources and remain retryable jobs."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from aps_midi_prep_tool_app.bulk_extraction_job import read_extraction_job
from aps_midi_prep_tool_app.floppy_image import FloppyOperationCancelled
from test_bulk_extraction import FakeImageSession, _minimal_midi_bytes


def _album(tmp_path, extension):
    source, output = tmp_path / "images", tmp_path / "output"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic image provided by the test session")
    header = bytearray(0x57 if extension == "MDA" else 0x77)
    header[0] = 0xFE
    header[7:15] = b"COM-ESEQ"
    payload = bytes(header) + b"\xF1\x00\xFB\x01"  # Incomplete relative-tempo command.
    name = f"MUSIC/SONG.{extension}"
    files = {name: payload}
    loader = lambda path, **_kwargs: FakeImageSession(path, files)
    return source, output, name, payload, loader


@pytest.mark.parametrize("extension", ["FIL", "MDA"])
@pytest.mark.parametrize("include_sources", [False, True])
def test_failed_conversion_preserves_requested_source_and_cleans_partial_midi(
    tmp_path, extension, include_sources,
):
    source, output, name, payload, loader = _album(tmp_path, extension)
    staged_paths = []

    def converter(_source, destination, **_options):
        staged_paths.append(Path(destination))
        Path(destination).write_bytes(b"MThd partial conversion")
        raise ValueError("damaged event stream")

    result = bulk_extract_images(
        source, output, convert_eseq=True, include_eseq_sources=include_sources,
        session_loader=loader, eseq_converter=converter,
    )

    music = output / "disk" / "MUSIC"
    assert result.files_converted == 0
    assert result.files_extracted == int(include_sources)
    assert result.errors == (f"disk.img / {name} (E-SEQ conversion): damaged event stream",)
    assert not any(path.exists() for path in staged_paths)
    assert not list(music.glob(".aps_convert_*"))
    assert not list(music.glob("*.mid"))
    if include_sources:
        assert (output / "disk" / name).read_bytes() == payload
    else:
        assert not (output / "disk" / name).exists()


@pytest.mark.parametrize("extension", ["FIL", "MDA"])
@pytest.mark.parametrize("modify_retained_source", [False, True])
def test_saved_job_retries_conversion_and_reuses_only_verified_source_outputs(
    tmp_path, extension, modify_retained_source,
):
    source, output, name, payload, loader = _album(tmp_path, extension)
    checkpoint = output / "job.json"
    calls = []
    fail = True

    def converter(source_path, destination, **_options):
        calls.append(Path(source_path).read_bytes())
        Path(destination).write_bytes(b"partial" if fail else _minimal_midi_bytes())
        if fail:
            raise ValueError("damaged event stream")

    options = dict(
        convert_eseq=True, include_eseq_sources=True, job_record_path=checkpoint,
        session_loader=loader, eseq_converter=converter,
    )
    first = bulk_extract_images(source, output, **options)
    retained = output / "disk" / name
    assert retained.read_bytes() == payload
    assert first.files_extracted == 1 and first.files_converted == 0
    record = read_extraction_job(checkpoint)["images"]["disk.img"]
    assert record["state"] == record["entries"][name]["state"] == "failed"
    assert len(record["entries"][name]["outputs"]) == 1
    assert record["entries"][name]["outputs"][0]["converted"] is False
    if modify_retained_source:
        retained.write_bytes(b"user edited source")

    retried = bulk_extract_images(source, output, **options, resume=True)
    assert len(calls) == 2
    assert retried.errors and retried.files_converted == 0
    assert retried.files_extracted == int(modify_retained_source)
    assert retried.files_reused == int(not modify_retained_source)
    record = read_extraction_job(checkpoint)["images"]["disk.img"]
    assert record["state"] == record["entries"][name]["state"] == "failed"
    sources = list(retained.parent.glob(f"*.{extension}"))
    assert len(sources) == 1 + int(modify_retained_source)

    fail = False
    resumed = bulk_extract_images(source, output, **options, resume=True)

    assert calls == [payload, payload, payload]
    assert resumed.files_converted == 1 and resumed.files_extracted == 0
    assert resumed.files_reused == 1 and not resumed.errors
    assert (retained.parent / "SONG.mid").read_bytes() == _minimal_midi_bytes()
    assert len(list(retained.parent.glob(f"*.{extension}"))) == len(sources)
    assert not checkpoint.exists()
    assert resumed.job_record_path == ""
    if modify_retained_source:
        assert retained.read_bytes() == b"user edited source"
        assert (retained.parent / f"SONG_2.{extension}").read_bytes() == payload
    else:
        assert retained.read_bytes() == payload


def test_existing_midi_survives_failed_conversion_with_source_inclusion(tmp_path):
    source, output, name, payload, _loader = _album(tmp_path, "FIL")
    files = {name: payload, "MUSIC/SONG.MID": _minimal_midi_bytes()}

    def converter(_source, destination, **_options):
        Path(destination).write_bytes(b"partial")
        raise ValueError("damaged event stream")

    result = bulk_extract_images(
        source, output, convert_eseq=True, include_eseq_sources=True,
        session_loader=lambda path, **_kwargs: FakeImageSession(path, files),
        eseq_converter=converter,
    )

    assert result.files_extracted == 2 and result.files_converted == 0
    assert len(result.errors) == 1
    assert (output / "disk" / name).read_bytes() == payload
    assert (output / "disk" / "MUSIC" / "SONG.MID").read_bytes() == _minimal_midi_bytes()
    assert not (output / "disk" / "MUSIC" / "SONG_converted.mid").exists()


@pytest.mark.parametrize("signal", ["exception", "callback"])
def test_conversion_cancellation_cleans_staging_and_stops_before_copying_source(tmp_path, signal):
    source, output, name, _payload, loader = _album(tmp_path, "FIL")
    staged_paths = []
    cancelled = False

    def converter(_source, destination, **_options):
        nonlocal cancelled
        staged_paths.append(Path(destination))
        Path(destination).write_bytes(b"partial")
        if signal == "exception":
            raise FloppyOperationCancelled("cancelled conversion")
        cancelled = True
        raise ValueError("damaged event stream")

    with pytest.raises(FloppyOperationCancelled, match="cancelled"):
        bulk_extract_images(
            source, output, convert_eseq=True, include_eseq_sources=True,
            session_loader=loader, eseq_converter=converter,
            cancel_callback=lambda: cancelled,
        )
    assert not any(path.exists() for path in staged_paths)
    assert not (output / "disk" / name).exists()


@pytest.mark.parametrize("interruption", ["after_midi", "after_source", "source_error"])
def test_resume_reuses_midi_published_before_source_preservation_finishes(
    tmp_path, monkeypatch, interruption,
):
    from aps_midi_prep_tool_app import bulk_extraction

    source, output, name, payload, loader = _album(tmp_path, "FIL")
    checkpoint = output / "job.json"
    calls = []
    cancelled = False
    write_bytes = bulk_extraction.atomic_write_bytes

    def converter(_source, destination, **_options):
        calls.append(destination)
        Path(destination).write_bytes(_minimal_midi_bytes())

    def interrupted_write(path, data, **kwargs):
        nonlocal cancelled
        is_source = Path(path).suffix == ".FIL"
        if interruption == "source_error" and is_source:
            raise OSError("source preservation failed")
        write_bytes(path, data, **kwargs)
        if interruption == ("after_source" if is_source else "after_midi"):
            cancelled = True

    options = dict(
        convert_eseq=True, include_eseq_sources=True, job_record_path=checkpoint,
        session_loader=loader, eseq_converter=converter,
    )
    monkeypatch.setattr(bulk_extraction, "atomic_write_bytes", interrupted_write)
    if interruption == "source_error":
        first = bulk_extract_images(source, output, **options)
        assert any("source preservation failed" in error for error in first.errors)
    else:
        with pytest.raises(FloppyOperationCancelled):
            bulk_extract_images(source, output, **options, cancel_callback=lambda: cancelled)

    music = output / "disk" / "MUSIC"
    midi = music / "SONG.mid"
    assert midi.read_bytes() == _minimal_midi_bytes()
    entry = read_extraction_job(checkpoint)["images"]["disk.img"]["entries"][name]
    assert entry["state"] == "failed"
    assert any(record["converted"] and Path(record["path"]) == midi.relative_to(output)
               for record in entry["outputs"])

    if interruption == "source_error":
        # Repeated failures must also retain ownership of the first MIDI.
        retry = bulk_extract_images(source, output, **options, resume=True)
        assert retry.files_converted == 0 and retry.files_reused == 1
        assert len(calls) == 1

    monkeypatch.setattr(bulk_extraction, "atomic_write_bytes", write_bytes)
    resumed = bulk_extract_images(source, output, **options, resume=True)
    assert not resumed.errors
    assert resumed.files_converted == 0
    assert resumed.files_reused == (2 if interruption == "after_source" else 1)
    assert len(calls) == 1
    assert list(music.glob("*.mid")) == [midi]
    assert (output / "disk" / name).read_bytes() == payload
    assert not checkpoint.exists()


def test_resume_preserves_modified_midi_from_an_incomplete_entry(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import bulk_extraction

    source, output, name, payload, loader = _album(tmp_path, "FIL")
    checkpoint = output / "job.json"
    write_bytes = bulk_extraction.atomic_write_bytes

    def fail_source_copy(path, data, **kwargs):
        if Path(path).suffix == ".FIL":
            raise OSError("source preservation failed")
        write_bytes(path, data, **kwargs)

    options = dict(
        convert_eseq=True, include_eseq_sources=True, job_record_path=checkpoint,
        session_loader=loader,
        eseq_converter=lambda _source, dest, **_kwargs: Path(dest).write_bytes(_minimal_midi_bytes()),
    )
    monkeypatch.setattr(bulk_extraction, "atomic_write_bytes", fail_source_copy)
    assert bulk_extract_images(source, output, **options).errors
    midi = output / "disk" / "MUSIC" / "SONG.mid"
    midi.write_bytes(b"user edited MIDI")

    monkeypatch.setattr(bulk_extraction, "atomic_write_bytes", write_bytes)
    resumed = bulk_extract_images(source, output, **options, resume=True)
    assert not resumed.errors
    assert resumed.files_converted == 1 and resumed.files_reused == 0
    assert midi.read_bytes() == b"user edited MIDI"
    assert midi.with_name("SONG_converted.mid").read_bytes() == _minimal_midi_bytes()
    assert (output / "disk" / name).read_bytes() == payload
    assert not checkpoint.exists()
