from pathlib import Path

import pytest

from aps_midi_prep_tool_app.emulator_image_builder import _catalog_song_matches
from aps_midi_prep_tool_app.yamaha_catalogs import (
    yamaha_catalog_metadata_from_directory, yamaha_metadata_from_pianodir_bytes,
)
from test_emulator_image_builder import _write_eseq_catalog_album, _write_mng_catalogs
from test_prepared_image_delivery import _apply, window


def test_pianodir_metadata_survives_an_invalid_midi_catalog(tmp_path):
    _write_eseq_catalog_album(tmp_path)
    (tmp_path / "PSONG.MNG").write_bytes(b"truncated")
    (tmp_path / "PDISK.MNG").write_bytes(b"truncated")
    originals = {path: path.read_bytes() for path in tmp_path.iterdir()}

    metadata = yamaha_catalog_metadata_from_directory(tmp_path)

    assert metadata.disk_title == "PIANODIR album"
    assert [(song.filename, song.title, song.raw_record) for song in metadata.songs] == [
        ("FIRST.FIL", "PIANODIR song 1", b""), ("SECOND.FIL", "PIANODIR song 2", b""),
    ]
    assert metadata.song_catalog == metadata.disk_catalog == b""
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals
    assert yamaha_metadata_from_pianodir_bytes((tmp_path / "PIANODIR.FIL").read_bytes()) == metadata


def test_mixed_midi_eseq_catalogs_match_the_exact_source_format(tmp_path):
    _write_eseq_catalog_album(tmp_path)
    _write_mng_catalogs(tmp_path, "MIDI album", [("FIRST.MID", "MIDI song")])
    metadata = yamaha_catalog_metadata_from_directory(tmp_path)
    paths = [str(tmp_path / name) for name in ("FIRST.MID", "FIRST.FIL", "SECOND.FIL")]

    matches = _catalog_song_matches(metadata.songs, paths)

    assert metadata.disk_title == "MIDI album"
    assert [matches[path].title for path in paths] == ["MIDI song", "PIANODIR song 1", "PIANODIR song 2"]
    assert matches[paths[0]].raw_record
    assert matches[paths[1]].raw_record == b""


@pytest.mark.parametrize("payload", [b"", b"PIANODIR", b"\0" * 6144])
def test_malformed_pianodir_does_not_create_song_metadata(tmp_path, payload):
    (tmp_path / "PIANODIR.FIL").write_bytes(payload)
    metadata = yamaha_catalog_metadata_from_directory(tmp_path)
    assert metadata.songs == ()
    assert metadata.disk_title == ""
    with pytest.raises(ValueError):
        yamaha_metadata_from_pianodir_bytes(payload)


@pytest.mark.parametrize("profile_key", ["mark_ii_xg", "enspire"])
def test_nested_image_pianodir_titles_follow_their_source_folder(window, tmp_path, profile_key):
    from aps_midi_prep_tool_app import floppy_image
    from aps_midi_prep_tool_app.smart_pianosoft import (
        parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog,
    )
    from aps_midi_prep_tool_app.yamaha_catalog_delivery import image_catalog_payloads

    image = tmp_path / 'albums.img'
    floppy_image.create_blank_floppy_image(image, floppy_image.DISK_FORMAT_BY_KEY['ibm.720'])
    for album in ('A', 'B'):
        folder = tmp_path / album
        _write_eseq_catalog_album(folder)
        catalog = folder / 'PIANODIR.FIL'
        payload = catalog.read_bytes()
        for index in (1, 2):
            before = f'PIANODIR song {index}'.encode()
            payload = payload.replace(before, f'{album} song {index}'.encode().ljust(len(before)))
        payload = payload.replace(b'PIANODIR album', f'Album {album}'.encode().ljust(len(b'PIANODIR album')))
        catalog.write_bytes(payload)
        floppy_image._run_command(
            [floppy_image._require_command('mmd'), '-i', str(image), floppy_image.mtools_path(album)],
            'Could not create the test album directory',
        )
        for path in folder.iterdir():
            floppy_image._copy_host_file_into_image(image, path, f'{album}/{path.name}')
    original = image.read_bytes()
    session = floppy_image.FloppyImageSession.load(image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    assert window.imageFileInfo['A/FIRST.FIL']['title'] == 'A song 1'
    assert window.imageFileInfo['B/FIRST.FIL']['title'] == 'B song 1'
    _apply(window, profile_key, 'usb' if profile_key == 'enspire' else 'flashfloppy_img')
    window.pendingImageTitleEdits['B/FIRST.FIL'] = 'Edited B song'
    payloads = image_catalog_payloads(window)
    for album in ('A', 'B'):
        songs = parse_smart_pianosoft_song_catalog(payloads[f'{album}/PSONG.MNG'])
        assert [song.title for song in songs] == [
            'Edited B song' if album == 'B' else 'A song 1', f'{album} song 2',
        ]
        assert parse_smart_pianosoft_disk_title(payloads[f'{album}/PDISK.MNG']) == f'Album {album}'
    output = tmp_path / 'export'
    window._export_image_session_files_to_folder(str(output))
    for album in ('A', 'B'):
        for name in ('PSONG.MNG', 'PDISK.MNG'):
            assert (output / album / name).read_bytes() == payloads[f'{album}/{name}']
        for song in parse_smart_pianosoft_song_catalog((output / album / 'PSONG.MNG').read_bytes()):
            assert (output / album / song.filename).is_file()
    assert image.read_bytes() == original
