"""Verified MIDI conversion with optional retention of backed-up E-SEQ originals."""

import hashlib
import errno
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.app_info import APP_NAME, APP_VERSION
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.markiv_backup import backup
from aps_midi_prep_tool_app.markiv_backup.library import safe_name, scan_library
from aps_midi_prep_tool_app.markiv_backup.metadata import MetadataCatalog
from aps_midi_prep_tool_app.midi_type0_converter import _parse_track_events


def _fil(title='Header title'):
    # Synthetic Mark IV stream: program, pedal, note, 384 ticks, release.
    stream = (b'\xF1\x00\xC0\x28\xB0\x40\x7F\x90\x3C\x40'
              b'\xF4\x00\x03\x80\x3C\x00\xB0\x40\x00\xF2')
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, 'little')
    header[7:15] = b'COM-ESEQ'
    header[0x1F:0x23] = len(stream).to_bytes(4, 'little')
    header[0x24] = header[0x33] = 88
    header[0x34:0x36] = b'\x04\x04'
    header[0x57:0x77] = title.encode('latin-1').ljust(32, b' ')
    return bytes(header) + stream


def _source(tmp_path, files):
    source = tmp_path / 'source'
    for name, payload in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return source


def _manifest(result):
    return json.loads((result.folder / backup.MANIFEST).read_text())


@pytest.mark.parametrize('keep_originals', [True, False])
@pytest.mark.parametrize('extension', ['FIL', 'bin'])
def test_conversion_headroom_is_required_before_any_copy(tmp_path, monkeypatch, keep_originals, extension):
    source = _source(tmp_path, {f'songs/user/1/SONG.{extension}': _fil()})
    plan = scan_library(source)
    assert plan.files[0].convertible_eseq
    # Enough for the old originals-only preflight, but not the conversion reserve.
    free = plan.total_bytes + backup._manifest_allowance(plan)
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=free))
    target = tmp_path / 'backup'
    monkeypatch.setattr(backup, '_hash', lambda *args: pytest.fail('Copy started before space validation'))
    with pytest.raises(ValueError, match='MIDI conversions'):
        backup.run_backup(plan, target, convert_eseq=True, keep_originals=keep_originals)
    assert not target.exists()
    assert backup._validate_target(plan, target) == target


def test_conversion_rechecks_space_after_copy_and_keeps_original(tmp_path, monkeypatch):
    source = _source(tmp_path, {'songs/user/1/SONG.FIL': _fil()})
    plan = scan_library(source)
    free = 1024 ** 3
    def progress(event):
        nonlocal free
        if event['completed'] == len(plan.files):
            free = 0  # Another writer consumed the remaining destination space.
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=free))
    monkeypatch.setattr(backup, 'convert_eseq_bytes_to_midi_bytes',
                        lambda *args, **kwargs: pytest.fail('Conversion started without enough space'))
    result = backup.run_backup(plan, tmp_path / 'backup', progress=progress,
                               convert_eseq=True, keep_originals=False)
    assert (result.status, result.verified, result.converted) == ('incomplete', 1, 0)
    assert 'free space' in result.errors[0]
    assert (result.folder / plan.files[0].destination).read_bytes() == _fil()
    assert not list(result.folder.rglob('*.mid'))


def _assert_originals(result, source, originals):
    records = _manifest(result)['files']
    assert {record['source'] for record in records} == set(originals)
    for record in records:
        expected = originals[record['source']]
        assert (source / record['source']).read_bytes() == expected
        assert (result.folder / record['destination']).read_bytes() == expected
        assert record['sha256'] == hashlib.sha256(expected).hexdigest()
        assert record['status'] == 'verified'


