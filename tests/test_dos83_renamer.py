"""In-place renaming must preserve every original across dependencies and errors."""

import builtins
import json
import shutil
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import dos83_renamer as renamer


@pytest.fixture(autouse=True)
def recovery_locations(monkeypatch):
    locations = []
    real_mkdtemp = renamer.tempfile.mkdtemp

    def track_directory(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "aps_midi_rename_recovery_":
            locations.append(Path(path))
        return path

    monkeypatch.setattr(renamer.tempfile, "mkdtemp", track_directory)
    yield locations
    for path in locations:
        shutil.rmtree(path, ignore_errors=True)


def _make_plan(tmp_path, shape):
    names = ["00A.mid", "00ADKSON.MID", "01ADKSON.MID"]
    paths = [tmp_path / name for name in names]
    originals = {path: f"original recording {index}".encode() for index, path in enumerate(paths)}
    for path, contents in originals.items():
        path.write_bytes(contents)
    if shape == "chain":
        plan = renamer.build_midi_dos83_plan(paths)
        assert [Path(target).name for _, target in plan] == [
            "00ADKSON.MID", "01ADKSON.MID", "02ADKSON.MID",
        ]
    else:
        targets = {
            "swap": [paths[1], paths[0], tmp_path / "third.MID"],
            "cycle": [paths[1], paths[2], paths[0]],
            "independent": [tmp_path / f"output{index}.MID" for index in range(3)],
        }[shape]
        plan = [(str(source), str(target)) for source, target in zip(paths, targets)]
    return plan, originals


@pytest.mark.parametrize("shape", ["chain", "swap", "cycle", "independent"])
@pytest.mark.parametrize("failure_position", range(3))
def test_publication_failure_restores_all_original_bytes(
    tmp_path, monkeypatch, recovery_locations, shape, failure_position,
):
    plan, originals = _make_plan(tmp_path, shape)
    real_replace = renamer.os.replace
    failed = False

    def fail_publication(source, destination):
        nonlocal failed
        if not failed and str(destination) == plan[failure_position][1]:
            failed = True
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renamer.os, "replace", fail_publication)
    with pytest.raises(RuntimeError, match="injected publication failure") as caught:
        renamer.apply_midi_dos83_plan(plan)

    assert failed
    assert "Rollback issues" not in str(caught.value)
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals
    assert not any(path.exists() for path in recovery_locations)


@pytest.mark.parametrize("shape", ["chain", "swap", "cycle", "independent"])
def test_successful_rename_preserves_each_recording(tmp_path, recovery_locations, shape):
    plan, originals = _make_plan(tmp_path, shape)

    result = renamer.apply_midi_dos83_plan(plan)

    assert result.renamed == plan
    assert result.unchanged == []
    assert result.backups_created == []
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == {
        Path(target): originals[Path(source)] for source, target in plan
    }
    assert not any(path.exists() for path in recovery_locations)


def test_default_backups_do_not_overwrite_selected_sources_or_existing_backups(tmp_path):
    source = tmp_path / "song.mid"
    selected_backup = tmp_path / "song_backup.mid"
    existing_backup = tmp_path / "song_backup_2.mid"
    source.write_bytes(b"first recording")
    selected_backup.write_bytes(b"second recording")
    existing_backup.write_bytes(b"previous backup")
    plan = renamer.build_midi_dos83_plan([source, selected_backup])
    originals = {str(source): source.read_bytes(), str(selected_backup): selected_backup.read_bytes()}

    result = renamer.apply_midi_dos83_plan(plan, create_backups=True)

    assert existing_backup.read_bytes() == b"previous backup"
    for (original, target), backup in zip(plan, result.backups_created):
        assert Path(target).read_bytes() == originals[original]
        assert Path(backup).read_bytes() == originals[original]
    assert len(set(result.backups_created)) == 2


