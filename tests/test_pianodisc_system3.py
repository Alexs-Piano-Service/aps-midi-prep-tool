from pathlib import Path

from aps_midi_prep_tool_app import floppy_image
from aps_midi_prep_tool_app.additional_formats import pianodisc_system3
from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from aps_midi_prep_tool_app.floppy_image import FloppyImageSession
from aps_midi_prep_tool_app.midi_metadata import extract_first_title_from_midi
from aps_midi_prep_tool_app.midi_type0_converter import (
    _parse_midi_chunks,
    _parse_track_events,
)


def _system3_image(song_specs):
    data = bytearray([0xFF] * 737_280)
    system_offset = 9 * pianodisc_system3.SECTOR_SIZE
    data[
        system_offset:system_offset + len(pianodisc_system3.SYSTEM_SIGNATURE)
    ] = pianodisc_system3.SYSTEM_SIGNATURE
    version_offset = system_offset + pianodisc_system3.SYSTEM_VERSION_OFFSET
    data[
        version_offset:version_offset + len(pianodisc_system3.SYSTEM_VERSION)
    ] = pianodisc_system3.SYSTEM_VERSION

    catalog_offset = system_offset + pianodisc_system3.SECTOR_SIZE
    start_sector = 72
    for index, (title, stream) in enumerate(song_specs):
        sector_count = max(
            1,
            (len(stream) + pianodisc_system3.SECTOR_SIZE - 1)
            // pianodisc_system3.SECTOR_SIZE,
        )
        record = bytearray(pianodisc_system3.CATALOG_RECORD_SIZE)
        title_bytes = title.encode("cp1252")[:pianodisc_system3.CATALOG_TITLE_SIZE]
        record[:len(title_bytes)] = title_bytes
        record[22:24] = (1).to_bytes(2, "little")
        record[24:26] = sector_count.to_bytes(2, "little")
        record[26:28] = start_sector.to_bytes(2, "little")
        record[40:42] = (
            b"\xFE\xFE"
            if index == 0
            else ((index - 1) * pianodisc_system3.CATALOG_RECORD_SIZE).to_bytes(2, "little")
        )
        record[42:44] = ((index + 1) * pianodisc_system3.CATALOG_RECORD_SIZE).to_bytes(
            2,
            "little",
        )
        record_offset = catalog_offset + index * pianodisc_system3.CATALOG_RECORD_SIZE
        data[record_offset:record_offset + len(record)] = record

        song_offset = start_sector * pianodisc_system3.SECTOR_SIZE
        data[song_offset:song_offset + sector_count * pianodisc_system3.SECTOR_SIZE] = (
            stream.ljust(sector_count * pianodisc_system3.SECTOR_SIZE, b"\x00")
        )
        start_sector += sector_count

    next_record = catalog_offset + len(song_specs) * pianodisc_system3.CATALOG_RECORD_SIZE
    data[next_record:next_record + pianodisc_system3.CATALOG_RECORD_SIZE] = bytes(
        [0xFE] * pianodisc_system3.CATALOG_RECORD_SIZE
    )
    return bytes(data)


def _simple_song_stream():
    return bytes(
        [
            0xFE,
            0x00,
            0x00,
            0x90,
            60,
            100,
            0xFE,
            0xF0,
            0x00,
            0x80,
            60,
            64,
            0x00,
            0xFC,
            0x01,
            0x00,
        ]
    )


def _midi_track_events(midi_data):
    _header_end, format_type, track_count, chunks = _parse_midi_chunks(midi_data)
    track_chunk = next(chunk for chunk in chunks if chunk["id"] == b"MTrk")
    events, last_tick = _parse_track_events(
        midi_data[track_chunk["data_start"]:track_chunk["data_end"]]
    )
    return format_type, track_count, events, last_tick


