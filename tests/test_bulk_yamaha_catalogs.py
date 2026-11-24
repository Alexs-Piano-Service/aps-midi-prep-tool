"""Compatible Yamaha extraction catalogs match actual MIDI names and metadata."""

import io
import json
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import bulk_extraction_catalogs as catalogs
from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from aps_midi_prep_tool_app.bulk_extraction_job import read_extraction_job
from aps_midi_prep_tool_app.dos83_renamer import is_dos83_filename
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import (
    _assemble_pianodir_records, build_dos83_name_bytes, build_pianodir_metadata_bytes,
)
from aps_midi_prep_tool_app.floppy_image import FloppyOperationCancelled
from aps_midi_prep_tool_app.midi_metadata import read_first_title_from_midi
from aps_midi_prep_tool_app.smart_pianosoft import (
    build_smart_pianosoft_song_catalog, build_smart_pianosoft_song_record,
    parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog,
    update_smart_pianosoft_disk_title,
)
from test_bulk_extraction import FakeImageSession


def _midi(title="Original MIDI title", *, midi_format=0):
    midi = mido.MidiFile(type=midi_format)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.Message("note_on", note=60, velocity=64),
        mido.Message("note_off", note=60, time=480),
    ]))
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _pianodir(songs, title="Catalog album"):
    records = []
    for filename, song_title in songs:
        record = bytearray(0x50)
        record[:11] = build_dos83_name_bytes(filename)
        record[0x30:0x50] = song_title.encode("cp1252").ljust(32, b" ")
        records.append(bytes(record))
    return _assemble_pianodir_records(records, build_pianodir_metadata_bytes(disk_title=title))


def _run(tmp_path, files, **options):
    source = tmp_path / "images"
    source.mkdir(exist_ok=True)
    image = source / "disk.img"
    if not image.exists():
        image.write_bytes(b"Original synthetic floppy image")
    return bulk_extract_images(
        source, tmp_path / "output",
        session_loader=lambda path, **_kwargs: FakeImageSession(path, files),
        **{"generate_yamaha_catalogs": True, **options},
    )


def test_eseq_catalog_converts_titles_order_and_disk_title_with_actual_dos_names(tmp_path):
    # Physical image order differs from PIANODIR order, and a preexisting MIDI
    # name collides with the natural converted name.
    eseq = convert_midi_bytes_to_eseq_bytes(_midi(), title_override="Header title")
    files = {
        "SECOND.FIL": eseq,
        "FIRST.FIL": eseq,
        "FIRST.MID": _midi("Existing MIDI"),
        "PIANODIR.FIL": _pianodir([("FIRST.FIL", "Catalog first"), ("SECOND.FIL", "Catalog second")]),
    }
    before = dict(files)
    result = _run(tmp_path, files, convert_eseq=True, long_midi_filenames=True)
    assert not result.errors
    folder = Path(result.output_directories[0])
    songs = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
    assert [song.title for song in songs] == ["Catalog first", "Catalog second", "Existing MIDI"]
    assert len({song.filename.casefold() for song in songs}) == 3
    assert all(is_dos83_filename(song.filename) for song in songs)
    for song in songs:
        assert (folder / song.filename).is_file()
        assert read_first_title_from_midi(folder / song.filename) == song.title
    assert parse_smart_pianosoft_disk_title((folder / "PDISK.MNG").read_bytes()) == "Catalog album"
    assert not (folder / "PIANODIR.FIL").exists()
    assert files == before
    assert (tmp_path / "images/disk.img").read_bytes() == b"Original synthetic floppy image"


