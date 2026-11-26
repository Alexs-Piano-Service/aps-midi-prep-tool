"""Standalone performances join playback exports without copying companion MIDI twice."""

from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import mido
import pytest

from aps_midi_prep_tool_app.audio_preparation import ENCODED_WAV, MIDI_MP3
from aps_midi_prep_tool_app.markiv_backup import backup
from aps_midi_prep_tool_app.markiv_backup.library import CancelledError, scan_library
from aps_midi_prep_tool_app.markiv_backup.solo_export import prepare_solo_midi_copies


def _midi(*, notes=True):
    midi = mido.MidiFile(type=0)
    track = mido.MidiTrack()
    if notes:
        track.extend([mido.Message('note_on', note=64, velocity=75),
                      mido.Message('note_off', note=64, time=480)])
    track.append(mido.MetaMessage('end_of_track'))
    midi.tracks.append(track)
    stream = io.BytesIO()
    midi.save(file=stream)
    return stream.getvalue()


def _source(tmp_path, names=('Solo.v1.mid',)):
    source = tmp_path / 'source'
    album = source / 'songs/user/1'
    album.mkdir(parents=True)
    for name in names:
        (album / name).write_bytes(_midi())
    return source


def _session(tmp_path, source):
    plan = scan_library(source)
    folder = tmp_path / 'prepared'
    folder.mkdir()
    records = [dict(asdict(item), status='referenced',
                    sha256=hashlib.sha256((source / item.source).read_bytes()).hexdigest())
               for item in plan.files]
    # Direct helper tests use original paths as their verified destinations.
    for item, record in zip(plan.files, records):
        item.destination = item.source
        record['destination'] = item.source
    manifest = {'files': records, 'derivatives': [], 'errors': [], 'warnings': [],
                'conversion_errors': []}
    return plan, folder, records, manifest, backup._ManifestJournal(folder, manifest)


@pytest.mark.parametrize('output', (MIDI_MP3, ENCODED_WAV))
@pytest.mark.parametrize('archive', (False, True))
def test_solo_performance_is_copied_byte_exactly_for_both_playback_formats(tmp_path, output, archive):
    source = _source(tmp_path)
    original = (source / 'songs/user/1/Solo.v1.mid').read_bytes()
    if archive:
        source = backup.run_backup(scan_library(source), tmp_path / 'archives').folder
    result = backup.run_backup(scan_library(source), tmp_path / 'output', audio_output=output)
    assert (result.status, result.decoded, result.audio_skipped) == ('complete', 1, 0), result.errors
    manifest = json.loads((result.folder / 'manifest.json').read_text())
    derivative, = manifest['derivatives']
    assert derivative['destination'] == 'MIDI/User/User 1/Solo.v1.mid'
    assert derivative['status'] == 'verified'
    assert (result.folder / derivative['destination']).read_bytes() == original
    assert derivative['sha256'] == hashlib.sha256(original).hexdigest()
    assert manifest['files'][0]['playback_prepare']['association'] == 'solo_midi'
    assert backup.verify_backup(result.folder) == []
    if archive:
        source.rename(tmp_path / 'archive-disconnected')
        assert backup.verify_backup(result.folder) == []


