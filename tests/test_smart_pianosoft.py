from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.smart_pianosoft import (
    build_smart_pianosoft_song_catalog,
    build_smart_pianosoft_song_record,
    parse_smart_pianosoft_song_catalog,
    parse_smart_pianosoft_disk_title,
    smart_pianosoft_catalog_from_session,
    smart_pianosoft_disk_title_from_session,
    smart_pianosoft_metadata_from_directory,
    update_smart_pianosoft_song_catalog,
    update_smart_pianosoft_disk_title,
)


def _psong_bytes(records):
    record_size = 0xB0
    data = bytearray(b" " * (0x80 + len(records) * record_size))
    data[0x00:0x10] = b"PSONG   MNG   \r\n"
    data[0x10:0x20] = f"MAX{len(records):03d}        \r\n".encode("ascii")
    data[0x20:0x30] = f"FILE{len(records):03d}       \r\n".encode("ascii")
    for index, (filename, title) in enumerate(records):
        start = 0x80 + index * record_size
        stem, extension = filename.rsplit(".", 1)
        data[start:start + 8] = stem.encode("cp1252").ljust(8, b" ")
        data[start + 8:start + 11] = extension.encode("cp1252").ljust(3, b" ")
        data[start + 14:start + 16] = b"\r\n"
        data[start + 16:start + 48] = title.encode("cp1252").ljust(32, b" ")
    return bytes(data)


def _pdisk_bytes(title, size=128):
    data = bytearray(b" " * size)
    data[:16] = b"PDISK   MNG   \r\n"
    data[16:32] = b"P.PLAYER      \r\n"
    data[32:48] = b"Ver1.01DMV0.53\r\n"
    data[48:112] = title.encode("cp1252").ljust(64, b" ")
    data[112:114] = b"\r\n"
    return bytes(data)


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\n"])
@pytest.mark.parametrize("size", [114, 128])
def test_pdisk_reads_full_album_field_in_both_observed_sizes(size, line_ending):
    title = "Jazz Caf\xe9 - Evening Performances from the Studio Collection"
    assert parse_smart_pianosoft_disk_title(
        _pdisk_bytes(title, size).replace(b"\r\n", line_ending)
    ) == title
    assert parse_smart_pianosoft_disk_title(
        _pdisk_bytes("", size).replace(b"\r\n", line_ending)
    ) == ""


@pytest.mark.parametrize("payload", [
    b"", _pdisk_bytes("Album")[:111], b"X" * 128,
    _pdisk_bytes("Album")[:112] + b"xx",
])
def test_pdisk_rejects_malformed_catalogs(payload):
    with pytest.raises(ValueError):
        parse_smart_pianosoft_disk_title(payload)


def test_folder_metadata_reads_mixed_case_catalogs_and_isolates_invalid_files(tmp_path):
    psong = tmp_path / "pSoNg.MnG"
    pdisk = tmp_path / "pDiSk.mNg"
    psong.write_bytes(_psong_bytes([("FIRST.MID", "Song title")]))
    pdisk.write_bytes(_pdisk_bytes("Album title", 114))
    metadata = smart_pianosoft_metadata_from_directory(tmp_path)
    assert metadata.disk_title == "Album title"
    assert metadata.songs[0].title == "Song title"
    assert metadata.song_catalog == psong.read_bytes()
    assert metadata.disk_catalog == pdisk.read_bytes()
    psong.write_bytes(b"truncated")
    metadata = smart_pianosoft_metadata_from_directory(tmp_path)
    assert metadata.disk_title == "Album title"
    assert metadata.songs == ()
    assert metadata.song_catalog == b""
    psong.write_bytes(_psong_bytes([("FIRST.MID", "Song title")]))
    pdisk.write_bytes(b"truncated")
    metadata = smart_pianosoft_metadata_from_directory(tmp_path)
    assert metadata.disk_title == ""
    assert metadata.songs[0].title == "Song title"
    assert metadata.disk_catalog == b""
    assert smart_pianosoft_metadata_from_directory(tmp_path / "absent").songs == ()


