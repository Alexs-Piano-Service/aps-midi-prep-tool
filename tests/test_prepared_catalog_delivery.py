"""Prepared image delivery retains Smart PianoSoft catalogs and MIDI bytes."""

import io
from pathlib import Path
from types import SimpleNamespace

import mido
import pytest
from PySide6.QtCore import Qt

from aps_midi_prep_tool_app import emulator_image_builder, prepared_delivery
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files
from aps_midi_prep_tool_app.preparation_profiles import SETTING_GENERATE_YAMAHA_CATALOGS
from aps_midi_prep_tool_app.smart_pianosoft import parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog
from test_emulator_image_builder import _write_eseq_catalog_album
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


def _build(window, monkeypatch, *, carry_catalogs=True, expected_generation=None):
    results = []

    def build(source, output, **options):
        assert options["preserve_catalog_midi"] is True
        assert options["include_subfolders"] is carry_catalogs
        if expected_generation is not None:
            assert options["generate_yamaha_catalogs"] is expected_generation
        elif window._preparation_profile().key == "enspire":
            assert options["generate_yamaha_catalogs"] is False
        if window._preparation_profile().key == "enspire":
            assert options["preserve_yamaha_catalogs"] is True
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


@pytest.mark.parametrize("profile_key", ["mark_ii_xg", "enspire"])
def test_image_delivery_retains_catalog_title_edits_and_opaque_fields_without_rewriting_midi(window, monkeypatch, tmp_path, profile_key):
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
    _apply(window, profile_key, "usb" if profile_key == "enspire" else "flashfloppy_img")
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


@pytest.mark.parametrize("image_mode", [False, True])
@pytest.mark.parametrize("profile_key", ["mark_ii_xg", "enspire"])
def test_prepared_eseq_conversion_carries_pianodir_and_pending_edits_into_midi_catalogs(window, monkeypatch, tmp_path, image_mode, profile_key):
    source = tmp_path / "ESEQ album"
    _write_eseq_catalog_album(source)
    originals = {path: path.read_bytes() for path in source.iterdir()}
    if image_mode:
        source_image = tmp_path / "source.img"
        create_floppy_images_from_files(
            [(str(path), path.name) for path in source.iterdir()], str(source_image), "img", DISK_FORMAT_BY_KEY["ibm.720"],
        )
        original_image = source_image.read_bytes()
        session = FloppyImageSession.load(source_image)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    else:
        window._load_regular_files([str(source / name) for name in ("FIRST.FIL", "SECOND.FIL")], "", prepare_destination=False)
    _apply(window, profile_key, "usb" if profile_key == "enspire" else "flashfloppy_img")
    rows = tuple(window._preparation_song_rows())
    edited_source = rows[0][1]
    edits = window.pendingImageTitleEdits if image_mode else window.pendingEdits
    edits[edited_source] = "Edited converted song"
    monkeypatch.setattr(window, "_current_album_metadata_for_preservation", lambda: SimpleNamespace(disk_title="Edited converted album"))

    files, = _build(window, monkeypatch)

    songs = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
    assert [song.title for song in songs] == [
        "Edited converted song" if path == edited_source else f"PIANODIR song {1 if Path(path).stem == 'FIRST' else 2}"
        for _row, path, _kind, _filename in rows
    ]
    assert {song.filename for song in songs} == {name for name in files if name.endswith(".MID")}
    assert parse_smart_pianosoft_disk_title(files["PDISK.MNG"]) == "Edited converted album"
    assert "PIANODIR.FIL" not in files
    assert {path: path.read_bytes() for path in source.iterdir()} == originals
    if image_mode:
        assert source_image.read_bytes() == original_image


@pytest.mark.parametrize("profile_key", ["enspire", "mark_ii_xg"])
def test_prepared_plain_midi_with_generation_disabled_does_not_create_catalogs(window, monkeypatch, tmp_path, profile_key):
    source = tmp_path / "SONG.MID"
    original = _song("Plain song")
    source.write_bytes(original)
    window._load_regular_files([str(source)], "", prepare_destination=False)
    _apply(window, profile_key, "usb" if profile_key == "enspire" else "flashfloppy_img")
    window.settings.setValue(SETTING_GENERATE_YAMAHA_CATALOGS, False)
    window.imagePianodirTitleEdit.setText("A display title is not source metadata")

    files, = _build(window, monkeypatch, carry_catalogs=False, expected_generation=False)

    assert not {"PSONG.MNG", "PDISK.MNG", "PIANODIR.FIL"}.intersection(files)
    assert len(files) == 1
    assert next(iter(files.values())) == original
    assert source.read_bytes() == original


