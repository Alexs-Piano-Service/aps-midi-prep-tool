import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import update_installer as updater
from aps_midi_prep_tool_app.self_update import StagedUpdate, UpdateCancelled, UpdateError, UpdateTarget


@pytest.fixture
def update_job(tmp_path, monkeypatch):
    temporary = tmp_path / "system temp"
    temporary.mkdir()
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(temporary))
    root = tmp_path / "USB music café"
    root.mkdir()
    target = root / "My renamed APS.AppImage"
    target.write_bytes(b"old executable")
    target.chmod(0o751)
    music = root / "customer music.mid"
    music.write_bytes(b"music remains")
    config = root / "aps-midi-prep-tool.json"
    config.write_bytes(b"configuration remains")
    directory = root / ".aps-update-1234"
    directory.mkdir()
    payload = directory / "payload.AppImage"
    payload.write_bytes(b"new executable")
    helper = temporary / ".aps-update-helper-1234"
    helper.mkdir()
    token = "a" * 64
    updater._write_marker(helper / "owner", token)
    job = {
        "format": 1, "target": str(target), "payload": str(payload),
        "kind": "linux-appimage", "version": "99.0", "sha256": updater._hash_file(payload),
        "original_sha256": updater._hash_file(target), "parent_pid": os.getpid(),
        "parent_identity": updater._parent_identity(os.getpid()), "arguments": [],
        "helper_directory": str(helper), "token": token,
    }
    updater._write_json(directory / "job.json", job)
    job["directory"] = str(directory)
    return job


def _staged(job):
    return StagedUpdate(
        UpdateTarget(Path(job["target"]), job["kind"], "x86_64"), Path(job["directory"]),
        Path(job["payload"]), job["version"], job["sha256"], job["original_sha256"],
    )


def _acknowledging_restart(job, *, acknowledge):
    if acknowledge:
        updater._write_marker(Path(job["directory"]) / "startup-ack", job["token"])
    return SimpleNamespace(poll=lambda: None)


def test_installs_same_unicode_filename_and_preserves_music_config_and_old_versions(update_job):
    job = update_job
    target = Path(job["target"])
    backup = Path(str(target) + ".previous")
    backup.write_bytes(b"older executable")
    updater._replace_and_restart(job, restart=_acknowledging_restart)
    assert target.read_bytes() == b"new executable"
    assert backup.read_bytes() == b"old executable"
    assert (Path(job["directory"]) / "older.previous").read_bytes() == b"older executable"
    assert not list(target.parent.glob(target.name + ".previous.*"))
    assert (target.parent / "customer music.mid").read_bytes() == b"music remains"
    assert (target.parent / "aps-midi-prep-tool.json").read_bytes() == b"configuration remains"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o751


def test_installation_rename_is_atomic_and_old_target_stays_present(update_job, monkeypatch):
    job = update_job
    original_replace = updater.os.replace
    seen = []

    def replace(source, destination):
        if Path(source) == Path(job["payload"]):
            seen.append(Path(job["target"]).read_bytes())
            assert Path(job["target"] + ".previous").read_bytes() == b"old executable"
        return original_replace(source, destination)

    monkeypatch.setattr(updater.os, "replace", replace)
    updater._replace_and_restart(job, restart=_acknowledging_restart)
    assert seen == [b"old executable"]


@pytest.mark.parametrize("changed", ["target", "payload"])
def test_changed_executable_refuses_replacement(update_job, changed):
    job = update_job
    Path(job[changed]).write_bytes(b"unexpected data")
    original = Path(job["target"]).read_bytes()
    with pytest.raises(UpdateError, match="changed after verification"):
        updater._replace_and_restart(job, restart=_acknowledging_restart)
    assert Path(job["target"]).read_bytes() == original
    assert not Path(job["target"] + ".previous").exists()


