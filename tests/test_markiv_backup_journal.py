"""Durable progress scales linearly and survives interrupted backup runs."""

import json
import pytest

from aps_midi_prep_tool_app.markiv_backup import backup
from aps_midi_prep_tool_app.markiv_backup.library import BackupPlan, PlannedFile


class PowerLoss(BaseException):
    """Bypass orderly exception cleanup to leave a recoverable journal."""


def _plan(tmp_path, count):
    source = tmp_path / 'source'
    source.mkdir(exist_ok=True)
    files = []
    for index in range(count):
        name = f'recording-{index:04}.FIL'
        path = source / name
        path.write_bytes(b'original music')
        files.append(PlannedFile(name, name, path.stat().st_size, 'E-SEQ', {},
                                 path.stat().st_mtime_ns))
    return BackupPlan(source, files, [], [], sum(item.size for item in files))


@pytest.mark.parametrize('convert', [False, True])
def test_manifest_serialization_bytes_grow_linearly(tmp_path, monkeypatch, convert):
    plan = _plan(tmp_path, 128)
    atomic = backup._atomic_json
    dumps = json.dumps
    totals = []
    snapshots = []
    monkeypatch.setattr(backup, 'is_conversion_candidate', lambda *args: True)
    monkeypatch.setattr(backup, 'convert_eseq_bytes_to_midi_bytes', lambda *args, **kwargs: b'MIDI')

    def measure_snapshot(path, value):
        atomic(path, value)
        totals[-1] += path.stat().st_size
        snapshots[-1] += 1

    def measure_event(value, **kwargs):
        result = dumps(value, **kwargs)
        totals[-1] += len(result.encode('utf-8')) + 1
        return result

    monkeypatch.setattr(backup, '_atomic_json', measure_snapshot)
    monkeypatch.setattr(backup.json, 'dumps', measure_event)
    for count in (32, 64, 128):
        subset = BackupPlan(plan.source, plan.files[:count], [], [],
                            sum(item.size for item in plan.files[:count]))
        totals.append(0)
        snapshots.append(0)
        result = backup.run_backup(subset, tmp_path / f'target-{count}',
                                   convert_eseq=convert, keep_originals=False)
        assert result.status == 'complete'
        assert not (result.folder / backup.JOURNAL).exists()
        assert backup.verify_backup(result.folder) == []
    assert totals[1] < totals[0] * 2.5, totals
    assert totals[2] < totals[1] * 2.5, totals
    assert max(snapshots) <= 8, snapshots


def test_interruption_replays_verified_files_and_ignores_partial_tail(tmp_path):
    plan = _plan(tmp_path, 8)

    def interrupt(event):
        if event['completed'] == 3:
            raise PowerLoss

    target = tmp_path / 'backup'
    with pytest.raises(PowerLoss):
        backup.run_backup(plan, target, progress=interrupt)
    folder, = target.iterdir()
    snapshot = json.loads((folder / backup.MANIFEST).read_text())
    assert all(record['status'] == 'pending' for record in snapshot['files'])
    with (folder / backup.JOURNAL).open('ab') as stream:
        stream.write(b'{"sequence":4,"record":')
    recovered = backup._load_manifest(folder)
    assert [record['status'] for record in recovered['files']] == ['verified'] * 3 + ['pending'] * 5
    errors = backup.verify_backup(folder)
    assert len(errors) == 6
    assert 'not complete' in errors[0]
    assert all('File was not verified' in error for error in errors[1:])
    (folder / plan.files[0].destination).write_bytes(b'x' * plan.files[0].size)
    assert any('checksum' in error for error in backup.verify_backup(folder))