@pytest.mark.parametrize('extension', ['FIL', 'MDA', 'bin', 'MID'])
def test_content_detection_adds_verified_midi_for_fil_mda_and_unusual_extensions(tmp_path, extension):
    data = _fil()
    if extension == 'MDA':
        data = convert_midi_bytes_to_eseq_bytes(
            convert_eseq_bytes_to_midi_bytes(data),
            container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA,
        )
    originals = {f'songs/user/1/SONG.{extension}': data}
    source = _source(tmp_path, originals)
    events = []

    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               progress=events.append, convert_eseq=True)

    assert (result.status, result.verified, result.converted) == ('complete', 1, 1)
    _assert_originals(result, source, originals)
    manifest = _manifest(result)
    assert manifest['application'] == {'name': APP_NAME, 'version': APP_VERSION}
    derivative, = manifest['derivatives']
    midi = (result.folder / derivative['destination']).read_bytes()
    assert midi == convert_eseq_bytes_to_midi_bytes(data)
    assert derivative['sha256'] == hashlib.sha256(midi).hexdigest()
    assert derivative['size'] == len(midi)
    assert derivative['destination'] != derivative['original']
    parsed, _end = _parse_track_events(midi[22:])
    notes = [(tick, raw) for tick, _, raw in parsed if raw[0] >> 4 in (8, 9)]
    assert notes == [(0, b'\x90\x3C\x40'), (384, b'\x80\x3C\x00')]
    assert backup.verify_backup(result.folder) == []
    conversions = [event for event in events if event['event'] == 'conversion']
    assert conversions[-1]['converted'] == 1
    assert conversions[-1]['conversion_total'] == 1
    for event in conversions:
        assert event['completed'] == event['total'] == 1
        assert event['bytes_done'] == event['bytes_total'] == len(data) * 2


def test_conversion_is_opt_in(tmp_path):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup')

    assert result.status == 'complete'
    assert result.converted == 0
    assert _manifest(result)['derivatives'] == []
    _assert_originals(result, source, originals)
    assert backup.verify_backup(result.folder) == []


def test_invalid_extensions_packages_and_metadata_are_not_blindly_converted(tmp_path):
    originals = {
        'songs/user/1/WRONG.FIL': b'this is not E-SEQ',
        'songs/user/1/WRONG.MDA': b'this is not MDA',
        'songs/user/1/PIECE.pspg': _fil(),
        'songs/user/1/IMAGE.bmp': b'bitmap image bytes',
        'songs/tool/catalog.dat': _fil(),
    }
    source = _source(tmp_path, originals)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert (result.status, result.converted) == ('complete', 0)
    _assert_originals(result, source, originals)


def test_derivatives_avoid_existing_midi_and_other_derivatives(tmp_path):
    originals = {
        'songs/user/1/SONG.FIL': _fil(),
        'songs/user/1/SONG.MDA': _fil(),
        'songs/user/1/SONG.mid': b'original MIDI companion',
        'songs/user/1/SONG (2).MID': b'another original MIDI',
    }
    source = _source(tmp_path, originals)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert result.converted == 2
    _assert_originals(result, source, originals)
    manifest = _manifest(result)
    paths = [record['destination'].casefold() for record in manifest['files'] + manifest['derivatives']]
    assert len(paths) == len(set(paths))
    assert {Path(record['destination']).name for record in manifest['derivatives']} == {'SONG (3).mid', 'SONG (4).mid'}
    assert backup.verify_backup(result.folder) == []


def test_derivative_name_does_not_replace_directory(tmp_path):
    originals = {'songs/user/1/SONG.FIL': _fil(),
                 'songs/user/1/SONG.mid/asset.bin': b'original asset'}
    source = _source(tmp_path, originals)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert (result.status, result.converted) == ('complete', 1)
    _assert_originals(result, source, originals)
    assert _manifest(result)['derivatives'][0]['destination'].endswith('SONG (2).mid')


def test_recovered_catalog_title_is_used_in_midi_and_original_keeps_header(tmp_path, monkeypatch):
    originals = {'songs/user/1/SONG.FIL': _fil('Original title')}
    source = _source(tmp_path, originals)
    catalog = MetadataCatalog(
        albums={'songs/user/1': {'title': 'Recovered album'}},
        tracks={'songs/user/1/SONG.FIL': {'title': 'Edited on piano', 'track_number': 4,
                                       'metadata_source': 'postgresql-7.3'}},
    )
    monkeypatch.setattr('aps_midi_prep_tool_app.markiv_backup.metadata.load_metadata', lambda source: catalog)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    _assert_originals(result, source, originals)
    derivative, = _manifest(result)['derivatives']
    assert derivative['destination'] == 'User/Recovered album/04 - Edited on piano.mid'
    midi = (result.folder / derivative['destination']).read_bytes()
    assert midi == convert_eseq_bytes_to_midi_bytes(_fil('Original title'), title_override='Edited on piano')