def test_recognizes_catalog_and_converts_timing_to_smf0(tmp_path):
    image_data = _system3_image([("Test / Song", _simple_song_stream())])

    assert pianodisc_system3.looks_like_pianodisc_system3_bytes(image_data)
    songs = pianodisc_system3.parse_pianodisc_system3_image(image_data)
    assert len(songs) == 1
    assert songs[0].title == "Test / Song"
    assert songs[0].start_sector == 72

    conversion = pianodisc_system3.convert_pianodisc_system3_image(image_data)
    assert len(conversion.files) == 1
    assert conversion.errors == ()
    assert conversion.files[0].filename == "PIANO001.MID"

    long_name_conversion = pianodisc_system3.convert_pianodisc_system3_image(
        image_data,
        long_filenames=True,
    )
    assert long_name_conversion.files[0].filename == "01 - Test Song.mid"

    midi_path = tmp_path / conversion.files[0].filename
    midi_path.write_bytes(conversion.files[0].data)
    assert extract_first_title_from_midi(midi_path) == "Test / Song"
    format_type, track_count, events, last_tick = _midi_track_events(
        conversion.files[0].data
    )
    assert format_type == 0
    assert track_count == 1
    assert [
        (tick, raw)
        for tick, _order, raw in events
        if raw and (raw[0] & 0xF0) in (0x80, 0x90)
    ] == [
        (0, bytes((0x90, 60, 100))),
        (240, bytes((0x80, 60, 64))),
    ]
    assert last_tick == 240 + pianodisc_system3.END_PAUSE_TICKS


def test_recognizes_production_header_without_optional_version_text():
    image_data = bytearray(
        _system3_image([("FIRSTþSECOND", _simple_song_stream())])
    )
    system_offset = 9 * pianodisc_system3.SECTOR_SIZE
    version_offset = system_offset + pianodisc_system3.SYSTEM_VERSION_OFFSET
    image_data[
        version_offset:version_offset + len(pianodisc_system3.SYSTEM_VERSION)
    ] = b"\x00" * len(pianodisc_system3.SYSTEM_VERSION)

    assert pianodisc_system3.looks_like_pianodisc_system3_bytes(image_data)
    songs = pianodisc_system3.parse_pianodisc_system3_image(image_data)
    assert [song.title for song in songs] == ["FIRST SECOND"]
    conversion = pianodisc_system3.convert_pianodisc_system3_image(image_data)
    assert [item.filename for item in conversion.files] == ["PIANO001.MID"]
    assert conversion.errors == ()


def test_corrupt_catalog_record_does_not_hide_later_valid_songs():
    image_data = bytearray(
        _system3_image(
            [
                ("First", _simple_song_stream()),
                ("Damaged", _simple_song_stream()),
                ("Third", _simple_song_stream()),
            ]
        )
    )
    catalog_offset = 10 * pianodisc_system3.SECTOR_SIZE
    damaged_record = catalog_offset + pianodisc_system3.CATALOG_RECORD_SIZE
    image_data[damaged_record + 24:damaged_record + 26] = b"\xFE\xFE"

    songs = pianodisc_system3.parse_pianodisc_system3_image(image_data)
    assert [(song.number, song.title) for song in songs] == [
        (1, "First"),
        (3, "Third"),
    ]
    conversion = pianodisc_system3.convert_pianodisc_system3_image(image_data)
    assert [item.filename for item in conversion.files] == [
        "PIANO001.MID",
        "PIANO003.MID",
    ]
    assert len(conversion.errors) == 1
    assert "Track 02 (Damaged)" in conversion.errors[0]
    assert "outside the image" in conversion.errors[0]


def test_aligned_product_signature_without_valid_catalog_is_rejected():
    image_data = bytearray([0xFF] * 737_280)
    system_offset = 9 * pianodisc_system3.SECTOR_SIZE
    image_data[
        system_offset:system_offset + len(pianodisc_system3.SYSTEM_SIGNATURE)
    ] = pianodisc_system3.SYSTEM_SIGNATURE

    assert not pianodisc_system3.looks_like_pianodisc_system3_bytes(image_data)


def test_running_status_and_piano_program_are_preserved():
    stream = bytes(
        [
            0x00,
            0x90,
            60,
            90,
            10,
            64,
            80,
            20,
            0x80,
            60,
            64,
            10,
            64,
            64,
            0,
            0xFC,
            1,
            0,
        ]
    )
    conversion = pianodisc_system3.convert_pianodisc_system3_image(
        _system3_image([("Running Status", stream)])
    )
    _format_type, _track_count, events, _last_tick = _midi_track_events(
        conversion.files[0].data
    )
    channel_events = [(tick, raw) for tick, _order, raw in events if raw and raw[0] < 0xF0]
    assert channel_events[:5] == [
        (0, bytes((0xC0, 0))),
        (0, bytes((0x90, 60, 90))),
        (10, bytes((0x90, 64, 80))),
        (30, bytes((0x80, 60, 64))),
        (40, bytes((0x80, 64, 64))),
    ]


