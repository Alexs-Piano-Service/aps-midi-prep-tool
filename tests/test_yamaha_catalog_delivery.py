"""Disklavier title edits survive real Save and Save As paths in Yamaha catalogs."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image, main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import PianodirMetadata, PianodirTrackEntry, build_pianodir_bytes
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files
from aps_midi_prep_tool_app.preparation_profiles import requires_yamaha_midi_catalogs
from aps_midi_prep_tool_app.smart_pianosoft import (
    build_smart_pianosoft_song_catalog, build_smart_pianosoft_song_record,
    parse_smart_pianosoft_song_catalog, parse_smart_pianosoft_disk_title,
    update_smart_pianosoft_disk_title,
)
from aps_midi_prep_tool_app.yamaha_catalog_delivery import image_catalog_payloads, regular_catalog_payloads
from test_prepared_image_delivery import _apply, _song, window
from test_smart_pianosoft import _psong_bytes, _pdisk_bytes


@pytest.fixture(autouse=True)
def no_modal_errors(window, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    def fail(*args, **kwargs):
        pytest.fail(str(args))
    monkeypatch.setattr(window, '_show_error_list', fail)
    monkeypatch.setattr(main_window.QMessageBox, 'warning', fail)
    monkeypatch.setattr(main_window.QMessageBox, 'question', lambda *a, **k: main_window.QMessageBox.Yes)


def _read(directory):
    directory = Path(directory)
    return (parse_smart_pianosoft_song_catalog((directory / 'PSONG.MNG').read_bytes()),
            parse_smart_pianosoft_disk_title((directory / 'PDISK.MNG').read_bytes()))


def _load_nested_catalog_image(window, tmp_path, *, include_plain=False):
    image = tmp_path / 'albums.img'
    floppy_image.create_blank_floppy_image(image, DISK_FORMAT_BY_KEY['ibm.720'])
    for album in (('A', 'B', 'PLAIN') if include_plain else ('A', 'B')):
        floppy_image._run_command(
            [floppy_image._require_command('mmd'), '-i', str(image), floppy_image.mtools_path(album)],
            'Could not create the test album directory',
        )
        source = tmp_path / album
        source.mkdir()
        (source / 'SONG.MID').write_bytes(_song('Embedded ' + album))
        if album != 'PLAIN':
            (source / 'PSONG.MNG').write_bytes(_psong_bytes([('SONG.MID', 'Catalog ' + album)]))
            (source / 'PDISK.MNG').write_bytes(_pdisk_bytes('Album ' + album))
        for path in source.iterdir():
            floppy_image._copy_host_file_into_image(image, path, f'{album}/{path.name}')
    original = image.read_bytes()
    session = FloppyImageSession.load(image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    _apply(window)
    return image, original


def _assert_nested_catalogs(payloads, titles):
    for album, title in titles.items():
        songs = parse_smart_pianosoft_song_catalog(payloads[f'{album}/PSONG.MNG'])
        assert [(song.filename, song.title) for song in songs] == [('SONG.MID', title)]
        assert parse_smart_pianosoft_disk_title(payloads[f'{album}/PDISK.MNG']) == f'Album {album}'


def test_nested_image_catalogs_keep_each_folders_titles_and_album_on_save(window, tmp_path):
    image, original = _load_nested_catalog_image(window, tmp_path)
    titles = {'A': 'Catalog A', 'B': 'Catalog B'}
    assert {album: window.imageFileInfo[f'{album}/SONG.MID']['title'] for album in titles} == titles
    _assert_nested_catalogs(image_catalog_payloads(window), titles)

    renames, deletes, additions, replacements, title_edits, _ = window._collect_image_operations()
    outputs = window.image_session.export_to_images(
        str(tmp_path / 'saved.img'), 'img', DISK_FORMAT_BY_KEY['ibm.720'],
        renames=renames, deletes=deletes, additions=additions,
        replacements=replacements, title_edits=title_edits,
    )
    saved = FloppyImageSession.load(outputs[0])
    try:
        _assert_nested_catalogs({f'{album}/{name}': Path(saved.extract_file(f'{album}/{name}')).read_bytes()
                                 for album in titles for name in ('PSONG.MNG', 'PDISK.MNG')}, titles)
    finally:
        saved.cleanup()
    assert image.read_bytes() == original


def test_nested_image_catalog_title_edits_discard_and_undo_stay_in_their_album(window, tmp_path):
    image, original = _load_nested_catalog_image(window, tmp_path)
    window._stage_image_title_edit('B/SONG.MID', 'Edited B')
    assert 'B/PSONG.MNG' in window.pendingImageReplacements
    assert 'A/PSONG.MNG' not in window.pendingImageReplacements
    _assert_nested_catalogs(image_catalog_payloads(window), {'A': 'Catalog A', 'B': 'Edited B'})

    window._stage_image_title_edit('A/SONG.MID', 'Edited A')
    window._stage_image_title_edit('B/SONG.MID', 'Revised B')
    inspected = {item['source_path']: Path(item['path']).read_bytes()
                 for item in window._inspection_catalog_items([])}
    _assert_nested_catalogs(inspected, {'A': 'Edited A', 'B': 'Revised B'})

    window.discard_staged_song_changes(['B/SONG.MID'])
    assert window.pendingSmartPianoSoftTitleEdits == {'A/SONG.MID': 'Edited A'}
    assert window.imageFileInfo['B/SONG.MID']['title'] == 'Catalog B'
    assert window._image_title_is_smart_pianosoft_catalog_backed('B/SONG.MID')
    _assert_nested_catalogs(image_catalog_payloads(window), {'A': 'Edited A', 'B': 'Catalog B'})

    window.undo_last_staged_batch()
    _assert_nested_catalogs(image_catalog_payloads(window), {'A': 'Edited A', 'B': 'Revised B'})
    assert image.read_bytes() == original


@pytest.mark.parametrize('key, expected', [
    ('mark_ii_xg', True), ('mark_iii', True), ('dsr1', True), ('mark_iv', True),
    ('e3_850', True), ('enspire', False), ('mark_i', False), ('mark_ii', False),
    ('pianodisc_prodigy', False), ('qrs_pmii', False), ('midi_export', False), ('custom', False),
])
def test_catalog_generation_is_limited_to_compatible_yamaha_profiles(key, expected):
    assert requires_yamaha_midi_catalogs(key) is expected


def test_fresh_catalogs_roundtrip_without_a_source_template():
    payload = build_smart_pianosoft_song_catalog(b'', [
        build_smart_pianosoft_song_record('SONG.MID', 'New title', midi_format=1),
    ])
    song, = parse_smart_pianosoft_song_catalog(payload)
    assert (song.filename, song.title) == ('SONG.MID', 'New title')
    assert b'SMF1' in song.raw_record
    assert parse_smart_pianosoft_disk_title(update_smart_pianosoft_disk_title(b'', 'New album')) == 'New album'


@pytest.mark.parametrize('image_source', [False, True])
@pytest.mark.parametrize('eseq_source', [False, True])
def test_enspire_plain_songs_never_acquire_catalogs(window, tmp_path, image_source, eseq_source):
    source = tmp_path / 'source'
    source.mkdir()
    song = source / ('SONG.FIL' if eseq_source else 'SONG.MID')
    data = _song('Embedded title')
    song.write_bytes(convert_midi_bytes_to_eseq_bytes(data) if eseq_source else data)
    if image_source:
        image = tmp_path / 'source.img'
        create_floppy_images_from_files([(str(song), song.name)], str(image), 'img', DISK_FORMAT_BY_KEY['ibm.720'])
        session = FloppyImageSession.load(image)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    else:
        window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window, 'enspire', 'usb')
    window.imagePianodirTitleEdit.setText('New album')
    output = tmp_path / 'export'
    if image_source:
        assert image_catalog_payloads(window) == {}
        operations = window._collect_image_operations()
        assert not any(Path(path).suffix.upper() == '.MNG' for path in operations[2])
        window._export_image_session_files_to_folder(str(output))
    else:
        errors, *_ = window._export_regular_files_to_folder(str(output))
        assert not errors
        assert not any(Path(path).suffix.upper() == '.MNG'
                       for path in window._regular_save_as_destination_paths(str(output)))
        stage = tmp_path / 'stage'
        stage.mkdir()
        specs = window._stage_files_for_image_export(str(stage))
        assert not any(Path(spec['image_path']).suffix.upper() == '.MNG' for spec in specs)
        window.save_pending_changes()
        assert not list(source.glob('*.MNG'))
    assert list(output.glob('*.MID')) or list(output.glob('*.mid'))
    assert not list(output.rglob('*.MNG'))


@pytest.mark.parametrize('descriptive_names', [False, True])
def test_enspire_catalog_preservation_is_scoped_to_each_image_folder(window, tmp_path, descriptive_names):
    _load_nested_catalog_image(window, tmp_path, include_plain=True)
    _apply(window, 'enspire', 'usb')
    if descriptive_names:
        window.pendingFloppyReadLongFilenames = True
        assert window._apply_pending_floppy_read_long_filenames() == 1
        assert window.pendingImageExportFilenames == {'PLAIN/SONG.MID': '01 - Embedded PLAIN.mid'}
    payloads = image_catalog_payloads(window)
    _assert_nested_catalogs(payloads, {'A': 'Catalog A', 'B': 'Catalog B'})
    assert len(payloads) == 4
    output = tmp_path / 'export'
    window._export_image_session_files_to_folder(str(output))
    assert (output / 'PLAIN' / ('01 - Embedded PLAIN.mid' if descriptive_names else 'SONG.MID')).is_file()
    assert not list((output / 'PLAIN').glob('*.MNG'))
    for album in ('A', 'B'):
        songs, _title = _read(output / album)
        assert (output / album / songs[0].filename).is_file()


@pytest.mark.parametrize('source_catalog', [False, True])
def test_profile_switch_does_not_publish_cached_new_catalogs(window, tmp_path, source_catalog):
    song = tmp_path / 'SONG.MID'
    song.write_bytes(_song('Title'))
    if source_catalog:
        (tmp_path / 'PSONG.MNG').write_bytes(_psong_bytes([('SONG.MID', 'Catalog title')]))
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window)
    # A failed supported-profile save retains prepared catalog bytes for retry.
    window.pendingYamahaCatalogPayloads = regular_catalog_payloads(window)
    assert {Path(path).name for path in window.pendingYamahaCatalogPayloads} == {'PSONG.MNG', 'PDISK.MNG'}
    _apply(window, 'enspire', 'usb')
    assert {Path(path).name for path in regular_catalog_payloads(window)} == ({'PSONG.MNG'} if source_catalog else set())


@pytest.mark.parametrize('image_source', [False, True])
@pytest.mark.parametrize('catalog_name', ['PSONG.MNG', 'PDISK.MNG'])
@pytest.mark.parametrize('profile', ['mark_ii_xg', 'enspire'])
def test_only_supported_profiles_complete_a_partial_catalog_pair(window, tmp_path, image_source, catalog_name, profile):
    source = tmp_path / 'source'
    source.mkdir()
    song = source / 'SONG.MID'
    song.write_bytes(_song('Embedded title'))
    catalog = source / catalog_name
    catalog.write_bytes(_psong_bytes([('SONG.MID', 'Catalog title')]) if catalog_name == 'PSONG.MNG'
                        else _pdisk_bytes('Catalog album'))
    if image_source:
        image = tmp_path / 'source.img'
        create_floppy_images_from_files([(str(path), path.name) for path in (song, catalog)],
                                       str(image), 'img', DISK_FORMAT_BY_KEY['ibm.720'])
        session = FloppyImageSession.load(image)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    else:
        window._load_regular_files([str(song), str(catalog)], '', prepare_destination=False)
    _apply(window, profile)
    expected = {'PSONG.MNG', 'PDISK.MNG'} if profile == 'mark_ii_xg' else {catalog_name}
    output = tmp_path / 'export'
    if image_source:
        assert set(image_catalog_payloads(window)) == expected
        window._export_image_session_files_to_folder(str(output))
    else:
        assert {Path(path).name for path in window._regular_save_as_destination_paths(str(output))
                if Path(path).suffix.upper() == '.MNG'} == expected
        errors, *_ = window._export_regular_files_to_folder(str(output))
        assert not errors
        window.save_pending_changes()
        assert {path.name for path in source.glob('*.MNG')} == expected
    assert {path.name for path in output.glob('*.MNG')} == expected


def test_save_creates_catalogs_and_updates_album_even_without_song_edits(window, tmp_path):
    song = tmp_path / 'SONG.MID'
    song.write_bytes(_song('Song title'))
    original = song.read_bytes()
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window)
    window.imagePianodirTitleEdit.setText('Album title')
    window.save_pending_changes()
    songs, album = _read(tmp_path)
    assert [(s.filename, s.title) for s in songs] == [('SONG.MID', 'Song title')]
    assert album == 'Album title'
    assert song.read_bytes() == original
    window.imagePianodirTitleEdit.clear()
    window.save_pending_changes()
    assert _read(tmp_path)[1] == ''


@pytest.mark.parametrize('profile', ['mark_ii_xg', 'enspire'])
def test_save_updates_titles_and_renames_in_existing_catalogs_preserving_unlisted_siblings(window, tmp_path, profile):
    song = tmp_path / 'OLD.MID'
    other = tmp_path / 'OTHER.MID'
    song.write_bytes(_song('Embedded'))
    other.write_bytes(_song('Other embedded'))
    psong = bytearray(_psong_bytes([('OLD.MID', 'Catalog title'), ('OTHER.MID', 'Other catalog')]))
    psong[0x30:0x40] = b'Header sentinel!'
    (tmp_path / 'PSONG.MNG').write_bytes(psong)
    (tmp_path / 'PDISK.MNG').write_bytes(_pdisk_bytes('Old album'))
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window, profile)
    row = next(row for row in window._regular_file_rows() if window.table.item(row, 1).text() == str(song))
    window.pendingEdits[str(song)] = 'Updated song'
    window._stage_regular_row_pending_rename(row, str(song), 'NEW.MID')
    window.imagePianodirTitleEdit.setText('Updated album')
    window.save_pending_changes()
    songs, album = _read(tmp_path)
    assert [(s.filename, s.title) for s in songs] == [('NEW.MID', 'Updated song'), ('OTHER.MID', 'Other catalog')]
    assert album == 'Updated album'
    assert (tmp_path / 'PSONG.MNG').read_bytes()[0x30:0x40] == b'Header sentinel!'
    assert (tmp_path / 'NEW.MID').exists()
    assert not song.exists()


@pytest.mark.parametrize('image_source', [False, True])
@pytest.mark.parametrize('profile', ['mark_ii_xg', 'enspire'])
def test_pianodir_conversion_exports_catalog_titles_and_album(window, tmp_path, image_source, profile):
    source = tmp_path / 'source'
    source.mkdir()
    song = source / 'SONG.FIL'
    song.write_bytes(convert_midi_bytes_to_eseq_bytes(_song('Embedded title')))
    catalog = source / 'PIANODIR.FIL'
    data = bytearray(build_pianodir_bytes(
        [PianodirTrackEntry('SONG.FIL', str(song), 'Catalog title')],
        metadata=PianodirMetadata(disk_title='ESEQ album'),
    ))
    # The directory title can differ from the embedded song title.
    data[0x10 + 0x30:0x10 + 0x50] = b'Catalog title'.ljust(32, b' ')
    catalog.write_bytes(data)
    originals = (song.read_bytes(), catalog.read_bytes())
    if image_source:
        image = tmp_path / 'source.img'
        create_floppy_images_from_files([(str(song), song.name), (str(catalog), catalog.name)],
                                       str(image), 'img', DISK_FORMAT_BY_KEY['ibm.720'])
        session = FloppyImageSession.load(image)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    else:
        window._load_regular_files([str(song), str(catalog)], '', prepare_destination=False)
    _apply(window, profile)
    output = tmp_path / 'export'
    if image_source:
        window._export_image_session_files_to_folder(str(output))
    else:
        errors, *_ = window._export_regular_files_to_folder(str(output))
        assert not errors
    songs, album = _read(output)
    assert len(songs) == 1
    assert songs[0].title == 'Catalog title'
    assert album == 'ESEQ album'
    assert (output / songs[0].filename).is_file()
    assert songs[0].filename.upper().endswith('.MID')
    assert (song.read_bytes(), catalog.read_bytes()) == originals


def test_regular_save_as_image_stages_catalogs_with_actual_image_names(window, tmp_path):
    song = tmp_path / 'Long descriptive song.mid'
    song.write_bytes(_song('Display title'))
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window, 'mark_iv', 'usb')
    window.imagePianodirTitleEdit.setText('Album')
    stage = tmp_path / 'stage'
    stage.mkdir()
    specs = window._stage_files_for_image_export(str(stage))
    by_name = {spec['image_path']: spec for spec in specs}
    songs = parse_smart_pianosoft_song_catalog(Path(by_name['PSONG.MNG']['host_path']).read_bytes())
    assert len(songs) == 1
    assert songs[0].filename in by_name
    assert songs[0].filename != Path(by_name[songs[0].filename]['host_path']).name
    assert parse_smart_pianosoft_disk_title(Path(by_name['PDISK.MNG']['host_path']).read_bytes()) == 'Album'


def test_image_save_catalog_operations_include_title_album_rename_and_delete(window, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('FIRST.MID', 'SECOND.MID'):
        (source / name).write_bytes(_song(name))
    (source / 'PSONG.MNG').write_bytes(_psong_bytes([('FIRST.MID', 'First title'), ('SECOND.MID', 'Second title')]))
    (source / 'PDISK.MNG').write_bytes(_pdisk_bytes('Old album'))
    image = tmp_path / 'source.img'
    create_floppy_images_from_files([(str(path), path.name) for path in source.iterdir()],
                                   str(image), 'img', DISK_FORMAT_BY_KEY['ibm.720'])
    session = FloppyImageSession.load(image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    _apply(window)
    window._stage_image_title_edit('FIRST.MID', 'Updated title')
    window.pendingImageRenames['FIRST.MID'] = 'RENAMED.MID'
    window.pendingImageDeletes.add('SECOND.MID')
    window.imagePianodirTitleEdit.setText('Updated album')
    renames, deletes, additions, replacements, title_edits, _ = window._collect_image_operations()
    result = tmp_path / 'result.img'
    outputs = session.export_to_images(str(result), 'img', DISK_FORMAT_BY_KEY['ibm.720'],
                                      renames=renames, deletes=deletes, additions=additions,
                                      replacements=replacements, title_edits=title_edits)
    saved = FloppyImageSession.load(outputs[0])
    try:
        songs = parse_smart_pianosoft_song_catalog(Path(saved.extract_file('PSONG.MNG')).read_bytes())
        assert [(s.filename, s.title) for s in songs] == [('RENAMED.MID', 'Updated title')]
        assert parse_smart_pianosoft_disk_title(Path(saved.extract_file('PDISK.MNG')).read_bytes()) == 'Updated album'
    finally:
        saved.cleanup()


@pytest.mark.parametrize('failure_stage', ['song', 'catalog'])
@pytest.mark.parametrize('revise_rename', [False, True])
def test_failed_catalog_save_keeps_retry_metadata_after_song_rename(window, monkeypatch, tmp_path,
                                                                 failure_stage, revise_rename):
    song = tmp_path / 'OLD.MID'
    song.write_bytes(_song('Original'))
    psong = bytearray(_psong_bytes([('OLD.MID', 'Original')]))
    psong[0x80 + 0x90:0x80 + 0xA0] = b'Opaque sentinel!'
    (tmp_path / 'PSONG.MNG').write_bytes(psong)
    (tmp_path / 'PDISK.MNG').write_bytes(_pdisk_bytes('Album'))
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window)
    window.pendingEdits[str(song)] = 'Saved song title'
    window._stage_regular_row_pending_rename(0, str(song), 'NEW.MID')
    errors = []
    monkeypatch.setattr(window, '_show_error_list', lambda *a, **k: errors.append(a))
    real_write = main_window.write_catalog_payloads
    real_update = main_window.update_midi_title
    if failure_stage == 'catalog':
        monkeypatch.setattr(main_window, 'write_catalog_payloads', lambda *a, **k: (_ for _ in ()).throw(OSError('Disk full')))
    else:
        monkeypatch.setattr(main_window, 'update_midi_title', lambda *a: 'Disk full')
    window.save_pending_changes()
    assert errors
    assert bool(window.pendingEdits) == (failure_stage == 'song')
    current_path = tmp_path / ('OLD.MID' if failure_stage == 'song' else 'NEW.MID')
    assert current_path.exists()
    assert window._pending_changes_to_discard()
    assert window.pendingYamahaCatalogPayloads
    if revise_rename:
        window._stage_regular_row_pending_rename(0, str(current_path), 'THIRD.MID')
    monkeypatch.setattr(main_window, 'write_catalog_payloads', real_write)
    monkeypatch.setattr(main_window, 'update_midi_title', real_update)
    window.save_pending_changes()
    songs, _ = _read(tmp_path)
    assert [(song.filename, song.title) for song in songs] == [
        ('THIRD.MID' if revise_rename else 'NEW.MID', 'Saved song title')]
    assert songs[0].raw_record[0x90:0xA0] == b'Opaque sentinel!'
    assert not window.pendingYamahaCatalogPayloads
    assert not window._pending_changes_to_discard()


def test_image_deleting_last_song_rebuilds_empty_catalog(window, tmp_path):
    song = tmp_path / 'SONG.MID'
    song.write_bytes(_song('Song'))
    psong = tmp_path / 'PSONG.MNG'
    psong.write_bytes(_psong_bytes([('SONG.MID', 'Song')]))
    pdisk = tmp_path / 'PDISK.MNG'
    pdisk.write_bytes(_pdisk_bytes('Album'))
    image = tmp_path / 'source.img'
    create_floppy_images_from_files([(str(path), path.name) for path in (song, psong, pdisk)],
                                   str(image), 'img', DISK_FORMAT_BY_KEY['ibm.720'])
    session = FloppyImageSession.load(image)
    window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    _apply(window)
    window.pendingImageDeletes.add('SONG.MID')
    _, _, _, replacements, _, _ = window._collect_image_operations()
    assert parse_smart_pianosoft_song_catalog(Path(replacements['PSONG.MNG']).read_bytes()) == ()
    assert parse_smart_pianosoft_disk_title(Path(replacements['PDISK.MNG']).read_bytes()) == 'Album'


@pytest.mark.parametrize('approve', [False, True])
def test_save_as_reviews_existing_lowercase_catalogs(window, monkeypatch, tmp_path, approve):
    source = tmp_path / 'source'
    source.mkdir()
    song = source / 'SONG.MID'
    song.write_bytes(_song('New song title'))
    window._load_regular_files([str(song)], '', prepare_destination=False)
    _apply(window)
    output = tmp_path / 'output'
    output.mkdir()
    old_song = _psong_bytes([('OLD.MID', 'Old title')])
    old_disk = _pdisk_bytes('Old album')
    (output / 'psong.mng').write_bytes(old_song)
    (output / 'pdisk.mng').write_bytes(old_disk)
    monkeypatch.setattr(main_window.QFileDialog, 'getExistingDirectory', lambda *a, **k: str(output))
    monkeypatch.setattr(window, '_destination_with_album_subfolder', lambda path: path)
    questions = []
    def question(*args, **kwargs):
        questions.append(args)
        return main_window.QMessageBox.Yes if approve else main_window.QMessageBox.No
    monkeypatch.setattr(main_window.QMessageBox, 'question', question)
    window.save_as_changes()
    assert questions
    assert 'psong.mng' in str(questions)
    assert not (output / 'PSONG.MNG').exists()
    assert not (output / 'PDISK.MNG').exists()
    if approve:
        catalog = parse_smart_pianosoft_song_catalog((output / 'psong.mng').read_bytes())
        assert [(song.filename, song.title) for song in catalog] == [('SONG.MID', 'New song title')]
        assert (output / 'SONG.MID').exists()
    else:
        assert (output / 'psong.mng').read_bytes() == old_song
        assert (output / 'pdisk.mng').read_bytes() == old_disk
        assert not (output / 'SONG.MID').exists()


def test_in_place_save_keeps_existing_catalog_order_and_case_insensitive_siblings(window, tmp_path):
    first = tmp_path / 'FIRST.MID'
    second = tmp_path / 'second.mid'
    for path in (first, second):
        path.write_bytes(_song('Embedded'))
    (tmp_path / 'PSONG.MNG').write_bytes(_psong_bytes([('SECOND.MID', 'Second'), ('FIRST.MID', 'First')]))
    (tmp_path / 'PDISK.MNG').write_bytes(_pdisk_bytes('Album'))
    window._load_regular_files([str(first)], '', prepare_destination=False)
    _apply(window)
    window.pendingEdits[str(first)] = 'Updated first'
    window.save_pending_changes()
    songs, _ = _read(tmp_path)
    assert [(song.filename, song.title) for song in songs] == [('SECOND.MID', 'Second'), ('FIRST.MID', 'Updated first')]


def test_title_edited_before_conversion_wins_original_pianodir_title(window, tmp_path):
    song = tmp_path / 'SONG.FIL'
    song.write_bytes(convert_midi_bytes_to_eseq_bytes(_song('Original')))
    catalog = tmp_path / 'PIANODIR.FIL'
    catalog.write_bytes(build_pianodir_bytes([PianodirTrackEntry('SONG.FIL', str(song), 'Old catalog title')],
                                           metadata=PianodirMetadata(disk_title='Album')))
    window._load_regular_files([str(song), str(catalog)], '', prepare_destination=False)
    row, = window._regular_eseq_rows()
    window.pendingEdits[str(song)] = 'Edited before conversion'
    window.table.setItem(row, 4, window._make_title_item('Edited before conversion', title_mode='eseq', edited=True))
    _apply(window)
    assert window.pendingRegularConversions[str(song)]['title_edited']
    errors, *_ = window._export_regular_files_to_folder(str(tmp_path / 'output'))
    assert not errors
    songs, _ = _read(tmp_path / 'output')
    assert songs[0].title == 'Edited before conversion'