@pytest.mark.parametrize("source_catalog", ["PSONG.MNG", "PDISK.MNG"])
@pytest.mark.parametrize("profile_key", ["enspire", "mark_ii_xg"])
def test_prepared_generation_disabled_retains_exact_source_catalogs(window, monkeypatch, tmp_path, source_catalog, profile_key):
    source = tmp_path / "album"
    _catalogs(source, ["SONG.MID"])
    missing = "PDISK.MNG" if source_catalog == "PSONG.MNG" else "PSONG.MNG"
    (source / missing).unlink()
    song = source / "SONG.MID"
    song.write_bytes(_song("Embedded title"))
    original_catalog = (source / source_catalog).read_bytes()
    original_song = song.read_bytes()
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window, profile_key, "usb" if profile_key == "enspire" else "flashfloppy_img")
    window.settings.setValue(SETTING_GENERATE_YAMAHA_CATALOGS, False)

    files, = _build(window, monkeypatch, expected_generation=False)

    assert {name for name in files if name.endswith('.MNG')} == {source_catalog}
    if source_catalog == "PSONG.MNG":
        record, = parse_smart_pianosoft_song_catalog(files[source_catalog])
        assert record.title == "Catalog song 0"
        assert record.raw_record[0x90:0xA0] == b"Opaque record 0 "
        assert files[record.filename] == original_song
    else:
        assert files[source_catalog] == original_catalog
    assert (source / source_catalog).read_bytes() == original_catalog
    assert song.read_bytes() == original_song
    assert not (source / missing).exists()


@pytest.mark.parametrize("profile_key", ["enspire", "midi_export"])
def test_prepared_manual_generation_creates_matching_catalogs_for_other_profiles(window, monkeypatch, tmp_path, profile_key):
    source = tmp_path / "A descriptive song filename.mid"
    original = _song("Manual catalog title")
    source.write_bytes(original)
    window._load_regular_files([str(source)], "", prepare_destination=False)
    _apply(window, profile_key, "usb")
    window.settings.setValue(SETTING_GENERATE_YAMAHA_CATALOGS, True)
    window.imagePianodirTitleEdit.setText("Manual album title")

    files, = _build(window, monkeypatch, carry_catalogs=False, expected_generation=True)

    assert {name for name in files if name.endswith('.MNG')} == {"PSONG.MNG", "PDISK.MNG"}
    song, = parse_smart_pianosoft_song_catalog(files['PSONG.MNG'])
    assert song.title == "Manual catalog title"
    assert len(Path(song.filename).stem) <= 8
    assert files[song.filename] == original
    assert parse_smart_pianosoft_disk_title(files['PDISK.MNG']) == "Manual album title"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob('*.MNG'))


def test_custom_manual_catalog_generation_delegates_to_generic_image_export(window, monkeypatch, tmp_path):
    source = tmp_path / "A descriptive song filename.mid"
    original = _song("Custom song")
    source.write_bytes(original)
    window._load_regular_files([str(source)], "", prepare_destination=False)
    window.settings.setValue(SETTING_GENERATE_YAMAHA_CATALOGS, True)
    _apply(window, "custom", "custom")
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
        str(tmp_path / 'custom.img'), 'img', DISK_FORMAT_BY_KEY['ibm.720'],
    ))
    monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *_args, **_kwargs: None)

    outputs = prepared_delivery.create_prepared_disk_images(window)

    assert not window.delivery_errors
    assert len(outputs) == 1
    assert window.image_session is not None
    catalog_path = window.image_session.extract_file('PSONG.MNG')
    song, = parse_smart_pianosoft_song_catalog(Path(catalog_path).read_bytes())
    assert song.title == "Custom song"
    assert len(Path(song.filename).stem) <= 8
    assert Path(window.image_session.extract_file(song.filename)).read_bytes() == original
    assert Path(window.image_session.extract_file('PDISK.MNG')).is_file()
    assert window.settings.value(SETTING_GENERATE_YAMAHA_CATALOGS, False, type=bool) is True
    assert source.read_bytes() == original
