"""Batch publication protects sources, existing outputs, and recovery data."""

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from aps_midi_prep_tool_app.helpers import file_batch


def _file(directory, name, contents):
    path = directory / name
    path.write_bytes(contents)
    return path


@pytest.fixture
def recovery_locations(monkeypatch):
    locations = []
    original = file_batch.tempfile.mkdtemp

    def create(*args, **kwargs):
        result = original(*args, **kwargs)
        locations.append(Path(result))
        return result

    monkeypatch.setattr(file_batch.tempfile, "mkdtemp", create)
    yield locations
    for path in locations:
        shutil.rmtree(path, ignore_errors=True)


def test_swapped_prepared_inputs_are_frozen_before_any_destination_changes(tmp_path, recovery_locations):
    first = _file(tmp_path, "first.mid", b"original first")
    second = _file(tmp_path, "second.mid", b"original second")
    progress = []
    backups = []

    def backup(destination):
        path = Path(destination)
        backups.append((path.name, path.read_bytes()))

    file_batch.publish_file_batch(
        iter([(first, second), (second, first)]),
        backup_callback=backup,
        progress_callback=lambda index, total, destination: progress.append((index, total, Path(destination).name)),
    )

    assert first.read_bytes() == b"original second"
    assert second.read_bytes() == b"original first"
    assert backups == [("second.mid", b"original second"), ("first.mid", b"original first")]
    assert progress == [(1, 2, "second.mid"), (2, 2, "first.mid")]
    assert recovery_locations and not any(path.exists() for path in recovery_locations)


def test_later_failure_restores_replacements_removes_created_outputs_and_keeps_backups(tmp_path, monkeypatch, recovery_locations):
    first = _file(tmp_path, "first.mid", b"original first")
    second = tmp_path / "new.mid"
    third = _file(tmp_path, "third.mid", b"original third")
    unrelated = _file(tmp_path, "unrelated.txt", b"untouched")
    inputs = [_file(tmp_path, f"prepared-{index}", f"new {index}".encode()) for index in range(3)]
    original_atomic = file_batch.atomic_write_bytes
    backup_paths = []

    def backup(destination):
        source = Path(destination)
        path = source.with_name(source.name + ".backup")
        shutil.copyfile(source, path)
        backup_paths.append(path)

    def fail_third(destination, payload, **kwargs):
        if Path(destination) == third and payload == b"new 2":
            raise OSError("Destination became unavailable")
        return original_atomic(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_third)
    with pytest.raises(file_batch.FileBatchWriteError, match="Destination became unavailable") as caught:
        file_batch.publish_file_batch(zip(inputs, [first, second, third]), backup_callback=backup)

    assert first.read_bytes() == b"original first"
    assert not second.exists()
    assert third.read_bytes() == b"original third"
    assert unrelated.read_bytes() == b"untouched"
    assert [path.read_bytes() for path in backup_paths] == [b"original first", b"original third"]
    assert caught.value.rollback_errors == []
    assert caught.value.recovery_directory == ""
    assert isinstance(caught.value.original_exception, OSError)
    assert not any(path.exists() for path in recovery_locations)


@pytest.mark.parametrize("raised", [False, True])
def test_backup_failure_prevents_all_publication(tmp_path, recovery_locations, raised):
    targets = [_file(tmp_path, f"song-{index}", f"old {index}".encode()) for index in range(2)]
    inputs = [_file(tmp_path, f"staged-{index}", f"new {index}".encode()) for index in range(2)]
    backup_calls = []
    progress = []

    def backup(destination):
        backup_calls.append(Path(destination))
        if len(backup_calls) == 2:
            if raised:
                raise PermissionError("Backup folder is unavailable")
            return "Backup folder is unavailable"

    with pytest.raises(file_batch.FileBatchWriteError, match="Backup folder is unavailable"):
        file_batch.publish_file_batch(
            zip(inputs, targets), backup_callback=backup,
            progress_callback=lambda *args: progress.append(args),
        )

    assert backup_calls == targets
    assert [path.read_bytes() for path in targets] == [b"old 0", b"old 1"]
    assert progress == []
    assert not any(path.exists() for path in recovery_locations)


@pytest.mark.parametrize("alias_kind", ["relative", "symlink"])
def test_duplicate_resolved_destinations_fail_before_backups_or_writes(tmp_path, monkeypatch, recovery_locations, alias_kind):
    destination = _file(tmp_path, "song.mid", b"original")
    staged = _file(tmp_path, "prepared", b"new")
    monkeypatch.chdir(tmp_path)
    alias = Path("song.mid")
    if alias_kind == "symlink":
        alias = tmp_path / "alias.mid"
        try:
            alias.symlink_to(destination)
        except OSError:
            pytest.skip("Symbolic links are unavailable")
    backups = []

    with pytest.raises(file_batch.FileBatchWriteError, match="Duplicate output destination"):
        file_batch.publish_file_batch([(staged, destination), (staged, alias)], backup_callback=backups.append)

    assert destination.read_bytes() == b"original"
    assert backups == []
    assert recovery_locations == []


