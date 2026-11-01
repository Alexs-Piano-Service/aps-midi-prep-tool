"""Cloned floppy contents never substitute for an OS media-change check."""

import errno
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from test_linux_floppy_save import floppy_save, _manifest  # noqa: F401


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


def _mutates_target(args, target):
    command = os.path.basename(os.fspath(args[0]))
    return target in args and (
        command == "mdel" or command == "mcopy" and str(args[-1]).startswith("::/")
    )


@pytest.fixture
def media(floppy_save, monkeypatch, tmp_path):
    _session, _source, target, _read_file = floppy_save
    path = Path(target)
    clone = tmp_path / "cloned-floppy.img"
    clone.write_bytes(path.read_bytes())
    state = SimpleNamespace(
        generation=1, guards=[], error=None, swapped_bytes=None, clone=clone,
    )

    def swap():
        before = path.stat()
        # Two media can be identical at the instant of substitution. Reuse
        # the same host inode to model a physical device node that persists
        # while the inserted floppy changes.
        clone.write_bytes(path.read_bytes())
        payload = clone.read_bytes()
        with path.open("r+b") as handle:
            handle.write(payload)
            handle.truncate(len(payload))
        after = path.stat()
        assert (before.st_dev, before.st_ino, before.st_rdev) == (
            after.st_dev, after.st_ino, after.st_rdev,
        )
        assert path.read_bytes()[:512] == payload[:512]
        state.swapped_bytes = payload
        state.generation += 1

    class Guard:
        def __init__(self, requested):
            assert os.fspath(requested) == target
            self.baseline = state.generation
            self.closed = False
            self.failure = None
            state.guards.append(self)

        def check(self):
            assert not self.closed
            if self.failure is None:
                if state.error is not None:
                    self.failure = state.error
                elif self.baseline != state.generation:
                    self.failure = OSError("The inserted floppy changed")
            if self.failure is not None:
                raise self.failure

        def close(self):
            assert not self.closed
            self.closed = True

    state.swap = swap
    monkeypatch.setattr(image, "_posix_media_change_guard", Guard, raising=False)
    return state


@pytest.mark.parametrize("checks", [True, False], ids=["checks-enabled", "checks-disabled"])
@pytest.mark.parametrize("boundary", ["ready", "delete", "replace"])
def test_clone_swap_after_preflight_or_journal_stops_before_next_mutation(
    floppy_save, media, monkeypatch, checks, boundary,
):
    session, source, target, _read_file = floppy_save
    package_type = image.floppy_save_recovery.SaveRecoveryPackage
    checkpoint, before = package_type.checkpoint, package_type.before
    mutations_after_swap = []
    run = session._run_mtools

    def checkpoint_then_swap(package, **fields):
        checkpoint(package, **fields)
        if boundary == "ready" and fields.get("status") == "ready":
            media.swap()

    def before_then_swap(package, operation, name):
        before(package, operation, name)
        if operation == boundary and media.swapped_bytes is None:
            media.swap()

    def record(args, message, **kwargs):
        if media.swapped_bytes is not None and _mutates_target(args, target):
            mutations_after_swap.append(args)
        return run(args, message, **kwargs)

    monkeypatch.setattr(package_type, "checkpoint", checkpoint_then_swap)
    monkeypatch.setattr(package_type, "before", before_then_swap)
    monkeypatch.setattr(session, "_run_mtools", record)

    with pytest.raises(image.FloppyImageError, match="changed|media"):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=checks, check_originals=checks, verify_copies=checks,
        )

    assert media.swapped_bytes is not None
    assert Path(target).read_bytes() == media.swapped_bytes == media.clone.read_bytes()
    assert mutations_after_swap == []
    assert len(media.guards) == 1 and media.guards[0].closed
    diagnostics = session.last_floppy_save_diagnostics
    if boundary != "replace":
        assert diagnostics["target_mutation_attempted"] is False
        assert session.last_write_verification["confidence"] == "not_written"
    else:
        assert diagnostics["files_copied"] == 0
        assert diagnostics["restoration"]["status"] == ("incomplete" if checks else "unavailable")
    _manifest(session)


