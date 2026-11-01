"""A physical disk change must stop saves even when both disks are clones."""

from pathlib import Path
import shutil

import pytest

from aps_midi_prep_tool_app import floppy_image
from test_windows_floppy_save import (
    _package,
    _prepared_save,
    mounted_drive,
    protected_target,
    save_session,
)


def _contents(root):
    return {path.name: path.read_bytes() for path in root.iterdir()}


class _Guard:
    def __init__(self, media):
        self.media = media
        self.baseline_count = media.generation
        self.closed = False
        self.error = None

    def check(self):
        assert not self.closed, "The media guard must remain open through recovery"
        if self.media.check_error:
            self.error = OSError("The media-change query is unavailable")
        elif self.media.generation != self.baseline_count:
            self.error = OSError("The floppy media-change counter changed")
        if self.error is not None:
            raise self.error

    def close(self):
        assert not self.closed
        self.closed = True


@pytest.fixture
def media(mounted_drive, monkeypatch):
    class Media:
        generation = 7
        check_error = False

        def __init__(self):
            self.guards = []
            self.swapped_contents = None

        def open(self, _drive):
            guard = _Guard(self)
            self.guards.append(guard)
            return guard

        def swap(self):
            assert self.swapped_contents is None
            self.swapped_contents = _contents(mounted_drive)
            removed = mounted_drive.with_name("removed-floppy")
            mounted_drive.rename(removed)
            shutil.copytree(removed, mounted_drive)
            self.generation += 1

    result = Media()
    # Neither volume metadata nor file hashes can distinguish these disks.
    monkeypatch.setattr(floppy_image, "_windows_volume_identity", lambda _: {
        "serial": 0xAFD570F9, "label": "NO NAME", "filesystem": "FAT",
    })
    monkeypatch.setattr(floppy_image, "_windows_media_change_guard", result.open)
    return result


def _swap_after_checkpoint(monkeypatch, media, *, operation=None):
    package_type = floppy_image.floppy_save_recovery.SaveRecoveryPackage
    if operation is None:
        original = package_type.checkpoint

        def checkpoint(package, **fields):
            original(package, **fields)
            if fields.get("status") == "ready":
                media.swap()

        monkeypatch.setattr(package_type, "checkpoint", checkpoint)
    else:
        original = package_type.before

        def before(package, action, name):
            original(package, action, name)
            if action == operation:
                media.swap()

        monkeypatch.setattr(package_type, "before", before)


@pytest.mark.parametrize("stage", ["ready", "delete", "replace"])
@pytest.mark.parametrize("check_originals", [True, False])
def test_clone_swapped_after_preflight_or_checkpoint_is_not_modified(
    save_session, mounted_drive, media, tmp_path, monkeypatch, stage, check_originals,
):
    # Addition has no deletion before its publication checkpoint, so every
    # variation can prove that the replacement disk receives no mutations.
    direct = stage == "replace"
    job = _prepared_save(tmp_path, {"SONG.FIL": b"new song"}) if direct else save_session
    _swap_after_checkpoint(monkeypatch, media, operation=None if stage == "ready" else stage)

    with pytest.raises(floppy_image.FloppyImageError):
        job._sync_modified_image_files_to_windows_drive(
            None if direct else "prepared.img", "A:", check_originals=check_originals,
        )

    assert media.swapped_contents == {"OLD.MID": b"original song"}
    assert _contents(mounted_drive) == media.swapped_contents
    assert len(media.guards) == 1 and media.guards[0].closed
    diagnostics = job.last_floppy_save_diagnostics
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
    assert job.last_write_verification["confidence"] == "not_written"
    _, manifest = _package(job)
    assert manifest["status"] == "failed"