def test_new_destination_race_keeps_the_arriving_file_and_rolls_back_previous_output(tmp_path, monkeypatch, recovery_locations):
    first = _file(tmp_path, "existing.mid", b"original")
    late = tmp_path / "late.mid"
    staged = _file(tmp_path, "prepared", b"new")
    original_atomic = file_batch.atomic_write_bytes
    publish_flags = []

    def create_race(destination, payload, **kwargs):
        publish_flags.append((Path(destination).name, kwargs.get("replace_existing", True)))
        if Path(destination) == late:
            late.write_bytes(b"another program's file")
        return original_atomic(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", create_race)
    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, late)])

    assert isinstance(caught.value.original_exception, FileExistsError)
    assert first.read_bytes() == b"original"
    assert late.read_bytes() == b"another program's file"
    assert ("late.mid", False) in publish_flags
    assert caught.value.rollback_errors == []
    assert not any(path.exists() for path in recovery_locations)


def test_publication_preserves_destination_symlink_and_existing_permissions(tmp_path, recovery_locations):
    target = _file(tmp_path, "actual.mid", b"original")
    alias = tmp_path / "song.mid"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable")
    target.chmod(stat.S_IREAD | stat.S_IWRITE)
    original_mode = stat.S_IMODE(target.stat().st_mode)
    staged = _file(tmp_path, "prepared", b"new")
    backups = []
    progress = []

    file_batch.publish_file_batch(
        [(staged, alias)],
        backup_callback=lambda path: backups.append((path, Path(path).read_bytes())),
        progress_callback=lambda index, total, path: progress.append((index, total, path)),
    )

    assert alias.is_symlink()
    assert alias.read_bytes() == target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == original_mode
    assert backups == [(str(alias), b"original")]
    assert progress == [(1, 1, str(alias))]
    assert not any(path.exists() for path in recovery_locations)


def test_read_only_later_destination_restores_earlier_write(tmp_path, recovery_locations):
    first = _file(tmp_path, "first.mid", b"original first")
    protected = _file(tmp_path, "protected.mid", b"original protected")
    staged = _file(tmp_path, "prepared", b"new")
    protected.chmod(stat.S_IREAD)
    try:
        if os.access(protected, os.W_OK):
            pytest.skip("Current privileges can write read-only files")
        with pytest.raises(file_batch.FileBatchWriteError):
            file_batch.publish_file_batch([(staged, first), (staged, protected)])
        assert first.read_bytes() == b"original first"
        assert protected.read_bytes() == b"original protected"
        assert not any(path.exists() for path in recovery_locations)
    finally:
        protected.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_rollback_failure_keeps_manifest_and_original_bytes_for_recovery(tmp_path, monkeypatch, recovery_locations):
    first = _file(tmp_path, "first.mid", b"original first")
    second = _file(tmp_path, "second.mid", b"original second")
    staged = _file(tmp_path, "prepared", b"new")
    original_atomic = file_batch.atomic_write_bytes

    def fail_commit_and_restore(destination, payload, **kwargs):
        if Path(destination) == second:
            raise OSError("Storage disconnected")
        if Path(destination) == first and payload == b"original first":
            raise PermissionError("Original destination cannot be restored")
        return original_atomic(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_commit_and_restore)
    with pytest.raises(file_batch.FileBatchWriteError, match="Storage disconnected") as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    error = caught.value
    assert len(error.rollback_errors) == 1
    assert str(first) in error.rollback_errors[0]
    recovery = Path(error.recovery_directory)
    assert recovery.is_dir()
    manifest = json.loads((recovery / "manifest.json").read_text())
    assert manifest["rollback_errors"] == error.rollback_errors
    first_record = next(record for record in manifest["files"] if record["destination"] == str(first))
    assert first_record["published"] and not first_record["restored"]
    assert (recovery / first_record["original_file"]).read_bytes() == b"original first"
    assert (recovery / first_record["prepared_file"]).read_bytes() == b"new"
    assert first.read_bytes() == b"new"
    assert second.read_bytes() == b"original second"


def test_progress_failure_also_rolls_back_the_completed_write(tmp_path, recovery_locations):
    destination = _file(tmp_path, "song.mid", b"original")
    staged = _file(tmp_path, "prepared", b"new")

    def progress(_index, _total, _destination):
        assert destination.read_bytes() == b"new"
        raise RuntimeError("Progress callback failed")

    with pytest.raises(file_batch.FileBatchWriteError, match="Progress callback failed"):
        file_batch.publish_file_batch([(staged, destination)], progress_callback=progress)

    assert destination.read_bytes() == b"original"
    assert not any(path.exists() for path in recovery_locations)


def test_unreadable_prepared_file_or_directory_target_cannot_publish_anything(tmp_path, recovery_locations):
    destination = _file(tmp_path, "song.mid", b"original")
    staged = _file(tmp_path, "prepared", b"new")
    missing = tmp_path / "missing"

    with pytest.raises(file_batch.FileBatchWriteError):
        file_batch.publish_file_batch([(staged, destination), (missing, tmp_path / "next.mid")])
    assert destination.read_bytes() == b"original"

    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(file_batch.FileBatchWriteError, match="not a regular file"):
        file_batch.publish_file_batch([(staged, destination), (staged, directory)])
    assert destination.read_bytes() == b"original"
    assert not any(path.exists() for path in recovery_locations)


def test_empty_batch_does_not_create_recovery_storage_or_call_callbacks(recovery_locations):
    calls = []
    file_batch.publish_file_batch([], backup_callback=calls.append, progress_callback=lambda *args: calls.append(args))
    assert calls == []
    assert recovery_locations == []
