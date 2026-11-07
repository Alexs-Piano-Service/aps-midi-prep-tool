"""Rollback must preserve changes arriving after its ownership checks."""

import builtins
import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.helpers import atomic_file, file_batch
from test_file_batch import recovery_locations


def _file(directory, name, contents):
    path = directory / name
    path.write_bytes(contents)
    return path


def _is_destination(path, destination):
    # Also recognize operations anchored to a directory descriptor.
    candidate = Path(path)
    return candidate == destination or candidate == Path(destination.name)


def _assert_recoverable_external_file(error, destination, identity, contents, root):
    assert error.rollback_errors
    assert error.recovery_directory
    recovery = Path(error.recovery_directory)
    assert recovery.is_dir()
    manifest = json.loads((recovery / "manifest.json").read_text())
    assert manifest["rollback_errors"] == error.rollback_errors
    diagnostics = "\n".join(error.rollback_errors)
    manifest_text = json.dumps(manifest)
    candidates = [destination, *root.rglob("*"), *recovery.rglob("*")]
    retained = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        info = candidate.stat()
        if (info.st_dev, info.st_ino) == identity and candidate.read_bytes() == contents:
            retained.append(candidate)
    assert retained, "Rollback lost the external file that arrived after its ownership check"
    assert any(
        path == destination or str(path) in diagnostics or str(path) in manifest_text
        for path in retained
    ), "A displaced external file must have a reported recovery location"
    record = next(item for item in manifest["files"] if item["destination"] == str(destination))
    assert record["published"] and not record["restored"]
    assert (recovery / record["prepared_file"]).read_bytes() == b"prepared image"
    if record["existed"]:
        assert (recovery / record["original_file"]).read_bytes() == b"original first"
    return manifest