def test_swap_during_host_alias_preparation_cannot_launch_copy_or_recovery(
    floppy_save, media, monkeypatch,
):
    session, source, target, _read_file = floppy_save
    prepare_alias = image._mtools_host_source_path
    popen = image.subprocess.Popen
    launches_after_swap = []

    def prepare_then_swap(*args, **kwargs):
        result = prepare_alias(*args, **kwargs)
        if media.swapped_bytes is None:
            media.swap()
        return result

    def record_popen(args, *rest, **kwargs):
        if media.swapped_bytes is not None and _mutates_target(args, target):
            launches_after_swap.append(args)
        return popen(args, *rest, **kwargs)

    monkeypatch.setattr(image, "_mtools_host_source_path", prepare_then_swap)
    monkeypatch.setattr(image.subprocess, "Popen", record_popen)

    with pytest.raises(image.FloppyImageError, match="changed|media"):
        session._sync_modified_image_files_to_floppy_drive(source, target)

    assert media.swapped_bytes is not None
    assert launches_after_swap == []
    assert Path(target).read_bytes() == media.swapped_bytes
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "incomplete"
    assert media.guards[0].closed


def test_swap_at_command_launch_is_checked_before_starting_mtools(
    floppy_save, media, monkeypatch,
):
    session, source, target, _read_file = floppy_save
    run = session._run_mtools
    popen = image.subprocess.Popen
    mutations = []

    def swap_before_command(args, message, **kwargs):
        if _mutates_target(args, target) and media.swapped_bytes is None:
            media.swap()
        return run(args, message, **kwargs)

    def record_popen(args, *rest, **kwargs):
        if _mutates_target(args, target):
            mutations.append(args)
        return popen(args, *rest, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", swap_before_command)
    monkeypatch.setattr(image.subprocess, "Popen", record_popen)

    with pytest.raises(image.FloppyImageError, match="changed|media"):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=False, check_originals=False, verify_copies=False,
        )

    assert media.swapped_bytes is not None
    assert mutations == []
    assert Path(target).read_bytes() == media.swapped_bytes
    assert media.guards[0].closed


def test_media_change_while_mtools_runs_terminates_it_and_blocks_recovery(
    floppy_save, media, monkeypatch,
):
    session, source, target, _read_file = floppy_save
    popen = image.subprocess.Popen
    stopped = threading.Event()
    later_mutations = []

    class RunningCommand:
        returncode = None

        def communicate(self):
            assert stopped.wait(timeout=5), "Media monitoring did not stop the running tool"
            return "", ""

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15
            stopped.set()

        def wait(self, timeout=None):
            assert stopped.wait(timeout=timeout)
            return self.returncode

        kill = terminate

    def swap_after_launch(args, *rest, **kwargs):
        if _mutates_target(args, target):
            if media.swapped_bytes is None:
                media.swap()
                return RunningCommand()
            later_mutations.append(args)
        return popen(args, *rest, **kwargs)

    monkeypatch.setattr(image.subprocess, "Popen", swap_after_launch)

    with pytest.raises(image.FloppyImageError, match="changed|media"):
        session._sync_modified_image_files_to_floppy_drive(source, target)

    assert stopped.is_set()
    assert later_mutations == []
    assert Path(target).read_bytes() == media.swapped_bytes
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "incomplete"
    assert media.guards[0].closed


@pytest.mark.parametrize("failure", ["initial", "after-preflight"])
def test_unavailable_media_monitor_stops_without_mutating_target(
    floppy_save, media, monkeypatch, failure,
):
    session, source, target, _read_file = floppy_save
    original = Path(target).read_bytes()
    error = OSError(errno.EOPNOTSUPP, "Media monitoring unavailable")
    if failure == "initial":
        def fail_guard(_path):
            raise error

        monkeypatch.setattr(image, "_posix_media_change_guard", fail_guard)
    else:
        package_type = image.floppy_save_recovery.SaveRecoveryPackage
        checkpoint = package_type.checkpoint

        def lose_monitor(package, **fields):
            checkpoint(package, **fields)
            if fields.get("status") == "ready":
                media.error = error

        monkeypatch.setattr(package_type, "checkpoint", lose_monitor)

    with pytest.raises(image.FloppyImageError, match="media|Media"):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=False, check_originals=False, verify_copies=False,
        )

    assert Path(target).read_bytes() == original
    assert session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    assert session.last_write_verification["confidence"] == "not_written"
    assert all(guard.closed for guard in media.guards)


def test_successful_save_closes_retained_media_guard(floppy_save, media):
    session, source, target, read_file = floppy_save

    session._sync_modified_image_files_to_floppy_drive(source, target)

    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert len(media.guards) == 1 and media.guards[0].closed