@pytest.mark.parametrize("failure_position", range(3))
def test_staging_failure_restores_originals(tmp_path, monkeypatch, recovery_locations, failure_position):
    plan, originals = _make_plan(tmp_path, "chain")
    real_replace = renamer.os.replace
    failed = False

    def fail_staging(source, destination):
        nonlocal failed
        if not failed and str(source) == plan[failure_position][0]:
            failed = True
            raise OSError("injected staging failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renamer.os, "replace", fail_staging)
    with pytest.raises(renamer.RenameError, match="before finalizing names") as caught:
        renamer.apply_midi_dos83_plan(plan)

    assert caught.value.rollback_errors == []
    assert caught.value.recovery_directory == ""
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals
    assert not any(path.exists() for path in recovery_locations)


def _assert_recovery(caught, originals, tmp_path):
    error = caught.value
    assert len(error.rollback_errors) == 1
    recovery = Path(error.recovery_directory)
    assert str(recovery) in str(error)
    manifest = json.loads((recovery / "manifest.json").read_text())
    assert {Path(record["source"]): (recovery / record["original_file"]).read_bytes()
            for record in manifest["files"]} == originals
    # The live originals also survive at source, target, or staging paths.
    live_bytes = [path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()]
    assert sorted(live_bytes) == sorted(originals.values())
    assert all("target" in record and "temporary_path" in record for record in manifest["files"])


@pytest.mark.parametrize("shape", ["chain", "swap", "cycle"])
@pytest.mark.parametrize("rollback_call", range(5))
def test_rollback_failure_keeps_every_original_and_recovery_snapshot(
    tmp_path, monkeypatch, shape, rollback_call,
):
    plan, originals = _make_plan(tmp_path, shape)
    real_replace = renamer.os.replace
    calls = 0

    def fail_publication_and_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls == 6:
            raise OSError("injected publication failure")
        # Two published targets must be evacuated, then all three originals
        # restored. Fail each of those five rollback moves in turn.
        if calls == 7 + rollback_call:
            raise OSError("injected rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renamer.os, "replace", fail_publication_and_rollback)
    with pytest.raises(renamer.RenameError, match="injected rollback failure") as caught:
        renamer.apply_midi_dos83_plan(plan)

    _assert_recovery(caught, originals, tmp_path)
    assert calls == (8 if rollback_call < 2 else 11)


@pytest.mark.parametrize("rollback_call", range(2))
def test_staging_rollback_failure_retains_recovery(tmp_path, monkeypatch, rollback_call):
    plan, originals = _make_plan(tmp_path, "chain")
    real_replace = renamer.os.replace
    calls = 0

    def fail_staging_and_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3 or calls == 4 + rollback_call:
            raise OSError("injected staging/rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renamer.os, "replace", fail_staging_and_rollback)
    with pytest.raises(renamer.RenameError, match="Rollback issues") as caught:
        renamer.apply_midi_dos83_plan(plan)

    _assert_recovery(caught, originals, tmp_path)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["staging", "publication", "rollback"])
def test_interruption_cannot_delete_staged_originals(
    tmp_path, monkeypatch, recovery_locations, interrupt, phase,
):
    plan, originals = _make_plan(tmp_path, "chain")
    real_replace = renamer.os.replace
    calls = 0

    def interrupt_rename(source, destination):
        nonlocal calls
        calls += 1
        if phase == "rollback" and calls == 6:
            raise OSError("injected publication failure")
        if calls == {"staging": 3, "publication": 6, "rollback": 7}[phase]:
            raise interrupt("interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(renamer.os, "replace", interrupt_rename)
    with pytest.raises(renamer.RenameError, match="interrupted") as caught:
        renamer.apply_midi_dos83_plan(plan)

    if phase == "rollback":
        _assert_recovery(caught, originals, tmp_path)
    else:
        assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals
        assert not any(path.exists() for path in recovery_locations)


@pytest.mark.parametrize("backup_destination", ["source", "target", "unchanged", "shared"])
def test_custom_backups_avoid_all_sources_targets_and_each_other(tmp_path, backup_destination):
    plan, originals = _make_plan(tmp_path, "independent")
    unchanged = tmp_path / "unchanged.mid"
    unchanged.write_bytes(b"unchanged recording")
    plan.append((str(unchanged), str(unchanged)))
    desired = {
        "source": Path(plan[0][0]), "target": Path(plan[0][1]),
        "unchanged": unchanged, "shared": tmp_path / "shared.mid",
    }[backup_destination]

    result = renamer.apply_midi_dos83_plan(
        plan, create_backups=True, backup_path_builder=lambda _: desired,
    )

    assert unchanged.read_bytes() == b"unchanged recording"
    assert result.unchanged == [str(unchanged)]
    assert len(set(result.backups_created)) == 3
    assert not set(result.backups_created).intersection(path for entry in plan for path in entry)
    for (source, target), backup in zip(plan, result.backups_created):
        assert Path(target).read_bytes() == Path(backup).read_bytes() == originals[Path(source)]


def test_backup_planning_finishes_before_any_files_are_written(tmp_path, recovery_locations):
    plan, originals = _make_plan(tmp_path, "chain")

    def fail_later_builder(source):
        if source == plan[1][0]:
            raise ValueError("injected backup planning failure")
        return tmp_path / "backup.mid"

    with pytest.raises(ValueError, match="injected backup planning failure"):
        renamer.apply_midi_dos83_plan(plan, create_backups=True, backup_path_builder=fail_later_builder)

    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals
    assert recovery_locations == []


def test_backup_creation_race_cannot_overwrite_arriving_file(tmp_path, monkeypatch, recovery_locations):
    plan, originals = _make_plan(tmp_path, "chain")
    backup = tmp_path / "backup.mid"

    def racing_open(file, mode="r", *args, **kwargs):
        if Path(file) == backup and mode == "xb":
            backup.write_bytes(b"arrived after planning")
        return builtins.open(file, mode, *args, **kwargs)

    monkeypatch.setattr(renamer, "open", racing_open, raising=False)
    with pytest.raises(RuntimeError, match="Backup failed"):
        renamer.apply_midi_dos83_plan(plan, create_backups=True, backup_path_builder=lambda _: backup)

    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == {
        **originals, backup: b"arrived after planning",
    }
    assert not any(path.exists() for path in recovery_locations)


def test_failed_backup_copy_removes_partial_backup_without_renaming(tmp_path, monkeypatch, recovery_locations):
    plan, originals = _make_plan(tmp_path, "chain")
    real_copy = renamer.shutil.copyfileobj
    copies = 0

    def fail_second_copy(source, destination, *args, **kwargs):
        nonlocal copies
        # copyfile may also use copyfileobj for recovery snapshots, depending
        # on the platform. Inject only into the user-visible backup writes.
        if Path(destination.name).parent != tmp_path:
            return real_copy(source, destination, *args, **kwargs)
        copies += 1
        if copies == 2:
            destination.write(b"incomplete")
            raise OSError("injected backup copy failure")
        return real_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(renamer.shutil, "copyfileobj", fail_second_copy)
    with pytest.raises(RuntimeError, match="injected backup copy failure"):
        renamer.apply_midi_dos83_plan(plan, create_backups=True)

    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == {
        **originals, tmp_path / "00A_backup.mid": originals[Path(plan[0][0])],
    }
    assert not any(path.exists() for path in recovery_locations)


def test_snapshot_failure_leaves_all_sources_untouched(tmp_path, monkeypatch, recovery_locations):
    plan, originals = _make_plan(tmp_path, "chain")
    real_copy = renamer.shutil.copyfile

    def fail_snapshot(source, destination, *args, **kwargs):
        if Path(destination).name == "original-0001.bin":
            raise OSError("injected snapshot failure")
        return real_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(renamer.shutil, "copyfile", fail_snapshot)
    with pytest.raises(RuntimeError, match="injected snapshot failure"):
        renamer.apply_midi_dos83_plan(plan, create_backups=True)

    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == originals
    assert not any(path.exists() for path in recovery_locations)


@pytest.mark.parametrize("collision_kind", ["dangling_symlink", "target_directory_alias"])
def test_backup_planning_handles_symlink_collisions(tmp_path, collision_kind):
    source = tmp_path / "song.mid"
    source.write_bytes(b"original recording")
    target = tmp_path / "output.MID"
    alias = tmp_path / "alias"
    try:
        if collision_kind == "dangling_symlink":
            alias.symlink_to(tmp_path / "missing.mid")
            backup = alias
        else:
            alias.symlink_to(tmp_path, target_is_directory=True)
            backup = alias / target.name
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    result = renamer.apply_midi_dos83_plan(
        [(source, target)], create_backups=True, backup_path_builder=lambda _: backup,
    )

    assert alias.is_symlink()
    assert result.backups_created != [str(backup)]
    assert Path(result.backups_created[0]).read_bytes() == target.read_bytes() == b"original recording"
    assert not (tmp_path / "missing.mid").exists()


def test_backups_survive_successful_publication_rollback(tmp_path, monkeypatch, recovery_locations):
    plan, originals = _make_plan(tmp_path, "chain")
    real_replace = renamer.os.replace
    failed = False

    def fail_publication(source, destination):
        nonlocal failed
        if not failed and str(destination) == plan[1][1]:
            failed = True
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renamer.os, "replace", fail_publication)
    with pytest.raises(renamer.RenameError):
        renamer.apply_midi_dos83_plan(plan, create_backups=True)

    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == {
        **originals,
        **{path.with_name(f"{path.stem}_backup{path.suffix}"): contents
           for path, contents in originals.items()},
    }
    assert not any(path.exists() for path in recovery_locations)
