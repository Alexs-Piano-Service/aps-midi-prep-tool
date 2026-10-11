"""Physical file comparisons must not read unused floppy sectors."""

import os
import hashlib
import json
from pathlib import Path
import shutil
import threading
from dataclasses import replace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


@pytest.fixture
def floppy_save(tmp_path, monkeypatch):
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    target = tmp_path / "floppy.img"
    source = tmp_path / "prepared.img"
    song = tmp_path / "song.mid"
    image.create_blank_floppy_image(target, image.DISK_FORMAT_BY_KEY["ibm.720"])
    for name, payload in (("CHANGE.MID", b"old recording"), ("KEEP.MID", b"unchanged recording")):
        song.write_bytes(payload)
        image._copy_host_file_into_image(target, song, name)
    shutil.copyfile(target, source)
    prepared = bytearray(source.read_bytes())
    offset = prepared.index(b"old recording")
    prepared[offset:offset + len(b"old recording")] = b"new recording"
    source.write_bytes(prepared)
    session = image.FloppyImageSession.load(source)
    native_reader = image._read_fat12_file_bytes

    def read_image(path, name):
        assert os.fspath(path) != str(target), "File comparison read the entire physical disk"
        return native_reader(path, name)

    monkeypatch.setattr(image, "_is_block_device_path", lambda path: os.fspath(path) == str(target))
    monkeypatch.setattr(image, "_read_fat12_file_bytes", read_image)
    try:
        yield session, str(source), str(target), native_reader
    finally:
        session.cleanup()


