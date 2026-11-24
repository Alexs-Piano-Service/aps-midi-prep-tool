"""Split MIDI disks keep album titles and catalog only their resident songs."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageError, FloppyImageSession,
    create_blank_floppy_image, create_floppy_images_from_files, read_image_listing,
)
from aps_midi_prep_tool_app.smart_pianosoft import (
    build_smart_pianosoft_song_catalog, build_smart_pianosoft_song_record,
    parse_smart_pianosoft_song_catalog, update_smart_pianosoft_disk_title,
)
from test_eseq_image_contents import _midi


def _collection(tmp_path, sizes):
    source = tmp_path / 'source'
    source.mkdir()
    files, records = {}, []
    for index, size in enumerate(sizes, 1):
        name = f'SONG{index:03d}.MID'
        files[name] = _midi().ljust(size, b'\0')
        record = bytearray(build_smart_pianosoft_song_record(name, f'Edited title {index}'))
        record[0x90:0x94] = bytes([index % 256]) * 4
        records.append(bytes(record))
    # Input file order deliberately differs from piano playback order.
    files['PSONG.MNG'] = build_smart_pianosoft_song_catalog(b'', reversed(records))
    files['PDISK.MNG'] = update_smart_pianosoft_disk_title(b'', 'Edited album') + b'opaque tail'
    for name, payload in files.items():
        (source / name).write_bytes(payload)
    return source, files


def _export(source, files, output, **kwargs):
    return create_floppy_images_from_files(
        [{'host_path': str(source / name), 'image_path': name} for name in files],
        output, 'img', DISK_FORMAT_BY_KEY['ibm.720'], **kwargs,
    )


def _read(path):
    session = FloppyImageSession.load(str(path))
    try:
        listing = session.list_entries()
        assert listing.free_space >= 0
        return {entry.path: Path(session.extract_file(entry.path)).read_bytes()
                for entry in listing.entries}
    finally:
        session.cleanup()


def _assert_catalogs(paths, originals):
    original_songs = parse_smart_pianosoft_song_catalog(originals['PSONG.MNG'])
    delivered, ordered_names = {}, []
    for path in paths:
        files = _read(path)
        songs = parse_smart_pianosoft_song_catalog(files.pop('PSONG.MNG'))
        assert files.pop('PDISK.MNG') == originals['PDISK.MNG']
        expected = [song for song in original_songs if song.filename in files]
        assert [(song.filename, song.raw_record) for song in songs] == [
            (song.filename, song.raw_record) for song in expected]
        assert {song.filename for song in songs} == {name for name in files if name.endswith('.MID')}
        assert not delivered.keys() & files.keys()
        assert files == {name: originals[name] for name in files}
        ordered_names.extend(song.filename for song in songs)
        delivered.update(files)
    assert ordered_names == [song.filename for song in original_songs]
    assert delivered == {name: value for name, value in originals.items() if not name.endswith('.MNG')}


@pytest.mark.parametrize('count', [3, 5])
def test_split_midi_images_duplicate_disk_title_and_subset_song_records(tmp_path, count):
    source, originals = _collection(tmp_path, [300 * 1024] * count)

    paths = _export(source, originals, tmp_path / 'Album.img')

    assert len(paths) == (count + 1) // 2
    _assert_catalogs(paths, originals)
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


def test_unsplit_midi_image_preserves_both_catalogs_byte_for_byte(tmp_path):
    source, originals = _collection(tmp_path, [1024, 2048])
    paths = _export(source, originals, tmp_path / 'Album.img')
    assert len(paths) == 1
    assert _read(paths[0]) == originals


def test_split_reserves_disk_and_song_catalog_space(tmp_path):
    blank = create_blank_floppy_image(tmp_path / 'blank.img', DISK_FORMAT_BY_KEY['ibm.720'])
    capacity = read_image_listing(blank)
    first = capacity.free_space // (2 * capacity.cluster_size) * capacity.cluster_size
    source, originals = _collection(tmp_path, [first, capacity.free_space - first])

    paths = _export(source, originals, tmp_path / 'Album.img')

    assert len(paths) == 2
    _assert_catalogs(paths, originals)


def test_midi_catalog_growth_to_another_cluster_moves_a_song(tmp_path):
    blank = create_blank_floppy_image(tmp_path / 'blank.img', DISK_FORMAT_BY_KEY['ibm.720'])
    capacity = read_image_listing(blank)
    count = (capacity.cluster_size - 0x80) // 0xB0 + 1
    # All music plus two single-cluster catalogs would fit exactly. PSONG
    # grows to two clusters when the last song joins the disk.
    largest = capacity.free_space - (count + 1) * capacity.cluster_size
    source, originals = _collection(tmp_path, [128] * (count - 1) + [largest])

    paths = _export(source, originals, tmp_path / 'Album.img')

    assert len(paths) == 2
    _assert_catalogs(paths, originals)


def test_split_midi_catalogs_reserve_both_root_slots(tmp_path):
    source, originals = _collection(tmp_path, [128, 128])
    # Put one MIDI amid sidecars so both resulting disks have playback data.
    files = {'SONG001.MID': originals['SONG001.MID']}
    for index in range(40):
        if index == 32:
            files['SONG002.MID'] = originals['SONG002.MID']
        name = f'Accompaniment notes number {index:04d}.txt'
        files[name] = b'notes'
        (source / name).write_bytes(files[name])
    files.update({name: originals[name] for name in ('PSONG.MNG', 'PDISK.MNG')})

    paths = _export(source, files, tmp_path / 'Album.img')

    assert len(paths) == 2
    _assert_catalogs(paths, files)


def test_malformed_split_song_catalog_does_not_publish_images(tmp_path):
    source, originals = _collection(tmp_path, [300 * 1024] * 3)
    (source / 'PSONG.MNG').write_bytes(b'broken')
    output = tmp_path / 'Album.img'
    output.write_bytes(b'previous output')

    with pytest.raises(FloppyImageError, match='PSONG.MNG.*split export'):
        _export(source, originals, output)

    assert output.read_bytes() == b'previous output'
    assert not list(tmp_path.glob('Album_*.img'))


def test_midi_too_large_with_catalogs_is_rejected_before_publication(tmp_path):
    blank = create_blank_floppy_image(tmp_path / 'blank.img', DISK_FORMAT_BY_KEY['ibm.720'])
    source, originals = _collection(tmp_path, [read_image_listing(blank).free_space])

    with pytest.raises(FloppyImageError, match='too large.*Yamaha MIDI catalogs'):
        _export(source, originals, tmp_path / 'Album.img')

    assert not list(tmp_path.glob('Album*.img'))


def test_thousand_midi_songs_keep_explicit_records_and_order_beyond_bounded_template(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    payload = _midi()
    records = {}
    for index in range(1, 1001):
        name = f'SONG{index:04d}.MID'
        (source / name).write_bytes(payload)
        record = bytearray(build_smart_pianosoft_song_record(name, f'Edited song {index}'))
        record[0x90:0xA0] = f'Opaque song {index}'.encode().ljust(16)
        records[name] = bytes(record)
    # A catalog can carry only 999 entries. Its order deliberately differs
    # from the final staged order, which includes the unrepresented last song.
    template = bytearray(build_smart_pianosoft_song_catalog(b'', list(records.values())[:999]))
    template[0x30:0x40] = b'Header sentinel!'
    (source / 'PSONG.MNG').write_bytes(template)
    album = update_smart_pianosoft_disk_title(b'', 'Thousand song collection')
    (source / 'PDISK.MNG').write_bytes(album)
    ordered_names = list(reversed(records))
    specs = [
        {'host_path': str(source / name), 'image_path': name, 'yamaha_song_record': records[name]}
        for name in ordered_names
    ]
    specs.extend({'host_path': str(source / name), 'image_path': name}
                 for name in ('PSONG.MNG', 'PDISK.MNG'))

    paths = create_floppy_images_from_files(
        specs, tmp_path / 'Collection.img', 'img', DISK_FORMAT_BY_KEY['ibm.720'],
    )

    delivered = []
    assert len(paths) == 10
    for path in paths:
        files = _read(path)
        catalog = files.pop('PSONG.MNG')
        assert catalog[0x30:0x40] == b'Header sentinel!'
        songs = parse_smart_pianosoft_song_catalog(catalog)
        assert len(songs) <= 109
        assert files.pop('PDISK.MNG') == album
        assert {song.filename for song in songs} == set(files)
        assert all(files[song.filename] == payload and song.raw_record == records[song.filename]
                   for song in songs)
        delivered.extend(song.filename for song in songs)
    assert delivered == ordered_names
    assert (source / 'PSONG.MNG').read_bytes() == template
    assert all((source / name).read_bytes() == payload for name in ordered_names)


def test_explicit_catalog_record_must_match_its_song_before_publication(tmp_path):
    source, originals = _collection(tmp_path, [128])
    specs = [{'host_path': str(source / name), 'image_path': name} for name in originals]
    specs[0]['yamaha_song_record'] = build_smart_pianosoft_song_record('OTHER.MID', 'Wrong song')

    with pytest.raises(FloppyImageError, match='Invalid Yamaha catalog record.*SONG001.MID'):
        create_floppy_images_from_files(specs, tmp_path / 'Album.img', 'img', DISK_FORMAT_BY_KEY['ibm.720'])

    assert not list(tmp_path.glob('Album*.img'))