@pytest.mark.parametrize("keep_originals", [False, True])
def test_existing_mng_opaque_fields_and_optional_original_catalog_copies(tmp_path, keep_originals):
    record = bytearray(build_smart_pianosoft_song_record("FIRST.MID", "Catalog song", midi_format=0))
    record[0x90:0x96] = b"OPAQUE"
    psong = build_smart_pianosoft_song_catalog(b"", [bytes(record)]) + (b" " * 14 + b"\r\n") * 470
    pdisk = update_smart_pianosoft_disk_title(b"", "Original disk") + b"Original opaque tail"
    files = {"PSONG.MNG": psong, "PDISK.MNG": pdisk, "FIRST.MID": _midi(midi_format=1)}
    options = {"keep_original_catalogs": True} if keep_originals else {}
    result = _run(tmp_path, files, long_midi_filenames=True, **options)
    assert not result.errors
    folder = Path(result.output_directories[0])
    song, = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
    assert song.title == "Catalog song"
    assert song.raw_record[0x90:0x96] == b"OPAQUE"
    assert b"SMF1" in song.raw_record
    assert (folder / "PDISK.MNG").read_bytes() == pdisk
    assert len((folder / "PSONG.MNG").read_bytes()) == 128 + 176
    if keep_originals:
        assert (folder / "Original catalogs/PSONG.MNG").read_bytes() == psong
        assert (folder / "Original catalogs/PDISK.MNG").read_bytes() == pdisk
    else:
        assert not (folder / "Original catalogs").exists()
    assert (folder / song.filename).read_bytes() == files["FIRST.MID"]


@pytest.mark.parametrize("midi_album", ["Updated MIDI album", ""])
def test_mixed_catalogs_prefer_mng_titles_and_order_with_pianodir_fallback(tmp_path, midi_album):
    record = bytearray(build_smart_pianosoft_song_record("SECOND.MID", "Updated second"))
    record[0x90:0x96] = b"OPAQUE"
    psong = build_smart_pianosoft_song_catalog(b"", [
        bytes(record), build_smart_pianosoft_song_record("FIRST.MID", "Updated first"),
    ])
    pdisk = update_smart_pianosoft_disk_title(b"", midi_album)
    files = {
        "FIRST.MID": _midi("First embedded title"),
        "THIRD.FIL": convert_midi_bytes_to_eseq_bytes(_midi("Third embedded title")),
        "SECOND.MID": _midi("Second embedded title"),
        "PSONG.MNG": psong,
        "PDISK.MNG": pdisk,
        "PIANODIR.FIL": _pianodir([
            ("FIRST.MID", "Old first"), ("THIRD.FIL", "Converted third"),
            ("SECOND.MID", "Old second"),
        ], title="Fallback ESEQ album"),
    }

    result = _run(tmp_path, files, convert_eseq=True)

    assert not result.errors
    folder = Path(result.output_directories[0])
    songs = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
    assert [(song.filename, song.title) for song in songs] == [
        ("SECOND.MID", "Updated second"), ("FIRST.MID", "Updated first"),
        ("THIRD.MID", "Converted third"),
    ]
    assert songs[0].raw_record[0x90:0x96] == b"OPAQUE"
    assert all((folder / song.filename).is_file() for song in songs)
    assert read_first_title_from_midi(folder / "THIRD.MID") == "Converted third"
    assert parse_smart_pianosoft_disk_title((folder / "PDISK.MNG").read_bytes()) == (
        midi_album or "Fallback ESEQ album"
    )
    for name in ("FIRST.MID", "SECOND.MID"):
        assert (folder / name).read_bytes() == files[name]
    assert not (folder / "Original catalogs").exists()


def test_subdirectories_receive_catalogs_and_long_midi_names_become_dos_names(tmp_path):
    files = {"A/Long piano filename.mid": _midi("First folder"),
             "B/Long piano filename.mid": _midi("Second folder")}
    result = _run(tmp_path, files)
    assert not result.errors
    root = Path(result.output_directories[0])
    assert not (root / "PSONG.MNG").exists()
    for name, title in (("A", "First folder"), ("B", "Second folder")):
        folder = root / name
        song, = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
        assert is_dos83_filename(song.filename)
        assert song.title == title and (folder / song.filename).is_file()
        assert parse_smart_pianosoft_disk_title((folder / "PDISK.MNG").read_bytes()) == "disk"


