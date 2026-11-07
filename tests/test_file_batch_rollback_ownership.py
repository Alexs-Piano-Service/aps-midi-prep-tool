"""A failed batch must preserve outputs changed by another process."""

import errno
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.helpers import atomic_file, file_batch
from test_file_batch import _assert_retained_publications, recovery_locations


def _file(directory, name, contents):
    path = directory / name
    path.write_bytes(contents)
    return path


def _assert_retained_conflict(error, locations, destination, *, existed, prepared):
    assert len(error.rollback_errors) == 1
    assert str(destination) in error.rollback_errors[0]
    recovery = Path(error.recovery_directory)
    assert recovery in locations
    assert recovery.is_dir()
    manifest = json.loads((recovery / "manifest.json").read_text())
    assert manifest["rollback_errors"] == error.rollback_errors
    record = next(item for item in manifest["files"] if item["destination"] == str(destination))
    assert record["published"] and not record["restored"]
    assert record["existed"] is existed
    assert (recovery / record["prepared_file"]).read_bytes() == prepared
    publication = record["publication"]
    assert publication["sha256"] == hashlib.sha256(prepared).hexdigest()
    assert len(publication["identity"]) == 4
    assert publication["identity"][2] == len(prepared)
    assert len(publication["parent_identity"]) == 2
    if existed:
        assert (recovery / record["original_file"]).read_bytes() == b"original first"
    else:
        assert record["original_file"] is None
    return manifest


@pytest.mark.parametrize("existed", [False, True])
@pytest.mark.parametrize("mutation", ["edit", "replace_same_bytes"])
@pytest.mark.parametrize("when", ["after_atomic_publication", "after_progress"])
def test_later_failure_preserves_changed_publication(
    tmp_path, monkeypatch, recovery_locations, existed, mutation, when,
):
    first = tmp_path / "DISK001.HFE"
    if existed:
        first.write_bytes(b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    original_atomic = file_batch.atomic_write_bytes
    expected = b"external change" if mutation == "edit" else payload
    changed_identity = None
    published_identity = None

    def external_change():
        nonlocal changed_identity, published_identity
        before = first.stat()
        published_identity = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns]
        if mutation == "edit":
            # The digest must protect equal-sized edits even if another
            # application restores the output's modification timestamp.
            assert len(expected) == before.st_size
            first.write_bytes(expected)
            os.utime(first, ns=(before.st_atime_ns, before.st_mtime_ns))
        else:
            replacement = _file(tmp_path, "external.hfe", payload)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, first)
            assert (first.stat().st_dev, first.stat().st_ino) != (before.st_dev, before.st_ino)
        changed_identity = (first.stat().st_dev, first.stat().st_ino)

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        result = original_atomic(destination, data, **kwargs)
        if Path(destination) == first and data == payload and when == "after_atomic_publication":
            # This runs before publish_file_batch regains control: a receipt
            # based on a later stat/hash would mistakenly adopt this change.
            external_change()
        return result

    def progress(index, _total, _destination):
        if index == 1 and when == "after_progress":
            external_change()

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)], progress_callback=progress)

    assert first.read_bytes() == expected
    assert (first.stat().st_dev, first.stat().st_ino) == changed_identity
    assert second.read_bytes() == b"original second"
    assert isinstance(caught.value.original_exception, OSError)
    manifest = _assert_retained_conflict(
        caught.value, recovery_locations, first, existed=existed, prepared=payload,
    )
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["publication"]["identity"] == published_identity
    parent = first.parent.stat()
    assert record["publication"]["parent_identity"] == [parent.st_dev, parent.st_ino]


@pytest.mark.parametrize("existed", [False, True])
def test_later_failure_preserves_symlink_replacing_publication(
    tmp_path, monkeypatch, recovery_locations, existed,
):
    payload = b"new image bytes"
    target = _file(tmp_path, "external.hfe", payload)
    probe = tmp_path / "probe.hfe"
    try:
        probe.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable")
    probe.unlink()
    first = tmp_path / "DISK001.HFE"
    if existed:
        first.write_bytes(b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    staged = _file(tmp_path, "prepared.hfe", payload)
    original_atomic = file_batch.atomic_write_bytes

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    def external_change(index, _total, _destination):
        if index == 1:
            first.unlink()
            first.symlink_to(target)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)], progress_callback=external_change)

    assert first.is_symlink()
    assert first.resolve() == target
    assert target.read_bytes() == payload
    assert second.read_bytes() == b"original second"
    _assert_retained_conflict(
        caught.value, recovery_locations, first, existed=existed, prepared=payload,
    )