def test_snapshot_replay_does_not_duplicate_updates_or_errors(tmp_path, monkeypatch):
    plan = _plan(tmp_path, 32)
    (plan.source / plan.files[0].source).unlink()
    save = backup._ManifestJournal.snapshot
    snapshots = 0

    def interrupt_after_checkpoint(journal):
        nonlocal snapshots
        save(journal)
        snapshots += 1
        if snapshots == 2:
            raise PowerLoss

    monkeypatch.setattr(backup._ManifestJournal, 'snapshot', interrupt_after_checkpoint)
    target = tmp_path / 'backup'
    with pytest.raises(PowerLoss):
        backup.run_backup(plan, target)
    folder, = target.iterdir()
    snapshot = json.loads((folder / backup.MANIFEST).read_text())
    assert snapshot['journal_sequence'] > 0
    assert len(snapshot['errors']) == 1
    assert backup._load_manifest(folder) == snapshot


@pytest.mark.parametrize('stage', ['before_removal', 'after_removal'])
def test_interrupted_midi_only_removal_recovers_verified_replacement(tmp_path, monkeypatch, stage):
    plan = _plan(tmp_path, 1)
    append = backup._ManifestJournal.append
    monkeypatch.setattr(backup, 'is_conversion_candidate', lambda *args: True)
    monkeypatch.setattr(backup, 'convert_eseq_bytes_to_midi_bytes', lambda *args, **kwargs: b'MIDI')

    def interrupt(journal, collection, index, record):
        if stage == 'after_removal' and record['status'] == 'replaced':
            raise PowerLoss
        append(journal, collection, index, record)
        if stage == 'before_removal' and record['status'] == 'replacement_pending':
            raise PowerLoss

    monkeypatch.setattr(backup._ManifestJournal, 'append', interrupt)
    target = tmp_path / 'backup'
    with pytest.raises(PowerLoss):
        backup.run_backup(plan, target, convert_eseq=True, keep_originals=False)
    folder, = target.iterdir()
    manifest = backup._load_manifest(folder)
    record, = manifest['files']
    derivative, = manifest['derivatives']
    assert record['status'] == 'replacement_pending'
    assert derivative['status'] == 'verified'
    assert (folder / record['destination']).exists() == (stage == 'before_removal')
    assert (plan.source / record['source']).read_bytes() == b'original music'
    assert backup.verify_backup(folder) == ['Backup is not complete (status: in_progress)']
    (folder / derivative['destination']).write_bytes(b'bad!')
    assert any('checksum' in error for error in backup.verify_backup(folder))


def test_final_snapshot_failure_preserves_replayable_journal(tmp_path, monkeypatch):
    plan = _plan(tmp_path, 2)
    save = backup._atomic_json

    def fail_final_snapshot(path, manifest):
        if manifest['status'] == 'complete':
            raise OSError('Cannot publish final snapshot')
        save(path, manifest)

    monkeypatch.setattr(backup, '_atomic_json', fail_final_snapshot)
    target = tmp_path / 'backup'
    with pytest.raises(OSError, match='final snapshot'):
        backup.run_backup(plan, target)
    folder, = target.iterdir()
    assert (folder / '.incomplete').exists()
    assert backup.verify_backup(folder) == ['Backup is not complete (status: in_progress)']


@pytest.mark.parametrize('broken_line', [b'not JSON\n', b'{"sequence":99}\n'])
def test_complete_corrupt_journal_lines_are_reported(tmp_path, broken_line):
    plan = _plan(tmp_path, 2)

    def interrupt(event):
        if event['completed'] == 1:
            raise PowerLoss

    target = tmp_path / 'backup'
    with pytest.raises(PowerLoss):
        backup.run_backup(plan, target, progress=interrupt)
    folder, = target.iterdir()
    with (folder / backup.JOURNAL).open('ab') as stream:
        stream.write(broken_line)
    with pytest.raises(ValueError, match='progress journal'):
        backup.verify_backup(folder)


@pytest.mark.parametrize('destination', [backup.JOURNAL, backup.MANIFEST + '.tmp'])
def test_progress_filenames_are_reserved(tmp_path, destination):
    plan = _plan(tmp_path, 1)
    plan.files[0].destination = destination
    with pytest.raises(ValueError, match='Reserved backup filename'):
        backup.run_backup(plan, tmp_path / 'backup')