def test_replacement_failure_keeps_original(update_job, monkeypatch):
    job = update_job
    original_replace = updater._replace_with_retry
    launches = []

    def replace(source, destination, **kwargs):
        if Path(source) == Path(job["payload"]):
            raise PermissionError("USB write failed")
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr(updater, "_replace_with_retry", replace)
    with pytest.raises(PermissionError):
        updater._replace_and_restart(job, restart=lambda job, acknowledge: launches.append(acknowledge))
    assert Path(job["target"]).read_bytes() == b"old executable"
    assert Path(job["target"] + ".previous").read_bytes() == b"old executable"
    assert launches == [False]


@pytest.mark.parametrize("failure", ["launch", "startup_exit"])
def test_failed_startup_restores_original(update_job, failure):
    job = update_job
    launches = []

    def restart(job, *, acknowledge):
        launches.append(acknowledge)
        if acknowledge and failure == "launch":
            raise OSError("cannot launch")
        return SimpleNamespace(poll=lambda: 1)

    with pytest.raises((UpdateError, OSError)):
        updater._replace_and_restart(job, restart=restart)
    assert Path(job["target"]).read_bytes() == b"old executable"
    assert launches == [True, False]


def test_unacknowledged_live_app_is_never_killed_or_overwritten(update_job):
    job = update_job
    with pytest.raises(UpdateError, match="did not confirm"):
        updater._replace_and_restart(job, restart=lambda job, acknowledge: SimpleNamespace(poll=lambda: None),
                                     startup_timeout=0)
    assert Path(job["target"]).read_bytes() == b"new executable"
    assert Path(job["target"] + ".previous").read_bytes() == b"old executable"


@pytest.mark.parametrize("committed,parent_exited", [(False, True), (True, False), (False, False)])
def test_helper_requires_commit_and_parent_exit(update_job, monkeypatch, committed, parent_exited):
    job = update_job
    directory = Path(job["directory"])
    if committed:
        updater._write_marker(directory / "commit", job["token"])
    monkeypatch.setattr(updater, "PARENT_TIMEOUT", 0.001)
    calls = []
    parent = SimpleNamespace(exited=lambda: parent_exited, close=lambda: calls.append("closed"))
    result = updater._run_helper(directory / "job.json", parent_factory=lambda *args: parent,
                                 replace_and_restart=lambda job: calls.append("installed"))
    assert result == 1
    assert calls == ["closed"]
    assert Path(job["target"]).read_bytes() == b"old executable"
    assert "did not authorize" in json.loads((directory / "failure.json").read_text(encoding="utf-8"))["error"]


def test_cancelled_helper_cleans_staging_without_replacement(update_job):
    job = update_job
    directory = Path(job["directory"])
    updater._write_marker(directory / "cancel", job["token"])
    updater._write_marker(directory / "commit", job["token"])
    parent = SimpleNamespace(exited=lambda: True, close=lambda: None)
    assert updater._run_helper(directory / "job.json", parent_factory=lambda *args: parent) == 0
    assert not directory.exists()
    assert Path(job["target"]).read_bytes() == b"old executable"


def test_authorized_helper_installs_once_and_cleans_staging(update_job):
    job = update_job
    directory = Path(job["directory"])
    updater._write_marker(directory / "commit", job["token"])
    parent = SimpleNamespace(exited=lambda: True, close=lambda: None)
    assert updater._run_helper(
        directory / "job.json", parent_factory=lambda *args: parent,
        replace_and_restart=lambda job: updater._replace_and_restart(job, restart=_acknowledging_restart),
    ) == 0
    assert Path(job["target"]).read_bytes() == b"new executable"
    assert not directory.exists()