@pytest.mark.parametrize("existed", [False, True])
def test_conflict_does_not_prevent_rollback_of_other_owned_outputs(
    tmp_path, monkeypatch, recovery_locations, existed,
):
    owned = _file(tmp_path, "DISK001.HFE", b"original owned")
    conflict = tmp_path / "DISK002.HFE"
    if existed:
        conflict.write_bytes(b"original first")
    last = _file(tmp_path, "DISK003.HFE", b"original last")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    original_atomic = file_batch.atomic_write_bytes

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == last:
            conflict.write_bytes(b"another application's changes")
            raise OSError("Third image publication failed")
        return original_atomic(destination, data, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Third image publication failed") as caught:
        file_batch.publish_file_batch([(staged, owned), (staged, conflict), (staged, last)])

    assert conflict.read_bytes() == b"another application's changes"
    assert owned.read_bytes() == b"original owned"
    assert last.read_bytes() == b"original last"
    manifest = _assert_retained_conflict(
        caught.value, recovery_locations, conflict, existed=existed, prepared=payload,
    )
    owned_record = next(item for item in manifest["files"] if item["destination"] == str(owned))
    assert owned_record["published"] and owned_record["restored"]
    last_record = next(item for item in manifest["files"] if item["destination"] == str(last))
    assert not last_record["published"] and not last_record["restored"]


@pytest.mark.parametrize("existed", [False, True])
def test_later_failure_preserves_output_in_replaced_parent_directory(
    tmp_path, monkeypatch, recovery_locations, existed,
):
    output = tmp_path / "output"
    output.mkdir()
    first = output / "DISK001.HFE"
    if existed:
        first.write_bytes(b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    moved = tmp_path / "moved-output"
    original_atomic = file_batch.atomic_write_bytes

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    def external_change(index, _total, _destination):
        if index == 1:
            output.rename(moved)
            output.mkdir()
            first.write_bytes(b"another application's image")

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)], progress_callback=external_change)

    assert first.read_bytes() == b"another application's image"
    assert (moved / first.name).read_bytes() == payload
    assert second.read_bytes() == b"original second"
    _assert_retained_conflict(
        caught.value, recovery_locations, first, existed=existed, prepared=payload,
    )


def test_later_failure_keeps_external_deletion_of_existing_output(
    tmp_path, monkeypatch, recovery_locations,
):
    first = _file(tmp_path, "DISK001.HFE", b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    original_atomic = file_batch.atomic_write_bytes

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            first.unlink()
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    assert not first.exists()
    assert second.read_bytes() == b"original second"
    _assert_retained_conflict(
        caught.value, recovery_locations, first, existed=True, prepared=payload,
    )


def test_external_change_while_staging_restore_is_preserved(
    tmp_path, monkeypatch, recovery_locations,
):
    first = _file(tmp_path, "DISK001.HFE", b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    original_atomic = file_batch.atomic_write_bytes
    original_mkstemp = atomic_file.tempfile.mkstemp
    restoring = False
    edit_injected = False

    def publish_or_fail(destination, data, **kwargs):
        nonlocal restoring
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        restoring = Path(destination) == first and data == b"original first"
        try:
            return original_atomic(destination, data, **kwargs)
        finally:
            restoring = False

    def create_restore_temp(*args, **kwargs):
        nonlocal edit_injected
        result = original_mkstemp(*args, **kwargs)
        if restoring:
            first.write_bytes(b"external change during rollback")
            edit_injected = True
        return result

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    monkeypatch.setattr(atomic_file.tempfile, "mkstemp", create_restore_temp)
    with pytest.raises(file_batch.FileBatchWriteError) as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)])

    assert edit_injected
    assert first.read_bytes() == b"external change during rollback"
    assert second.read_bytes() == b"original second"
    assert list(tmp_path.glob(".aps_write_*.tmp")) == []
    _assert_retained_conflict(
        caught.value, recovery_locations, first, existed=True, prepared=payload,
    )


