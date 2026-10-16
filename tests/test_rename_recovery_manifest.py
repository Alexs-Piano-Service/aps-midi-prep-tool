"""Journal retries retain the last durable checkpoint and never weaken access."""

import errno
import json

import pytest

from aps_midi_prep_tool_app import rename_recovery as recovery


def windows_error(code):
    error = PermissionError(errno.EACCES, "Windows manifest publication denied")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_windows_replace_failure_retries_the_flushed_journal(tmp_path, monkeypatch, code):
    recovery.write_manifest(tmp_path, {"status": "ready"})
    replace = recovery._atomic_replace
    syncs, waits, attempts = [], [], []
    fsync = recovery.os.fsync

    def sync(descriptor):
        fsync(descriptor)
        syncs.append(descriptor)

    def publish(staged, destination):
        attempts.append(staged.read_bytes())
        assert json.loads(destination.read_bytes()) == {"status": "ready"}
        assert syncs, "Journal must be flushed before its first publication attempt"
        if len(attempts) < 3:
            raise windows_error(code)
        replace(staged, destination)

    monkeypatch.setattr(recovery.os, "fsync", sync)
    monkeypatch.setattr(recovery, "_atomic_replace", publish)
    monkeypatch.setattr(recovery.time, "sleep", waits.append)
    recovery.write_manifest(tmp_path, {"status": "writing"})
    assert len(attempts) == 3
    assert attempts[0] == attempts[1] == attempts[2]
    assert sum(waits) <= 1
    assert json.loads((tmp_path / "manifest.json").read_bytes()) == {"status": "writing"}
    assert not (tmp_path / "manifest.json.new").exists()


@pytest.mark.parametrize("code", [5, 32, 33])
def test_permanent_windows_replace_failure_is_bounded_and_preserves_both_journals(tmp_path, monkeypatch, code):
    recovery.write_manifest(tmp_path, {"status": "ready"})
    previous = (tmp_path / "manifest.json").read_bytes()
    error = windows_error(code)
    attempts, waits = [], []

    def deny(*_):
        attempts.append(True)
        raise error

    monkeypatch.setattr(recovery, "_atomic_replace", deny)
    monkeypatch.setattr(recovery.time, "sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        recovery.write_manifest(tmp_path, {"status": "writing"})
    assert caught.value is error
    assert 2 <= len(attempts) <= 21
    assert sum(waits) <= 1.01
    assert (tmp_path / "manifest.json").read_bytes() == previous
    assert json.loads((tmp_path / "manifest.json.new").read_bytes()) == {"status": "writing"}


@pytest.mark.parametrize("error", [PermissionError(errno.EACCES, "POSIX access denied"),
                                   windows_error(19), windows_error(112)])
def test_unrelated_publication_errors_are_not_retried(tmp_path, monkeypatch, error):
    attempts = []

    def deny(*_):
        attempts.append(True)
        raise error

    monkeypatch.setattr(recovery, "_atomic_replace", deny)
    monkeypatch.setattr(recovery.time, "sleep", lambda _: pytest.fail("Unrelated failure must not wait"))
    with pytest.raises(OSError) as caught:
        recovery.write_manifest(tmp_path, {"status": "ready"})
    assert caught.value is error
    assert len(attempts) == 1


def test_cancellation_during_retry_preserves_the_previous_checkpoint(tmp_path, monkeypatch):
    recovery.write_manifest(tmp_path, {"status": "ready"})
    attempts, waits = [], []

    class Cancelled(Exception):
        pass

    def cancel():
        if waits:
            raise Cancelled()

    def deny(*_):
        attempts.append(True)
        raise windows_error(5)

    monkeypatch.setattr(recovery, "_atomic_replace", deny)
    monkeypatch.setattr(recovery.time, "sleep", waits.append)
    with pytest.raises(Cancelled):
        recovery.write_manifest(tmp_path, {"status": "writing"}, check_cancel=cancel)
    assert len(attempts) == 1
    assert sum(waits) <= 0.05
    assert json.loads((tmp_path / "manifest.json").read_bytes()) == {"status": "ready"}
    assert json.loads((tmp_path / "manifest.json.new").read_bytes()) == {"status": "writing"}


def test_already_cancelled_retry_does_not_wait_or_publish(tmp_path, monkeypatch):
    recovery.write_manifest(tmp_path, {"status": "ready"})
    attempts = []

    class Cancelled(Exception):
        pass

    def cancel():
        raise Cancelled()

    def deny(*_):
        attempts.append(True)
        raise windows_error(32)

    monkeypatch.setattr(recovery, "_atomic_replace", deny)
    monkeypatch.setattr(recovery.time, "sleep", lambda _: pytest.fail("Cancellation must not wait"))
    with pytest.raises(Cancelled):
        recovery.write_manifest(tmp_path, {"status": "writing"}, check_cancel=cancel)
    assert len(attempts) == 1
    assert json.loads((tmp_path / "manifest.json").read_bytes()) == {"status": "ready"}


@pytest.mark.parametrize("phase", ["file_flush", "directory_flush"])
def test_flush_failure_is_fatal_even_with_retryable_windows_error(tmp_path, monkeypatch, phase):
    recovery.write_manifest(tmp_path, {"status": "ready"})
    error = windows_error(5)

    def fail(*_):
        raise error

    monkeypatch.setattr(recovery.time, "sleep", lambda _: pytest.fail("Only replacement may retry"))
    if phase == "file_flush":
        monkeypatch.setattr(recovery.os, "fsync", fail)
        monkeypatch.setattr(recovery, "_atomic_replace", lambda *_: pytest.fail("Unflushed journal must not publish"))
    else:
        monkeypatch.setattr(recovery, "sync_directory", fail)
    with pytest.raises(OSError) as caught:
        recovery.write_manifest(tmp_path, {"status": "writing"})
    assert caught.value is error
    expected = "ready" if phase == "file_flush" else "writing"
    assert json.loads((tmp_path / "manifest.json").read_bytes()) == {"status": expected}
