"""Exclusive publication also works on filesystems without hard links."""

import builtins
import errno
import os

import pytest

from aps_midi_prep_tool_app.helpers import atomic_file


@pytest.fixture
def unsupported_links(monkeypatch):
    def no_links(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, "Hard links are unavailable")
    monkeypatch.setattr(atomic_file.os, "link", no_links)


def test_unsupported_links_copy_and_verify_without_consuming_stage(tmp_path, unsupported_links):
    staged, destination = tmp_path / "staged.wav", tmp_path / "output.wav"
    content = bytes(range(256)) * 10000
    staged.write_bytes(content)
    atomic_file.publish_new_file(staged, destination)
    assert staged.read_bytes() == content
    assert destination.read_bytes() == content


def test_destination_created_at_fallback_boundary_is_not_overwritten(tmp_path, monkeypatch):
    staged, destination = tmp_path / "staged.wav", tmp_path / "output.wav"
    staged.write_bytes(b"prepared")

    def competing_create(*_args, **_kwargs):
        destination.write_bytes(b"another operation")
        raise OSError(errno.EPERM, "No hard links")

    monkeypatch.setattr(atomic_file.os, "link", competing_create)
    with pytest.raises(FileExistsError):
        atomic_file.publish_new_file(staged, destination)
    assert destination.read_bytes() == b"another operation"
    assert staged.read_bytes() == b"prepared"


def test_cancel_during_fallback_removes_only_new_partial_file(tmp_path, unsupported_links):
    staged, destination = tmp_path / "staged.wav", tmp_path / "output.wav"
    staged.write_bytes(b"x" * (2 * 1024 * 1024))

    def cancel():
        if destination.exists() and destination.stat().st_size:
            raise InterruptedError("Cancelled")

    with pytest.raises(InterruptedError):
        atomic_file.publish_new_file(staged, destination, check_cancel=cancel)
    assert not destination.exists()
    assert staged.stat().st_size == 2 * 1024 * 1024


def test_copy_error_removes_partial_and_preserves_stage(tmp_path, unsupported_links, monkeypatch):
    staged, destination = tmp_path / "staged.wav", tmp_path / "output.wav"
    staged.write_bytes(b"prepared output")

    class FailingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def write(self, data):
            self.handle.write(data[:3])
            self.handle.flush()
            raise OSError(errno.ENOSPC, "No space left")

    def open_file(path, mode):
        handle = builtins.open(path, mode)
        return FailingWriter(handle) if mode == "xb" else handle

    monkeypatch.setattr(atomic_file, "open", open_file, raising=False)
    with pytest.raises(OSError, match="No space"):
        atomic_file.publish_new_file(staged, destination)
    assert not destination.exists()
    assert staged.read_bytes() == b"prepared output"


@pytest.mark.skipif(os.name == "nt", reason="Windows does not allow replacing the open output")
def test_cancel_does_not_remove_a_replacement_owned_by_someone_else(tmp_path, unsupported_links):
    staged, destination = tmp_path / "staged.wav", tmp_path / "output.wav"
    staged.write_bytes(b"x" * (2 * 1024 * 1024))

    def replace_then_cancel():
        if destination.exists() and destination.stat().st_size:
            destination.unlink()
            destination.write_bytes(b"concurrent replacement")
            raise InterruptedError("Cancelled")

    with pytest.raises(InterruptedError):
        atomic_file.publish_new_file(staged, destination, check_cancel=replace_then_cancel)
    assert destination.read_bytes() == b"concurrent replacement"


def test_non_filesystem_errors_do_not_create_destination(tmp_path, monkeypatch):
    staged, destination = tmp_path / "staged.wav", tmp_path / "output.wav"
    staged.write_bytes(b"prepared")

    def denied(*_args, **_kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(atomic_file.os, "link", denied)
    with pytest.raises(OSError, match="Permission denied"):
        atomic_file.publish_new_file(staged, destination)
    assert not destination.exists()