@pytest.mark.parametrize("alter_catalog", [False, True])
@pytest.mark.parametrize("preserve_only", [False, True])
@pytest.mark.parametrize("keep_originals", [False, True])
def test_interrupted_catalog_pair_reuses_verified_song_and_never_overwrites_changes(
    tmp_path, monkeypatch, alter_catalog, preserve_only, keep_originals,
):
    files = {"FIRST.MID": _midi()}
    if preserve_only:
        files["PDISK.MNG"] = update_smart_pianosoft_disk_title(b"", "Original album")
        files["PSONG.MNG"] = build_smart_pianosoft_song_catalog(b"", [
            build_smart_pianosoft_song_record("FIRST.MID", "Original song"),
        ])
    policy = dict(generate_yamaha_catalogs=not preserve_only, preserve_yamaha_catalogs=preserve_only,
                  keep_original_catalogs=keep_originals)
    checkpoint = tmp_path / "output/job.json"
    original_publish = catalogs.atomic_write_bytes

    def interrupt(destination, payload, **options):
        if Path(destination).name == "PDISK.MNG":
            raise FloppyOperationCancelled("Stop between catalogs")
        return original_publish(destination, payload, **options)

    monkeypatch.setattr(catalogs, "atomic_write_bytes", interrupt)
    with pytest.raises(FloppyOperationCancelled):
        _run(tmp_path, files, job_record_path=checkpoint, **policy)
    job = read_extraction_job(checkpoint)
    assert job["options"]["generate_yamaha_catalogs"] is not preserve_only
    assert job["options"]["preserve_yamaha_catalogs"] is preserve_only
    assert job["options"]["keep_original_catalogs"] is keep_originals
    assert len(job["images"]["disk.img"]["catalogs"]) == 1
    folder = tmp_path / "output" / job["images"]["disk.img"]["output_directory"]
    original_midi = (folder / "FIRST.MID").read_bytes()
    if alter_catalog:
        (folder / "PSONG.MNG").write_bytes(b"Another writer's catalog")
    monkeypatch.setattr(catalogs, "atomic_write_bytes", original_publish)
    result = _run(tmp_path, files, job_record_path=checkpoint, resume=True, **policy)
    assert result.files_reused == (len(files) if keep_originals else 1)
    assert (folder / "Original catalogs").exists() is (keep_originals and preserve_only)
    assert (folder / "FIRST.MID").read_bytes() == original_midi
    if alter_catalog:
        assert result.errors
        assert (folder / "PSONG.MNG").read_bytes() == b"Another writer's catalog"
        assert checkpoint.is_file()
    else:
        assert not result.errors
        assert (folder / "PDISK.MNG").is_file()
        assert not checkpoint.exists()
        assert len(list(folder.glob("*.MID"))) == 1


def test_catalog_option_is_recorded_and_cannot_change_on_resume(tmp_path):
    files = {"FIRST.MID": _midi(), "SECOND.MID": _midi()}
    checkpoint = tmp_path / "output/job.json"

    def cancel_after_first(detail):
        if detail["stage"] == "extracting" and detail["file_completed"] == 1:
            raise FloppyOperationCancelled("Pause extraction")

    with pytest.raises(FloppyOperationCancelled):
        _run(tmp_path, files, job_record_path=checkpoint, progress_detail_callback=cancel_after_first)
    original = checkpoint.read_bytes()
    with pytest.raises(ValueError, match="different.*options"):
        bulk_extract_images(tmp_path / "images", tmp_path / "output", job_record_path=checkpoint, resume=True)
    assert checkpoint.read_bytes() == original
    for value in ("yes", None, 1):
        malformed = json.loads(original)
        malformed["options"]["generate_yamaha_catalogs"] = value
        checkpoint.write_text(json.dumps(malformed))
        with pytest.raises(ValueError, match="malformed options"):
            read_extraction_job(checkpoint)


def test_failed_eseq_conversion_defers_catalogs_until_retry_succeeds(tmp_path):
    files = {"FIRST.MID": _midi(), "SECOND.FIL": convert_midi_bytes_to_eseq_bytes(_midi("Second song"))}
    checkpoint = tmp_path / "output/job.json"

    def fail(*_args, **_kwargs):
        raise ValueError("Conversion interrupted")

    first = _run(tmp_path, files, convert_eseq=True, eseq_converter=fail, job_record_path=checkpoint)
    folder = Path(first.output_directories[0])
    assert first.errors
    assert not (folder / "PSONG.MNG").exists()
    second = _run(tmp_path, files, convert_eseq=True, job_record_path=checkpoint, resume=True)
    assert not second.errors
    assert second.files_reused == 1 and second.files_converted == 1
    songs = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
    assert len(songs) == 2 and all((folder / song.filename).is_file() for song in songs)