def test_mda_imports_outside_songs_are_included(tmp_path):
    originals = {'songs/user/1/SONG.FIL': _fil(), 'imports/LEGACY.MDA': _fil()}
    source = _source(tmp_path, originals)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert (result.status, result.verified, result.converted) == ('complete', 2, 2)
    _assert_originals(result, source, originals)


def test_failed_conversion_keeps_original_and_continues_other_songs(tmp_path):
    originals = {'songs/user/1/BROKEN.FIL': b'\xFE' + b'\0' * 6 + b'COM-ESEQ',
                 'songs/user/1/VALID.FIL': _fil()}
    source = _source(tmp_path, originals)

    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert (result.status, result.verified, result.converted) == ('incomplete', 2, 1)
    _assert_originals(result, source, originals)
    manifest = _manifest(result)
    assert len(manifest['conversion_errors']) == len(result.errors) == 1
    assert 'BROKEN.FIL' in result.errors[0]
    failed, complete = manifest['derivatives']
    assert failed['status'] == 'failed' and complete['status'] == 'verified'
    assert not (result.folder / failed['destination']).exists()
    assert (result.folder / '.incomplete').is_file()
    assert any('not verified' in error for error in backup.verify_backup(result.folder))


@pytest.mark.parametrize('keep_originals', [False, True])
def test_cancel_during_conversion_preserves_all_originals_and_no_partial_midi(tmp_path, monkeypatch, keep_originals):
    originals = {'songs/user/1/OLD.FIL': _fil(), 'songs/user/1/VOICE.WAV': b'audio original'}
    source = _source(tmp_path, originals)
    cancel = threading.Event()

    def convert(*args, **kwargs):
        cancel.set()
        return convert_eseq_bytes_to_midi_bytes(*args, **kwargs)

    monkeypatch.setattr(backup, 'convert_eseq_bytes_to_midi_bytes', convert)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', cancel=cancel,
                               convert_eseq=True, keep_originals=keep_originals)

    assert (result.status, result.verified, result.converted) == ('cancelled', 2, 0)
    _assert_originals(result, source, originals)
    derivative, = _manifest(result)['derivatives']
    assert derivative['status'] == 'cancelled'
    assert not (result.folder / derivative['destination']).exists()
    assert not list(result.folder.rglob('*.partial'))
    assert (result.folder / '.incomplete').is_file()


def test_cancel_during_derivative_write_removes_partial(tmp_path, monkeypatch):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)
    cancel = threading.Event()
    real_hash = backup._hash

    def interrupted_hash(path, *args, **kwargs):
        if path.name.startswith('.convert-'):
            cancel.set()
        return real_hash(path, *args, **kwargs)

    monkeypatch.setattr(backup, '_hash', interrupted_hash)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', cancel=cancel, convert_eseq=True)

    assert (result.status, result.verified, result.converted) == ('cancelled', 1, 0)
    _assert_originals(result, source, originals)
    assert not list(result.folder.rglob('*.partial'))
    assert not list(result.folder.rglob('*.mid'))


@pytest.mark.parametrize('change', ['same_size_corruption', 'truncate', 'missing', 'traversal', 'symlink'])
def test_verify_detects_derivative_damage_and_unsafe_paths(tmp_path, change):
    source = _source(tmp_path, {'songs/user/1/OLD.FIL': _fil()})
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)
    manifest = _manifest(result)
    derivative = manifest['derivatives'][0]
    path = result.folder / derivative['destination']
    if change == 'same_size_corruption':
        path.write_bytes(b'x' * path.stat().st_size)
    elif change == 'truncate':
        path.write_bytes(b'bad')
    elif change == 'missing':
        path.unlink()
    elif change == 'traversal':
        derivative['destination'] = '../../outside.mid'
        (result.folder / backup.MANIFEST).write_text(json.dumps(manifest))
    elif change == 'symlink':
        path.unlink()
        outside = tmp_path / 'outside.mid'
        outside.write_bytes(convert_eseq_bytes_to_midi_bytes(_fil()))
        path.symlink_to(outside)

    assert backup.verify_backup(result.folder)


def test_verification_accepts_original_tool_manifests_without_derivatives(tmp_path):
    source = _source(tmp_path, {'songs/user/1/OLD.FIL': _fil()})
    result = backup.run_backup(scan_library(source), tmp_path / 'backup')
    manifest = _manifest(result)
    for field in ('derivatives', 'conversion_errors', 'converted', 'options'):
        manifest.pop(field)
    (result.folder / backup.MANIFEST).write_text(json.dumps(manifest))

    assert backup.verify_backup(result.folder) == []