def test_solo_export_excludes_paired_and_missing_required_companion_midis(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.markiv_backup import playback

    source = _source(tmp_path, ('Solo.mid', 'Paired.mid', 'Missing.mid'))
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    by_name = {Path(item.source).name: item for item in plan.files}
    monkeypatch.setattr(playback, 'resolve_playback_pairs', lambda *_args, **_kwargs: SimpleNamespace(
        pairs=(SimpleNamespace(midi=by_name['Paired.mid']),),
        issues=(SimpleNamespace(midi_source=by_name['Missing.mid'].original_source),)))
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (1, 0)
    assert [Path(row['original']).name for row in manifest['derivatives']] == ['Solo.mid']


def test_missing_explicit_companion_is_not_silently_exported_as_solo(tmp_path):
    source = _source(tmp_path)
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    plan.files[0].metadata['add_file'] = 'Missing.wav'
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (0, 0)
    assert not manifest['derivatives']


def test_metadata_wrapper_and_setup_only_midi_are_not_playback_songs(tmp_path):
    source = _source(tmp_path, ('Wrapper.mid', 'Setup.mid'))
    (source / 'songs/user/1/Wrapper.mid').write_bytes(_midi(notes=False))
    setup = mido.MidiFile(type=0)
    setup.tracks.append(mido.MidiTrack([mido.Message('program_change', program=0)]))
    setup.save(source / 'songs/user/1/Setup.mid')
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (0, 0)
    assert not manifest['derivatives']
    assert all(row['playback_prepare']['status'] == 'not_applicable' for row in records)
    assert not manifest['errors']


@pytest.mark.parametrize('generate', (False, True))
def test_solo_copies_keep_catalog_names_and_collision_suffixes(tmp_path, generate):
    source = _source(tmp_path, ('A very long song.mid', 'A very long song.midi'))
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    assert prepare_solo_midi_copies(
        plan, folder, source, records, manifest, journal,
        generate_yamaha_catalogs=generate, cataloged_sources={'songs/user/1'}) == (2, 0)
    paths = [Path(row['destination']) for row in manifest['derivatives']]
    assert len({path.name for path in paths}) == 2
    assert all(len(path.stem) <= 8 and path.suffix == '.mid' for path in paths)
    assert all((folder / path).read_bytes() == _midi() for path in paths)


def test_solo_midi_tampering_after_verification_prevents_output(tmp_path):
    source = _source(tmp_path)
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    path = source / plan.files[0].source
    path.write_bytes(path.read_bytes() + b'changed')
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (0, 1)
    assert records[0]['playback_prepare']['status'] == 'failed'
    assert not manifest['derivatives']
    assert 'checksum' in manifest['errors'][0]


def test_solo_export_uses_validated_bytes_if_original_is_replaced_during_publication(tmp_path, monkeypatch):
    source = _source(tmp_path)
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    original = source / plan.files[0].source
    expected = original.read_bytes()
    write = backup._write_derivative

    def replace_source(path, payload, cancel):
        original.write_bytes(b'changed outside APS')
        return write(path, payload, cancel)

    monkeypatch.setattr(backup, '_write_derivative', replace_source)
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (1, 0)
    assert (folder / manifest['derivatives'][0]['destination']).read_bytes() == expected


def test_solo_export_keeps_existing_destination_and_records_publication_failure(tmp_path, monkeypatch):
    source = _source(tmp_path)
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    write = backup._write_derivative
    occupied = []

    def occupy(path, payload, cancel):
        path.write_bytes(b'An unrelated existing file')
        occupied.append(path)
        return write(path, payload, cancel)

    monkeypatch.setattr(backup, '_write_derivative', occupy)
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (0, 1)
    assert occupied[0].read_bytes() == b'An unrelated existing file'
    assert manifest['derivatives'][0]['status'] == 'failed'
    assert not list(folder.rglob('*.partial'))


def test_solo_export_checks_space_before_reading_midi(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.markiv_backup import solo_export

    source = _source(tmp_path)
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    monkeypatch.setattr(solo_export.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    assert prepare_solo_midi_copies(plan, folder, source, records, manifest, journal) == (0, 1)
    assert 'free space' in manifest['errors'][0]
    assert not manifest['derivatives']


def test_solo_cancellation_keeps_completed_files_and_journals_pending_work(tmp_path, monkeypatch):
    source = _source(tmp_path, ('First.mid', 'Second.mid'))
    plan, folder, records, manifest, journal = _session(tmp_path, source)
    cancel = threading.Event()
    write = backup._write_derivative
    calls = 0

    def stop_second(path, payload, cancellation):
        nonlocal calls
        calls += 1
        if calls == 2:
            cancel.set()
        return write(path, payload, cancellation)

    monkeypatch.setattr(backup, '_write_derivative', stop_second)
    with pytest.raises(CancelledError):
        prepare_solo_midi_copies(plan, folder, source, records, manifest, journal, cancel=cancel)
    assert [row['status'] for row in manifest['derivatives']] == ['verified', 'cancelled']
    assert [row['playback_prepare']['status'] for row in records] == ['prepared', 'cancelled']
    assert len(list(folder.rglob('*.mid'))) == 1
    assert not list(folder.rglob('*.partial'))
    assert not manifest['errors']