def test_clone_swapped_before_readonly_clear_keeps_its_original_attributes(
    save_session, mounted_drive, protected_target, media, monkeypatch,
):
    attributes, operations = protected_target
    expected_attributes = dict(attributes)
    _swap_after_checkpoint(monkeypatch, media, operation="clear_readonly")

    with pytest.raises(floppy_image.FloppyImageError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert _contents(mounted_drive) == media.swapped_contents == save_session.originals
    assert attributes == expected_attributes
    assert operations == []
    assert save_session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    assert media.guards[0].closed


def test_attribute_recovery_does_not_modify_a_clone_with_identical_contents(
    save_session, mounted_drive, protected_target, media, monkeypatch,
):
    attributes, operations = protected_target
    set_attributes = floppy_image._set_windows_file_attributes
    clone_attributes = {}

    def clear_then_swap_and_fail(path, value):
        set_attributes(path, value)
        clone_attributes.update(attributes)
        media.swap()
        raise OSError("Floppy became unavailable after clearing attributes")

    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", clear_then_swap_and_fail)

    with pytest.raises(OSError, match="Floppy became unavailable"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert _contents(mounted_drive) == media.swapped_contents == save_session.originals
    assert attributes == clone_attributes
    assert operations == [("attributes", "LAST.FIL", 0x26)]
    _, manifest = _package(save_session)
    assert manifest["attributes_pending"]["LAST.FIL"]["attributes"] == 0x27
    assert "LAST.FIL" in manifest["diagnostics"]["attribute_restoration"]["errors"]
    assert media.guards[0].closed


def test_unavailable_media_monitor_stops_before_target_mutation(
    save_session, mounted_drive, monkeypatch,
):
    def unsupported(_drive):
        raise floppy_image.FloppyImageError("Windows cannot monitor media changes")

    monkeypatch.setattr(floppy_image, "_windows_media_change_guard", unsupported)

    with pytest.raises(floppy_image.FloppyImageError, match="cannot monitor"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert _contents(mounted_drive) == save_session.originals
    assert save_session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    assert save_session.last_write_verification["confidence"] == "not_written"


def test_media_query_becoming_unavailable_after_preflight_stops_save(
    save_session, mounted_drive, media, monkeypatch,
):
    package_type = floppy_image.floppy_save_recovery.SaveRecoveryPackage
    checkpoint = package_type.checkpoint

    def lose_query(package, **fields):
        checkpoint(package, **fields)
        if fields.get("status") == "ready":
            media.check_error = True

    monkeypatch.setattr(package_type, "checkpoint", lose_query)

    with pytest.raises(floppy_image.FloppyImageError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert _contents(mounted_drive) == save_session.originals
    assert save_session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    assert media.guards[0].closed


def test_successful_file_save_releases_the_media_guard(save_session, mounted_drive, media):
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert _contents(mounted_drive) == save_session.prepared
    assert len(media.guards) == 1 and media.guards[0].closed


def test_clone_swapped_during_copy_receives_no_subsequent_chunks(
    save_session, mounted_drive, media, tmp_path, monkeypatch,
):
    payload = b"new song" * 20_000
    job = _prepared_save(tmp_path, {"SONG.FIL": payload})
    destination = mounted_drive / "SONG.FIL"
    real_open = open
    writes = []

    class SwappingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, chunk):
            writes.append((media.generation, bytes(chunk)))
            count = self.handle.write(chunk)
            if len(writes) == 1:
                # Closing permits a portable directory swap on Windows too.
                # Rebind the simulated driver output to the replacement disk
                # so a missing check would visibly append to the wrong disk.
                self.handle.close()
                media.swap()
                self.handle = real_open(destination, "ab", buffering=0)
            return count

    def open_file(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if Path(path) == destination and mode == "xb":
            assert kwargs.get("buffering") == 0
            return SwappingWriter(handle)
        return handle

    monkeypatch.setattr(floppy_image, "open", open_file, raising=False)

    with pytest.raises(floppy_image.FloppyImageError):
        job._sync_modified_image_files_to_windows_drive(None, "A:")

    assert len(writes) == 1
    assert writes[0][1] == payload[:64 * 1024]
    assert _contents(mounted_drive) == media.swapped_contents
    assert destination.read_bytes() == payload[:64 * 1024]
    assert job.last_floppy_save_diagnostics["files_copied"] == 0
    assert media.guards[0].closed


def test_media_change_during_created_file_checkpoint_prevents_first_write(
    save_session, mounted_drive, media, monkeypatch,
):
    save_session.prepared = {"OLD.MID": b"replacement song" * 10_000}
    destination = mounted_drive / "OLD.MID"
    package_type = floppy_image.floppy_save_recovery.SaveRecoveryPackage
    checkpoint = package_type.checkpoint
    swaps = []
    writes = []
    real_open = open

    def checkpoint_then_swap(package, **fields):
        checkpoint(package, **fields)
        if "OLD.MID" in fields.get("attributes_pending", {}):
            assert destination.stat().st_size == 0
            # The file is already open, so simulate the driver's counter here
            # without relying on directory renames permitted only on POSIX.
            media.generation += 1
            swaps.append(media.generation)

    class RecordingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, chunk):
            writes.append(bytes(chunk))
            return self.handle.write(chunk)

    def open_file(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if Path(path) == destination and mode == "xb":
            assert kwargs.get("buffering") == 0
            return RecordingWriter(handle)
        return handle

    monkeypatch.setattr(package_type, "checkpoint", checkpoint_then_swap)
    monkeypatch.setattr(floppy_image, "open", open_file, raising=False)

    with pytest.raises(floppy_image.FloppyImageError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert len(swaps) == 1
    assert writes == []
    assert destination.stat().st_size == 0
    assert media.guards[0].closed


def test_clone_swapped_after_copy_is_not_given_original_file_attributes(
    save_session, mounted_drive, protected_target, media, monkeypatch,
):
    attributes, operations = protected_target
    copy = floppy_image._copy_prepared_floppy_file
    clone_attributes = {}

    def copy_then_swap(source, destination, **kwargs):
        copy(source, destination, **kwargs)
        clone_attributes.update(attributes)
        media.swap()

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", copy_then_swap)

    with pytest.raises(floppy_image.FloppyImageError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert _contents(mounted_drive) == media.swapped_contents
    assert attributes == clone_attributes
    assert operations == [("attributes", "LAST.FIL", 0x26), ("publish", "FIRST.FIL")]
    _, manifest = _package(save_session)
    assert set(manifest["attributes_pending"]) == {"FIRST.FIL"}
    assert media.guards[0].closed
