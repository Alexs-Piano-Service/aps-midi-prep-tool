"""Physical file comparisons must not read unused floppy sectors."""

import os
import hashlib
import json
from pathlib import Path
import shutil
import threading

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
    assert "Keeping unchanged KEEP.MID on floppy..." in progress
    assert "Removing old CHANGE.MID from floppy..." in progress
    assert not any(os.path.basename(args[0]) == "mdel" and args[-1] == "::/KEEP.MID"
                   for args in commands)


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


def _force_host_staging(monkeypatch, target):
    read_listing = image.read_image_listing

    def listing(path, **kwargs):
        result = read_listing(path, **kwargs)
        if os.fspath(path) == target:
            return image.ImageListing(result.entries, 0, result.cluster_size)
        return result

    monkeypatch.setattr(image, "read_image_listing", listing)


@pytest.mark.parametrize("host_staging", [False, True])
@pytest.mark.parametrize("stop_after", [1, 2])
@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_replacement_restores_both_originals(
    two_replacements, monkeypatch, host_staging, stop_after, cancel,
):
    session, source, target, read_file = two_replacements
    if host_staging:
        _force_host_staging(monkeypatch, target)
    original = {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries}
    stopped = threading.Event()
    run = session._run_mtools
    published = []

    def fail(args, message, cancel_callback=None):
        command = os.path.basename(args[0])
        publication = (command == "mren" or (
            command == "mcopy" and args[-1].startswith("::/")
            and not args[-1].endswith(".TMP") and "replacements-" in args[-2]
        ))
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
    assert diagnostics["staging_method"] == ("host_recovery" if host_staging else "floppy")
    _, manifest = _manifest(session)
    assert manifest["status"] == ("cancelled" if cancel else "failed")


@pytest.mark.parametrize("stop_after", [1, 2])
def test_staging_failure_keeps_original_files_without_deleting_them(two_replacements, monkeypatch, stop_after):
    session, source, target, read_file = two_replacements
    original = {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries}
    run = session._run_mtools
    stages = []
    deleted = []

    def fail(args, message, cancel_callback=None):
        if os.path.basename(args[0]) == "mdel":
            deleted.append(args[-1])
        result = run(args, message, cancel_callback=cancel_callback)
        if os.path.basename(args[0]) == "mcopy" and args[-1].endswith(".TMP"):
            stages.append(args[-1])
            if len(stages) == stop_after:
                raise image.FloppyImageError("Staging failed")
        return result

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(image.FloppyImageError, match="Staging failed"):
        session._sync_modified_image_files_to_floppy_drive(source, target)
    assert all(name.endswith(".TMP") for name in deleted)
    assert {entry.path: read_file(target, entry.path) for entry in image.read_image_listing(target).entries} == original
    assert session.last_floppy_save_diagnostics["restoration"]["status"] == "complete"


def test_full_floppy_uses_verified_host_backups_and_completes(two_replacements, monkeypatch):
    session, source, target, read_file = two_replacements
    _force_host_staging(monkeypatch, target)
    session._sync_modified_image_files_to_floppy_drive(source, target)
    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "SECOND.MID") == b"second prepared"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert session.last_floppy_save_diagnostics["staging_method"] == "host_recovery"
    directory, manifest = _manifest(session)
    assert manifest["status"] == "contents_verified"
    image._finish_windows_save_recovery(session, "complete")
    assert json.loads((directory / "manifest.json").read_text())["status"] == "complete"


def test_unknown_partial_copy_retains_backups_without_destructive_rollback(two_replacements, monkeypatch):
    session, source, target, read_file = two_replacements
    _force_host_staging(monkeypatch, target)
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
    _force_host_staging(monkeypatch, target)
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
    _force_host_staging(monkeypatch, target)
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
