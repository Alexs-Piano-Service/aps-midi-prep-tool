"""Rollback must verify and restore the same destination path."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.helpers import atomic_file, file_batch


@pytest.fixture
def recovery_root(tmp_path, monkeypatch):
    directory = tmp_path / "recovery"
    directory.mkdir()
    real_mkdtemp = file_batch.tempfile.mkdtemp

    def make_recovery(*args, **kwargs):
        if kwargs.get("prefix") == "aps_file_batch_" and not kwargs.get("dir"):
            kwargs["dir"] = directory
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(file_batch.tempfile, "mkdtemp", make_recovery)
    return directory


def _require_symlinks(directory):
    probe = directory / "symlink-probe"
    try:
        probe.symlink_to(directory / "unused")
    except OSError:
        pytest.skip("Symbolic links are unavailable")
    probe.unlink()


def _assert_retained_original(error, first):
    assert error.rollback_errors
    recovery = Path(error.recovery_directory)
    manifest = json.loads((recovery / "manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["published"] is True
    assert record["restored"] is False
    assert (recovery / record["original_file"]).read_bytes() == b"original first"
    assert (recovery / record["prepared_file"]).read_bytes() == b"new image"
    return record


def test_rollback_refuses_snapshot_for_a_different_publication_path(
    tmp_path, monkeypatch, recovery_root,
):
    _require_symlinks(tmp_path)
    first, second = tmp_path / "first.img", tmp_path / "second.img"
    actual, victim = tmp_path / "actual.img", tmp_path / "victim.img"
    staged = tmp_path / "prepared.img"
    first.write_bytes(b"original first")
    second.write_bytes(b"original second")
    actual.write_bytes(b"unrelated original")
    victim.write_bytes(b"another application's file")
    staged.write_bytes(b"new image")
    real_write = file_batch.atomic_write_bytes

    def change_path_before_publication(destination, payload, **kwargs):
        if Path(destination) == first and payload == b"new image":
            # The batch resolved the original path before this alias appeared.
            first.unlink()
            first.symlink_to(actual)
        if Path(destination) == second:
            first.unlink()
            first.symlink_to(victim)
            raise OSError("Second publication failed")
        return real_write(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", change_path_before_publication)

    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    assert first.is_symlink()
    assert victim.read_bytes() == b"another application's file"
    assert actual.read_bytes() == b"new image"
    assert second.read_bytes() == b"original second"
    record = _assert_retained_original(caught.value, first)
    assert record["publication"]["destination"] == str(actual)


def test_rollback_does_not_resolve_destination_through_a_transient_symlink(
    tmp_path, monkeypatch, recovery_root,
):
    _require_symlinks(tmp_path)
    first, second = tmp_path / "first.img", tmp_path / "second.img"
    victim, anchor = tmp_path / "victim.img", tmp_path / "published.img"
    staged = tmp_path / "prepared.img"
    first.write_bytes(b"original first")
    second.write_bytes(b"original second")
    victim.write_bytes(b"another application's file")
    staged.write_bytes(b"new image")
    real_write = file_batch.atomic_write_bytes
    real_realpath = os.path.realpath
    resolve_restore = False
    intercepted = []

    def resolve_transient_alias(path, *args, **kwargs):
        nonlocal resolve_restore
        if resolve_restore and Path(path) == first:
            resolve_restore = False
            first.rename(anchor)
            first.symlink_to(victim)
            resolved = real_realpath(path, *args, **kwargs)
            first.unlink()
            anchor.rename(first)
            intercepted.append(resolved)
            return resolved
        return real_realpath(path, *args, **kwargs)

    # Keep the simulated resolution race local to the atomic writer.
    path_queries = SimpleNamespace(**(vars(os.path) | {"realpath": resolve_transient_alias}))
    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(**(vars(os) | {"path": path_queries})))

    def fail_second_then_redirect_restore(destination, payload, **kwargs):
        nonlocal resolve_restore
        if Path(destination) == second:
            raise OSError("Second publication failed")
        if Path(destination) == first and payload == b"original first":
            # Any further realpath call here could redirect restoration even
            # if the original publication reappears before the final check.
            resolve_restore = True
        return real_write(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_second_then_redirect_restore)

    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    assert intercepted == []
    assert victim.read_bytes() == b"another application's file"
    assert first.read_bytes() == b"original first"
    assert not first.is_symlink()
    assert second.read_bytes() == b"original second"
    assert not caught.value.rollback_errors
    assert [Path(path).read_bytes() for path in caught.value.retained_paths] == [b"new image"]


def test_partial_publication_cleanup_error_keeps_batch_recovery(
    tmp_path, monkeypatch, recovery_root,
):
    first, second = tmp_path / "first.img", tmp_path / "second.img"
    staged = tmp_path / "prepared.img"
    first.write_bytes(b"original first")
    staged.write_bytes(b"new image")
    real_write = file_batch.atomic_write_bytes

    def fail_partial_cleanup(destination, payload, **kwargs):
        if Path(destination) == second:
            second.write_bytes(payload[:3])
            # Exclusive fallback reports retained output before it can issue
            # a completed publication receipt to the batch.
            raise atomic_file.FilePublicationCleanupError(
                destination, OSError("Copy interrupted"),
                PermissionError("Partial output could not be removed"),
            )
        return real_write(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_partial_cleanup)

    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    error = caught.value
    assert isinstance(error.original_exception, atomic_file.FilePublicationCleanupError)
    assert error.rollback_errors
    assert any(str(second) in issue for issue in error.rollback_errors)
    assert first.read_bytes() == b"original first"
    assert second.read_bytes() == b"new"
    recovery = Path(error.recovery_directory)
    manifest = json.loads((recovery / "manifest.json").read_text(encoding="utf-8"))
    first_record, second_record = manifest["files"]
    assert first_record["restored"] is True
    assert (recovery / first_record["original_file"]).read_bytes() == b"original first"
    assert second_record["published"] is False
    assert second_record["restored"] is False
    assert (recovery / second_record["prepared_file"]).read_bytes() == b"new image"


def test_restore_temp_cleanup_failure_cannot_hide_captured_external_file(
    tmp_path, monkeypatch, recovery_root,
):
    first, second = tmp_path / "first.img", tmp_path / "second.img"
    staged, external = tmp_path / "prepared.img", tmp_path / "external.img"
    first.write_bytes(b"original first")
    second.write_bytes(b"original second")
    staged.write_bytes(b"new image")
    external.write_bytes(b"another application's image")
    external_info = external.stat()
    real_write = file_batch.atomic_write_bytes
    rolling_back = False
    captures = []
    failed_cleanups = []

    def replace_at_quarantine(source, destination, *args, **kwargs):
        if rolling_back and Path(source) == first:
            # The last ownership check passed, but the move captures a newer
            # file. Its verification failure must report this recovery path.
            os.replace(external, first)
            result = os.rename(source, destination, *args, **kwargs)
            captures.append(Path(destination))
            return result
        return os.rename(source, destination, *args, **kwargs)

    def fail_restore_temp_cleanup(path, *args, **kwargs):
        if rolling_back and Path(path).name.startswith(".aps_write_"):
            failed_cleanups.append(Path(path))
            raise PermissionError("Restoration temp cleanup failed")
        return os.unlink(path, *args, **kwargs)

    def fail_second_publication(destination, payload, **kwargs):
        nonlocal rolling_back
        if Path(destination) == second:
            rolling_back = True
            raise OSError("Second publication failed")
        return real_write(destination, payload, **kwargs)

    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(**(vars(os) | {
        "rename": replace_at_quarantine, "unlink": fail_restore_temp_cleanup,
    })))
    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_second_publication)

    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    assert len(captures) == len(failed_cleanups) == 1
    captured = captures[0]
    captured_info = captured.stat()
    assert (captured_info.st_dev, captured_info.st_ino) == (
        external_info.st_dev, external_info.st_ino,
    )
    assert captured.read_bytes() == b"another application's image"
    assert not first.exists()
    assert second.read_bytes() == b"original second"
    error = caught.value
    assert any("Restoration temp cleanup failed" in issue for issue in error.rollback_errors)
    assert str(captured) in error.retained_paths
    record = _assert_retained_original(error, first)
    assert str(captured) in record["retained_paths"]
    mapping = json.loads((captured.parent / "recovery.json").read_text(encoding="utf-8"))
    assert mapping["destination"] == str(first)
    assert mapping["captured"] == str(captured)
