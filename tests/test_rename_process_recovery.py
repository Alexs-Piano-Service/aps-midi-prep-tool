"""Kill a separate interpreter inside a physical rename, then recover on relaunch."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import dos83_renamer as renamer
from aps_midi_prep_tool_app import rename_recovery as storage
from test_dos83_renamer import _make_plan


@pytest.fixture(autouse=True)
def persistent_recovery_location(tmp_path, monkeypatch):
    monkeypatch.setenv("APS_MIDI_RENAME_RECOVERY_DIR", str(tmp_path / "recovery"))


def _run(code, *arguments):
    return subprocess.run([sys.executable, "-c", code, *arguments], capture_output=True, text=True, timeout=30)


def _crash(plan, phase, position, directory=None, action="restore"):
    result = _run('''
import json, os, sys
from aps_midi_prep_tool_app import dos83_renamer as renamer
phase, position, directory, action = sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
def die_after_move(actual_phase, index):
    if (actual_phase, index) == (phase, position):
        os._exit(93)
renamer._fault_hook = die_after_move
if directory:
    renamer.recover_midi_dos83_transaction(directory, action)
else:
    renamer.apply_midi_dos83_plan(json.loads(sys.argv[1]))
''', json.dumps(plan), phase, str(position), str(directory or ""), action)
    assert result.returncode == 93, result.stdout + result.stderr


def _recover(directory, action):
    result = _run('''
import sys
from aps_midi_prep_tool_app.dos83_renamer import recover_midi_dos83_transaction
recover_midi_dos83_transaction(sys.argv[1], sys.argv[2])
''', str(directory), action)
    assert result.returncode == 0, result.stdout + result.stderr
    assert renamer.find_pending_midi_renames() == []


def _songs(tmp_path, shape):
    directory = tmp_path / "songs"
    directory.mkdir()
    plan, originals = _make_plan(directory, shape)
    return directory, plan, originals


def _assert_files(directory, plan, originals, action):
    expected = originals if action == "restore" else {
        Path(target): originals[Path(source)] for source, target in plan
    }
    assert {path: path.read_bytes() for path in directory.iterdir()} == expected


@pytest.mark.parametrize("shape", ["chain", "swap", "cycle", "independent"])
@pytest.mark.parametrize("phase", ["staging", "publication"])
@pytest.mark.parametrize("position", range(3))
@pytest.mark.parametrize("action", ["restore", "resume"])
def test_hard_exit_at_every_move_is_recoverable_on_relaunch(tmp_path, shape, phase, position, action):
    songs, plan, originals = _songs(tmp_path, shape)
    _crash(plan, phase, position)
    directory, = renamer.find_pending_midi_renames()
    manifest = json.loads((Path(directory) / "manifest.json").read_text())
    assert manifest["pending"] is not None  # Exit before updating the location journal.
    assert {Path(r["source"]): (Path(directory) / r["original_file"]).read_bytes()
            for r in manifest["files"]} == originals
    _recover(directory, action)
    _assert_files(songs, plan, originals, action)


@pytest.mark.parametrize("action,phase", [
    ("restore", "restore_staging"), ("restore", "restore_publication"),
    ("resume", "recovery_staging"), ("resume", "recovery_publication"),
])
@pytest.mark.parametrize("position", range(3))
def test_recovery_itself_can_be_killed_and_relaunched(tmp_path, action, phase, position):
    songs, plan, originals = _songs(tmp_path, "cycle")
    _crash(plan, "publication", 2)
    directory, = renamer.find_pending_midi_renames()
    _crash(plan, phase, position, directory, action)
    _recover(directory, action)
    _assert_files(songs, plan, originals, action)


def test_recovery_refuses_to_overwrite_a_file_created_after_the_crash(tmp_path):
    songs, plan, originals = _songs(tmp_path, "independent")
    _crash(plan, "staging", 2)
    unrelated = Path(plan[0][0])
    unrelated.write_bytes(b"another application created this")
    before = {path: path.read_bytes() for path in songs.iterdir()}
    directory, = renamer.find_pending_midi_renames()
    with pytest.raises(FileExistsError, match="unrelated file"):
        renamer.recover_midi_dos83_transaction(directory)
    assert {path: path.read_bytes() for path in songs.iterdir()} == before
    assert renamer.find_pending_midi_renames() == [directory]
    unrelated.unlink()
    _recover(directory, "restore")
    _assert_files(songs, plan, originals, "restore")


def test_a_second_process_cannot_recover_a_live_transaction(tmp_path):
    root = storage.recovery_root()
    with storage.recovery_lock(root):
        result = _run('''
from aps_midi_prep_tool_app.dos83_renamer import find_pending_midi_renames
find_pending_midi_renames()
''')
        assert result.returncode != 0
        assert "Another APS rename or recovery is running" in result.stderr


def test_incomplete_transaction_blocks_new_renames(tmp_path):
    _, plan, _ = _songs(tmp_path, "chain")
    _crash(plan, "staging", 0)
    other = tmp_path / "new.mid"
    other.write_bytes(b"another recording")
    with pytest.raises(renamer.RenameError, match="unfinished rename"):
        renamer.apply_midi_dos83_plan([(other, tmp_path / "newname.mid")])
    assert other.read_bytes() == b"another recording"


def test_corrupt_journal_is_discovered_and_preserved(tmp_path):
    root = storage.recovery_root()
    directory = root / "aps_midi_rename_recovery_corrupt"
    directory.mkdir(parents=True)
    manifest = directory / "manifest.json"
    manifest.write_text("invalid json")
    assert renamer.find_pending_midi_renames() == [str(directory)]
    with pytest.raises(ValueError):
        renamer.recover_midi_dos83_transaction(directory)
    assert manifest.read_text() == "invalid json"


def test_space_preflight_reports_required_space_without_moving_sources(tmp_path, monkeypatch):
    songs, plan, originals = _songs(tmp_path, "chain")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(OSError, match="Rename needs approximately 1 MB.*No files were renamed"):
        renamer.apply_midi_dos83_plan(plan)
    _assert_files(songs, plan, originals, "restore")
    assert renamer.find_pending_midi_renames() == []


def test_system_temp_and_source_subdirectory_permissions_are_not_required(tmp_path, monkeypatch):
    songs, plan, originals = _songs(tmp_path, "cycle")
    original_mkdir = os.mkdir
    def deny_source_subdirectories(path, *args, **kwargs):
        if Path(path).parent == songs:
            raise PermissionError("subdirectory creation denied")
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", deny_source_subdirectories)
    monkeypatch.setattr(renamer.tempfile, "gettempdir", lambda: (_ for _ in ()).throw(OSError("TEMP is full or unwritable")))
    renamer.apply_midi_dos83_plan(plan)
    _assert_files(songs, plan, originals, "resume")


def test_staging_file_permission_failure_is_reported_before_any_moves(tmp_path, monkeypatch):
    import builtins
    songs, plan, originals = _songs(tmp_path, "chain")
    def deny_staging(path, mode="r", *args, **kwargs):
        if mode == "xb" and Path(path).parent == songs:
            raise PermissionError("staging file creation denied")
        return builtins.open(path, mode, *args, **kwargs)
    monkeypatch.setattr(renamer, "open", deny_staging, raising=False)
    with pytest.raises(RuntimeError, match="staging file creation denied.*No files were renamed"):
        renamer.apply_midi_dos83_plan(plan)
    _assert_files(songs, plan, originals, "restore")
    assert renamer.find_pending_midi_renames() == []


def test_recovery_journal_write_failure_preserves_sources(tmp_path, monkeypatch):
    songs, plan, originals = _songs(tmp_path, "chain")
    def deny_journal(*_):
        raise OSError("recovery location is full or unwritable")
    monkeypatch.setattr(storage, "_atomic_replace", deny_journal)
    with pytest.raises(RuntimeError, match="recovery location is full or unwritable"):
        renamer.apply_midi_dos83_plan(plan)
    _assert_files(songs, plan, originals, "restore")


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows ACLs and icacls")
def test_windows_acl_denies_subdirectories_but_allows_file_rename(tmp_path):
    import csv
    songs, plan, originals = _songs(tmp_path, "cycle")
    identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
    sid = next(csv.reader([identity.strip()]))[1]
    # AD = FILE_ADD_SUBDIRECTORY on this directory only; no inheritance to files.
    command = ["icacls", str(songs), "/deny", f"*{sid}:(AD)"]
    subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        with pytest.raises(PermissionError):
            (songs / "forbidden-subdirectory").mkdir()
        renamer.apply_midi_dos83_plan(plan)
        _assert_files(songs, plan, originals, "resume")
    finally:
        subprocess.run(["icacls", str(songs), "/remove:d", f"*{sid}"], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("action", ["restore", "resume"])
def test_process_death_during_snapshot_preparation_leaves_a_recoverable_journal(tmp_path, action):
    songs, plan, originals = _songs(tmp_path, "chain")
    result = _run('''
import json, os, sys
from aps_midi_prep_tool_app import dos83_renamer as renamer
copy = renamer.shutil.copyfile
def copy_and_exit(source, destination):
    copy(source, destination)
    os._exit(93)
renamer.shutil.copyfile = copy_and_exit
renamer.apply_midi_dos83_plan(json.loads(sys.argv[1]), create_backups=True)
''', json.dumps(plan))
    assert result.returncode == 93, result.stderr
    directory, = renamer.find_pending_midi_renames()
    _recover(directory, action)
    if action == "resume":
        for (source, target) in plan:
            assert Path(target).read_bytes() == originals[Path(source)]
            assert Path(source).with_name(f"{Path(source).stem}_backup{Path(source).suffix}").read_bytes() == originals[Path(source)]
    else:
        _assert_files(songs, plan, originals, action)


@pytest.mark.parametrize("choice,action", [("Restore originals", "restore"), ("Resume rename", "resume"), ("Later", None)])
def test_startup_recovery_dialog_applies_only_the_selected_action(tmp_path, monkeypatch, choice, action):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from aps_midi_prep_tool_app.rename_recovery_dialog import show_rename_recovery_dialogs
    app = QApplication.instance() or QApplication([])
    songs, plan, originals = _songs(tmp_path, "cycle")
    _crash(plan, "publication", 1)
    before = {path: path.read_bytes() for path in songs.iterdir()}
    original_exec = QMessageBox.exec
    def choose(dialog):
        button = next(button for button in dialog.buttons() if button.text() == choice)
        QTimer.singleShot(0, button.click)
        return original_exec(dialog)
    monkeypatch.setattr(QMessageBox, "exec", choose)
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: notices.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args)))
    show_rename_recovery_dialogs(None)
    if action is None:
        assert renamer.find_pending_midi_renames()
        assert {path: path.read_bytes() for path in songs.iterdir()} == before
        assert notices == []
    else:
        assert renamer.find_pending_midi_renames() == []
        _assert_files(songs, plan, originals, action)
        assert len(notices) == 1
    app.processEvents()