@pytest.mark.parametrize("existed", [False, True])
def test_replacement_after_last_check_is_preserved_by_rollback(
    tmp_path, monkeypatch, recovery_locations, existed,
):
    first = tmp_path / "DISK001.HFE"
    if existed:
        first.write_bytes(b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    prepared = _file(tmp_path, "prepared.hfe", b"prepared image")
    external = _file(tmp_path, "external.hfe", b"another application's image")
    external_info = external.stat()
    external_identity = (external_info.st_dev, external_info.st_ino)
    original_atomic = file_batch.atomic_write_bytes
    rolling_back = False
    replaced = False

    def inject_before_change(source=None, destination=None):
        nonlocal replaced
        if not rolling_back or replaced:
            return
        if ((source is not None and _is_destination(source, first)) or
                (destination is not None and _is_destination(destination, first))):
            # A syscall boundary is after every caller-side stat/hash check.
            # In the fixed path this moves the contender to quarantine, where
            # verification must reject it rather than delete its contents.
            os.replace(external, first)
            replaced = True

    def replace(source, destination, *args, **kwargs):
        inject_before_change(source, destination)
        return os.replace(source, destination, *args, **kwargs)

    def rename(source, destination, *args, **kwargs):
        inject_before_change(source, destination)
        return os.rename(source, destination, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        inject_before_change(source=path)
        return os.unlink(path, *args, **kwargs)

    def publish_or_fail(destination, data, **kwargs):
        nonlocal rolling_back
        if Path(destination) == second:
            rolling_back = True
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    wrapped_os = SimpleNamespace(**(vars(os) | {
        "replace": replace, "rename": rename, "unlink": unlink,
    }))
    monkeypatch.setattr(atomic_file, "os", wrapped_os)
    monkeypatch.setattr(file_batch, "os", wrapped_os)
    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
        file_batch.publish_file_batch([(prepared, first), (prepared, second)])

    assert replaced
    assert second.read_bytes() == b"original second"
    _assert_recoverable_external_file(
        caught.value, first, external_identity, b"another application's image", tmp_path,
    )


def test_competing_file_during_original_restoration_is_not_overwritten(
    tmp_path, monkeypatch, recovery_locations,
):
    first = _file(tmp_path, "DISK001.HFE", b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    prepared = _file(tmp_path, "prepared.hfe", b"prepared image")
    external = _file(tmp_path, "external.hfe", b"late competing image")
    external_info = external.stat()
    external_identity = (external_info.st_dev, external_info.st_ino)
    original_atomic = file_batch.atomic_write_bytes
    restoring = False
    inserted = False

    def inject_before_publication(destination):
        nonlocal inserted
        if restoring and not inserted and _is_destination(destination, first):
            # On the corrected path quarantine has vacated the output name.
            # Another process may acquire that name while restoration stages.
            os.replace(external, first)
            inserted = True

    def replace(source, destination, *args, **kwargs):
        inject_before_publication(destination)
        return os.replace(source, destination, *args, **kwargs)

    def rename(source, destination, *args, **kwargs):
        inject_before_publication(destination)
        return os.rename(source, destination, *args, **kwargs)

    def link(source, destination, *args, **kwargs):
        inject_before_publication(destination)
        return os.link(source, destination, *args, **kwargs)

    def publish_or_fail(destination, data, **kwargs):
        nonlocal restoring
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        restoring = Path(destination) == first and data == b"original first"
        try:
            return original_atomic(destination, data, **kwargs)
        finally:
            restoring = False

    wrapped_os = SimpleNamespace(**(vars(os) | {
        "replace": replace, "rename": rename, "link": link,
    }))
    monkeypatch.setattr(atomic_file, "os", wrapped_os)
    monkeypatch.setattr(file_batch, "os", wrapped_os)
    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
        file_batch.publish_file_batch([(prepared, first), (prepared, second)])

    assert inserted
    assert first.read_bytes() == b"late competing image"
    assert second.read_bytes() == b"original second"
    _assert_recoverable_external_file(
        caught.value, first, external_identity, b"late competing image", tmp_path,
    )


def test_competitor_after_quarantine_is_preserved_and_reported_with_captured_output(
    tmp_path, monkeypatch, recovery_locations,
):
    first = tmp_path / "DISK001.HFE"
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    prepared = _file(tmp_path, "prepared.hfe", b"prepared image")
    original_atomic = file_batch.atomic_write_bytes
    captured = None

    def rename_then_compete(source, destination, *args, **kwargs):
        nonlocal captured
        result = os.rename(source, destination, *args, **kwargs)
        if _is_destination(source, first):
            captured = Path(destination)
            assert captured.read_bytes() == b"prepared image"
            assert not first.exists()
            first.write_bytes(b"new arrival after quarantine")
        return result

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(**(vars(os) | {
        "rename": rename_then_compete,
    })))
    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
        file_batch.publish_file_batch([(prepared, first), (prepared, second)])

    assert captured is not None
    assert captured.read_bytes() == b"prepared image"
    assert first.read_bytes() == b"new arrival after quarantine"
    assert second.read_bytes() == b"original second"
    error = caught.value
    assert error.rollback_errors
    assert set(error.retained_paths) == {str(first), str(captured)}
    recovery = Path(error.recovery_directory)
    manifest = json.loads((recovery / "manifest.json").read_text())
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["published"] and not record["restored"]
    assert set(record["retained_paths"]) == {str(first), str(captured)}
    assert manifest["rollback_errors"] == error.rollback_errors


@pytest.mark.skipif(os.name == "nt", reason="Ordinary Windows handles prevent moving an open file")
@pytest.mark.parametrize("existed", [False, True])
def test_open_descriptor_edits_after_rollback_remain_in_reported_quarantine(
    tmp_path, monkeypatch, recovery_locations, existed,
):
    first = tmp_path / "DISK001.HFE"
    if existed:
        first.write_bytes(b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    prepared = _file(tmp_path, "prepared.hfe", b"prepared image")
    original_atomic = file_batch.atomic_write_bytes
    writer = None

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    def hold_published_file(index, _total, _destination):
        nonlocal writer
        if index == 1:
            writer = open(first, "r+b", buffering=0)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    try:
        with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
            file_batch.publish_file_batch(
                [(prepared, first), (prepared, second)], progress_callback=hold_published_file,
            )

        assert writer is not None
        identity = os.fstat(writer.fileno())
        # No number of earlier stat/hash checks can prevent this write.
        writer.seek(0)
        writer.write(b"external write after completed rollback")
        writer.truncate()
        writer.flush()
        os.fsync(writer.fileno())
    finally:
        if writer is not None:
            writer.close()

    error = caught.value
    assert error.rollback_errors == []
    if existed:
        assert first.read_bytes() == b"original first"
    else:
        assert not first.exists()
    assert second.read_bytes() == b"original second"
    assert error.retained_paths
    retained = [Path(path) for path in error.retained_paths]
    edited = [path for path in retained if path.is_file()
              and (path.stat().st_dev, path.stat().st_ino) == (identity.st_dev, identity.st_ino)]
    assert len(edited) == 1
    assert edited[0].read_bytes() == b"external write after completed rollback"
    recovery = Path(error.recovery_directory)
    assert recovery.is_dir()
    manifest = json.loads((recovery / "manifest.json").read_text())
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["published"] and record["restored"]
    assert str(edited[0]) in record["retained_paths"]
    assert (recovery / record["prepared_file"]).read_bytes() == b"prepared image"
    if existed:
        assert (recovery / record["original_file"]).read_bytes() == b"original first"


def test_restoration_without_hardlinks_retains_recovery_and_never_streams_over_output(
    tmp_path, monkeypatch, recovery_locations,
):
    first = _file(tmp_path, "DISK001.HFE", b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    prepared = _file(tmp_path, "prepared.hfe", b"prepared image")
    original_atomic = file_batch.atomic_write_bytes
    attempted_links = []
    attempted_copies = []

    def no_hardlinks(source, destination, *args, **kwargs):
        attempted_links.append((source, destination))
        raise OSError(errno.EOPNOTSUPP, "Hard links are unavailable")

    def observe_copy(path, mode="r", *args, **kwargs):
        if _is_destination(path, first) and mode == "xb":
            attempted_copies.append(path)
        return builtins.open(path, mode, *args, **kwargs)

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(**(vars(os) | {
        "name": "posix", "link": no_hardlinks,
    })))
    monkeypatch.setattr(atomic_file, "open", observe_copy, raising=False)
    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
        file_batch.publish_file_batch([(prepared, first), (prepared, second)])

    assert attempted_links
    assert attempted_copies == []
    error = caught.value
    assert error.rollback_errors
    assert error.retained_paths
    assert any(Path(path).read_bytes() == b"prepared image" for path in error.retained_paths)
    assert not first.exists()
    assert second.read_bytes() == b"original second"
    recovery = Path(error.recovery_directory)
    manifest = json.loads((recovery / "manifest.json").read_text())
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["published"] and not record["restored"]
    assert record["retained_paths"]
    assert (recovery / record["original_file"]).read_bytes() == b"original first"
    assert (recovery / record["prepared_file"]).read_bytes() == b"prepared image"