@pytest.mark.parametrize("version, generate", [(2, False), (3, False), (3, True), (4, False), (4, True)])
def test_older_jobs_keep_their_catalog_policy_without_preservation(tmp_path, version, generate):
    files = {"FIRST.MID": _midi(), "SECOND.MID": _midi()}
    if generate:
        files["PDISK.MNG"] = update_smart_pianosoft_disk_title(b"", "Original disk")
    checkpoint = tmp_path / "output/job.json"

    def cancel_after_first(detail):
        if detail["stage"] == "extracting" and detail["file_completed"] == 1:
            raise FloppyOperationCancelled("Pause extraction")

    source = tmp_path / "images"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic image")
    loader = lambda path, **_kwargs: FakeImageSession(path, files)
    with pytest.raises(FloppyOperationCancelled):
        bulk_extract_images(source, tmp_path / "output", session_loader=loader,
                            job_record_path=checkpoint, progress_detail_callback=cancel_after_first,
                            generate_yamaha_catalogs=generate, keep_original_catalogs=True,
                            use_album_names=False)
    legacy = json.loads(checkpoint.read_bytes())
    legacy["version"] = version
    if version == 2:
        del legacy["options"]["generate_yamaha_catalogs"]
    if version < 4:
        del legacy["options"]["preserve_yamaha_catalogs"]
    del legacy["options"]["keep_original_catalogs"]
    checkpoint.write_text(json.dumps(legacy))
    unchanged = checkpoint.read_bytes()
    normalized = read_extraction_job(checkpoint)
    assert normalized["version"] == 5
    assert normalized["options"]["generate_yamaha_catalogs"] is generate
    assert normalized["options"]["preserve_yamaha_catalogs"] is False
    assert normalized["options"]["keep_original_catalogs"] is True
    assert checkpoint.read_bytes() == unchanged
    result = bulk_extract_images(source, tmp_path / "output", session_loader=loader,
                                 job_record_path=checkpoint, resume=True, **normalized["options"])
    assert not result.errors
    assert (tmp_path / "output/disk/PSONG.MNG").exists() is generate
    if generate:
        assert (tmp_path / "output/disk/Original catalogs/PDISK.MNG").read_bytes() == files["PDISK.MNG"]


@pytest.mark.parametrize("preserve_only", [False, True])
@pytest.mark.parametrize("keep_originals", [False, True])
def test_bulk_worker_forwards_catalog_policy_to_real_extraction(
    tmp_path, monkeypatch, qt_application, preserve_only, keep_originals,
):
    from aps_midi_prep_tool_app import bulk_extraction
    from aps_midi_prep_tool_app.disk_session_worker import BulkExtractionWorker

    source = tmp_path / "images"
    source.mkdir()
    (source / "disk.img").write_bytes(b"Synthetic image")
    files = {"CON.MID": _midi("Reserved device name"),
             "PDISK.MNG": update_smart_pianosoft_disk_title(b"", "Original album")}
    monkeypatch.setattr(bulk_extraction.FloppyImageSession, "load",
                        lambda path, **_kwargs: FakeImageSession(path, files))
    worker = BulkExtractionWorker(source, tmp_path / "output", generate_yamaha_catalogs=not preserve_only,
                                  preserve_yamaha_catalogs=preserve_only, keep_original_catalogs=keep_originals)
    results, failures = [], []
    worker.extractionFinished.connect(results.append)
    worker.extractionFailed.connect(failures.append)
    worker.run()
    assert len(results) == 1 and not failures
    assert not results[0].errors
    folder = Path(results[0].output_directories[0])
    song_path, = folder.glob("*.MID")
    assert song_path.name != "CON.MID"
    assert (folder / "PDISK.MNG").is_file()
    assert (folder / "Original catalogs/PDISK.MNG").is_file() is keep_originals
    if preserve_only:
        assert not (folder / "PSONG.MNG").exists()
    else:
        song, = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
        assert song.filename == song_path.name


