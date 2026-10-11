"""Loaded-floppy edits must select changes without optional physical reads."""

import os
from pathlib import Path
import shutil
import threading

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from test_linux_floppy_save import floppy_save, _manifest  # noqa: F401


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


@pytest.fixture
def loaded_floppy_save(floppy_save):
    session, source, target, read_file = floppy_save
    # A loaded floppy retains its original file contents in the working image;
    # pending edits are applied to the separate prepared image at save time.
    shutil.copyfile(target, session.working_img_path)
    session.source_kind = "floppy_usb"
    session.source_path = target
    return session, source, target, read_file


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("verify_copies", [False, True])
def test_loaded_floppy_only_writes_changed_file_without_backups_or_rechecks(
    loaded_floppy_save, monkeypatch, cancel, verify_copies,
):
    session, source, target, read_file = loaded_floppy_save
    mutations = []
    original_reads = []
    stopped = threading.Event()
    run = session._run_mtools
    extract = session._extract_from_image

    def record(args, message, **kwargs):
        command = os.path.basename(args[0])
        publication = command == "mcopy" and args[-1].startswith("::/")
        if command == "mdel" or publication:
            mutations.append((command, args[-1]))
        if publication and cancel:
            stopped.set()
            raise image.FloppyOperationCancelled("Cancelled at changed-file copy")
        return run(args, message, **kwargs)

    def record_read(path, name, destination, **kwargs):
        if os.fspath(path) == target and not mutations:
            original_reads.append(name)
        return extract(path, name, destination, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    monkeypatch.setattr(session, "_extract_from_image", record_read)

    def save():
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=False, check_originals=False,
            verify_copies=verify_copies, cancel_callback=stopped.is_set,
        )

    if cancel:
        with pytest.raises(image.FloppyOperationCancelled, match="Cancelled at changed-file copy"):
            save()
        assert session.last_floppy_save_diagnostics["restoration"]["status"] == "unavailable"
        assert "CHANGE.MID" not in {entry.path for entry in image.read_image_listing(target).entries}
    else:
        save()
        assert read_file(target, "CHANGE.MID") == b"new recording"

    assert mutations == [("mdel", "::/CHANGE.MID"), ("mcopy", "::/CHANGE.MID")]
    assert not original_reads
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    _, manifest = _manifest(session)
    assert not manifest["originals"]
    assert manifest["status"] == (
        "cancelled" if cancel else "contents_verified" if verify_copies else "files_saved"
    )


def test_delivery_to_another_floppy_does_not_use_loaded_floppy_baseline(
    loaded_floppy_save, monkeypatch, tmp_path,
):
    session, source, target, read_file = loaded_floppy_save
    other_target = str(tmp_path / "other-floppy.img")
    original_disk = Path(target).read_bytes()
    Path(other_target).write_bytes(original_disk.replace(b"unchanged recording", b"different recording"))
    monkeypatch.setattr(
        image, "_is_block_device_path", lambda path: os.fspath(path) in {target, other_target},
    )
    mutations = []
    run = session._run_mtools

    def record(args, message, **kwargs):
        command = os.path.basename(args[0])
        if command == "mdel" or (command == "mcopy" and args[-1].startswith("::/")):
            mutations.append((command, args[-1]))
        return run(args, message, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    session._sync_modified_image_files_to_floppy_drive(
        source, other_target, backup_originals=False, check_originals=False, verify_copies=False,
    )

    assert ("mdel", "::/KEEP.MID") in mutations
    assert ("mcopy", "::/KEEP.MID") in mutations
    assert read_file(other_target, "CHANGE.MID") == b"new recording"
    assert read_file(other_target, "KEEP.MID") == b"unchanged recording"
    assert Path(target).read_bytes() == original_disk


@pytest.mark.parametrize("backup_originals", [False, True])
@pytest.mark.parametrize("check_originals", [False, True])
def test_original_content_checks_are_independent_of_change_selection(
    loaded_floppy_save, monkeypatch, backup_originals, check_originals,
):
    session, source, target, read_file = loaded_floppy_save
    changed_disk = Path(target).read_bytes().replace(b"unchanged recording", b"different recording")
    Path(target).write_bytes(changed_disk)
    mutations = []
    run = session._run_mtools

    def record(args, message, **kwargs):
        command = os.path.basename(args[0])
        if command == "mdel" or (command == "mcopy" and args[-1].startswith("::/")):
            mutations.append((command, args[-1]))
        return run(args, message, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)

    def save():
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=backup_originals,
            check_originals=check_originals, verify_copies=False,
        )

    if check_originals:
        with pytest.raises(image.FloppyImageError, match="contents changed since it was opened"):
            save()
        assert not mutations
        assert not session.last_floppy_save_diagnostics["target_mutation_attempted"]
        assert Path(target).read_bytes() == changed_disk
    else:
        save()
        assert mutations == [("mdel", "::/CHANGE.MID"), ("mcopy", "::/CHANGE.MID")]
        assert read_file(target, "CHANGE.MID") == b"new recording"

    assert read_file(target, "KEEP.MID") == b"different recording"
    _, manifest = _manifest(session)
    assert manifest["status"] == ("failed" if check_originals else "files_saved")