def test_successive_successful_updates_keep_only_the_latest_recovery_copy(update_job):
    job = update_job
    target = Path(job["target"])
    previous = target.read_bytes()
    parent = SimpleNamespace(exited=lambda: True, close=lambda: None)
    for generation in range(4):
        directory = Path(job["directory"])
        directory.mkdir(exist_ok=True)
        new_bytes = f"executable version {generation}".encode()
        Path(job["payload"]).write_bytes(new_bytes)
        job["original_sha256"] = updater._hash_file(target)
        job["sha256"] = updater._hash_file(Path(job["payload"]))
        updater._write_json(directory / "job.json", job)
        updater._write_marker(directory / "commit", job["token"])

        def restart(active_job, *, acknowledge):
            assert acknowledge
            assert target.read_bytes() == new_bytes
            assert Path(str(target) + ".previous").read_bytes() == previous
            if generation:
                assert (directory / "older.previous").is_file()
            return _acknowledging_restart(active_job, acknowledge=acknowledge)

        assert updater._run_helper(
            directory / "job.json", parent_factory=lambda *args: parent,
            replace_and_restart=lambda active_job: updater._replace_and_restart(active_job, restart=restart),
        ) == 0
        assert target.read_bytes() == new_bytes
        assert Path(str(target) + ".previous").read_bytes() == previous
        assert not list(target.parent.glob(target.name + ".previous.*"))
        assert not list(target.parent.glob(".aps-update-*"))
        previous = new_bytes
    assert (target.parent / "customer music.mid").read_bytes() == b"music remains"
    assert (target.parent / "aps-midi-prep-tool.json").read_bytes() == b"configuration remains"


@pytest.mark.parametrize("child_running", [False, True])
def test_failed_update_retains_older_backup_in_its_recovery_directory(update_job, child_running):
    job = update_job
    directory = Path(job["directory"])
    Path(job["target"] + ".previous").write_bytes(b"older executable")
    updater._write_marker(directory / "commit", job["token"])
    parent = SimpleNamespace(exited=lambda: True, close=lambda: None)
    assert updater._run_helper(
        directory / "job.json", parent_factory=lambda *args: parent,
        replace_and_restart=lambda active_job: updater._replace_and_restart(
            active_job, restart=lambda *_args, **_kwargs: SimpleNamespace(
                poll=lambda: None if child_running else 1), startup_timeout=0.001),
    ) == 1
    assert Path(job["target"]).read_bytes() == (b"new executable" if child_running else b"old executable")
    if child_running:
        assert Path(job["target"] + ".previous").read_bytes() == b"old executable"
    assert (directory / "older.previous").read_bytes() == b"older executable"
    assert (directory / "failure.json").is_file()


@pytest.mark.parametrize("phase", ["verification", "replacement", "startup"])
def test_failed_install_restarts_original_after_recording_failure(update_job, monkeypatch, phase):
    job = update_job
    directory = Path(job["directory"])
    updater._write_marker(directory / "commit", job["token"])
    parent = SimpleNamespace(exited=lambda: True, close=lambda: None)
    launches = []

    def restart(job, *, acknowledge):
        launches.append(acknowledge)
        if acknowledge:
            return SimpleNamespace(poll=lambda: 1)
        failure = json.loads((directory / "failure.json").read_text(encoding="utf-8"))
        assert failure["target"] == job["target"]
        assert Path(job["target"]).read_bytes() == b"old executable"
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(updater, "_restart", restart)
    if phase == "verification":
        Path(job["payload"]).write_bytes(b"invalid payload")
    if phase == "replacement":
        original_replace = updater._replace_with_retry

        def replace(source, destination, **kwargs):
            if Path(source) == Path(job["payload"]):
                raise PermissionError("USB error")
            return original_replace(source, destination, **kwargs)

        monkeypatch.setattr(updater, "_replace_with_retry", replace)
    assert updater._run_helper(directory / "job.json", parent_factory=lambda *args: parent) == 1
    assert Path(job["target"]).read_bytes() == b"old executable"
    assert launches == ([True, False] if phase == "startup" else [False])
    assert (directory / "failure.json").exists()