def test_completed_checkpoint_cannot_claim_missing_catalog_pair(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.bulk_extraction_job import ExtractionJob

    files = {"FIRST.MID": _midi()}
    checkpoint = tmp_path / "output/job.json"
    monkeypatch.setattr(ExtractionJob, "remove_completed", lambda _self: False)
    result = _run(tmp_path, files, job_record_path=checkpoint)
    assert not result.errors
    saved = json.loads(checkpoint.read_bytes())
    saved["images"]["disk.img"]["catalogs"].pop()
    checkpoint.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="missing completed image entries"):
        read_extraction_job(checkpoint)


@pytest.mark.parametrize("eseq", [False, True])
@pytest.mark.parametrize("long_names", [False, True])
def test_preserve_only_without_metadata_keeps_normal_names_and_creates_no_catalogs(
    tmp_path, monkeypatch, eseq, long_names,
):
    from aps_midi_prep_tool_app.bulk_extraction_job import ExtractionJob

    source_name = "SONG.FIL" if eseq else "Long original filename.mid"
    song = _midi("Descriptive song title")
    files = {source_name: convert_midi_bytes_to_eseq_bytes(song) if eseq else song}
    checkpoint = tmp_path / "output/job.json"
    monkeypatch.setattr(ExtractionJob, "remove_completed", lambda _self: False)
    options = dict(generate_yamaha_catalogs=False, preserve_yamaha_catalogs=True,
                   convert_eseq=eseq, long_midi_filenames=long_names, job_record_path=checkpoint)

    result = _run(tmp_path, files, **options)

    assert not result.errors
    folder = Path(result.output_directories[0])
    outputs = list(folder.iterdir())
    assert len(outputs) == 1 and outputs[0].suffix.lower() == ".mid"
    if long_names:
        assert "Descriptive song title" in outputs[0].name
        assert not is_dos83_filename(outputs[0].name)
    else:
        assert outputs[0].name == ("SONG.mid" if eseq else source_name)
    if not eseq:
        assert outputs[0].read_bytes() == song
    job = read_extraction_job(checkpoint)
    assert job["images"]["disk.img"]["catalog_sources"] == {}
    assert job["images"]["disk.img"]["catalogs_complete"]
    repeated = _run(tmp_path, files, **options, resume=True)
    assert not repeated.errors and repeated.images_skipped == 1
    assert list(folder.iterdir()) == outputs


@pytest.mark.parametrize("source_catalog", ["psong", "pdisk", "pianodir", "empty-pianodir"])
@pytest.mark.parametrize("generate", [False, True])
def test_catalog_policy_preserves_exact_names_or_completes_the_pair(
    tmp_path, monkeypatch, source_catalog, generate,
):
    from aps_midi_prep_tool_app.bulk_extraction_job import ExtractionJob

    monkeypatch.setattr(ExtractionJob, "remove_completed", lambda _self: False)
    eseq = source_catalog in {"pianodir", "empty-pianodir"}
    name = "SONG.FIL" if eseq else "SONG.MID"
    song = _midi("Embedded title")
    files = {name: convert_midi_bytes_to_eseq_bytes(song) if eseq else song}
    expected_title = "Catalog title" if source_catalog in {"psong", "pianodir"} else "Embedded title"
    if source_catalog == "psong":
        files["PSONG.MNG"] = build_smart_pianosoft_song_catalog(b"", [
            build_smart_pianosoft_song_record(name, expected_title),
        ])
    elif source_catalog == "pdisk":
        files["PDISK.MNG"] = update_smart_pianosoft_disk_title(b"", "Original album")
    else:
        files["PIANODIR.FIL"] = _pianodir(
            [] if source_catalog == "empty-pianodir" else [(name, expected_title)], title="Original album",
        )

    checkpoint = tmp_path / "output/job.json"
    options = dict(generate_yamaha_catalogs=generate, preserve_yamaha_catalogs=True,
                   convert_eseq=eseq, long_midi_filenames=True, job_record_path=checkpoint)
    result = _run(tmp_path, files, **options)

    assert not result.errors
    folder = Path(result.output_directories[0])
    expected_names = {"PSONG.MNG", "PDISK.MNG"} if generate or eseq else {source_catalog.upper() + ".MNG"}
    assert {path.name for path in folder.glob("*.MNG")} == expected_names
    if "PSONG.MNG" in expected_names:
        saved, = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
        assert (saved.filename, saved.title) == ("SONG.MID", expected_title)
    assert (folder / "SONG.MID").is_file()
    if "PDISK.MNG" in expected_names:
        assert parse_smart_pianosoft_disk_title((folder / "PDISK.MNG").read_bytes()) == (
            "disk" if source_catalog == "psong" else "Original album"
        )
    if not eseq:
        assert (folder / "SONG.MID").read_bytes() == song
    assert not (folder / "Original catalogs").exists()
    job = read_extraction_job(checkpoint)
    assert {Path(output["path"]).name for output in job["images"]["disk.img"]["catalogs"]} == expected_names
    resumed = _run(tmp_path, files, **options, resume=True)
    assert not resumed.errors and resumed.images_skipped == 1