def test_lf_catalogs_supply_folder_and_session_metadata_without_modifying_sources(tmp_path):
    psong = _psong_bytes([("01.MID", "First Song"), ("02.MID", "Second Song")])
    pdisk = _pdisk_bytes("Album title")
    source_payloads = {
        "PSONG.MNG": psong.replace(b"\r", b""),
        "PDISK.MNG": pdisk.replace(b"\r", b""),
    }
    for name, payload in source_payloads.items():
        (tmp_path / name).write_bytes(payload)

    metadata = smart_pianosoft_metadata_from_directory(tmp_path)
    assert metadata.disk_title == "Album title"
    assert [(song.filename, song.title) for song in metadata.songs] == [
        ("01.MID", "First Song"), ("02.MID", "Second Song"),
    ]
    assert metadata.song_catalog == psong
    assert metadata.disk_catalog == pdisk
    assert all(len(song.raw_record) == 0xB0 for song in metadata.songs)

    session = SimpleNamespace(extract_file=lambda name: str(tmp_path / name))
    entries = [SimpleNamespace(path=name) for name in source_payloads]
    catalog = smart_pianosoft_catalog_from_session(session, entries)
    assert catalog["01.mid"].title == "First Song"
    assert catalog["02.mid"].title == "Second Song"
    assert smart_pianosoft_disk_title_from_session(session, entries) == "Album title"
    for name, payload in source_payloads.items():
        assert (tmp_path / name).read_bytes() == payload


def test_smart_pianosoft_catalog_reads_titles_and_track_order():
    songs = parse_smart_pianosoft_song_catalog(
        _psong_bytes(
            [
                ("FIRST.MID", "  Moon   River  "),
                ("SECOND.MID", "You Can't AlwaysGet What You..."),
            ]
        )
    )

    assert [(song.track_number, song.filename, song.title) for song in songs] == [
        (1, "FIRST.MID", "Moon River"),
        (2, "SECOND.MID", "You Can't Always Get What You..."),
    ]


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\n"])
def test_smart_pianosoft_catalog_title_update_preserves_other_records(line_ending):
    original = _psong_bytes(
        [
            ("FIRST.MID", "Moon River"),
            ("SECOND.MID", "Summer Wind"),
        ]
    )

    patched = update_smart_pianosoft_song_catalog(
        original.replace(b"\r\n", line_ending),
        {"FIRST.MID": "The New Title"},
    )
    songs = parse_smart_pianosoft_song_catalog(patched)

    assert [song.title for song in songs] == ["The New Title", "Summer Wind"]
    assert patched[:0x90] == original[:0x90]
    assert patched[0xB0:] == original[0xB0:]


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\n"])
def test_rebuilt_song_catalog_preserves_source_fields_and_removes_stale_records(line_ending):
    template = bytearray(_psong_bytes([("FIRST.MID", "First"), ("SECOND.MID", "Second")]))
    template[0x30:0x40] = b"Header sentinel!"
    second_start = 0x80 + 0xB0
    template[second_start + 0x50:second_start + 0x60] = b"A,I,P,M,SMF0,0\r\n"
    template[second_start + 0x90:second_start + 0xA0] = b"Record sentinel!"
    template += b"stale catalog tail"
    template = bytes(template).replace(b"\r\n", line_ending)
    source = parse_smart_pianosoft_song_catalog(template)[1]
    record = build_smart_pianosoft_song_record(
        "001NEW.MID", "New title", source_record=source.raw_record, midi_format=1,
    )
    output = build_smart_pianosoft_song_catalog(template, [record])
    songs = parse_smart_pianosoft_song_catalog(output)
    assert [(song.track_number, song.filename, song.title) for song in songs] == [(1, "001NEW.MID", "New title")]
    assert len(output) == 0x80 + 0xB0
    assert output[0x10:0x30] == b"MAX001        \r\nFILE001       \r\n"
    assert output[0x30:0x40] == b"Header sentinel!"
    assert songs[0].raw_record[0x90:0xA0] == b"Record sentinel!"
    assert songs[0].raw_record[0x50:0x60] == b"A,I,P,M,SMF1,0\r\n"
    assert record[0x30:0x50] == source.raw_record[0x30:0x50]
    assert record[0x60:] == source.raw_record[0x60:]