def test_restored_app_receives_validated_failure_message(update_job, monkeypatch):
    job = update_job
    updater._record_failure(job, "The USB drive rejected the replacement.")
    monkeypatch.setenv("APS_UPDATE_FAILURE", str(Path(job["directory"]) / "failure.json"))
    monkeypatch.setenv("APS_UPDATE_TOKEN", job["token"])
    monkeypatch.setenv("APPIMAGE", job["target"])
    monkeypatch.setattr(updater.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    message = updater.mark_update_started()
    assert "USB drive rejected" in message
    assert "failure.json" in message


def test_restored_app_reads_failure_before_collecting_exited_helper(update_job, monkeypatch):
    job = update_job
    helper = Path(job["helper_directory"])
    updater._record_failure(job, "The update failed; the original was restored.")
    updater._write_marker(helper / "done", job["token"])
    monkeypatch.setenv("APS_UPDATE_FAILURE", str(Path(job["directory"]) / "failure.json"))
    monkeypatch.setenv("APS_UPDATE_TOKEN", job["token"])
    monkeypatch.setenv("APS_UPDATE_HELPER_DIRECTORY", str(helper))
    monkeypatch.setenv("APPIMAGE", job["target"])

    def immediate_thread(*, target, args, daemon):
        return SimpleNamespace(start=lambda: target(*args))

    monkeypatch.setattr(updater.threading, "Thread", immediate_thread)
    message = updater.mark_update_started()
    assert "original was restored" in message
    assert "failure.json" in message
    assert not helper.exists()


@pytest.mark.parametrize("field,value", [
    ("payload", "/tmp/not-the-payload"), ("target", "/tmp/unrelated-program"),
    ("token", "invalid"), ("arguments", ["--aps-windows-raw-write"]),
])
def test_rejects_jobs_outside_expected_layout(update_job, field, value):
    job = dict(update_job)
    job[field] = value
    path = Path(job["directory"]) / "job.json"
    path.write_text(json.dumps(job))
    with pytest.raises(UpdateError):
        updater._read_job(path)


def test_rejects_staging_directory_symlink(update_job, tmp_path):
    directory = Path(update_job["directory"])
    alias = directory.parent / ".aps-update-alias"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    if not alias.is_symlink():
        pytest.skip("symlinks unavailable")
    with pytest.raises(UpdateError):
        updater._read_job(alias / "job.json")


def test_cancel_during_helper_copy_removes_stage_and_partial_helper(update_job, monkeypatch):
    stage = _staged(update_job)
    # prepare_handoff owns a fresh helper/job; remove the fixture's unused helper.
    updater.shutil.rmtree(update_job["helper_directory"])
    (stage.directory / "job.json").unlink()
    with pytest.raises(UpdateCancelled):
        updater.prepare_handoff(stage, cancel_callback=lambda: True)
    assert not stage.directory.exists()
    assert list(Path(updater.tempfile.gettempdir()).iterdir()) == []
    assert stage.target.path.read_bytes() == b"old executable"


def test_prepare_uses_private_copy_clean_environment_and_explicit_commit(update_job, monkeypatch):
    job = update_job
    stage = _staged(job)
    updater.shutil.rmtree(job["helper_directory"])
    (stage.directory / "job.json").unlink()
    launched = []
    monkeypatch.setattr(updater.sys, "argv", [str(stage.target.path), "ordinary argument"])
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/old/bundle")
    monkeypatch.setenv("APPIMAGE", str(stage.target.path))
    monkeypatch.setenv("LD_LIBRARY_PATH", "/old/bundle")
    monkeypatch.setenv("APPDIR", "/old")
    monkeypatch.setenv("PATH", os.pathsep.join(("/old/usr/bin", "/usr/bin")))

    def launch(command, *, cwd, environment):
        launched.append((command, cwd, environment))
        new_job = updater._read_job(Path(command[2]))
        updater._write_marker(stage.directory / "ready", new_job["token"])
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(updater, "_launch", launch)
    handoff = updater.prepare_handoff(stage)
    command, cwd, environment = launched[0]
    assert Path(command[0]).parent == handoff.helper_directory
    assert Path(command[0]).read_bytes() == b"old executable"
    assert cwd != stage.target.path.parent
    assert "APPIMAGE" not in environment and "LD_LIBRARY_PATH" not in environment
    assert "_PYI_APPLICATION_HOME_DIR" not in environment
    assert environment["PATH"] == "/usr/bin"
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert not (stage.directory / "commit").exists()
    handoff.commit()
    handoff.commit()
    assert updater._has_marker(stage.directory / "commit", handoff.token)


def test_commit_refuses_an_exited_helper(update_job):
    job = update_job
    handoff = updater.UpdateHandoff(Path(job["directory"]), Path(job["helper_directory"]),
                                    job["token"], SimpleNamespace(poll=lambda: 1))
    with pytest.raises(UpdateError):
        handoff.commit()
    assert not (handoff.directory / "commit").exists()


def test_startup_ack_requires_matching_installed_executable(update_job, monkeypatch):
    job = update_job
    ack = Path(job["directory"]) / "startup-ack"
    monkeypatch.setenv("APS_UPDATE_ACK", str(ack))
    monkeypatch.setenv("APS_UPDATE_TOKEN", job["token"])
    monkeypatch.setenv("APPIMAGE", "/tmp/different-app")
    updater.mark_update_started()
    assert not ack.exists()
    monkeypatch.setenv("APS_UPDATE_ACK", str(ack))
    monkeypatch.setenv("APS_UPDATE_TOKEN", job["token"])
    monkeypatch.setenv("APPIMAGE", job["target"])
    monkeypatch.setattr(updater.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    updater.mark_update_started()
    assert updater._has_marker(ack, job["token"])


def test_locked_replace_retries_without_removing_the_original(tmp_path, monkeypatch):
    source, target = tmp_path / "new", tmp_path / "old"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    original_replace = updater.os.replace
    calls = []

    def replace(source, target):
        calls.append(1)
        if len(calls) == 1:
            assert target.read_bytes() == b"old"
            raise PermissionError(13, "sharing violation")
        return original_replace(source, target)

    monkeypatch.setattr(updater.os, "replace", replace)
    monkeypatch.setattr(updater.time, "sleep", lambda delay: None)
    updater._replace_with_retry(source, target)
    assert target.read_bytes() == b"new"
    assert len(calls) == 2


def test_helper_argument_dispatch_does_not_intercept_normal_startup():
    assert updater.run_update_helper_from_argv(["APS", "music.mid"]) is None
    assert updater.run_update_helper_from_argv(["APS", updater.HELPER_ARGUMENT]) == 2


def test_another_process_update_lock_prevents_all_replacement_and_restart(update_job):
    job = update_job
    directory = Path(job["directory"])
    target = Path(job["target"])
    ready, release = directory / "lock-test-ready", directory / "lock-test-release"
    repository = str(Path(updater.__file__).resolve().parent.parent)
    harness = (
        "import sys,time\n"
        "from pathlib import Path\n"
        "from aps_midi_prep_tool_app.update_installer import _TargetUpdateLock\n"
        "target,ready,release=map(Path,sys.argv[1:])\n"
        "with _TargetUpdateLock(target):\n"
        " ready.write_text('locked')\n"
        " deadline=time.monotonic()+10\n"
        " while not release.exists() and time.monotonic()<deadline:time.sleep(.02)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", harness, str(target), str(ready), str(release)],
                             cwd=repository, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    calls = []
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "The competing helper did not acquire its lock."
        with pytest.raises(updater._UpdateLockBusy, match="Another APS update"):
            updater._replace_and_restart(job, restart=lambda *args, **kwargs: calls.append(True))
        assert calls == []
        assert target.read_bytes() == b"old executable"
        assert Path(job["payload"]).read_bytes() == b"new executable"
        assert not Path(str(target) + ".previous").exists()
    finally:
        release.write_text("released")
        child.wait(timeout=12)
    assert child.returncode == 0, child.stderr.read().decode("utf-8", errors="replace")
    child.stderr.close()
    updater._replace_and_restart(job, restart=_acknowledging_restart)
    assert target.read_bytes() == b"new executable"


def test_update_lock_remains_valid_after_usb_directory_moves(update_job):
    target = Path(update_job["target"])
    with updater._TargetUpdateLock(target):
        pass
    new_directory = target.parent.with_name("USB different drive mount")
    target.parent.rename(new_directory)
    relocated = new_directory / target.name
    with updater._TargetUpdateLock(relocated):
        assert relocated.read_bytes() == b"old executable"
    assert (new_directory / (target.name + ".update-lock")).is_file()


def test_update_lock_rejects_existing_customer_file_without_modifying_it(update_job):
    target = Path(update_job["target"])
    lock_path = Path(str(target) + ".update-lock")
    lock_path.write_bytes(b"customer-owned data")
    with pytest.raises(UpdateError, match="unrecognized file"):
        with updater._TargetUpdateLock(target):
            pass
    assert lock_path.read_bytes() == b"customer-owned data"
    assert target.read_bytes() == b"old executable"


def test_update_lock_rejects_symlinks_without_modifying_their_target(update_job):
    target = Path(update_job["target"])
    lock_path = Path(str(target) + ".update-lock")
    music = target.parent / "customer music.mid"
    try:
        lock_path.symlink_to(music)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    if not lock_path.is_symlink():
        pytest.skip("symlinks unavailable")
    with pytest.raises(UpdateError, match="symbolic link"):
        with updater._TargetUpdateLock(target):
            pass
    assert music.read_bytes() == b"music remains"


def test_install_lock_stays_held_until_startup_acknowledgement(update_job):
    job = update_job

    def restart(job, *, acknowledge):
        assert acknowledge
        with pytest.raises(updater._UpdateLockBusy):
            with updater._TargetUpdateLock(job["target"]):
                pass
        return _acknowledging_restart(job, acknowledge=acknowledge)

    updater._replace_and_restart(job, restart=restart)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-script integration test")
def test_detached_helper_replaces_only_after_real_parent_exit_and_receives_startup_ack(update_job):
    job = update_job
    target, payload, directory = (Path(job[key]) for key in ("target", "payload", "directory"))
    (directory / "job.json").unlink()
    repository = str(Path(updater.__file__).resolve().parent.parent)
    app_script = (
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {repository!r})\n"
        "from aps_midi_prep_tool_app.update_installer import run_update_helper_from_argv, mark_update_started\n"
        "result = run_update_helper_from_argv()\n"
        "if result is not None: sys.exit(result)\n"
        "os.environ['APPIMAGE'] = os.path.abspath(sys.argv[0])\n"
        "mark_update_started()\n"
        "Path(sys.argv[0] + '.started').write_text('started')\n"
        "time.sleep(1)\n"
    )
    old_content, new_content = (app_script + "# old release\n"), (app_script + "# new release\n")
    target.write_text(old_content)
    payload.write_text(new_content)
    harness = (
        "import hashlib,sys\n"
        "from pathlib import Path\n"
        "from aps_midi_prep_tool_app.self_update import StagedUpdate,UpdateTarget\n"
        "from aps_midi_prep_tool_app.update_installer import prepare_handoff\n"
        "target,directory,payload = map(Path,sys.argv[1:])\n"
        "sys.argv = [str(target)]\n"
        "digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()\n"
        "staged=StagedUpdate(UpdateTarget(target,'linux-appimage','x86_64'),directory,payload,'99',digest(payload),digest(target))\n"
        "handoff=prepare_handoff(staged)\n"
        "handoff.commit()\n"
        "assert target.read_bytes().endswith(b'# old release\\n')\n"
    )
    result = subprocess.run([sys.executable, "-c", harness, str(target), str(directory), str(payload)],
                            cwd=repository, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 10
    while directory.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert target.read_text() == new_content
    assert Path(str(target) + ".previous").read_text() == old_content
    assert Path(str(target) + ".started").read_text() == "started"
    assert not directory.exists()