@pytest.mark.parametrize("catalog_name", ["PSONG.MNG", "PDISK.MNG", "PIANODIR.FIL"])
def test_invalid_metadata_does_not_enable_preserve_only_catalog_generation(tmp_path, catalog_name):
    files = {"Long original filename.mid": _midi(), catalog_name: b"Invalid source metadata"}
    result = _run(tmp_path, files, generate_yamaha_catalogs=False, preserve_yamaha_catalogs=True)
    assert not result.errors
    folder = Path(result.output_directories[0])
    assert {path.name for path in folder.iterdir()} == set(files)
    assert all((folder / name).read_bytes() == payload for name, payload in files.items())


def test_preserve_only_catalogs_are_scoped_to_source_folder_and_verified_on_resume(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.bulk_extraction_job import ExtractionJob

    files = {
        "ROOT.MID": _midi("Root song"),
        "PDISK.MNG": update_smart_pianosoft_disk_title(b"", "Root album"),
        "Converted/SONG.FIL": convert_midi_bytes_to_eseq_bytes(_midi()),
        "Converted/PIANODIR.FIL": _pianodir([("SONG.FIL", "Converted song")], "Converted album"),
        "Plain/Long original filename.mid": _midi("Plain song"),
    }
    checkpoint = tmp_path / "output/job.json"
    monkeypatch.setattr(ExtractionJob, "remove_completed", lambda _self: False)
    options = dict(generate_yamaha_catalogs=False, preserve_yamaha_catalogs=True,
                   convert_eseq=True, job_record_path=checkpoint)
    result = _run(tmp_path, files, **options)
    assert not result.errors
    folder = Path(result.output_directories[0])
    assert {path.name for path in (folder / "Plain").iterdir()} == {"Long original filename.mid"}
    assert not (folder / "PSONG.MNG").exists()
    assert parse_smart_pianosoft_disk_title((folder / "PDISK.MNG").read_bytes()) == "Root album"
    converted = folder / "Converted"
    song, = parse_smart_pianosoft_song_catalog((converted / "PSONG.MNG").read_bytes())
    assert song.title == "Converted song" and (converted / song.filename).is_file()
    assert parse_smart_pianosoft_disk_title((converted / "PDISK.MNG").read_bytes()) == "Converted album"
    job = read_extraction_job(checkpoint)
    assert job["images"]["disk.img"]["catalog_sources"] == {"": ["PDISK.MNG"], "converted": ["PIANODIR.FIL"]}
    assert len(job["images"]["disk.img"]["catalogs"]) == 3
    resumed = _run(tmp_path, files, **options, resume=True)
    assert not resumed.errors and resumed.images_skipped == 1
    saved = json.loads(checkpoint.read_bytes())
    saved["images"]["disk.img"]["catalogs"].pop()
    checkpoint.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="missing completed image entries"):
        read_extraction_job(checkpoint)