def test_uncataloged_song_gets_a_complete_record_with_bounded_title():
    record = build_smart_pianosoft_song_record("001NEW.MID", "Caf\xe9 " + "x" * 40, midi_format=1)
    output = build_smart_pianosoft_song_catalog(_psong_bytes([]), [record])
    song, = parse_smart_pianosoft_song_catalog(output)
    assert song.filename == "001NEW.MID"
    assert song.title == "Caf\xe9 " + "x" * 27
    assert record[0x50:0x60] == b"A,I,P,M,SMF1,0\r\n"


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\n"])
@pytest.mark.parametrize("size", [114, 128])
def test_compilation_disk_title_preserves_original_disk_catalog_format(size, line_ending):
    template = _pdisk_bytes("Original album", size)
    output = update_smart_pianosoft_disk_title(
        template.replace(b"\r\n", line_ending), "DSKA0007",
    )
    assert parse_smart_pianosoft_disk_title(output) == "DSKA0007"
    assert output[:0x30] == template[:0x30]
    assert output[0x70:] == template[0x70:]


def test_crlf_catalogs_preserve_lone_lf_in_opaque_fields(tmp_path):
    psong = bytearray(_psong_bytes([("FIRST.MID", "First")]))
    psong[0xC1] = 0x0A
    pdisk = bytearray(_pdisk_bytes("Album"))
    pdisk[0x7C] = 0x0A
    (tmp_path / "PSONG.MNG").write_bytes(psong)
    (tmp_path / "PDISK.MNG").write_bytes(pdisk)
    metadata = smart_pianosoft_metadata_from_directory(tmp_path)
    assert metadata.song_catalog == psong
    assert metadata.disk_catalog == pdisk
    assert update_smart_pianosoft_song_catalog(psong, {}) == psong
    assert update_smart_pianosoft_disk_title(pdisk, "Album") == pdisk


def test_catalog_backed_image_title_edit_stages_psong_not_midi(tmp_path):
    catalog_path = tmp_path / "PSONG.MNG"
    catalog_path.write_bytes(_psong_bytes([("FIRST.MID", "Moon River")]))
    patched_dir = tmp_path / "patched"
    patched_dir.mkdir()
    info = {"title_source": "smart_pianosoft_catalog", "title": "Moon River"}
    window = SimpleNamespace(
        image_session=SimpleNamespace(
            patched_dir=str(patched_dir),
            extract_file=lambda _path: str(catalog_path),
        ),
        smartPianoSoftCatalogPath="PSONG.MNG",
        imageEntriesByPath={"PSONG.MNG": object()},
        pendingImageAdditions={},
        pendingImageDeletes=set(),
        pendingSmartPianoSoftCatalogReplacement="",
        pendingSmartPianoSoftTitleEdits={},
        pendingImageReplacements={},
        pendingImageTitleEdits={"FIRST.MID": "must not reach the MIDI"},
        _image_title_is_smart_pianosoft_catalog_backed=lambda _path: True,
        _image_info_for_path=lambda _path: info,
    )
    window._stage_smart_pianosoft_catalog_title = MethodType(
        MidiTitleWindow._stage_smart_pianosoft_catalog_title,
        window,
    )
    window._pending_or_extracted_image_path = MethodType(
        MidiTitleWindow._pending_or_extracted_image_path,
        window,
    )

    destination = MidiTitleWindow._stage_image_title_edit(
        window,
        "FIRST.MID",
        "The New Title",
    )

    assert destination == "smart_pianosoft_catalog"
    assert "FIRST.MID" not in window.pendingImageTitleEdits
    replacement = window.pendingImageReplacements["PSONG.MNG"]
    assert parse_smart_pianosoft_song_catalog(Path(replacement).read_bytes())[0].title == (
        "The New Title"
    )
    assert parse_smart_pianosoft_song_catalog(catalog_path.read_bytes())[0].title == "Moon River"
    assert info["title"] == "The New Title"