@pytest.mark.parametrize('prefix', ['.copy-', '.convert-'])
def test_failed_publication_removes_empty_reservation_and_partial(tmp_path, monkeypatch, prefix):
    source = _source(tmp_path, {'songs/user/1/OLD.FIL': _fil()})
    replace = backup.os.replace

    def fail_publication(src, dst):
        if Path(src).name.startswith(prefix):
            raise PermissionError('Could not publish verified file')
        return replace(src, dst)

    monkeypatch.setattr(backup.os, 'replace', fail_publication)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert result.status == 'incomplete'
    assert result.converted == 0
    assert result.verified == (1 if prefix == '.convert-' else 0)
    manifest = _manifest(result)
    failed = (manifest['derivatives'] if prefix == '.convert-' else manifest['files'])[0]
    assert failed['status'] == 'failed'
    assert not (result.folder / failed['destination']).exists()
    assert not list(result.folder.rglob('*.partial'))


def test_conversion_disk_full_reports_incomplete_and_keeps_verified_originals(tmp_path, monkeypatch):
    originals = {'songs/user/1/OLD.FIL': _fil(), 'songs/user/1/VOICE.WAV': b'audio original'}
    source = _source(tmp_path, originals)
    mkstemp = backup.tempfile.mkstemp

    def full_destination(*args, **kwargs):
        if kwargs.get('prefix') == '.convert-':
            raise OSError(errno.ENOSPC, 'No space left on device')
        return mkstemp(*args, **kwargs)

    monkeypatch.setattr(backup.tempfile, 'mkstemp', full_destination)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', convert_eseq=True)

    assert (result.status, result.verified, result.converted) == ('incomplete', 2, 0)
    _assert_originals(result, source, originals)
    assert any('No space left' in error for error in result.errors)
    assert _manifest(result)['conversion_errors']
    assert (result.folder / '.incomplete').exists()


@pytest.mark.parametrize('keep_originals', [False, True])
def test_failed_derivative_checksum_is_not_published(tmp_path, monkeypatch, keep_originals):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)
    real_hash = backup._hash

    def bad_derivative_hash(path, *args, **kwargs):
        if path.name.startswith('.convert-'):
            return 'bad checksum'
        return real_hash(path, *args, **kwargs)

    monkeypatch.setattr(backup, '_hash', bad_derivative_hash)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               convert_eseq=True, keep_originals=keep_originals)

    assert (result.status, result.verified, result.converted) == ('incomplete', 1, 0)
    _assert_originals(result, source, originals)
    assert not list(result.folder.rglob('*.mid'))
    assert not list(result.folder.rglob('*.partial'))
    assert any('read-back verification' in error for error in result.errors)


@pytest.mark.parametrize('name', ['COM¹', 'LPT².mid', 'COM³.FIL', 'CONIN$', 'CONOUT$.mid'])
def test_portable_names_share_host_windows_device_rules(name):
    assert safe_name(name) == '_' + name


def test_midi_only_replaces_converted_copies_and_keeps_other_assets(tmp_path):
    originals = {'songs/user/1/OLD.FIL': _fil(),
                 'songs/user/1/OLD.mid': convert_eseq_bytes_to_midi_bytes(_fil()),
                 'songs/user/1/OLD.WAV': b'audio original',
                 'songs/user/1/PACKAGE.pspg': _fil()}
    source = _source(tmp_path, originals)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               convert_eseq=True, keep_originals=False)
    assert (result.status, result.verified, result.converted) == ('complete', 4, 1)
    manifest = _manifest(result)
    assert manifest['options'] == {'convert_eseq': True, 'keep_originals': False}
    derivative, = manifest['derivatives']
    assert derivative['destination'].endswith('Header title (2).mid')
    for record in manifest['files']:
        assert (source / record['source']).read_bytes() == originals[record['source']]
        path = result.folder / record['destination']
        if record['source'].endswith('OLD.FIL'):
            assert not path.exists()
            assert record['status'] == 'replaced'
            assert record['replacement'] == derivative['destination']
            assert record['sha256'] == hashlib.sha256(_fil()).hexdigest()
        else:
            assert record['status'] == 'verified'
            assert path.read_bytes() == originals[record['source']]
    assert backup.verify_backup(result.folder) == []
    (result.folder / derivative['destination']).unlink()
    assert backup.verify_backup(result.folder)


