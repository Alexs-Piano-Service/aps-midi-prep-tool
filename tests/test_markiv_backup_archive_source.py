"""Existing backup scans use verified originals and preserve catalog identities."""

import hashlib
import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app.markiv_backup.library import scan_library
from aps_midi_prep_tool_app.markiv_backup.metadata import MetadataCatalog


def _write(root, name, payload=b'preserved bytes'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _record(root, original, destination, payload=b'preserved bytes', kind='MIDI'):
    _write(root, destination, payload)
    return {'source': original, 'destination': destination, 'kind': kind,
            'metadata': {'title': 'Earlier title'}, 'size': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest(), 'status': 'verified'}


def _manifest(root, records, **extras):
    data = {'schema_version': 1, 'files': records, 'albums': [], 'derivatives': [], **extras}
    _write(root, 'manifest.json', json.dumps(data).encode())
    return data


def test_archive_scan_preserves_original_identity_catalog_links_and_existing_hierarchy(tmp_path, monkeypatch):
    root = tmp_path / 'backup'
    records = [
        _record(root, 'songs/pianosoft/12/song.mid', 'PianoSoft/Album/01 - Song.mid'),
        _record(root, 'songs/audio/7/track-02.WAV', 'Audio/Disc/02 - Track.WAV', kind='WAV'),
        _record(root, 'local/pgsql/data/base/1/12', '_metadata/original/local/pgsql/data/base/1/12', kind='Metadata'),
    ]
    derivative = _record(root, records[0]['source'], 'Generated/duplicate.mid')
    _manifest(root, records, derivatives=[derivative], albums=[
        {'name': 'Album', 'category': 'PianoSoft', 'source': 'songs/pianosoft/12',
         'destination': 'PianoSoft/Album', 'metadata': {}},
        {'name': 'Disc', 'category': 'Audio', 'source': 'songs/audio/7',
         'destination': 'Audio/Disc', 'metadata': {}},
    ])
    link = {'midi_path': '/home/songs/pianosoft/12/song.mid',
            'wave_path': '/home/songs/audio/7/track-02.WAV', 'offsets': '-35 -31', 'cdtoc': 'DISC'}
    catalog = MetadataCatalog(
        tracks={records[0]['source']: {'title': 'Current title', 'type': 'SPS'},
                records[1]['source']: {'track_number': 2, 'cd_id': 'DISC'}},
        albums={'songs/pianosoft/12': {'cd_id': 'DISC'}}, sync_links=[link])
    calls = []

    def load(path):
        calls.append(path)
        return catalog

    monkeypatch.setattr('aps_midi_prep_tool_app.markiv_backup.metadata.load_metadata', load)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    plan = scan_library(root)
    assert plan.source_backup and plan.sync_links == [link]
    assert calls == [root / '_metadata/original']
    assert {item.source for item in plan.files} == {record['destination'] for record in records}
    assert {item.destination for item in plan.files} == {record['destination'] for record in records}
    by_original = {item.original_source: item for item in plan.files}
    midi = by_original[records[0]['source']]
    assert midi.metadata['title'] == 'Current title'
    assert midi.metadata['cd_id'] == 'DISC'
    assert midi.expected_sha256 == records[0]['sha256']
    assert plan.albums[0].source == 'PianoSoft/Album'
    assert plan.albums[0].metadata['original_source'] == 'songs/pianosoft/12'
    assert plan.scan_errors == []
    assert {path: path.read_bytes() for path in root.rglob('*') if path.is_file()} == before


def test_raw_scan_exposes_link_metadata_and_album_cd_id(tmp_path, monkeypatch):
    _write(tmp_path, 'songs/pianosoft/2/song.mid')
    catalog = MetadataCatalog(albums={'songs/pianosoft/2': {'cd_id': 'DISC'}},
                              sync_links=[{'midi_path': '/home/songs/pianosoft/2/song.mid'}])
    monkeypatch.setattr('aps_midi_prep_tool_app.markiv_backup.metadata.load_metadata', lambda _: catalog)
    plan = scan_library(tmp_path)
    assert not plan.source_backup
    assert plan.sync_links == catalog.sync_links
    assert plan.files[0].original_source == plan.files[0].source
    assert plan.files[0].metadata['cd_id'] == 'DISC'


@pytest.mark.parametrize('path', ['../outside.mid', '/tmp/outside.mid', 'C:/outside.mid', r'..\outside.mid'])
@pytest.mark.parametrize('field', ['source', 'destination'])
def test_archive_rejects_unsafe_manifest_paths(tmp_path, path, field):
    record = _record(tmp_path, 'songs/user/1/song.mid', 'User/Album/song.mid')
    record[field] = path
    _manifest(tmp_path, [record])
    with pytest.raises(ValueError, match='Unsafe backup manifest path'):
        scan_library(tmp_path)


@pytest.mark.parametrize('field,value', [('schema_version', 99), ('files', {}), ('albums', {}), ('derivatives', {})])
def test_archive_rejects_unsupported_manifest_structures(tmp_path, field, value):
    record = _record(tmp_path, 'songs/user/1/song.mid', 'User/Album/song.mid')
    data = _manifest(tmp_path, [record])
    data[field] = value
    (tmp_path / 'manifest.json').write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Unrecognized'):
        scan_library(tmp_path)


def test_archive_reports_missing_changed_and_unverified_originals_without_using_generated_copies(tmp_path):
    records = [_record(tmp_path, f'songs/user/1/{name}.mid', f'User/Album/{name}.mid')
               for name in ['keep', 'missing', 'changed', 'unverified']]
    (tmp_path / records[1]['destination']).unlink()
    (tmp_path / records[2]['destination']).write_bytes(b'wrong size')
    records[3]['status'] = 'pending'
    derivative = _record(tmp_path, records[1]['source'], 'Generated/missing.mid')
    _manifest(tmp_path, records, derivatives=[derivative])
    plan = scan_library(tmp_path)
    assert [item.source for item in plan.files] == ['User/Album/keep.mid']
    assert len(plan.scan_errors) == 3


def test_archive_accepts_verified_replacement_only_when_original_was_intentionally_removed(tmp_path):
    record = _record(tmp_path, 'songs/user/1/song.FIL', 'User/Album/song.FIL', kind='E-SEQ')
    (tmp_path / record['destination']).unlink()
    replacement = _record(tmp_path, record['source'], 'User/Album/song.mid')
    replacement['original'] = record['destination']
    record.update(status='replaced', replacement=replacement['destination'])
    _manifest(tmp_path, [record], derivatives=[replacement],
              options={'convert_eseq': True, 'keep_originals': False})
    plan = scan_library(tmp_path)
    item, = plan.files
    assert item.source == replacement['destination']
    assert item.original_source == record['source']
    assert item.kind == 'MIDI'
    assert item.expected_sha256 == replacement['sha256']


@pytest.mark.parametrize('link_parent', [False, True])
def test_archive_does_not_follow_links(tmp_path, link_parent):
    root = tmp_path / 'backup'
    record = _record(root, 'songs/user/1/song.mid', 'User/Album/song.mid')
    _manifest(root, [record])
    target = root / ('User/Album' if link_parent else record['destination'])
    outside = tmp_path / ('external' if link_parent else 'external.mid')
    target.rename(outside)
    try:
        target.symlink_to(outside, target_is_directory=link_parent)
    except (OSError, NotImplementedError):
        pytest.skip('Creating symbolic links is not available')
    with pytest.raises(ValueError, match='Path escapes|Symbolic links'):
        scan_library(root)