@pytest.mark.parametrize("option", ["preserve_yamaha_catalogs", "keep_original_catalogs"])
def test_catalog_options_are_immutable_and_require_a_boolean(tmp_path, option):
    files = {"FIRST.MID": _midi(), "SECOND.MID": _midi()}
    checkpoint = tmp_path / "output/job.json"
    def cancel_after_first(detail):
        if detail["stage"] == "extracting" and detail["file_completed"] == 1:
            raise FloppyOperationCancelled("Pause extraction")
    options = dict(generate_yamaha_catalogs=False, preserve_yamaha_catalogs=True, keep_original_catalogs=True,
                   job_record_path=checkpoint)
    with pytest.raises(FloppyOperationCancelled):
        _run(tmp_path, files, **options, progress_detail_callback=cancel_after_first)
    original = checkpoint.read_bytes()
    with pytest.raises(ValueError, match="different.*options"):
        _run(tmp_path, files, **{**options, option: False}, resume=True)
    assert checkpoint.read_bytes() == original
    for value in ("yes", None, 1, "missing"):
        malformed = json.loads(original)
        if value == "missing":
            del malformed["options"][option]
        else:
            malformed["options"][option] = value
        checkpoint.write_text(json.dumps(malformed))
        with pytest.raises(ValueError, match="malformed options"):
            read_extraction_job(checkpoint)


@pytest.mark.parametrize("keep_originals", [False, True])
def test_nested_catalog_archive_choice_survives_interrupted_extraction(tmp_path, keep_originals):
    psong = build_smart_pianosoft_song_catalog(b"", [
        build_smart_pianosoft_song_record("FIRST.MID", "First title"),
        build_smart_pianosoft_song_record("SECOND.MID", "Second title"),
    ])
    files = {"Album/PSONG.MNG": psong,
             "Album/FIRST.MID": _midi(), "Album/SECOND.MID": _midi()}
    checkpoint = tmp_path / "output/job.json"
    options = dict(generate_yamaha_catalogs=False, preserve_yamaha_catalogs=True,
                   keep_original_catalogs=keep_originals, job_record_path=checkpoint)

    def cancel_after_first_song(detail):
        if detail["stage"] == "extracting" and detail["file_completed"] == 2:
            raise FloppyOperationCancelled("Pause after first song")

    with pytest.raises(FloppyOperationCancelled):
        _run(tmp_path, files, **options, progress_detail_callback=cancel_after_first_song)
    original = checkpoint.read_bytes()
    saved = read_extraction_job(checkpoint)
    assert saved["images"]["disk.img"]["entries"]["Album/PSONG.MNG"]["state"] == (
        "complete" if keep_originals else "skipped"
    )
    # A catalog skip must never let a checkpoint claim an unverified song.
    malformed = json.loads(original)
    malformed["images"]["disk.img"]["entries"]["Album/FIRST.MID"] = {
        "state": "skipped", "outputs": [], "reason": "original_catalog",
    }
    checkpoint.write_text(json.dumps(malformed))
    with pytest.raises(ValueError, match="invalid skipped entry"):
        read_extraction_job(checkpoint)
    checkpoint.write_bytes(original)
    with pytest.raises(ValueError, match="different.*options"):
        _run(tmp_path, files, **{**options, "keep_original_catalogs": not keep_originals}, resume=True)
    assert checkpoint.read_bytes() == original

    result = _run(tmp_path, files, **options, resume=True)
    assert not result.errors and result.files_reused == 1 + keep_originals
    assert not checkpoint.exists()
    folder = Path(result.output_directories[0]) / "Album"
    songs = parse_smart_pianosoft_song_catalog((folder / "PSONG.MNG").read_bytes())
    assert [song.title for song in songs] == ["First title", "Second title"]
    assert all((folder / song.filename).read_bytes() == files[f"Album/{song.filename}"] for song in songs)
    assert (folder / "Original catalogs").exists() is keep_originals
    if keep_originals:
        assert (folder / "Original catalogs/PSONG.MNG").read_bytes() == psong


def test_default_archive_option_does_not_remove_catalogs_from_plain_extraction(tmp_path):
    files = {"FIRST.MID": _midi(), "PSONG.MNG": build_smart_pianosoft_song_catalog(b"", [
        build_smart_pianosoft_song_record("FIRST.MID", "First title"),
    ])}
    result = _run(tmp_path, files, generate_yamaha_catalogs=False, preserve_yamaha_catalogs=False)
    assert not result.errors
    folder = Path(result.output_directories[0])
    assert {path.name for path in folder.iterdir()} == set(files)
    assert all((folder / name).read_bytes() == data for name, data in files.items())
