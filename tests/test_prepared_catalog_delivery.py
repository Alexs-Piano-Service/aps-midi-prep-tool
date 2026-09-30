"""Prepared image delivery retains Smart PianoSoft catalogs and MIDI bytes."""

import io
from pathlib import Path

import mido
from PySide6.QtCore import Qt

from aps_midi_prep_tool_app import emulator_image_builder, prepared_delivery
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files
from aps_midi_prep_tool_app.smart_pianosoft import parse_smart_pianosoft_song_catalog
from test_prepared_image_delivery import _apply, _song, window
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes


def _catalogs(directory, names, *, album="Catalog album"):
    directory.mkdir(parents=True, exist_ok=True)
    psong = bytearray(_psong_bytes([(name, f"Catalog song {index}") for index, name in enumerate(names)]))
    psong[0x30:0x40] = b"Header sentinel!"
    for index in range(len(names)):
        offset = 0x80 + index * 0xB0
        psong[offset + 0x90:offset + 0xA0] = f"Opaque record {index}".encode().ljust(16)
        psong[offset + 0x50:offset + 0x60] = b"A,I,P,M,SMF0,0\r\n"
    pdisk = bytearray(_pdisk_bytes(album))
    pdisk[0x72:] = b"Opaque album!!"
    (directory / "PSONG.MNG").write_bytes(psong)
    (directory / "PDISK.MNG").write_bytes(pdisk)
    return bytes(psong), bytes(pdisk)


def _build(window, monkeypatch):
    results = []

    def build(source, output, **options):
        assert options["preserve_catalog_midi"] is True
        assert options["include_subfolders"] is True
        results.append(emulator_image_builder.build_emulator_disk_images(source, output, **options))

    monkeypatch.setattr(window, "_start_emulator_image_build", build)
    prepared_delivery.create_prepared_disk_images(window)
    assert not window.delivery_errors
    result, = results
    assert result.contents_verified
    files = []
    for output in result.output_paths:
        session = FloppyImageSession.load(output)
        try:
            files.append({entry.path: Path(session.extract_file(entry.path)).read_bytes()
                          for entry in session.list_entries().entries if not entry.directory})
        finally:
            session.cleanup()
    return files


def test_image_delivery_retains_catalog_title_edits_and_opaque_fields_without_rewriting_midi(window, monkeypatch, tmp_path):
    source = tmp_path / "album"
    names = ["FIRST.MID", "SECOND.MID", "THIRD.MID"]
    psong, pdisk = _catalogs(source, names)
    originals = {name: _song(f"Embedded {index}", 60 + index) for index, name in enumerate(names)}
    for name, payload in originals.items():
        (source / name).write_bytes(payload)
    source_image = tmp_path / "catalog.img"
    create_floppy_images_from_files(
        [(str(path), path.name) for path in source.iterdir()], str(source_image), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    original_image = source_image.read_bytes()
    session = FloppyImageSession.load(source_image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    _apply(window)
    window._stage_image_title_edit("FIRST.MID", "Catalog edit only")
    window.pendingImageDeletes.add("SECOND.MID")

    files, = _build(window, monkeypatch)

    catalog = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
    assert [song.title for song in catalog] == ["Catalog edit only", "Catalog song 2"]
    assert [files[song.filename] for song in catalog] == [originals["FIRST.MID"], originals["THIRD.MID"]]
    assert [song.raw_record[0x90:0xA0] for song in catalog] == [b"Opaque record 0 ", b"Opaque record 2 "]
    assert files["PSONG.MNG"][0x30:0x40] == psong[0x30:0x40]
    assert files["PDISK.MNG"] == pdisk
    assert source_image.read_bytes() == original_image
    assert (source / "PSONG.MNG").read_bytes() == psong
    assert (source / "PDISK.MNG").read_bytes() == pdisk


def test_regular_delivery_finds_catalogs_by_original_paths_after_staged_type0_conversion(window, monkeypatch, tmp_path):
    source = tmp_path / "album"
    psong, pdisk = _catalogs(source, ["SONG.MID"])
    midi = mido.MidiFile(file=io.BytesIO(_song("Embedded title")))
    midi.type = 1
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("end_of_track")]))
    path = source / "SONG.MID"
    midi.save(path)
    original = path.read_bytes()
    window._load_regular_files([str(path)], "", prepare_destination=False)
    _apply(window, "pianodisc_128plus", "emulator_custom")
    staged = Path(window._regular_source_material_path(str(path)))
    assert staged.parent != source
    assert mido.MidiFile(staged).type == 0
    staged_bytes = staged.read_bytes()

    files, = _build(window, monkeypatch)

    song, = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
    assert song.title == "Catalog song 0"
    assert song.raw_record[0x90:0xA0] == b"Opaque record 0 "
    assert files[song.filename] == staged_bytes
    assert files["PDISK.MNG"] == pdisk
    assert path.read_bytes() == original
    assert (source / "PSONG.MNG").read_bytes() == psong
    assert (source / "PDISK.MNG").read_bytes() == pdisk


def test_mixed_album_delivery_keeps_visible_order_catalog_identity_and_ordinary_title_edits(window, monkeypatch, tmp_path):
    sources = []
    originals = {}
    catalog_originals = {}
    for index, (album, embedded) in enumerate((('first', 'Zulu'), ('second', 'Mike'), ('first', 'Alpha'))):
        directory = tmp_path / album
        directory.mkdir(exist_ok=True)
        path = directory / f"SONG{index}.MID"
        payload = _song(embedded, 60 + index)
        path.write_bytes(payload)
        sources.append(path)
        originals[str(path)] = payload
    for album in ("first", "second"):
        directory = tmp_path / album
        names = [path.name for path in sources if path.parent == directory]
        catalog_originals[album] = _catalogs(directory, names, album=album)
    ordinary = tmp_path / "plain" / "PLAIN.MID"
    ordinary.parent.mkdir()
    ordinary.write_bytes(_song("Bravo", 72))
    sources.append(ordinary)
    originals[str(ordinary)] = ordinary.read_bytes()
    window._load_regular_files([str(path) for path in sources], "", prepare_destination=False)
    _apply(window)
    window.table.sortItems(4, Qt.SortOrder.AscendingOrder)
    window.pendingEdits[str(ordinary)] = "Edited ordinary title"
    expected = [path for _row, path, _kind, _filename in window._preparation_song_rows()]

    files, = _build(window, monkeypatch)

    catalog = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
    assert len(catalog) == len(expected)
    for delivered, source_path in zip(catalog, expected):
        if source_path == str(ordinary):
            parsed = mido.MidiFile(file=io.BytesIO(files[delivered.filename]))
            assert any(message.type == "track_name" and message.name == "Edited ordinary title"
                       for track in parsed.tracks for message in track)
            assert delivered.title == "Edited ordinary title"
        else:
            assert files[delivered.filename] == originals[source_path]
            assert delivered.raw_record[0x90:0xA0].startswith(b"Opaque record")
    for source_path, payload in originals.items():
        assert Path(source_path).read_bytes() == payload
    for album, (psong, pdisk) in catalog_originals.items():
        assert (tmp_path / album / "PSONG.MNG").read_bytes() == psong
        assert (tmp_path / album / "PDISK.MNG").read_bytes() == pdisk