def test_save_compares_file_contents_without_reading_entire_device(floppy_save, monkeypatch):
    session, source, target, read_file = floppy_save
    commands = []
    progress = []
    run = session._run_mtools

    def record(args, *args_rest, **kwargs):
        commands.append(args)
        return run(args, *args_rest, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    session._sync_modified_image_files_to_floppy_drive(
        source, target, progress_callback=lambda step, total, message: progress.append(message),
    )

    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert any(message.startswith("Preparing files...\n") and message.endswith("KEEP.MID") for message in progress)
    assert "Removing old files...\nFile 1 of 1: CHANGE.MID" in progress
    assert not any(os.path.basename(args[0]) == "mdel" and args[-1] == "::/KEEP.MID"
                   for args in commands)


def test_file_save_reports_current_file_before_io_and_completion_after_verification(floppy_save, monkeypatch):
    session, source, target, _read_file = floppy_save
    progress = []
    copied = []
    verified = []
    run = session._run_mtools

    def record(args, message, **kwargs):
        if os.path.basename(args[0]) == "mcopy":
            if args[-1].startswith("::/"):
                assert progress[-1] == (0, 1, "Copying files to floppy...\nFile 1 of 1: CHANGE.MID")
                assert args[-1] == "::/CHANGE.MID"
                copied.append(args[-1])
            elif args[-2] in copied:
                # Publication stays incomplete until the final copy is checked.
                assert progress[-1][0] == 0 or progress[-1][2].startswith("Verifying saved files...")
                verified.append(args[-2])
        return run(args, message, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    session._sync_modified_image_files_to_floppy_drive(
        source, target, progress_callback=lambda *event: progress.append(event),
    )

    assert copied and verified
    assert progress[0] == (0, 0, "Checking floppy...")
    assert not any(".TMP" in message for _step, _total, message in progress)
    for phase, count in (("Backing up files...", 2), ("Preparing files...", 2),
                         ("Copying files to floppy...", 1), ("Removing old files...", 1),
                         ("Verifying saved files...", 2)):
        events = [(step, total, message) for step, total, message in progress if message.startswith(phase + "\n")]
        assert [(step, total) for step, total, _message in events] == [
            (step, count) for index in range(count) for step in (index, index + 1)
        ]
        assert all("File " in message for _step, _total, message in events)


def test_cancellation_at_first_copy_does_not_report_it_as_completed(floppy_save):
    session, source, target, read_file = floppy_save
    before = {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries}
    progress = []
    stopped = threading.Event()

    def report(step, total, message):
        progress.append((step, total, message))
        if message.startswith("Copying files to floppy...\n"):
            stopped.set()

    with pytest.raises(image.FloppyOperationCancelled):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, progress_callback=report, cancel_callback=stopped.is_set,
        )

    assert [(step, total) for step, total, message in progress
            if message.startswith("Copying files to floppy...\n")] == [(0, 1)]
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "complete"
    assert {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries} == before


@pytest.mark.parametrize("error", [image.FloppyOperationCancelled, image.FloppyImageError])
def test_failed_or_cancelled_comparison_stops_before_writing(floppy_save, monkeypatch, error):
    session, source, target, _read_file = floppy_save
    with open(target, "rb") as handle:
        before = handle.read()
    commands = []

    def fail(args, message, cancel_callback=None):
        commands.append(args)
        raise error("Read stopped")

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(error, match="Read stopped"):
        session._sync_modified_image_files_to_floppy_drive(source, target)

    assert len(commands) == 1
    assert os.path.basename(commands[0][0]) == "mcopy"
    assert commands[0][2] == target
    with open(target, "rb") as handle:
        assert handle.read() == before


@pytest.fixture
def two_replacements(floppy_save, tmp_path):
    session, source, target, read_file = floppy_save
    song = tmp_path / "second.mid"
    song.write_bytes(b"second original")
    image._copy_host_file_into_image(target, song, "SECOND.MID")
    song.write_bytes(b"second prepared")
    image._copy_host_file_into_image(source, song, "SECOND.MID")
    return session, source, target, read_file


def _manifest(session):
    directory = Path(session.last_floppy_save_diagnostics["recovery_directory"])
    manifest = json.loads((directory / "manifest.json").read_text())
    for category in ("originals", "replacements"):
        for record in manifest[category].values():
            data = (directory / record["file"]).read_bytes()
            assert len(data) == record["size"]
            assert hashlib.sha256(data).hexdigest() == record["sha256"]
    return directory, manifest


def _set_no_free_space(monkeypatch, target):
    read_listing = image.read_image_listing

    def listing(path, **kwargs):
        result = read_listing(path, **kwargs)
        if os.fspath(path) == target:
            return image.ImageListing(result.entries, 0, result.cluster_size)
        return result

    monkeypatch.setattr(image, "read_image_listing", listing)


@pytest.mark.parametrize("full_disk", [False, True])
@pytest.mark.parametrize("stop_after", [1, 2])
@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_replacement_restores_both_originals(
    two_replacements, monkeypatch, full_disk, stop_after, cancel,
):
    session, source, target, read_file = two_replacements
    if full_disk:
        _set_no_free_space(monkeypatch, target)
    original = {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries}
    stopped = threading.Event()
    run = session._run_mtools
    published = []

    def fail(args, message, cancel_callback=None):
        command = os.path.basename(args[0])
        publication = (
            command == "mcopy" and args[-1].startswith("::/")
            and "replacements-" in args[-2]
        )
        if publication:
            # Recovery is durable before the first actual replacement.
            directory, manifest = _manifest(session)
            assert set(manifest["originals"]) == set(original)
            assert (directory / "prepared.img").read_bytes() == Path(source).read_bytes()
        result = run(args, message, cancel_callback=cancel_callback)
        if publication:
            published.append(args[-1])
            if len(published) == stop_after:
                if cancel:
                    stopped.set()
                else:
                    raise image.FloppyImageError("Injected replacement failure")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    exception = image.FloppyOperationCancelled if cancel else image.FloppyImageError
    with pytest.raises(exception, match="recovery files are retained"):
        session._sync_modified_image_files_to_floppy_drive(source, target, cancel_callback=stopped.is_set)
    assert len(published) == stop_after
    assert {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries} == original
    diagnostics = session.last_floppy_save_diagnostics
    assert diagnostics["restoration"]["status"] == "complete"
    assert diagnostics["staging_method"] == "host_recovery"
    assert diagnostics["files_staged"] == 0
    _, manifest = _manifest(session)
    assert manifest["status"] == ("cancelled" if cancel else "failed")


@pytest.mark.parametrize("stop_after", [1, 2])
def test_local_preparation_failure_keeps_original_files_without_deleting_them(
    two_replacements, monkeypatch, stop_after,
):
    session, source, target, read_file = two_replacements
    original = {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries}
    run = session._run_mtools
    prepared = []
    deleted = []
    retain = image.floppy_save_recovery.SaveRecoveryPackage.retain

    def fail(args, message, cancel_callback=None):
        if os.path.basename(args[0]) == "mdel":
            deleted.append(args[-1])
        return run(args, message, cancel_callback=cancel_callback)

    def fail_preparation(package, category, name, path):
        result = retain(package, category, name, path)
        if category == "replacements":
            prepared.append(name)
            if len(prepared) == stop_after:
                raise OSError("Local preparation failed")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    monkeypatch.setattr(image.floppy_save_recovery.SaveRecoveryPackage, "retain", fail_preparation)
    with pytest.raises(image.FloppyImageError, match="Local preparation failed"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert not deleted
    assert {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries} == original
    assert not session.last_floppy_save_diagnostics["target_mutation_attempted"]


@pytest.mark.parametrize("full_disk", [False, True])
def test_floppy_backups_and_preparation_stay_local(two_replacements, monkeypatch, full_disk):
    session, source, target, read_file = two_replacements
    if full_disk:
        _set_no_free_space(monkeypatch, target)
    run = session._run_mtools
    copies = []

    def record(args, message, **kwargs):
        command = os.path.basename(args[0])
        assert command != "mren", "A save must not stage temporary files on the floppy"
        if command == "mcopy" and args[-1].startswith("::/"):
            copies.append(args[-1])
            assert args[-1] in ("::/CHANGE.MID", "::/SECOND.MID")
            assert "replacements-" in args[-2]
        return run(args, message, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    session._sync_modified_image_files_to_floppy_drive(source, target)
    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "SECOND.MID") == b"second prepared"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert session.last_floppy_save_diagnostics["staging_method"] == "host_recovery"
    assert session.last_floppy_save_diagnostics["files_staged"] == 0
    assert copies == ["::/CHANGE.MID", "::/SECOND.MID"]
    assert {entry.path for entry in image.read_image_listing(target).entries} == {
        "CHANGE.MID", "SECOND.MID", "KEEP.MID",
    }
    directory, manifest = _manifest(session)
    assert directory.parent == Path(target).parent / "recovery"
    assert (directory / manifest["originals"]["CHANGE.MID"]["file"]).read_bytes() == b"old recording"
    assert (directory / manifest["originals"]["SECOND.MID"]["file"]).read_bytes() == b"second original"
    assert manifest["status"] == "contents_verified"
    image._finish_windows_save_recovery(session, "complete")
    assert json.loads((directory / "manifest.json").read_text())["status"] == "complete"


def test_final_file_set_must_fit_before_any_original_is_removed(floppy_save, monkeypatch, tmp_path):
    session, source, target, _read_file = floppy_save
    larger_song = tmp_path / "larger.mid"
    cluster_size = image.read_image_listing(target).cluster_size
    larger_song.write_bytes(b"x" * (cluster_size + 1))
    session._run_mtools(
        [image._require_command("mdel"), "-i", source, "::/CHANGE.MID"], "Could not prepare test image",
    )
    image._copy_host_file_into_image(source, larger_song, "CHANGE.MID")
    _set_no_free_space(monkeypatch, target)
    original_disk = Path(target).read_bytes()

    with pytest.raises(image.FloppyImageError, match="prepared files do not fit"):
        session._sync_modified_image_files_to_floppy_drive(source, target)

    assert Path(target).read_bytes() == original_disk
    assert not session.last_floppy_save_diagnostics["target_mutation_attempted"]
    directory, manifest = _manifest(session)
    assert (directory / manifest["originals"]["CHANGE.MID"]["file"]).read_bytes() == b"old recording"


def test_unknown_partial_copy_retains_backups_without_destructive_rollback(two_replacements, monkeypatch):
    session, source, target, read_file = two_replacements
    _set_no_free_space(monkeypatch, target)
    run = session._run_mtools
    failed = []

    def fail(args, message, cancel_callback=None):
        result = run(args, message, cancel_callback=cancel_callback)
        if not failed and os.path.basename(args[0]) == "mcopy" and args[-1] == "::/CHANGE.MID":
            failed.append(True)
            data = Path(target).read_bytes().replace(b"new recording", b"unknown bytes")
            Path(target).write_bytes(data)
            raise image.FloppyImageError("Partial write")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(image.FloppyImageError, match="recovery files are retained"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert read_file(target, "CHANGE.MID") == b"unknown bytes"
    directory, manifest = _manifest(session)
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "incomplete"
    assert (directory / manifest["originals"]["CHANGE.MID"]["file"]).read_bytes() == b"old recording"
    assert (directory / manifest["originals"]["SECOND.MID"]["file"]).read_bytes() == b"second original"


def test_backup_failure_stops_before_target_mutation(floppy_save, monkeypatch):
    session, source, target, _ = floppy_save
    before = Path(target).read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("Recovery storage unavailable")

    monkeypatch.setattr(image.floppy_save_recovery.SaveRecoveryPackage, "retain", fail)
    with pytest.raises(image.FloppyImageError, match="Recovery storage unavailable"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert Path(target).read_bytes() == before
    assert not session.last_floppy_save_diagnostics["target_mutation_attempted"]


@pytest.mark.parametrize("failure", ["ancestor", "package"])
def test_recovery_directory_sync_failure_stops_before_target_mutation(
    floppy_save, monkeypatch, tmp_path, failure,
):
    session, source, target, _ = floppy_save
    before = Path(target).read_bytes()
    recovery_root = tmp_path / "new" / "state" / "recovery"
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(recovery_root))
    failed_path = recovery_root.parent if failure == "ancestor" else recovery_root
    sync = image.floppy_save_recovery.sync_directory

    def fail(path):
        if Path(path) == failed_path:
            raise OSError("Recovery directory flush failed")
        sync(path)

    monkeypatch.setattr(image.floppy_save_recovery, "sync_directory", fail)
    with pytest.raises(OSError, match="Recovery directory flush failed"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert Path(target).read_bytes() == before
    assert not session.last_floppy_save_diagnostics["target_mutation_attempted"]


def test_prepared_file_read_failure_preserves_original_disk(two_replacements, monkeypatch):
    session, source, target, _ = two_replacements
    before = Path(target).read_bytes()
    extract = session._extract_from_image

    def fail(path, name, destination, **kwargs):
        if path == source and name == "SECOND.MID":
            raise image.FloppyImageError("Prepared file is unreadable")
        return extract(path, name, destination, **kwargs)

    monkeypatch.setattr(session, "_extract_from_image", fail)
    with pytest.raises(image.FloppyImageError, match="Prepared file is unreadable"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert Path(target).read_bytes() == before


@pytest.mark.parametrize("change", ["media_identity", "unrelated_file"])
def test_changed_target_stops_rollback_and_retains_original_backup(two_replacements, monkeypatch, change):
    session, source, target, read_file = two_replacements
    _set_no_free_space(monkeypatch, target)
    run = session._run_mtools
    commands_after_change = []
    changed = []
    changed_disk = []

    def fail(args, message, cancel_callback=None):
        if changed:
            commands_after_change.append(args)
        result = run(args, message, cancel_callback=cancel_callback)
        if not changed and os.path.basename(args[0]) == "mcopy" and args[-1] == "::/CHANGE.MID":
            changed.append(True)
            data = bytearray(Path(target).read_bytes())
            if change == "media_identity":
                data[39] ^= 1  # FAT volume serial; the readable file map remains valid.
            else:
                data = data.replace(b"unchanged recording", b"different recording")
            Path(target).write_bytes(data)
            changed_disk.append(bytes(data))
            raise image.FloppyImageError("Drive changed")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(image.FloppyImageError, match="recovery files are retained"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert Path(target).read_bytes() == changed_disk[0]
    assert all(os.path.basename(args[0]) == "mcopy" and not args[-1].startswith("::/")
               for args in commands_after_change)
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "incomplete"
    _manifest(session)


def test_rollback_write_failure_keeps_complete_recovery_package(two_replacements, monkeypatch):
    session, source, target, _ = two_replacements
    _set_no_free_space(monkeypatch, target)
    run = session._run_mtools

    def fail(args, message, cancel_callback=None):
        if os.path.basename(args[0]) == "mcopy" and args[-1].startswith("::/"):
            raise image.FloppyImageError("Drive no longer accepts writes")
        return run(args, message, cancel_callback=cancel_callback)

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(image.FloppyImageError, match="recovery files are retained"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    directory, manifest = _manifest(session)
    assert set(manifest["originals"]) == {"CHANGE.MID", "KEEP.MID", "SECOND.MID"}
    assert (directory / "prepared.img").is_file()
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "incomplete"


@pytest.mark.parametrize("full_disk", [False, True])
@pytest.mark.parametrize("backup_originals", [False, True])
@pytest.mark.parametrize("check_originals", [False, True])
@pytest.mark.parametrize("verify_copies", [False, True])
def test_optional_floppy_reads_and_backups(
    floppy_save, monkeypatch, full_disk, backup_originals, check_originals, verify_copies,
):
    session, source, target, read_file = floppy_save
    if full_disk:
        _set_no_free_space(monkeypatch, target)
    progress, original_reads, copied_reads, published = [], [], [], set()
    extract = session._extract_from_image
    run = session._run_mtools

    def read(path, name, destination, **kwargs):
        if path == target:
            (copied_reads if name in published else original_reads).append(name)
        return extract(path, name, destination, **kwargs)

    def write(args, message, **kwargs):
        result = run(args, message, **kwargs)
        command = os.path.basename(args[0])
        if command == "mcopy" and args[-1].startswith("::/"):
            published.add(args[-1].removeprefix("::/"))
        return result

    monkeypatch.setattr(session, "_extract_from_image", read)
    monkeypatch.setattr(session, "_run_mtools", write)
    session._sync_modified_image_files_to_floppy_drive(
        source, target, backup_originals=backup_originals,
        check_originals=check_originals, verify_copies=verify_copies,
        progress_callback=lambda _step, _total, message: progress.append(message),
    )

    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert bool(original_reads) == (backup_originals or check_originals)
    assert bool(copied_reads) == verify_copies
    if backup_originals and not check_originals and not verify_copies:
        assert sorted(original_reads) == ["CHANGE.MID", "KEEP.MID"]
    if not backup_originals and not check_originals:
        # This image delivery has no loaded-target baseline or original hashes.
        assert published == {"CHANGE.MID", "KEEP.MID"}
    for action, enabled in (("Backing up files...", backup_originals),
                            ("Checking original files...", check_originals),
                            ("Verifying copied files...", verify_copies),
                            ("Verifying saved files...", verify_copies)):
        assert any(message.startswith(action) for message in progress) == enabled
    _, manifest = _manifest(session)
    assert bool(manifest["originals"]) == backup_originals
    assert manifest["status"] == ("contents_verified" if verify_copies else "files_saved")
    assert session.last_floppy_save_diagnostics["file_contents_verified"] == verify_copies
    assert session.last_write_verification["confidence"] == ("contents_verified" if verify_copies else "written")


@pytest.mark.parametrize("full_disk", [False, True])
def test_failure_without_backup_does_not_claim_originals_restored(floppy_save, monkeypatch, full_disk):
    session, source, target, _ = floppy_save
    if full_disk:
        _set_no_free_space(monkeypatch, target)
    run = session._run_mtools
    progress = []

    def fail(args, message, **kwargs):
        result = run(args, message, **kwargs)
        if os.path.basename(args[0]) == "mdel":
            raise image.FloppyImageError("Injected failure after deletion")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(image.FloppyImageError, match="Original-file backups were disabled"):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=False, check_originals=False, verify_copies=False,
            progress_callback=lambda _step, _total, message: progress.append(message),
        )
    restoration = session.last_floppy_save_diagnostics["restoration"]
    assert restoration["status"] == "unavailable"
    assert not restoration["restored"]
    assert not any(message.startswith("Restoring original files...") for message in progress)
    assert "CHANGE.MID" not in {entry.path for entry in image.read_image_listing(target).entries}
    _, manifest = _manifest(session)
    assert not manifest["originals"]
    assert set(manifest["replacements"]) == {"CHANGE.MID", "KEEP.MID"}


def test_disabled_optional_reads_still_reject_short_final_copy(floppy_save, monkeypatch):
    session, source, target, _read_file = floppy_save
    read_listing = image.read_image_listing
    run = session._run_mtools
    published = set()

    def write(args, message, **kwargs):
        result = run(args, message, **kwargs)
        if os.path.basename(args[0]) == "mcopy" and args[-1].startswith("::/"):
            published.add(args[-1].removeprefix("::/"))
        return result

    def short_copy_listing(path, **kwargs):
        listing = read_listing(path, **kwargs)
        if os.fspath(path) == target:
            entries = [replace(entry, size=entry.size - 1) if entry.path in published else entry
                       for entry in listing.entries]
            return image.ImageListing(entries, listing.free_space, listing.cluster_size)
        return listing

    monkeypatch.setattr(image, "read_image_listing", short_copy_listing)
    monkeypatch.setattr(session, "_run_mtools", write)
    with pytest.raises(image.FloppyImageError, match="Floppy verification failed"):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=False, check_originals=False, verify_copies=False,
        )
    assert session.last_floppy_save_diagnostics["files_copied"] == 0
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "unavailable"
    assert set(_manifest(session)[1]["replacements"]) == {"CHANGE.MID", "KEEP.MID"}


def test_backup_still_restores_originals_with_checks_and_verification_disabled(floppy_save, monkeypatch):
    session, source, target, read_file = floppy_save
    original = {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries}
    run = session._run_mtools

    def fail(args, message, **kwargs):
        result = run(args, message, **kwargs)
        if (os.path.basename(args[0]) == "mcopy" and args[-1].startswith("::/")
                and "replacements-" in args[-2]):
            raise image.FloppyImageError("Injected publication failure")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(image.FloppyImageError, match="Injected publication failure"):
        session._sync_modified_image_files_to_floppy_drive(
            source, target, backup_originals=True, check_originals=False, verify_copies=False,
        )
    assert {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries} == original
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "complete"