def test_keep_originals_off_has_no_effect_without_conversion(tmp_path):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', keep_originals=False)
    _assert_originals(result, source, originals)
    assert result.converted == 0
    assert backup.verify_backup(result.folder) == []


def test_midi_only_failed_conversion_keeps_original_and_converts_next_file(tmp_path):
    originals = {'songs/user/1/BROKEN.FIL': b'\xFE' + b'\0' * 6 + b'COM-ESEQ',
                 'songs/user/1/VALID.FIL': _fil()}
    source = _source(tmp_path, originals)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               convert_eseq=True, keep_originals=False)
    assert (result.status, result.converted) == ('incomplete', 1)
    broken, valid = _manifest(result)['files']
    assert broken['status'] == 'verified'
    assert (result.folder / broken['destination']).read_bytes() == originals[broken['source']]
    assert valid['status'] == 'replaced'
    assert not (result.folder / valid['destination']).exists()
    for name, data in originals.items():
        assert (source / name).read_bytes() == data


def test_midi_only_cancel_after_midi_publication_keeps_original_and_verified_midi(tmp_path, monkeypatch):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)
    cancel = threading.Event()
    write = backup._write_derivative

    def cancel_after_write(*args, **kwargs):
        checksum = write(*args, **kwargs)
        cancel.set()
        return checksum

    monkeypatch.setattr(backup, '_write_derivative', cancel_after_write)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', cancel=cancel,
                               convert_eseq=True, keep_originals=False)
    assert (result.status, result.converted) == ('cancelled', 1)
    _assert_originals(result, source, originals)
    derivative, = _manifest(result)['derivatives']
    assert derivative['status'] == 'verified'
    assert (result.folder / derivative['destination']).is_file()


def test_midi_only_original_removal_failure_keeps_both_files_and_reports_error(tmp_path, monkeypatch):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)
    unlink = Path.unlink

    def refuse_original(path, *args, **kwargs):
        if path.suffix == '.FIL':
            raise PermissionError('Original is locked')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', refuse_original)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               convert_eseq=True, keep_originals=False)
    assert (result.status, result.converted) == ('incomplete', 1)
    _assert_originals(result, source, originals)
    derivative, = _manifest(result)['derivatives']
    assert derivative['status'] == 'verified'
    assert (result.folder / derivative['destination']).is_file()
    assert any('Original is locked' in error for error in result.errors)


def test_midi_only_journal_failure_before_removal_keeps_original(tmp_path, monkeypatch):
    originals = {'songs/user/1/OLD.FIL': _fil()}
    source = _source(tmp_path, originals)
    save = backup._ManifestJournal.append
    failed = False

    def fail_first_verified_derivative(journal, collection, index, record):
        nonlocal failed
        if not failed and collection == 'derivatives' and record['status'] == 'verified':
            failed = True
            raise OSError('Cannot save progress journal')
        save(journal, collection, index, record)

    monkeypatch.setattr(backup._ManifestJournal, 'append', fail_first_verified_derivative)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               convert_eseq=True, keep_originals=False)
    assert (result.status, result.converted) == ('incomplete', 1)
    _assert_originals(result, source, originals)
    assert any('Cannot save progress journal' in error for error in result.errors)


@pytest.mark.parametrize('change', ['no_derivative', 'wrong_original', 'wrong_replacement', 'not_verified'])
def test_midi_only_verification_requires_recorded_verified_replacement(tmp_path, change):
    source = _source(tmp_path, {'songs/user/1/OLD.FIL': _fil()})
    result = backup.run_backup(scan_library(source), tmp_path / 'backup',
                               convert_eseq=True, keep_originals=False)
    manifest = _manifest(result)
    if change == 'no_derivative':
        manifest['derivatives'] = []
    elif change == 'wrong_original':
        manifest['derivatives'][0]['original'] = 'another.FIL'
    elif change == 'wrong_replacement':
        manifest['files'][0]['replacement'] = 'another.mid'
    else:
        manifest['derivatives'][0]['status'] = 'failed'
    (result.folder / backup.MANIFEST).write_text(json.dumps(manifest))
    assert any('no verified MIDI replacement' in error for error in backup.verify_backup(result.folder))