def test_damaged_catalog_song_is_reported_without_losing_good_songs():
    image_data = _system3_image(
        [
            ("Readable", _simple_song_stream()),
            ("Incomplete", bytes((0, 0x90, 60, 100))),
        ]
    )
    conversion = pianodisc_system3.convert_pianodisc_system3_image(image_data)

    assert [item.song.title for item in conversion.files] == ["Readable"]
    assert len(conversion.errors) == 1
    assert "Incomplete" in conversion.errors[0]
    assert "Song data" in conversion.errors[0]


def test_floppy_session_exposes_decoded_midi_files_for_raw_images(tmp_path):
    image_path = Path(tmp_path) / "pianodisc.img"
    image_data = bytearray(
        _system3_image([("Session Song", _simple_song_stream())])
    )
    system_offset = 9 * pianodisc_system3.SECTOR_SIZE
    version_offset = system_offset + pianodisc_system3.SYSTEM_VERSION_OFFSET
    image_data[
        version_offset:version_offset + len(pianodisc_system3.SYSTEM_VERSION)
    ] = b"\x00" * len(pianodisc_system3.SYSTEM_VERSION)
    image_path.write_bytes(image_data)

    session = FloppyImageSession.load(image_path)
    try:
        assert session.read_only_format == "pianodisc_system3"
        assert session.disk_format.key == "pianodisc.system3"
        entries = session.list_entries().entries
        assert [entry.path for entry in entries] == ["PIANO001.MID"]
        extracted = Path(session.extract_file(entries[0].path))
        assert extracted.read_bytes().startswith(b"MThd")
    finally:
        session.cleanup()


def test_converted_hfe_routes_versionless_production_disk_to_pianodisc(
    monkeypatch,
    tmp_path,
):
    image_data = bytearray(
        _system3_image([("Converted Session", _simple_song_stream())])
    )
    system_offset = 9 * pianodisc_system3.SECTOR_SIZE
    version_offset = system_offset + pianodisc_system3.SYSTEM_VERSION_OFFSET
    image_data[
        version_offset:version_offset + len(pianodisc_system3.SYSTEM_VERSION)
    ] = b"\x00" * len(pianodisc_system3.SYSTEM_VERSION)
    source_path = Path(tmp_path) / "pianodisc.hfe"
    source_path.write_bytes(b"mock HFE source")

    def fake_convert(
        _input_path,
        output_path,
        _disk_format,
        cancel_callback=None,
        *,
        allow_sector_failures=False,
    ):
        Path(output_path).write_bytes(image_data)
        return ""

    monkeypatch.setattr(floppy_image, "_gw_convert", fake_convert)

    session = FloppyImageSession.load(source_path)
    try:
        assert session.read_only_format == "pianodisc_system3"
        assert session.disk_format.key == "pianodisc.system3"
        assert [entry.path for entry in session.list_entries().entries] == [
            "PIANO001.MID"
        ]
    finally:
        session.cleanup()


def test_bulk_extraction_writes_good_songs_and_reports_damaged_ones(tmp_path):
    source_directory = tmp_path / "images"
    output_directory = tmp_path / "output"
    source_directory.mkdir()
    (source_directory / "album.img").write_bytes(
        _system3_image(
            [
                ("Readable", _simple_song_stream()),
                ("Incomplete", bytes((0, 0x90, 60, 100))),
            ]
        )
    )

    job_path = output_directory / "job.json"
    result = bulk_extract_images(source_directory, output_directory, job_record_path=job_path)

    assert result.images_found == 1
    assert result.images_processed == 1
    assert result.files_extracted == 1
    assert len(result.errors) == 1
    assert "Incomplete" in result.errors[0]
    midi_paths = [
        path
        for path in output_directory.rglob("*")
        if path.is_file() and path.suffix.lower() == ".mid"
    ]
    assert [path.name for path in midi_paths] == ["PIANO001.MID"]

    resumed = bulk_extract_images(source_directory, output_directory, job_record_path=job_path, resume=True)
    assert resumed.files_reused == 1
    assert resumed.images_skipped == 0
    assert resumed.files_extracted == 0
    assert any("Incomplete" in error for error in resumed.errors)