@pytest.mark.parametrize("mutation", ["edit", "replace_same_bytes"])
def test_copy_fallback_receipt_protects_new_output_from_rollback(
    tmp_path, monkeypatch, recovery_locations, mutation,
):
    first = tmp_path / "DISK001.HFE"
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    original_atomic = file_batch.atomic_write_bytes
    expected = b"external change" if mutation == "edit" else payload
    publication_stat = None
    link_attempts = []

    def no_hardlinks(source, destination):
        link_attempts.append((source, destination))
        raise OSError(errno.EOPNOTSUPP, "Hard links are unavailable")

    # Exercise the removable-filesystem copy path on every host without
    # changing pathlib's platform selection or unrelated os operations.
    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(
        **(vars(os) | {"name": "posix", "link": no_hardlinks}),
    ))

    def publish_or_fail(destination, data, **kwargs):
        if Path(destination) == second:
            raise OSError("Second image publication failed")
        return original_atomic(destination, data, **kwargs)

    def external_change(index, _total, _destination):
        nonlocal publication_stat
        if index != 1:
            return
        publication_stat = first.stat()
        if mutation == "edit":
            assert len(expected) == publication_stat.st_size
            first.write_bytes(expected)
            os.utime(first, ns=(publication_stat.st_atime_ns, publication_stat.st_mtime_ns))
        else:
            replacement = _file(tmp_path, "external.hfe", payload)
            os.utime(replacement, ns=(publication_stat.st_atime_ns, publication_stat.st_mtime_ns))
            os.replace(replacement, first)
            assert first.stat().st_ino != publication_stat.st_ino

    monkeypatch.setattr(file_batch, "atomic_write_bytes", publish_or_fail)
    with pytest.raises(file_batch.FileBatchWriteError, match="Second image publication failed") as caught:
        file_batch.publish_file_batch([(staged, first), (staged, second)], progress_callback=external_change)

    assert len(link_attempts) == 1
    assert first.read_bytes() == expected
    assert second.read_bytes() == b"original second"
    manifest = _assert_retained_conflict(
        caught.value, recovery_locations, first, existed=False, prepared=payload,
    )
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["publication"]["identity"] == [
        publication_stat.st_dev, publication_stat.st_ino,
        publication_stat.st_size, publication_stat.st_mtime_ns,
    ]
    assert record["publication"]["ctime_ns"] == publication_stat.st_ctime_ns


@pytest.mark.parametrize("existed", [False, True])
def test_successful_publication_is_rolled_back_when_temp_cleanup_raises(
    tmp_path, monkeypatch, recovery_locations, existed,
):
    first = tmp_path / "DISK001.HFE"
    if existed:
        first.write_bytes(b"original first")
    second = _file(tmp_path, "DISK002.HFE", b"original second")
    payload = b"new image bytes"
    staged = _file(tmp_path, "prepared.hfe", payload)
    cleanup_failures = []
    progress_calls = []

    def fail_first_temp_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".aps_write_") and not cleanup_failures:
            assert first.read_bytes() == payload
            cleanup_failures.append(Path(path))
            raise OSError("Temporary output cleanup failed")
        return os.unlink(path, *args, **kwargs)

    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(
        **(vars(os) | {"unlink": fail_first_temp_cleanup}),
    ))
    with pytest.raises(file_batch.FileBatchWriteError, match="Temporary output cleanup failed") as caught:
        file_batch.publish_file_batch(
            [(staged, first), (staged, second)],
            progress_callback=lambda *args: progress_calls.append(args),
        )

    assert len(cleanup_failures) == 1
    if existed:
        assert first.read_bytes() == b"original first"
    else:
        assert not first.exists()
    assert second.read_bytes() == b"original second"
    assert progress_calls == []
    assert caught.value.rollback_errors == []
    assert isinstance(caught.value.original_exception, OSError)
    _assert_retained_publications(caught.value, [payload])
