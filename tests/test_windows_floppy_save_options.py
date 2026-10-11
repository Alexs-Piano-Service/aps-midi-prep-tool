"""Optional floppy rereads can be disabled independently of file publication."""

import builtins
from pathlib import Path
import threading

import pytest

from aps_midi_prep_tool_app import floppy_image
from test_windows_floppy_save import (
    _package, _prepared_save, mounted_drive, protected_target, save_session,
)


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("verify", [False, True])
def test_save_options_control_backups_and_readback_phases(
    save_session, mounted_drive, monkeypatch, backup, check, verify,
):
    events, reads = [], []
    digest = floppy_image.floppy_save_recovery.digest

    def record_digest(path):
        if Path(path).parent == mounted_drive:
            reads.append(Path(path).name)
        return digest(path)

    monkeypatch.setattr(floppy_image.floppy_save_recovery, "digest", record_digest)
    save_session._sync_modified_image_files_to_windows_drive(
        "prepared.img", "A:", backup_originals=backup, check_originals=check,
        verify_copies=verify, progress_callback=lambda *event: events.append(event),
    )

    assert (mounted_drive / "SONG.FIL").read_bytes() == b"new song"
    assert not (mounted_drive / "OLD.MID").exists()
    _, manifest = _package(save_session)
    assert bool(manifest["originals"]) is backup
    assert any(message.startswith("Backing up files...") for _, _, message in events) is backup
    assert any(message.startswith("Checking original files...") for _, _, message in events) is check
    assert any(message.startswith("Verifying copied files...") for _, _, message in events) is verify
    assert any(message.startswith("Verifying saved files...") for _, _, message in events) is verify
    assert ("OLD.MID" in reads) is check
    assert not any(name.endswith(".TMP") for name in reads)
    assert ("SONG.FIL" in reads) is verify
    assert save_session.last_floppy_save_diagnostics["file_contents_verified"] is verify
    assert save_session.last_floppy_save_diagnostics["status"] == ("complete" if verify else "files_saved")
    assert save_session.last_write_verification["confidence"] == ("contents_verified" if verify else "written")


def test_disabled_options_never_read_floppy_payloads(
    save_session, mounted_drive, monkeypatch,
):
    save_session.prepared = dict(save_session.originals)
    original_open = builtins.open

    def no_floppy_reads(path, mode="r", *args, **kwargs):
        if isinstance(path, (str, bytes, Path)) and Path(path).parent == mounted_drive:
            # sync_file opens the final file in r+b solely to call fsync.
            assert mode == "r+b" or (
                "r" not in mode and "+" not in mode
            ), f"Unexpected floppy read: {path}"
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_floppy_reads)
    save_session._sync_modified_image_files_to_windows_drive(
        "prepared.img", "A:", backup_originals=False, check_originals=False, verify_copies=False,
    )

    assert save_session.last_floppy_save_diagnostics["files_removed"] == 0
    assert save_session.last_floppy_save_diagnostics["files_copied"] == 0
    assert save_session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    _, manifest = _package(save_session)
    assert manifest["originals"] == {}
    assert manifest["original_metadata"]["OLD.MID"]["sha256"] is None


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("verify", [False, True])
def test_save_without_backup_or_recheck_never_rewrites_unchanged_song(
    save_session, mounted_drive, monkeypatch, cancel, verify,
):
    (mounted_drive / "OLD.MID").unlink()
    save_session.originals = {
        "CHANGE.MID": b"old recording",
        "KEEP.MID": b"unchanged recording",
    }
    for name, payload in save_session.originals.items():
        (mounted_drive / name).write_bytes(payload)
    save_session.prepared = dict(save_session.originals, **{"CHANGE.MID": b"new recording"})
    removed, copied = [], []
    cancelled = threading.Event()
    remove = floppy_image.os.remove
    copy = floppy_image._copy_prepared_floppy_file

    def record_remove(path):
        if Path(path).parent == mounted_drive:
            removed.append(Path(path).name)
        return remove(path)

    def record_copy(source, destination, **kwargs):
        copied.append(Path(destination).name)
        created = kwargs.pop("created_callback", None)

        def file_created():
            if created is not None:
                created()
            if cancel and Path(destination).name == "CHANGE.MID":
                cancelled.set()

        return copy(source, destination, created_callback=file_created, **kwargs)

    monkeypatch.setattr(floppy_image.os, "remove", record_remove)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", record_copy)
    kwargs = dict(
        backup_originals=False, check_originals=False, verify_copies=verify,
        cancel_callback=cancelled.is_set,
    )
    if cancel:
        with pytest.raises(floppy_image.FloppyOperationCancelled):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert save_session.last_floppy_save_diagnostics["status"] == "cancelled"
        assert save_session.last_floppy_save_diagnostics["restoration"]["status"] == "unavailable"
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert (mounted_drive / "CHANGE.MID").read_bytes() == b"new recording"
    assert (mounted_drive / "KEEP.MID").read_bytes() == b"unchanged recording"
    assert removed == ["CHANGE.MID"]
    assert copied == ["CHANGE.MID"]


def test_delivery_without_source_baseline_writes_all_prepared_files(
    save_session, mounted_drive,
):
    (mounted_drive / "OLD.MID").unlink()
    save_session.source_kind = "image"
    save_session.prepared = dict(save_session.originals)
    save_session._sync_modified_image_files_to_windows_drive(
        "prepared.img", "A:", backup_originals=False, check_originals=False, verify_copies=False,
    )
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert save_session.last_floppy_save_diagnostics["files_copied"] == 1


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("check", [False, True])
def test_original_content_check_can_be_disabled_independently_of_backup(
    save_session, mounted_drive, backup, check,
):
    (mounted_drive / "OLD.MID").write_bytes(b"changed! song")
    kwargs = dict(backup_originals=backup, check_originals=check, verify_copies=False)
    if check:
        with pytest.raises(floppy_image.FloppyImageError, match="changed since it was opened"):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert (mounted_drive / "SONG.FIL").read_bytes() == b"new song"


