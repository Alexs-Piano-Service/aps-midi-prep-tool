"""Completed extraction jobs leave outputs without disposable JSON/lock files."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from aps_midi_prep_tool_app import bulk_extraction_job
from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from test_bulk_extraction import FakeImageSession, _interrupt_after_completed_image


@pytest.fixture
def extraction(tmp_path):
    source, output = tmp_path / "images", tmp_path / "output"
    source.mkdir()
    (source / "disk.img").write_bytes(b"image")
    checkpoint = output / "job.json"
    loader = lambda path, **kwargs: FakeImageSession(path, {"ONE.TXT": b"one", "metadata.json": b'{"album": "sample"}'})
    return source, output, checkpoint, loader


def test_success_removes_only_job_files_and_keeps_extracted_json(extraction):
    source, output, checkpoint, loader = extraction
    output.mkdir()
    unrelated = output / "unrelated.json"
    unrelated.write_bytes(b'{"keep": true}')
    result = bulk_extract_images(source, output, job_record_path=checkpoint, session_loader=loader)
    assert result.errors == ()
    assert result.job_record_path == ""
    assert not checkpoint.exists()
    assert not Path(str(checkpoint) + ".lock").exists()
    assert (output / "disk" / "ONE.TXT").read_bytes() == b"one"
    assert (output / "disk" / "metadata.json").read_bytes() == b'{"album": "sample"}'
    assert unrelated.read_bytes() == b'{"keep": true}'


def test_resuming_completed_checkpoint_verifies_outputs_then_removes_record(extraction):
    source, output, checkpoint, loader = extraction
    _interrupt_after_completed_image(source, output, checkpoint, loader)

    def no_reextraction(*_args, **_kwargs):
        pytest.fail("Verified outputs should be reused")

    result = bulk_extract_images(source, output, job_record_path=checkpoint, resume=True, session_loader=no_reextraction)
    assert result.errors == ()
    assert result.files_reused == 2
    assert result.images_skipped == 1
    assert not checkpoint.exists()
    assert result.job_record_path == ""


def test_cleanup_failure_is_reported_and_can_be_retried_without_reextracting(extraction, monkeypatch):
    source, output, checkpoint, loader = extraction
    real_unlink = os.unlink

    def locked_job(path, *args, **kwargs):
        if os.fspath(path) == str(checkpoint):
            raise PermissionError("Record is locked")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(bulk_extraction_job.os, "unlink", locked_job)
        result = bulk_extract_images(source, output, job_record_path=checkpoint, session_loader=loader)
    assert result.files_extracted == 2
    assert len(result.errors) == 1 and "Could not remove completed extraction job" in result.errors[0]
    assert checkpoint.is_file()
    assert result.job_record_path == str(checkpoint)
    assert not Path(str(checkpoint) + ".lock").exists()
    retry = bulk_extract_images(source, output, job_record_path=checkpoint, resume=True, session_loader=loader)
    assert retry.errors == ()
    assert retry.files_reused == 2 and retry.files_extracted == 0
    assert not checkpoint.exists()


def test_replaced_checkpoint_is_not_deleted(extraction):
    source, output, checkpoint, loader = extraction
    replacement = b'{"another": "document"}'

    def replace_job(detail):
        if detail["stage"] == "finished":
            checkpoint.write_bytes(replacement)

    result = bulk_extract_images(source, output, job_record_path=checkpoint, session_loader=loader,
                                 progress_detail_callback=replace_job)
    assert any("changed before cleanup" in error for error in result.errors)
    assert checkpoint.read_bytes() == replacement
    assert (output / "disk" / "ONE.TXT").read_bytes() == b"one"


def test_empty_job_does_not_leave_lock_file(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()
    output = tmp_path / "output"
    result = bulk_extract_images(source, output, job_record_path=output / "job.json")
    assert result.images_found == 0
    assert result.job_record_path == ""
    assert not list(output.iterdir())


@pytest.mark.parametrize("failure", [False, True])
def test_lock_is_removed_when_job_returns_or_raises(tmp_path, failure):
    job_path = tmp_path / "job.json"
    lock_path = tmp_path / "job.json.lock"
    try:
        with bulk_extraction_job._job_lock(job_path):
            assert lock_path.is_file()
            if failure:
                raise RuntimeError("Operation failed")
    except RuntimeError:
        assert failure
    assert not lock_path.exists()
    with bulk_extraction_job._job_lock(job_path):
        assert lock_path.is_file()
    assert not lock_path.exists()


def test_contending_process_does_not_remove_owners_lock(tmp_path):
    job_path = tmp_path / "job.json"
    code = '''
import sys
from aps_midi_prep_tool_app.bulk_extraction_job import _job_lock
try:
    with _job_lock(sys.argv[1]):
        sys.exit(2)
except ValueError as error:
    assert "already running" in str(error)
'''
    with bulk_extraction_job._job_lock(job_path):
        result = subprocess.run([sys.executable, "-c", code, str(job_path)], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "job.json.lock").exists()
    assert not (tmp_path / "job.json.lock").exists()


def test_lock_does_not_follow_or_remove_a_symlink(tmp_path):
    other = tmp_path / "unrelated.json"
    other.write_bytes(b'{"keep": true}')
    link = tmp_path / "job.json.lock"
    try:
        link.symlink_to(other)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ValueError, match="symbolic link"):
        with bulk_extraction_job._job_lock(tmp_path / "job.json"):
            pytest.fail("A lock symlink was followed")
    assert link.is_symlink()
    assert other.read_bytes() == b'{"keep": true}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlink/open-inode race")
def test_contender_reopens_lock_if_previous_owner_unlinked_its_open_inode(tmp_path, monkeypatch):
    import fcntl

    job_path = tmp_path / "job.json"
    lock_path = tmp_path / "job.json.lock"
    real_flock = fcntl.flock
    acquired = []

    def replaced_lock(fd, operation):
        real_flock(fd, operation)
        if operation & fcntl.LOCK_EX:
            acquired.append(os.fstat(fd))
            if len(acquired) == 1:
                # The descriptor was opened before the previous owner removed
                # its path; a subsequent opener has already made a new inode.
                lock_path.unlink()
                lock_path.write_bytes(b"new lock")

    monkeypatch.setattr(fcntl, "flock", replaced_lock)
    with bulk_extraction_job._job_lock(job_path):
        assert len(acquired) == 2
        assert os.path.samestat(acquired[1], lock_path.stat())
        assert not os.path.samestat(acquired[0], lock_path.stat())
        with pytest.raises(ValueError, match="already running"):
            with bulk_extraction_job._job_lock(job_path):
                pytest.fail("A stale descriptor bypassed the current lock")
    assert not lock_path.exists()
