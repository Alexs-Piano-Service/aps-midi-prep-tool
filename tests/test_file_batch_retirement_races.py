"""Retirement checks the entry captured by rename, never a public pathname."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.helpers import file_batch
from test_file_batch import recovery_locations


@pytest.mark.parametrize("replacement", ["different_bytes", "same_bytes", "symlink"])
def test_replacement_after_final_check_survives_retirement(
    tmp_path, monkeypatch, recovery_locations, replacement,
):
    old = tmp_path / "retired.img"
    original = b"original disk"
    old.write_bytes(original)
    external = tmp_path / "external.img"
    contents = original if replacement == "same_bytes" else b"another application's disk"
    external.write_bytes(contents)
    before = old.stat()
    os.utime(external, ns=(before.st_atime_ns, before.st_mtime_ns))
    contender = external
    if replacement == "symlink":
        contender = tmp_path / "link.img"
        try:
            contender.symlink_to(external)
        except OSError:
            pytest.skip("Symbolic links are unavailable")
    external_info = contender.lstat()
    injected = False

    def compete(path):
        nonlocal injected
        if Path(path) == old and not injected:
            os.replace(contender, old)
            injected = True

    def rename(source, destination, *args, **kwargs):
        compete(source)
        return os.rename(source, destination, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        # Also reproduces the bug on the old check-then-unlink implementation.
        compete(path)
        return os.unlink(path, *args, **kwargs)

    monkeypatch.setattr(file_batch, "os", SimpleNamespace(**(vars(os) | {
        "rename": rename, "unlink": unlink,
    })))
    with pytest.raises(file_batch.FileBatchWriteError, match="ownership conflict") as caught:
        file_batch.publish_file_batch(
            [], delete_files=[old], expected_hashes={old: hashlib.sha256(original).hexdigest()},
        )

    assert injected
    error = caught.value
    assert error.rollback_errors == []
    assert old.read_bytes() == original
    captured, = map(Path, error.retained_paths)
    assert captured.read_bytes() == contents
    info = captured.lstat()
    assert (info.st_dev, info.st_ino) == (external_info.st_dev, external_info.st_ino)
    assert captured.is_symlink() == (replacement == "symlink")
    assert captured.parent.parent == old.parent
    recovery = Path(error.recovery_directory)
    manifest = json.loads((recovery / "manifest.json").read_text())
    record, = manifest["files"]
    assert record["published"] and record["restored"]
    assert record["retained_paths"] == [str(captured)]
    assert record["quarantine_directory"] == str(captured.parent)
    assert (recovery / record["original_file"]).read_bytes() == original
    mapping = json.loads((captured.parent / "recovery.json").read_text())
    assert mapping["destination"] == str(old)
    assert mapping["captured"] == str(captured)


def test_new_public_file_after_capture_is_never_deleted(tmp_path, monkeypatch, recovery_locations):
    old = tmp_path / "retired.img"
    old.write_bytes(b"original disk")
    captured_paths = []

    def rename_then_compete(source, destination, *args, **kwargs):
        result = os.rename(source, destination, *args, **kwargs)
        if Path(source) == old:
            captured_paths.append(Path(destination))
            old.write_bytes(b"new arrival after capture")
        return result

    def unlink(path, *args, **kwargs):
        assert Path(path) != old, "Retirement must never unlink the public destination"
        return os.unlink(path, *args, **kwargs)

    monkeypatch.setattr(file_batch, "os", SimpleNamespace(**(vars(os) | {
        "rename": rename_then_compete, "unlink": unlink,
    })))
    file_batch.publish_file_batch(
        [], delete_files=[old], expected_hashes={old: hashlib.sha256(old.read_bytes()).hexdigest()},
    )

    assert old.read_bytes() == b"new arrival after capture"
    assert len(captured_paths) == 1
    assert not captured_paths[0].parent.exists()
    assert not any(path.exists() for path in recovery_locations)


def test_capture_cleanup_failure_preserves_file_and_restores_original(
    tmp_path, monkeypatch, recovery_locations,
):
    old = tmp_path / "retired.img"
    old.write_bytes(b"original disk")

    def fail_captured_unlink(path, *args, **kwargs):
        if Path(path).name == "captured":
            raise OSError("capture cleanup failed")
        return os.unlink(path, *args, **kwargs)

    monkeypatch.setattr(file_batch, "os", SimpleNamespace(**(vars(os) | {
        "unlink": fail_captured_unlink,
    })))
    with pytest.raises(file_batch.FileBatchWriteError, match="capture cleanup failed") as caught:
        file_batch.publish_file_batch(
            [], delete_files=[old], expected_hashes={old: hashlib.sha256(old.read_bytes()).hexdigest()},
        )

    assert old.read_bytes() == b"original disk"
    assert caught.value.rollback_errors == []
    captured, = map(Path, caught.value.retained_paths)
    assert captured.read_bytes() == b"original disk"
    assert Path(caught.value.recovery_directory).is_dir()


def test_capture_edited_before_commit_is_preserved(tmp_path, recovery_locations):
    old = tmp_path / "retired.img"
    old.write_bytes(b"original disk")
    captures = []

    def edit_capture(_index, _total, _destination):
        captured, = tmp_path.glob(".aps_retire_*/captured")
        captures.append(captured)
        before = captured.stat()
        captured.write_bytes(b"modified disk")
        os.utime(captured, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(file_batch.FileBatchWriteError, match="ownership conflict") as caught:
        file_batch.publish_file_batch(
            [], delete_files=[old], expected_hashes={old: hashlib.sha256(old.read_bytes()).hexdigest()},
            progress_callback=edit_capture,
        )

    captured, = captures
    assert captured.read_bytes() == b"modified disk"
    assert str(captured) in caught.value.retained_paths
    assert old.read_bytes() == b"original disk"


def test_ambiguous_capture_failure_keeps_recovery_mapping(tmp_path, monkeypatch, recovery_locations):
    old = tmp_path / "retired.img"
    old.write_bytes(b"original disk")

    def move_then_fail(source, destination, *args, **kwargs):
        os.rename(source, destination, *args, **kwargs)
        raise OSError("rename completion unknown")

    monkeypatch.setattr(file_batch, "os", SimpleNamespace(**(vars(os) | {"rename": move_then_fail})))
    with pytest.raises(file_batch.FileBatchWriteError, match="rename completion unknown") as caught:
        file_batch.publish_file_batch(
            [], delete_files=[old], expected_hashes={old: hashlib.sha256(old.read_bytes()).hexdigest()},
        )

    captured, = tmp_path.glob(".aps_retire_*/captured")
    assert captured.read_bytes() == b"original disk"
    assert str(captured) in caught.value.retained_paths
    recovery = Path(caught.value.recovery_directory)
    record, = json.loads((recovery / "manifest.json").read_text())["files"]
    assert str(captured) in record["retained_paths"]
    assert (recovery / record["original_file"]).read_bytes() == b"original disk"