@pytest.mark.parametrize("verify", [False, True])
def test_copy_content_verification_is_opt_in_and_reports_its_limit(
    save_session, mounted_drive, monkeypatch, verify,
):
    copy = floppy_image._copy_prepared_floppy_file

    def damage_same_size(source, destination, **kwargs):
        copy(source, destination, **kwargs)
        Path(destination).write_bytes(b"bad song")

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", damage_same_size)
    kwargs = dict(backup_originals=False, check_originals=False, verify_copies=verify)
    if verify:
        with pytest.raises(floppy_image.FloppyImageError, match="Copied floppy file verification failed"):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert not (mounted_drive / "OLD.MID").exists()
        assert save_session.last_floppy_save_diagnostics["restoration"]["status"] == "unavailable"
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert save_session.last_write_verification["confidence"] == "written"
        assert save_session.last_floppy_save_diagnostics["file_contents_verified"] is False


@pytest.mark.parametrize("change", ["size", "identity"])
def test_cheap_target_checks_still_reject_changes_with_options_disabled(
    save_session, mounted_drive, monkeypatch, change,
):
    copy = floppy_image._copy_prepared_floppy_file

    def change_target(source, destination, **kwargs):
        copy(source, destination, **kwargs)
        if change == "size":
            (mounted_drive / "OLD.MID").write_bytes(b"a different size")
        else:
            monkeypatch.setattr(floppy_image, "_windows_volume_identity", lambda _: {"serial": 456})

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", change_target)
    with pytest.raises(floppy_image.FloppyImageError, match="changed during save|verification failed"):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", backup_originals=False, check_originals=False, verify_copies=False,
        )
    assert (mounted_drive / "SONG.FIL").exists()
    assert save_session.last_write_verification["confidence"] == "partial_or_uncertain"


def test_direct_save_without_backups_preserves_unrelated_files(
    save_session, mounted_drive, tmp_path, monkeypatch,
):
    job = _prepared_save(tmp_path, {"NEW.MID": b"new song"})
    digest = floppy_image.floppy_save_recovery.digest

    def no_floppy_digest(path):
        assert Path(path).parent != mounted_drive
        return digest(path)

    monkeypatch.setattr(floppy_image.floppy_save_recovery, "digest", no_floppy_digest)
    job._sync_modified_image_files_to_windows_drive(
        None, "A:", backup_originals=False, check_originals=False, verify_copies=False,
    )
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert (mounted_drive / "NEW.MID").read_bytes() == b"new song"
    _, manifest = _package(job)
    assert manifest["originals"] == {}


def test_failed_copy_without_backups_keeps_final_file_and_reports_no_rollback(
    save_session, mounted_drive, monkeypatch,
):
    copy = floppy_image._copy_prepared_floppy_file
    digest = floppy_image.floppy_save_recovery.digest

    def fail_after_copy(source, destination, **kwargs):
        copy(source, destination, **kwargs)
        raise OSError("Simulated write failure")

    def no_floppy_digest(path):
        assert Path(path).parent != mounted_drive
        return digest(path)

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail_after_copy)
    monkeypatch.setattr(floppy_image.floppy_save_recovery, "digest", no_floppy_digest)
    with pytest.raises(OSError, match="Simulated write failure"):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", backup_originals=False, check_originals=False, verify_copies=False,
        )
    assert list(mounted_drive.iterdir()) == [mounted_drive / "SONG.FIL"]
    _, manifest = _package(save_session)
    assert manifest["originals"] == {}
    assert manifest["diagnostics"]["restoration"]["status"] == "unavailable"


@pytest.mark.parametrize("cancel", [False, True])
def test_readonly_attributes_are_preserved_without_original_backups(
    save_session, mounted_drive, protected_target, monkeypatch, cancel,
):
    attributes, _ = protected_target
    expected = dict(attributes)
    digest = floppy_image.floppy_save_recovery.digest

    def no_floppy_digest(path):
        assert Path(path).parent != mounted_drive
        return digest(path)

    monkeypatch.setattr(floppy_image.floppy_save_recovery, "digest", no_floppy_digest)
    kwargs = dict(
        backup_originals=False, check_originals=False, verify_copies=False,
        cancel_callback=lambda: cancel and attributes != expected,
    )
    if cancel:
        with pytest.raises(floppy_image.FloppyOperationCancelled):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert all((mounted_drive / name).read_bytes() == value for name, value in save_session.originals.items())
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", **kwargs)
        assert all((mounted_drive / name).read_bytes() == value for name, value in save_session.prepared.items())
    assert attributes == expected
    assert not list(mounted_drive.glob("APS*.TMP"))


def test_failed_publication_reports_that_original_backups_are_unavailable(
    save_session, mounted_drive, monkeypatch,
):
    save_session.prepared = {"OLD.MID": b"replacement"}
    def fail_publication(*args, **kwargs):
        raise OSError("Simulated publication failure")

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail_publication)
    with pytest.raises(OSError, match="Simulated publication failure"):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", backup_originals=False, check_originals=False, verify_copies=False,
        )
    assert save_session.last_floppy_save_diagnostics["restoration"]["status"] == "unavailable"
    _, manifest = _package(save_session)
    assert manifest["originals"] == {}
    assert manifest["diagnostics"]["restoration"]["status"] == "unavailable"
