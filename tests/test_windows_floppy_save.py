"""Windows file saves fail safely and retain evidence without physical media."""

import ctypes
import hashlib
import json
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import disk_session_worker, floppy_image


def _windows_error(code):
    error = OSError(f"[WinError {code}] simulated Windows failure")
    error.winerror = code
    return error


def _fail(code):
    def fail(*_args, **_kwargs):
        raise _windows_error(code)
    return fail


def _mounted_root_info(root, root_entries=112):
    entries = []
    for index, path in enumerate(sorted(root.iterdir())):
        slots = floppy_image._windows_filename_root_slots(path.name)
        entries.append({
            "name": path.name,
            "short_name": f"F{index:07}.MID" if slots > 1 else "",
            "attributes": 0x10 if path.is_dir() else 0x20,
            "slots": slots,
        })
    return {"root_entries": root_entries, "entries": entries}


@pytest.mark.parametrize("requested,expected", [(0, 0x80), (0x01, 0x01), (0x27, 0x27), (0xA7, 0x27)])
def test_windows_attribute_api_normalizes_normal_flag_and_verifies_readback(monkeypatch, requested, expected):
    values = {"attributes": 0x20}
    calls = []
    def set_attributes(path, attributes):
        calls.append((path, attributes))
        values["attributes"] = attributes
        return True
    api = SimpleNamespace(GetFileAttributesW=lambda _: values["attributes"], SetFileAttributesW=set_attributes)
    monkeypatch.setattr(floppy_image, "_windows_ctypes", lambda: (ctypes, wintypes, api))
    floppy_image._set_windows_file_attributes("A:\\SONG.FIL", requested)
    assert calls == [("A:\\SONG.FIL", expected)]


@pytest.mark.parametrize("failure", ["get", "set", "readback"])
def test_windows_attribute_api_reports_failure(monkeypatch, failure):
    api = SimpleNamespace(
        GetFileAttributesW=lambda _: 0xFFFFFFFF if failure == "get" else 0x20,
        SetFileAttributesW=lambda *a: failure != "set",
    )
    ctypes_api = SimpleNamespace(WinError=_windows_error, get_last_error=lambda: 5)
    monkeypatch.setattr(floppy_image, "_windows_ctypes", lambda: (ctypes_api, wintypes, api))
    with pytest.raises((OSError, floppy_image.FloppyImageError)):
        if failure == "get":
            floppy_image._windows_file_attributes("A:\\SONG.FIL")
        else:
            floppy_image._set_windows_file_attributes("A:\\SONG.FIL", 0x27)


@pytest.mark.parametrize("geometry", [(2, 512, 7, 713), (0, 512, 7, 713), (2, 512, 0, 0), (2, 512, 714, 713)])
@pytest.mark.parametrize("succeeds", [False, True])
def test_legacy_space_query_uses_windows_geometry_and_preserves_error(monkeypatch, succeeds, geometry):
    calls = []

    def query(root, sectors, sector_bytes, free, total):
        calls.append(root)
        for pointer, value in zip((sectors, sector_bytes, free, total), geometry):
            pointer._obj.value = value
        return succeeds

    api = SimpleNamespace(GetDiskFreeSpaceW=query)
    ctypes_api = SimpleNamespace(
        POINTER=ctypes.POINTER, byref=ctypes.byref,
        get_last_error=lambda: 21, WinError=_windows_error,
    )
    monkeypatch.setattr(floppy_image, "_windows_ctypes", lambda: (ctypes_api, wintypes, api))
    if succeeds and geometry == (2, 512, 7, 713):
        assert floppy_image._windows_legacy_disk_space("A:\\") == (7168, 1024)
    elif succeeds:
        with pytest.raises(floppy_image.FloppyImageError, match="invalid floppy capacity"):
            floppy_image._windows_legacy_disk_space("A:\\")
    else:
        with pytest.raises(OSError) as failure:
            floppy_image._windows_legacy_disk_space("A:\\")
        assert failure.value.winerror == 21
    assert calls == ["A:\\"]


def test_unsupported_space_query_uses_legacy_api(monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(50))
    roots = []
    monkeypatch.setattr(
        floppy_image, "_windows_legacy_disk_space",
        lambda root: roots.append(root) or (4096, 512),
    )
    diagnostics = {}
    assert floppy_image._windows_floppy_disk_space("A:\\", diagnostics) == (4096, 512)
    assert roots == ["A:\\"]
    primary, fallback = diagnostics["space_queries"]
    assert primary["api"] == "GetDiskFreeSpaceExW"
    assert primary["error"]["winerror"] == 50
    assert fallback == {"api": "GetDiskFreeSpaceW", "status": "ok", "free_bytes": 4096, "cluster_size": 512}


@pytest.mark.parametrize("code", [5, 21, 23, 1117])
def test_other_space_errors_do_not_trigger_fallback(monkeypatch, code):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(code))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", lambda *_: pytest.fail("Unexpected fallback"))
    diagnostics = {}
    with pytest.raises(OSError) as failure:
        floppy_image._windows_floppy_disk_space("A:\\", diagnostics)
    assert failure.value.winerror == code
    assert len(diagnostics["space_queries"]) == 1


def test_successful_space_query_still_uses_actual_cluster_size(monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=2048))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", lambda *_: (2048, 512))
    assert floppy_image._windows_floppy_disk_space("A:\\", {}) == (2048, 512)


@pytest.fixture
def mounted_drive(tmp_path, monkeypatch):
    root = tmp_path / "floppy"
    root.mkdir()
    monkeypatch.setattr(floppy_image, "_windows_filesystem_root", lambda _: str(root))
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=700000))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", lambda _: (700000, 1024))
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    monkeypatch.setattr(floppy_image, "_windows_volume_identity", lambda _: {"serial": 123, "filesystem": "FAT"})
    monkeypatch.setattr(floppy_image, "_windows_file_attributes", lambda _: 0x20)
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", lambda *a: None)
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", lambda *a, **kw: _mounted_root_info(root))
    real_listing = floppy_image.read_image_listing
    monkeypatch.setattr(floppy_image, "read_image_listing", lambda path, **kwargs:
        floppy_image._read_windows_filesystem_drive_listing(path, **kwargs) if path == "A:"
        else real_listing(path, **kwargs))
    return root


def test_listing_uses_fallback_cluster_size_for_allocation(mounted_drive, monkeypatch):
    (mounted_drive / "SONG.FIL").write_bytes(b"x" * 513)
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(50))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", lambda _: (4096, 2048))
    listing = floppy_image._read_windows_filesystem_drive_listing("A:")
    assert listing.free_space == 4096
    assert listing.cluster_size == 2048
    assert listing.entries[0].packed_size == 2048


@pytest.fixture
def save_session(mounted_drive, tmp_path, monkeypatch):
    (mounted_drive / "OLD.MID").write_bytes(b"original song")
    scratch = tmp_path / "session"
    scratch.mkdir()
    session = object.__new__(floppy_image.FloppyImageSession)
    session.temp_dir = str(scratch)
    monkeypatch.chdir(scratch)
    Path("prepared.img").write_bytes(b"prepared image")
    session.source_kind = "floppy_usb"
    session.source_path = "A:"
    session.working_img_path = "baseline.img"
    session.originals = {"OLD.MID": b"original song"}
    session.prepared = {"SONG.FIL": b"new song"}

    def listing(path, **kwargs):
        data = session.prepared if path == "prepared.img" else session.originals if path == "baseline.img" else None
        if data is not None:
            return floppy_image.ImageListing(
                entries=[floppy_image.ImageEntry(path=name, size=len(value), packed_size=1024, attributes="")
                         for name, value in data.items()], free_space=700000, cluster_size=1024,
            )
        assert path == "A:"
        return floppy_image._read_windows_filesystem_drive_listing(path, **kwargs)

    monkeypatch.setattr(floppy_image, "read_image_listing", listing)
    monkeypatch.setattr(floppy_image, "_read_fat12_file_bytes", lambda _, name: session.originals[name])
    monkeypatch.setattr(floppy_image, "_require_command", lambda _: "mcopy")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, path: str(Path(root) / path))
    monkeypatch.setattr(floppy_image, "_windows_mcopy_host_path", lambda root, path: str(Path(root) / path))
    session._run_mtools = lambda args, *a, **k: Path(args[-1]).write_bytes(session.prepared[args[-2].removeprefix("::/")])
    session._extract_from_image = lambda _, name, dest, **kw: Path(dest).write_bytes(session.prepared[name])

    return session


def test_file_save_continues_when_only_extended_space_query_is_unsupported(
    save_session, mounted_drive, monkeypatch,
):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(50))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", lambda _: (700000, 1024))
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert not (mounted_drive / "OLD.MID").exists()
    assert (mounted_drive / "SONG.FIL").read_bytes() == b"new song"
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "complete"
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 1
    assert diagnostics["target_mutation_attempted"] is True
    for stage in ("preflight_listing", "post_write_listing"):
        assert diagnostics[stage]["space_queries"][0]["error"]["winerror"] == 50
        assert diagnostics[stage]["space_queries"][1]["status"] == "ok"


@pytest.mark.parametrize("stage", ["preflight_listing", "post_write_listing"])
def test_failed_space_queries_report_whether_target_changes_were_attempted(
    save_session, mounted_drive, monkeypatch, stage,
):
    queries = []

    def space(_root):
        queries.append(True)
        if stage == "post_write_listing" and save_session.last_floppy_save_diagnostics["stage"] != "post_write_listing":
            return SimpleNamespace(free=700000)
        raise _windows_error(50)

    monkeypatch.setattr(floppy_image.shutil, "disk_usage", space)
    def legacy(_):
        if stage == "post_write_listing" and save_session.last_floppy_save_diagnostics["stage"] != "post_write_listing":
            return (700000, 1024)
        raise _windows_error(21)
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", legacy)
    with pytest.raises(floppy_image.FloppyImageError, match="GetDiskFreeSpaceW"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "failed"
    assert diagnostics["stage"] == stage
    assert diagnostics["cause"]["winerror"] == 21
    assert [query["error"]["winerror"] for query in diagnostics[stage]["space_queries"]] == [50, 21]
    changed = stage == "post_write_listing"
    assert diagnostics["target_mutation_attempted"] is changed
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == int(changed)
    if not changed:
        assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
        assert not (mounted_drive / "SONG.FIL").exists()


@pytest.mark.parametrize("failure", ["walk", "stat"])
def test_unreadable_listing_aborts_before_file_changes(save_session, mounted_drive, monkeypatch, failure):
    if failure == "walk":
        def walk(_root, *, onerror):
            onerror(_windows_error(5))
            return iter(())
        monkeypatch.setattr(floppy_image.os, "walk", walk)
    else:
        original_stat = floppy_image.os.stat
        def stat(path, *args, **kwargs):
            if str(path) == str(mounted_drive / "OLD.MID"):
                raise _windows_error(23)
            return original_stat(path, *args, **kwargs)
        monkeypatch.setattr(floppy_image.os, "stat", stat)
    with pytest.raises(floppy_image.FloppyImageError, match="Could not list files"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["stage"] == "preflight_listing"
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["preflight_listing"]["stage"] == ("list_directory" if failure == "walk" else "stat_file")
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert not (mounted_drive / "SONG.FIL").exists()


def test_copy_failure_reports_partial_changes(save_session, mounted_drive, monkeypatch):
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", _fail(112))
    with pytest.raises(OSError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["stage"] == "copy_file"
    assert diagnostics["file"] == "SONG.FIL"
    assert diagnostics["error"]["winerror"] == 112
    assert diagnostics["target_mutation_attempted"] is True
    assert diagnostics["files_removed"] == 1
    assert diagnostics["files_copied"] == 0


def test_commit_worker_preserves_save_diagnostics_for_report(save_session, monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(50))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", _fail(50))
    save_session.commit_to_source = lambda **kwargs: save_session._sync_modified_image_files_to_windows_drive(
        "prepared.img", "A:", **kwargs,
    )
    worker = disk_session_worker.DiskSessionCommitWorker(save_session, {})
    failures = []
    worker.commitFailed.connect(failures.append)
    worker.run()
    assert len(failures) == 1
    assert "GetDiskFreeSpaceW" in failures[0]
    assert save_session.last_floppy_save_diagnostics["stage"] == "preflight_listing"
    assert save_session.last_floppy_save_diagnostics["target_mutation_attempted"] is False


def test_new_save_attempt_replaces_previous_failure_diagnostics(save_session, monkeypatch):
    with monkeypatch.context() as failure:
        failure.setattr(floppy_image.shutil, "disk_usage", _fail(5))
        with pytest.raises(floppy_image.FloppyImageError):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    previous = save_session.last_floppy_save_diagnostics
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert previous["status"] == "failed"
    assert save_session.last_floppy_save_diagnostics["status"] == "complete"
    assert "error" not in save_session.last_floppy_save_diagnostics


def _package(session):
    directory = Path(session.last_floppy_save_diagnostics["recovery_directory"])
    return directory, json.loads((directory / "manifest.json").read_text())


@pytest.mark.parametrize("stage", ["original_backup", "before_mutation"])
@pytest.mark.parametrize("persistent", [False, True])
def test_manifest_access_denial_never_bypasses_required_recovery_checkpoint(
    save_session, mounted_drive, monkeypatch, stage, persistent,
):
    from aps_midi_prep_tool_app import rename_recovery as storage

    replace = storage._atomic_replace
    remove = floppy_image.os.remove
    denied, waits = [], []

    def publish(staged, destination):
        manifest = json.loads(staged.read_bytes())
        selected = (manifest["status"] == "preparing" and bool(manifest["originals"])) if stage == "original_backup" else (
            manifest["status"] == "writing" and manifest["actions"][-1]["operation"] == "delete"
            and manifest["actions"][-1]["status"] == "started"
        )
        if selected and (persistent or not denied):
            denied.append(True)
            assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
            assert not (mounted_drive / "SONG.FIL").exists()
            raise _windows_error(5)
        replace(staged, destination)

    def remove_with_checkpoint(path):
        if Path(path) == mounted_drive / "OLD.MID":
            _, manifest = _package(save_session)
            assert manifest["actions"][-1] == {"operation": "delete", "file": "OLD.MID", "status": "started"}
            assert manifest["originals"]["OLD.MID"]["sha256"] == hashlib.sha256(b"original song").hexdigest()
        remove(path)

    monkeypatch.setattr(storage, "_atomic_replace", publish)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    monkeypatch.setattr(floppy_image.os, "remove", remove_with_checkpoint)
    if persistent:
        with pytest.raises(OSError) as caught:
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
        assert caught.value.winerror == 5
        assert 2 <= len(denied) <= 21
        assert sum(waits) <= 1.01
        assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
        assert not (mounted_drive / "SONG.FIL").exists()
        assert save_session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
        assert save_session.last_write_verification["confidence"] == "not_written"
        directory, manifest = _package(save_session)
        assert manifest["status"] == "failed"
        assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
        assert len(denied) == 1
        assert not (mounted_drive / "OLD.MID").exists()
        assert (mounted_drive / "SONG.FIL").read_bytes() == b"new song"
        assert _package(save_session)[1]["status"] == "complete"


def test_manifest_retry_cancellation_records_cancelled_state_without_mutating_floppy(
    save_session, mounted_drive, monkeypatch,
):
    from aps_midi_prep_tool_app import rename_recovery as storage

    replace = storage._atomic_replace
    denied, waits = [], []

    def publish(staged, destination):
        status = json.loads(staged.read_bytes())["status"]
        if status == "writing" or (status == "cancelled" and status not in denied):
            denied.append(status)
            raise _windows_error(32)
        replace(staged, destination)

    monkeypatch.setattr(storage, "_atomic_replace", publish)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    with pytest.raises(floppy_image.FloppyOperationCancelled):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", cancel_callback=lambda: bool(waits),
        )
    assert denied == ["writing", "cancelled"]
    assert len(waits) == 2  # Cancellation cannot suppress the final recovery status.
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert not (mounted_drive / "SONG.FIL").exists()
    _, manifest = _package(save_session)
    assert manifest["status"] == "cancelled"
    assert manifest["diagnostics"]["target_mutation_attempted"] is False
    assert manifest["diagnostics"]["files_removed"] == manifest["diagnostics"]["files_copied"] == 0


@pytest.mark.parametrize("failure_checkpoint_denied", [False, True])
def test_manifest_failure_after_deletion_stops_before_copying_and_retains_recovery(
    save_session, mounted_drive, monkeypatch, failure_checkpoint_denied,
):
    from aps_midi_prep_tool_app import rename_recovery as storage

    replace = storage._atomic_replace
    denied, waits = [], []

    def publish(staged, destination):
        manifest = json.loads(staged.read_bytes())
        deletion_complete = (
            manifest["status"] == "writing"
            and manifest["actions"][-1] == {"operation": "delete", "file": "OLD.MID", "status": "complete"}
        )
        if deletion_complete or (failure_checkpoint_denied and manifest["status"] == "failed"):
            denied.append(manifest["status"])
            assert not (mounted_drive / "OLD.MID").exists()
            assert not (mounted_drive / "SONG.FIL").exists()
            raise _windows_error(5)
        replace(staged, destination)

    monkeypatch.setattr(storage, "_atomic_replace", publish)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", lambda *a, **kw:
                        pytest.fail("A failed journal checkpoint must stop further copying"))
    with pytest.raises(OSError) as caught:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert caught.value.winerror == 5
    assert sum(waits) <= (2.01 if failure_checkpoint_denied else 1.01)
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "failed"
    assert diagnostics["target_mutation_attempted"] is True
    assert diagnostics["files_removed"] == 1
    assert diagnostics["files_copied"] == 0
    assert save_session.last_write_verification["confidence"] == "partial_or_uncertain"
    directory, manifest = _package(save_session)
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    assert (directory / manifest["replacements"]["SONG.FIL"]["file"]).read_bytes() == b"new song"
    if failure_checkpoint_denied:
        assert diagnostics["journal_error"]
        assert manifest["status"] == "writing"
        assert manifest["actions"][-1]["status"] == "started"
        staged = json.loads((directory / "manifest.json.new").read_bytes())
        assert staged["status"] == "failed"
        assert staged["actions"][-1]["status"] == "complete"
    else:
        assert "journal_error" not in diagnostics
        assert manifest["status"] == "failed"
        assert manifest["actions"][-1]["status"] == "complete"


def test_transient_backup_manifest_failure_still_rejects_stale_floppy_contents(
    save_session, mounted_drive, monkeypatch,
):
    from aps_midi_prep_tool_app import rename_recovery as storage

    changed = b"external song"
    assert len(changed) == len(save_session.originals["OLD.MID"])
    (mounted_drive / "OLD.MID").write_bytes(changed)
    replace = storage._atomic_replace
    denied = []

    def publish(staged, destination):
        manifest = json.loads(staged.read_bytes())
        if manifest["originals"] and not denied:
            denied.append(True)
            raise _windows_error(5)
        replace(staged, destination)

    monkeypatch.setattr(storage, "_atomic_replace", publish)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    with pytest.raises(floppy_image.FloppyImageError, match="contents changed since it was opened"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert len(denied) == 1
    assert (mounted_drive / "OLD.MID").read_bytes() == changed
    assert not (mounted_drive / "SONG.FIL").exists()
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
    directory, manifest = _package(save_session)
    assert manifest["status"] == "failed"
    assert manifest["actions"] == []
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == changed


@pytest.mark.parametrize("failed_copy", [1, 3])
def test_failed_copy_retains_every_original_and_durable_replacement_locally(save_session, mounted_drive, failed_copy, monkeypatch):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) * 40 for i in range(3)}
    copy = floppy_image._copy_prepared_floppy_file
    calls = []

    def fail_copy(args, *a, **kw):
        calls.append(args)
        if len(calls) == failed_copy:
            raise _windows_error(21)
        return copy(args, *a, **kw)

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail_copy)
    with pytest.raises(OSError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert not (mounted_drive / "OLD.MID").exists()
    assert save_session.last_floppy_save_diagnostics["files_removed"] == 1
    assert save_session.last_write_verification["confidence"] == "partial_or_uncertain"
    directory, manifest = _package(save_session)
    assert (directory / "prepared.img").read_bytes() == b"prepared image"
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    for name, data in save_session.prepared.items():
        assert (directory / manifest["replacements"][name]["file"]).read_bytes() == data
    assert manifest["status"] == "failed"
    assert manifest["actions"][-1]["status"] == "started"
    assert not list(mounted_drive.glob("APS*.TMP"))
    assert manifest.get("staging_files_remaining", []) == []
    assert manifest["diagnostics"]["files_copied"] == failed_copy - 1
    assert manifest["diagnostics"]["restoration"]["status"] == "not_attempted"
    assert {path.name for path in mounted_drive.iterdir()} == {f"SONG{i}.MID" for i in range(failed_copy - 1)}


def test_insufficient_space_stops_before_mutation_and_retains_recovery(save_session, mounted_drive, monkeypatch):
    save_session.prepared = {"SONG.FIL": b"x" * 1025}
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=1000))
    with pytest.raises(floppy_image.FloppyImageError, match="available after replacing old files"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert list(mounted_drive.iterdir()) == [mounted_drive / "OLD.MID"]
    assert save_session.last_write_verification["confidence"] == "not_written"
    directory, manifest = _package(save_session)
    assert manifest["originals"] and manifest["replacements"]
    assert (directory / "prepared.img").exists()
    assert not manifest["actions"]


@pytest.mark.parametrize("change", ["new_file", "changed_bytes", "missing_file", "empty_folder"])
def test_changed_target_is_rejected_before_mutation(save_session, mounted_drive, change):
    if change == "new_file":
        (mounted_drive / "OTHER.MID").write_bytes(b"other")
    elif change == "changed_bytes":
        (mounted_drive / "OLD.MID").write_bytes(b"different song")
    elif change == "missing_file":
        (mounted_drive / "OLD.MID").unlink()
    else:
        (mounted_drive / "FOLDER").mkdir()
    with pytest.raises(floppy_image.FloppyImageError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]
    assert not (mounted_drive / "SONG.FIL").exists()


def test_cancel_after_deletion_retains_original_and_does_not_publish_catalog(save_session, mounted_drive):
    save_session.prepared["PIANODIR.FIL"] = b"new catalog"
    with pytest.raises(floppy_image.FloppyOperationCancelled):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", cancel_callback=lambda: not (mounted_drive / "OLD.MID").exists(),
        )
    assert not (mounted_drive / "PIANODIR.FIL").exists()
    assert save_session.last_write_verification["confidence"] == "partial_or_uncertain"
    directory, manifest = _package(save_session)
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    assert manifest["status"] == "cancelled"
    assert manifest["actions"][-1] == {"operation": "delete", "file": "OLD.MID", "status": "complete"}
    assert not list(mounted_drive.iterdir())
    assert manifest["diagnostics"]["restoration"]["status"] == "not_attempted"


def test_catalog_is_published_after_songs_and_deletions(save_session):
    save_session.prepared["PIANODIR.FIL"] = b"new catalog"
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    _, manifest = _package(save_session)
    assert manifest["actions"][-1] == {"operation": "replace", "file": "PIANODIR.FIL", "status": "complete"}
    assert manifest["diagnostics"]["file_contents_verified"] is True


@pytest.mark.parametrize("damage", ["bytes", "name", "walk_failure"])
def test_final_verification_rejects_corruption_and_incomplete_listing(save_session, mounted_drive, monkeypatch, damage):
    real_listing = floppy_image.read_image_listing

    def listing(path, **kwargs):
        if save_session.last_floppy_save_diagnostics["stage"] == "post_write_listing":
            song = mounted_drive / "SONG.FIL"
            if damage == "bytes":
                song.write_bytes(b"bad song")
            elif damage == "name":
                song.rename(mounted_drive / "WRONG.FIL")
            else:
                def walk(root, *, onerror):
                    yield str(mounted_drive), [], ["SONG.FIL"]
                    onerror(_windows_error(23))
                monkeypatch.setattr(floppy_image.os, "walk", walk)
        return real_listing(path, **kwargs)

    monkeypatch.setattr(floppy_image, "read_image_listing", listing)
    with pytest.raises(floppy_image.FloppyImageError, match="verification failed|Could not list"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert save_session.last_write_verification["confidence"] == "partial_or_uncertain"
    assert not save_session.last_floppy_save_diagnostics.get("file_contents_verified")


def test_directory_is_independently_checked_when_both_space_queries_fail(mounted_drive, monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(50))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", _fail(50))
    diagnostics = {}
    with pytest.raises(floppy_image.FloppyImageError, match="filesystem space"):
        floppy_image._read_windows_filesystem_drive_listing("A:", diagnostics=diagnostics)
    assert diagnostics["directory_status"] == "complete"
    assert len(diagnostics["space_queries"]) == 2


def test_unsupported_space_and_directory_access_keeps_prepared_image_without_writing(
    save_session, mounted_drive, monkeypatch,
):
    """Reproduce a raw-readable disk whose Windows file APIs all return error 50."""
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", _fail(50))
    monkeypatch.setattr(floppy_image, "_windows_legacy_disk_space", _fail(50))

    def walk(_root, *, onerror):
        onerror(_windows_error(50))
        return iter(())

    monkeypatch.setattr(floppy_image.os, "walk", walk)
    for name in ("_copy_prepared_floppy_file", "_write_block_device", "_set_windows_file_attributes"):
        monkeypatch.setattr(floppy_image, name, lambda *a, **k: pytest.fail("Unexpected target mutation"))

    with pytest.raises(floppy_image.FloppyImageError, match="Could not list files"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["stage"] == "preflight_listing"
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_staged"] == diagnostics["files_copied"] == diagnostics["files_removed"] == 0
    assert save_session.last_write_verification["confidence"] == "not_written"
    listing = diagnostics["preflight_listing"]
    assert listing["stage"] == "list_directory"
    assert listing["directory_status"] == "failed"
    assert listing["error"]["winerror"] == 50
    assert [query["error"]["winerror"] for query in listing["space_queries"]] == [50, 50]
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == save_session.originals
    directory, manifest = _package(save_session)
    assert manifest["status"] == "failed"
    assert manifest["diagnostics"] == diagnostics
    assert not manifest["actions"]
    assert (directory / "prepared.img").read_bytes() == Path("prepared.img").read_bytes()


def test_save_roundtrip_with_real_images_and_native_copying(mounted_drive, tmp_path, monkeypatch):
    """Exercise local preparation, direct final-name copies, and readback together."""
    import os
    # Python 3.10 remains supported and does not provide hashlib.file_digest.
    monkeypatch.delattr(hashlib, "file_digest", raising=False)
    disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    baseline, prepared = tmp_path / "baseline.img", tmp_path / "prepared.img"
    original = tmp_path / "OLD.MID"
    original.write_bytes(b"original file")
    replacement = tmp_path / "NEW.MID"
    replacement.write_bytes(b"replacement file")
    floppy_image.create_blank_floppy_image(baseline, disk_format)
    floppy_image._copy_host_file_into_image(baseline, original, "OLD.MID")
    floppy_image.create_blank_floppy_image(prepared, disk_format)
    floppy_image._copy_host_file_into_image(prepared, replacement, "NEW.MID")
    (mounted_drive / "OLD.MID").write_bytes(original.read_bytes())
    session = object.__new__(floppy_image.FloppyImageSession)
    session.temp_dir, session.working_img_path = str(tmp_path), str(baseline)
    session.source_kind, session.source_path = "floppy_usb", "A:"
    monkeypatch.setattr(floppy_image, "read_image_listing", lambda path, **kw:
        floppy_image._read_windows_filesystem_drive_listing(path, **kw) if path == "A:"
        else floppy_image._read_fat12_image_listing(path))
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, name: str(Path(root) / name))
    def host(root, name):
        path = str(Path(root) / name)
        return "//?/" + path.replace("\\", "/") if os.name == "nt" else path
    monkeypatch.setattr(floppy_image, "_windows_mcopy_host_path", host)
    monkeypatch.setattr(session, "_run_mtools", lambda *_a, **_k: pytest.fail("Prepared files use native copying"))
    session._sync_modified_image_files_to_windows_drive(str(prepared), "A:")
    assert [path.name for path in mounted_drive.iterdir()] == ["NEW.MID"]
    assert (mounted_drive / "NEW.MID").read_bytes() == replacement.read_bytes()
    assert session.last_floppy_save_diagnostics["file_contents_verified"] is True


def _prepared_save(tmp_path, files, overwrite=()):
    specs = []
    for index, (name, payload) in enumerate(files.items()):
        host = tmp_path / f"prepared-{index}.bin"
        host.write_bytes(payload)
        specs.append({"host_path": str(host), "image_path": name})
    return floppy_image.PreparedWindowsFileSave(specs, overwrite_names=overwrite)


def test_direct_save_preserves_unrelated_files_and_folders(mounted_drive, tmp_path, monkeypatch):
    (mounted_drive / "KEEP.MID").write_bytes(b"unrelated")
    (mounted_drive / "FOLDER").mkdir()
    (mounted_drive / "FOLDER" / "OTHER.MID").write_bytes(b"nested")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, path: str(Path(root) / path))
    monkeypatch.setattr(floppy_image, "_require_command", lambda *_a: pytest.fail("Direct save requires no image tools"))
    job = _prepared_save(tmp_path, {"NEW.MID": b"new song"})

    job.write_to_floppy_target("floppy_usb", "A:")

    assert (mounted_drive / "NEW.MID").read_bytes() == b"new song"
    assert (mounted_drive / "KEEP.MID").read_bytes() == b"unrelated"
    assert (mounted_drive / "FOLDER" / "OTHER.MID").read_bytes() == b"nested"
    assert job.last_write_verification["confidence"] == "contents_verified"
    directory, manifest = _package(job)
    assert not (directory / "prepared.img").exists()
    assert manifest["status"] == "complete"


@pytest.mark.parametrize("approved", [False, True])
def test_direct_save_requires_confirmation_for_matching_names(mounted_drive, tmp_path, monkeypatch, approved):
    (mounted_drive / "song.mid").write_bytes(b"original")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, path: str(Path(root) / path))
    job = _prepared_save(tmp_path, {"SONG.MID": b"replacement"}, ["SONG.MID"] if approved else [])
    if approved:
        job.write_to_floppy_target("floppy_usb", "A:")
        assert (mounted_drive / "song.mid").read_bytes() == b"replacement"
        directory, manifest = _package(job)
        assert (directory / manifest["originals"]["song.mid"]["file"]).read_bytes() == b"original"
    else:
        with pytest.raises(floppy_image.FloppyImageError, match="not approved"):
            job.write_to_floppy_target("floppy_usb", "A:")
        assert (mounted_drive / "song.mid").read_bytes() == b"original"
        assert not job.last_floppy_save_diagnostics["target_mutation_attempted"]


def test_direct_save_checks_space_before_touching_target(mounted_drive, tmp_path, monkeypatch):
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, path: str(Path(root) / path))
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    job = _prepared_save(tmp_path, {"NEW.MID": b"new"})
    with pytest.raises(floppy_image.FloppyImageError, match="available after replacing old files"):
        job.write_to_floppy_target("floppy_usb", "A:")
    assert list(mounted_drive.iterdir()) == []
    assert not job.last_floppy_save_diagnostics["target_mutation_attempted"]


def test_direct_save_verification_detects_unrelated_changes_during_copying(mounted_drive, tmp_path, monkeypatch):
    original = mounted_drive / "OLD.MID"
    original.write_bytes(b"before")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, path: str(Path(root) / path))
    real_copy = floppy_image._copy_prepared_floppy_file
    def change_target(*args, **kwargs):
        real_copy(*args, **kwargs)
        original.write_bytes(b"another process changed this")
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", change_target)
    job = _prepared_save(tmp_path, {"NEW.MID": b"new"})
    with pytest.raises(floppy_image.FloppyImageError, match="contents differ"):
        job.write_to_floppy_target("floppy_usb", "A:")
    assert original.read_bytes() == b"another process changed this"
    assert (mounted_drive / "NEW.MID").read_bytes() == b"new"
    assert not list(mounted_drive.glob("APS*.TMP"))
    assert job.last_write_verification["confidence"] == "partial_or_uncertain"


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("failure", ["third_copy", "partial_copy", "cancel", "flush", "verify"])
def test_failed_save_retains_local_recovery_without_extra_floppy_files(
    save_session, mounted_drive, tmp_path, monkeypatch, direct, failure,
):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) * 40 for i in range(3)}
    session = _prepared_save(tmp_path, save_session.prepared) if direct else save_session
    copy = floppy_image._copy_prepared_floppy_file
    destinations = []
    def fail(source, destination, **kwargs):
        destinations.append(destination)
        if len(destinations) == 3 and failure == "third_copy":
            raise _windows_error(21)
        if len(destinations) == 3 and failure == "partial_copy":
            with open(destination, "xb") as handle:
                kwargs["created_callback"]()
                handle.write(b"partial")
            raise _windows_error(112)
        copy(source, destination, **kwargs)
        if len(destinations) == 3 and failure == "verify":
            Path(destination).write_bytes(b"corrupt copied file")
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail)
    if failure == "flush":
        real_sync = floppy_image.floppy_save_recovery.sync_file
        def sync(path):
            if Path(path).parent == mounted_drive:
                raise _windows_error(21)
            real_sync(path)
        monkeypatch.setattr(floppy_image.floppy_save_recovery, "sync_file", sync)
    with pytest.raises((OSError, floppy_image.FloppyImageError)):
        session._sync_modified_image_files_to_windows_drive(
            None if direct else "prepared.img", "A:",
            cancel_callback=lambda: failure == "cancel" and len(destinations) == 3,
        )
    assert (mounted_drive / "OLD.MID").exists() is direct
    assert not list(mounted_drive.glob("APS*.TMP"))
    assert {Path(path).name for path in destinations} <= set(save_session.prepared)
    directory, manifest = _package(session)
    assert manifest["status"] == ("cancelled" if failure == "cancel" else "failed")
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    for name, payload in save_session.prepared.items():
        assert (directory / manifest["replacements"][name]["file"]).read_bytes() == payload
    assert session.last_write_verification["confidence"] == "partial_or_uncertain"


def test_cancel_during_native_copy_retains_original_and_replacement_locally(save_session, mounted_drive):
    save_session.prepared = {"SONG.MID": b"x" * 150000}
    def cancelled():
        song = mounted_drive / "SONG.MID"
        return song.exists() and song.stat().st_size >= 65536
    with pytest.raises(floppy_image.FloppyOperationCancelled):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", cancel_callback=cancelled)
    assert {path.name for path in mounted_drive.iterdir()} == {"SONG.MID"}
    assert 0 < (mounted_drive / "SONG.MID").stat().st_size < 150000
    directory, manifest = _package(save_session)
    assert manifest["status"] == "cancelled"
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    assert (directory / manifest["replacements"]["SONG.MID"]["file"]).read_bytes() == b"x" * 150000


@pytest.mark.parametrize("identity_failure", ["swap", "unreadable", "unavailable_from_start"])
def test_failed_save_does_not_write_recovery_to_uncertain_media(save_session, mounted_drive, monkeypatch, identity_failure):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) * 40 for i in range(3)}
    real_copy = floppy_image._copy_prepared_floppy_file
    destinations = []
    def identity(_):
        if identity_failure == "unavailable_from_start" or (len(destinations) == 3 and identity_failure == "unreadable"):
            raise _windows_error(21)
        return {"serial": 456 if len(destinations) == 3 else 123}
    monkeypatch.setattr(floppy_image, "_windows_volume_identity", identity)
    def copy(source, destination, **kwargs):
        destinations.append(Path(destination))
        if len(destinations) == 3:
            raise _windows_error(21)
        real_copy(source, destination, **kwargs)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", copy)
    with pytest.raises(OSError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    directory, manifest = _package(save_session)
    assert all(path.exists() for path in destinations[:2])
    assert not list(mounted_drive.glob("APS*.TMP"))
    assert manifest["diagnostics"]["restoration"]["status"] == "not_attempted"
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"


def test_exclusive_creation_preserves_a_conflicting_final_name(save_session, mounted_drive, monkeypatch):
    copy = floppy_image._copy_prepared_floppy_file
    def collide(source, destination, **kwargs):
        Path(destination).write_bytes(b"another writer owns this")
        copy(source, destination, **kwargs)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", collide)
    with pytest.raises(FileExistsError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert (mounted_drive / "SONG.FIL").read_bytes() == b"another writer owns this"
    assert not list(mounted_drive.glob("APS*.TMP"))


def test_deletion_failure_retains_recovery_and_stops_before_copying(save_session, mounted_drive, monkeypatch):
    remove = floppy_image.os.remove
    def deny(path):
        if Path(path).parent == mounted_drive:
            raise _windows_error(5)
        return remove(path)
    monkeypatch.setattr(floppy_image.os, "remove", deny)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", lambda *a, **k: pytest.fail("Copy after failed removal"))
    with pytest.raises(OSError) as caught:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert caught.value.winerror == 5
    directory, manifest = _package(save_session)
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    assert manifest["diagnostics"]["files_copied"] == 0


def test_file_progress_counts_copied_files_only_after_readback(save_session, mounted_drive, monkeypatch):
    save_session.prepared["SECOND.MID"] = b"second song"
    events, copied, verified = [], [], set()
    copy = floppy_image._copy_prepared_floppy_file
    digest = floppy_image.floppy_save_recovery.digest

    def record_copy(source, destination, **kwargs):
        step, total, message = events[-1]
        assert (step, total) == (len(copied), 2)
        assert message == f"Copying files to floppy...\nFile {len(copied) + 1} of 2: {list(save_session.prepared)[len(copied)]}"
        copied.append(Path(destination))
        return copy(source, destination, **kwargs)

    def record_digest(path):
        result = digest(path)
        if Path(path) in copied and events[-1][2].startswith("Verifying copied files..."):
            assert events[-1][0] == len(verified)
            assert events[-1][2].startswith("Verifying copied files...\n")
            verified.add(Path(path))
        return result

    def progress(step, total, message):
        events.append((step, total, message))
        if message.startswith("Copying files to floppy...\n"):
            assert step <= len(verified)

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", record_copy)
    monkeypatch.setattr(floppy_image.floppy_save_recovery, "digest", record_digest)
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", progress_callback=progress)

    assert len(copied) == len(verified) == 2
    assert (0, 1, "Backing up files...\nFile 1 of 1: OLD.MID") in events
    assert (1, 1, "Backing up files...\nFile 1 of 1: OLD.MID") in events
    assert (2, 2, "Copying files to floppy...\nFile 2 of 2: SECOND.MID") in events
    for phase in ("Checking floppy...", "Preparing files...", "Checking original files...",
                  "Finishing file updates...", "Verifying saved files..."):
        assert (0, 0, phase) in events
    assert any(step == total == 2 and message.startswith("Verifying saved files...\n")
               for step, total, message in events)
    assert events[-1] == (0, 0, "Finishing floppy save...")
    assert save_session.last_write_verification["confidence"] == "contents_verified"


@pytest.mark.parametrize("failure", ["copy", "readback"])
def test_failed_copy_does_not_advance_file_progress(save_session, monkeypatch, failure):
    save_session.prepared["SECOND.MID"] = b"second song"
    copy = floppy_image._copy_prepared_floppy_file
    events, copies = [], []

    def fail_second(source, destination, **kwargs):
        copies.append(destination)
        if len(copies) == 2 and failure == "copy":
            raise OSError("Simulated copy failure")
        copy(source, destination, **kwargs)
        if len(copies) == 2:
            Path(destination).write_bytes(b"damaged")

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail_second)
    with pytest.raises((OSError, floppy_image.FloppyImageError)):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", progress_callback=lambda *event: events.append(event),
        )

    copying = [(step, total) for step, total, message in events
               if message.startswith("Copying files to floppy...\n")]
    assert copying == [(0, 2), (1, 2), (1, 2)]
    assert not any(message == "Finishing floppy save..." for _step, _total, message in events)


@pytest.mark.parametrize("free_entries", [0, 1, 2, 3])
def test_save_preflights_final_root_slots_separately_from_clusters(save_session, mounted_drive, monkeypatch, free_entries):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) for i in range(3)}
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info",
                        lambda *a, **kw: _mounted_root_info(mounted_drive, free_entries + 2))
    if free_entries + 1 < 3:
        with pytest.raises(floppy_image.FloppyImageError, match="root-directory entries"):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
        assert list(mounted_drive.iterdir()) == [mounted_drive / "OLD.MID"]
        assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
        assert not list(mounted_drive.glob("APS*.TMP"))


def test_root_capacity_query_does_not_require_raw_access(save_session, monkeypatch):
    monkeypatch.setattr(floppy_image, "_WindowsRecoveryVolumeHandle",
                        lambda *a, **kw: pytest.fail("File saving must not request raw-volume access"))
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert save_session.last_floppy_save_diagnostics["root_directory_check"]["status"] == "complete"


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("name", ["SECOND.MID", "A long song title.mid"])
def test_unavailable_root_capacity_rejects_growth_before_mutation(
    save_session, mounted_drive, monkeypatch, backup, name,
):
    save_session.prepared[name] = b"extra song"
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", _fail(5))
    with pytest.raises(floppy_image.FloppyImageError, match="root-directory"):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", backup_originals=backup,
        )
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {"OLD.MID": b"original song"}
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0


def test_unavailable_root_capacity_allows_same_name_replacement(save_session, mounted_drive, monkeypatch):
    save_session.prepared = {"OLD.MID": b"replacement"}
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", _fail(5))
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {"OLD.MID": b"replacement"}


def test_unavailable_root_capacity_rejects_new_short_name_with_unknown_tunnel_cache(
    save_session, mounted_drive, monkeypatch,
):
    save_session.prepared = {"NEW.MID": b"replacement"}
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", _fail(5))
    with pytest.raises(floppy_image.FloppyImageError, match="root-directory"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]


@pytest.mark.parametrize("original,first", [("一.MID", "FIRST.MID"), ("A long song title.mid", "ALONGS~1.MID")])
def test_unavailable_metadata_does_not_assume_oem_or_tunneled_slots(
    save_session, mounted_drive, monkeypatch, original, first,
):
    (mounted_drive / "OLD.MID").rename(mounted_drive / original)
    save_session.originals = {original: b"original song"}
    save_session.prepared = {first: b"first", "SECOND.MID": b"second"}
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", _fail(5))
    with pytest.raises(floppy_image.FloppyImageError, match="root-directory"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {original: b"original song"}
    assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]


def _image_root_info(path):
    """Adapt a real FAT12 directory to the metadata Windows exposes."""
    data = path.read_bytes()
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    directory = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    entries = []
    for entry in floppy_image._iter_fat_directory_entries(directory):
        if entry["attr"] & 0x08:
            continue
        offset = entry["offset"]
        first = offset
        while first >= 32 and directory[first - 32 + 11] == 0x0F:
            first -= 32
        lfn = [directory[index:index + 32] for index in range(first, offset, 32)]
        long_name = floppy_image._decode_fat_lfn_entries(lfn, entry["short_name_bytes"])
        entries.append({
            "name": entry["name"],
            "short_name": entry["short_name"] if long_name else "",
            "attributes": entry["attr"],
            "slots": 1 + (len(long_name.encode("utf-16-le")) + 25) // 26 if long_name else 1,
        })
    return {"root_entries": geometry.root_entries, "entries": entries}


def _delete_image_files(path, *names):
    floppy_image._run_command(
        [floppy_image._require_command("mdel"), "-i", str(path),
         *(floppy_image.mtools_path(name) for name in names)],
        "Could not prepare root-capacity fixture",
    )


def _add_image_file(path, tmp_path, name, payload):
    source = tmp_path / "replacement.bin"
    source.write_bytes(payload)
    floppy_image._copy_host_file_into_image(path, source, name)


@pytest.fixture
def full_windows_root(mounted_drive, tmp_path, monkeypatch):
    """The native metadata and mounted files describe the same full 720K root."""
    baseline, prepared = tmp_path / "baseline.img", tmp_path / "prepared.img"
    originals = {"OLD.MID": b"original song", **{f"KEEP{index:03}.MID": b"" for index in range(110)}}
    for name, payload in originals.items():
        (mounted_drive / name).write_bytes(payload)
    for path, format_key in ((baseline, "ibm.720"), (prepared, "ibm.1440")):
        floppy_image.create_blank_floppy_image(path, floppy_image.DISK_FORMAT_BY_KEY[format_key])
        floppy_image._run_command(
            [floppy_image._require_command("mcopy"), "-i", str(path),
             *(str(mounted_drive / name) for name in originals), "::/"],
            "Could not populate root-capacity fixture",
        )
    metadata = _image_root_info(baseline)
    assert metadata["root_entries"] == 112
    assert sum(entry["slots"] for entry in metadata["entries"]) == 111
    session = object.__new__(floppy_image.FloppyImageSession)
    session.temp_dir, session.working_img_path = str(tmp_path), str(baseline)
    session.source_kind, session.source_path = "floppy_usb", "A:"
    monkeypatch.setattr(floppy_image, "read_image_listing", lambda path, **kw:
                        floppy_image._read_windows_filesystem_drive_listing(path, **kw) if path == "A:"
                        else floppy_image._read_fat12_image_listing(path))
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, name: str(Path(root) / name))
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", lambda *a, **kw: _image_root_info(baseline))
    monkeypatch.setattr(floppy_image, "_WindowsRecoveryVolumeHandle",
                        lambda *a, **kw: pytest.fail("Capacity checks must not read the raw floppy"))
    return session, baseline, prepared


@pytest.mark.parametrize("backup", [False, True])
def test_full_fat12_root_rejects_three_slot_replacement_before_mutation(
    full_windows_root, mounted_drive, tmp_path, monkeypatch, backup,
):
    session, _baseline, prepared = full_windows_root
    _delete_image_files(prepared, "OLD.MID")
    _add_image_file(prepared, tmp_path, "A long song title.mid", b"replacement")
    monkeypatch.setattr(floppy_image, "_windows_file_attributes",
                        lambda path: 0x27 if Path(path).name == "OLD.MID" else 0x20)
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes",
                        lambda *a, **kw: pytest.fail("Directory preflight must precede attribute changes"))
    originals = {path.name: path.read_bytes() for path in mounted_drive.iterdir()}
    with pytest.raises(floppy_image.FloppyImageError, match="root-directory entries"):
        session._sync_modified_image_files_to_windows_drive(str(prepared), "A:", backup_originals=backup)
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == originals
    diagnostics = session.last_floppy_save_diagnostics
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
    assert diagnostics["root_directory_check"]["required_entries"] == 3
    assert diagnostics["root_directory_check"]["available_entries"] == 1


def test_windows_allows_fragmented_root_slots_that_fastfat_can_compact(full_windows_root, mounted_drive, tmp_path):
    session, baseline, prepared = full_windows_root
    for path in (baseline, prepared):
        _delete_image_files(path, "KEEP020.MID", "KEEP050.MID")
    for name in ("KEEP020.MID", "KEEP050.MID"):
        (mounted_drive / name).unlink()
    _delete_image_files(prepared, "OLD.MID")
    _add_image_file(prepared, tmp_path, "A long song title.mid", b"replacement")
    data = baseline.read_bytes()
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    directory = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    deleted = [index for index in range(geometry.root_entries) if directory[index * 32] == 0xE5]
    assert len(deleted) == 2 and deleted[1] - deleted[0] > 1
    session._sync_modified_image_files_to_windows_drive(str(prepared), "A:")
    assert (mounted_drive / "A long song title.mid").read_bytes() == b"replacement"
    assert not (mounted_drive / "OLD.MID").exists()
    check = session.last_floppy_save_diagnostics["root_directory_check"]
    assert check["free_entries"] == 2
    assert check["required_entries"] == check["available_entries"] == 3


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("replace_long_name", [False, True])
def test_windows_reclaims_all_long_filename_slots(full_windows_root, mounted_drive, tmp_path, backup, replace_long_name):
    session, baseline, prepared = full_windows_root
    name = "A long song title.mid"
    removed = ("OLD.MID", "KEEP000.MID", "KEEP001.MID")
    _delete_image_files(baseline, *removed)
    _add_image_file(baseline, tmp_path, name, b"original long song")
    for original in removed:
        (mounted_drive / original).unlink()
    (mounted_drive / name).write_bytes(b"original long song")
    if replace_long_name:
        _delete_image_files(prepared, *removed)
        _add_image_file(prepared, tmp_path, name, b"replacement")
    assert sum(entry["slots"] for entry in _image_root_info(baseline)["entries"]) == 111
    session._sync_modified_image_files_to_windows_drive(str(prepared), "A:", backup_originals=backup)
    check = session.last_floppy_save_diagnostics["root_directory_check"]
    assert check["free_entries"] == 0
    assert check["reclaimed_entries"] == check["required_entries"] == check["available_entries"] == 3
    if replace_long_name:
        assert (mounted_drive / name).read_bytes() == b"replacement"
    else:
        assert not (mounted_drive / name).exists()
        assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"


def test_windows_reclaims_redundant_lfn_for_short_name(full_windows_root, mounted_drive, tmp_path):
    session, baseline, prepared = full_windows_root
    _delete_image_files(baseline, "OLD.MID", "KEEP000.MID")
    _add_image_file(baseline, tmp_path, "Old.mid", b"original song")
    data = bytearray(baseline.read_bytes())
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    directory = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    entry = next(entry for entry in floppy_image._iter_fat_directory_entries(directory) if entry["name"] == "Old.mid")
    lfn_offset = geometry.root_offset + entry["offset"] - 32
    assert data[lfn_offset + 11] == 0x0F
    # Foreign writers can retain LFN records even when the visible spelling is
    # already uppercase 8.3. Windows still reports the associated short alias.
    name = "OLD.MID".encode("utf-16-le") + b"\x00\x00" + b"\xff\xff" * 5
    for start, end, part in ((1, 11, name[:10]), (14, 26, name[10:22]), (28, 32, name[22:26])):
        data[lfn_offset + start:lfn_offset + end] = part
    baseline.write_bytes(data)
    (mounted_drive / "KEEP000.MID").unlink()
    metadata = next(entry for entry in _image_root_info(baseline)["entries"] if entry["name"] == "OLD.MID")
    assert metadata["short_name"] == "OLD.MID" and metadata["slots"] == 2
    _delete_image_files(prepared, "OLD.MID", "KEEP000.MID")
    for name in ("FIRST.MID", "SECOND.MID"):
        _add_image_file(prepared, tmp_path, name, b"replacement")
    session._sync_modified_image_files_to_windows_drive(str(prepared), "A:")
    check = session.last_floppy_save_diagnostics["root_directory_check"]
    assert check["free_entries"] == 0
    assert check["reclaimed_entries"] == check["required_entries"] == 2
    assert (mounted_drive / "FIRST.MID").read_bytes() == (mounted_drive / "SECOND.MID").read_bytes() == b"replacement"


def test_capacity_counts_hidden_folders(mounted_drive, tmp_path, monkeypatch):
    (mounted_drive / "Hidden Folder").mkdir()
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, name: str(Path(root) / name))
    metadata = _mounted_root_info(mounted_drive, 3)
    metadata["entries"][0]["attributes"] = 0x12
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", lambda *a, **kw: metadata)
    job = _prepared_save(tmp_path, {"NEW.MID": b"new song"})
    with pytest.raises(floppy_image.FloppyImageError, match="root-directory entries"):
        job.write_to_floppy_target("floppy_usb", "A:")
    assert list(mounted_drive.iterdir()) == [mounted_drive / "Hidden Folder"]
    assert not job.last_floppy_save_diagnostics["target_mutation_attempted"]


def test_capacity_rejects_unapproved_existing_short_alias(mounted_drive, tmp_path, monkeypatch):
    original = mounted_drive / "A long song title.mid"
    original.write_bytes(b"original song")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, name: str(Path(root) / name))
    job = _prepared_save(tmp_path, {"F0000000.MID": b"replacement"})
    with pytest.raises(floppy_image.FloppyImageError, match="short alias"):
        job.write_to_floppy_target("floppy_usb", "A:")
    assert list(mounted_drive.iterdir()) == [original]
    assert original.read_bytes() == b"original song"
    assert not job.last_floppy_save_diagnostics["target_mutation_attempted"]


def test_capacity_uses_actual_destination_case(save_session, mounted_drive, monkeypatch):
    save_session.prepared = {"Old.mid": b"replacement"}
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info",
                        lambda *a, **kw: _mounted_root_info(mounted_drive, 2))
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert (mounted_drive / "OLD.MID").read_bytes() == b"replacement"
    assert save_session.last_floppy_save_diagnostics["root_directory_check"]["required_entries"] == 1


@pytest.mark.parametrize("name,required", [("é.mid", 2), ("😀" * 5 + ".mid", 3), ("Song.mid", 2)])
def test_windows_unicode_and_mixed_case_need_additional_slots(save_session, mounted_drive, monkeypatch, name, required):
    save_session.prepared = {name: b"replacement"}
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info",
                        lambda *a, **kw: _mounted_root_info(mounted_drive, 2))
    with pytest.raises(floppy_image.FloppyImageError, match="root-directory entries"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert save_session.last_floppy_save_diagnostics["root_directory_check"]["required_entries"] == required
    assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]


@pytest.mark.parametrize("outcome", ["complete", "failed", "cancelled"])
@pytest.mark.parametrize("operation", ["commit", "write_target"])
def test_package_waits_for_optional_readback_before_completion(save_session, monkeypatch, outcome, operation):
    save_session.create_modified_image = lambda **kw: "prepared.img"
    save_session.disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    save_session.drive_info = None
    save_session._extracted_files = {}
    def sync(image, drive, **kw):
        save_session._sync_modified_image_files_to_windows_drive(image, drive, finalize_recovery=False, **kw)
    save_session._sync_modified_image_files_to_floppy_drive = sync
    def readback(*a, **kw):
        _, manifest = _package(save_session)
        assert manifest["status"] == "contents_verified"
        if outcome == "failed":
            raise floppy_image.FloppyImageError("Readback failed")
        if outcome == "cancelled":
            raise floppy_image.FloppyOperationCancelled("Readback cancelled")
        return {"confidence": "contents_verified", "hardware_tested": False}
    monkeypatch.setattr(floppy_image, "_verify_physical_floppy_contents", readback)
    def save():
        if operation == "commit":
            save_session.commit_to_source(verify_after_write=True)
        else:
            save_session.write_to_floppy_target(
                "floppy_usb", floppy_image.FloppyDriveInfo("A:", save_session.disk_format.size_bytes),
                file_level=True, verify_after_write=True,
            )
    if outcome == "complete":
        save()
    else:
        with pytest.raises(floppy_image.FloppyImageError):
            save()
    directory, manifest = _package(save_session)
    assert manifest["status"] == outcome
    assert (directory / "prepared.img").exists()


@pytest.fixture
def protected_target(save_session, mounted_drive, monkeypatch):
    (mounted_drive / "OLD.MID").unlink()
    save_session.originals = {"FIRST.FIL": b"first original", "LAST.FIL": b"last original"}
    save_session.prepared = {"FIRST.FIL": b"first replacement", "LAST.FIL": b"last replacement"}
    for name, data in save_session.originals.items():
        (mounted_drive / name).write_bytes(data)
    attributes = {"FIRST.FIL": 0x20, "LAST.FIL": 0x27}
    operations = []
    def get_attributes(path):
        if not Path(path).exists():
            raise FileNotFoundError(path)
        return attributes.get(Path(path).name, 0x20)
    def set_attributes(path, value):
        operations.append(("attributes", Path(path).name, value))
        attributes[Path(path).name] = (value & ~0x80) or 0x80
    real_copy = floppy_image._copy_prepared_floppy_file
    def copy(source, destination, **kwargs):
        name = Path(destination).name
        operations.append(("publish", name))
        real_copy(source, destination, **kwargs)
        attributes[name] = 0x20
    real_remove = floppy_image.os.remove
    def remove(path):
        if Path(path).parent == mounted_drive and get_attributes(path) & 0x01:
            raise _windows_error(5)
        real_remove(path)
        if Path(path).parent == mounted_drive:
            attributes.pop(Path(path).name, None)
    monkeypatch.setattr(floppy_image, "_windows_file_attributes", get_attributes)
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", set_attributes)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", copy)
    monkeypatch.setattr(floppy_image.os, "remove", remove)
    return attributes, operations


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("flags", [0x01, 0x07, 0x27])
def test_protected_replacement_is_preflighted_and_preserves_attributes(
    save_session, mounted_drive, protected_target, tmp_path, direct, flags,
):
    attributes, operations = protected_target
    attributes["LAST.FIL"] = flags
    job = _prepared_save(tmp_path, save_session.prepared, save_session.prepared) if direct else save_session
    job._sync_modified_image_files_to_windows_drive(None if direct else "prepared.img", "A:")
    for name, payload in save_session.prepared.items():
        assert (mounted_drive / name).read_bytes() == payload
    assert attributes["FIRST.FIL"] == 0x20
    assert attributes["LAST.FIL"] == flags
    clear = operations.index(("attributes", "LAST.FIL", flags & ~0x01))
    assert clear < operations.index(("publish", "FIRST.FIL"))
    _, manifest = _package(job)
    assert manifest["originals"]["LAST.FIL"]["windows_attributes"] == flags
    assert manifest["attributes_pending"] == {}
    assert manifest["status"] == "complete"


@pytest.mark.parametrize("failure", ["query", "clear", "cancel"])
def test_attribute_rollback_preserves_originals_before_publication(
    save_session, mounted_drive, protected_target, monkeypatch, failure,
):
    attributes, operations = protected_target
    attributes["FIRST.FIL"] = 0x07
    original_attributes = dict(attributes)
    get_attributes, set_attributes = floppy_image._windows_file_attributes, floppy_image._set_windows_file_attributes
    if failure == "query":
        def query(path):
            if Path(path).name == "LAST.FIL":
                raise _windows_error(5)
            return get_attributes(path)
        monkeypatch.setattr(floppy_image, "_windows_file_attributes", query)
    elif failure == "clear":
        def set_flags(path, value):
            if Path(path).name == "LAST.FIL" and not value & 0x01:
                raise _windows_error(5)
            set_attributes(path, value)
        monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", set_flags)
    def cancel():
        return failure == "cancel" and attributes["FIRST.FIL"] != original_attributes["FIRST.FIL"]
    with pytest.raises((OSError, floppy_image.FloppyOperationCancelled)):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", cancel_callback=cancel)
    assert attributes == original_attributes
    assert all((mounted_drive / name).read_bytes() == data for name, data in save_session.originals.items())
    assert not any(operation[0] == "publish" for operation in operations)
    assert not list(mounted_drive.glob("APS*.TMP"))
    _, manifest = _package(save_session)
    assert not manifest.get("attributes_pending")
    if failure != "query":
        assert manifest["diagnostics"]["attribute_restoration"]["errors"] == {}


def test_failed_attribute_restore_retries_for_the_verified_published_file(
    save_session, mounted_drive, protected_target, monkeypatch,
):
    attributes, _ = protected_target
    set_attributes = floppy_image._set_windows_file_attributes
    failed = False
    def fail_once(path, value):
        nonlocal failed
        if Path(path).name == "LAST.FIL" and value == 0x27 and not failed:
            failed = True
            raise _windows_error(5)
        set_attributes(path, value)
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", fail_once)
    with pytest.raises(OSError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert failed
    assert attributes == {"FIRST.FIL": 0x20, "LAST.FIL": 0x27}
    assert all((mounted_drive / name).read_bytes() == data for name, data in save_session.prepared.items())
    _, manifest = _package(save_session)
    assert manifest["status"] == "failed"
    assert manifest["attributes_pending"] == {}
    assert manifest["diagnostics"]["attribute_restoration"]["restored"] == ["LAST.FIL"]


@pytest.mark.parametrize("change", ["disk_swap", "file_changed", "restore_denied"])
def test_attribute_rollback_keeps_journal_when_restoration_is_unsafe_or_denied(
    save_session, mounted_drive, protected_target, monkeypatch, change,
):
    attributes, _ = protected_target
    set_attributes = floppy_image._set_windows_file_attributes
    def clear_then_fail(path, value):
        if value & 0x01:
            if change == "restore_denied":
                raise _windows_error(5)
            pytest.fail("Must not restore attributes on an unrecognized target")
        set_attributes(path, value)
        if change == "disk_swap":
            monkeypatch.setattr(floppy_image, "_windows_volume_identity", lambda _: {"serial": 999})
        elif change == "file_changed":
            Path(path).write_bytes(b"another file's contents")
        raise _windows_error(21)
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", clear_then_fail)
    with pytest.raises(OSError) as caught:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert caught.value.winerror == 21
    assert attributes["LAST.FIL"] == 0x26
    _, manifest = _package(save_session)
    assert manifest["attributes_pending"]["LAST.FIL"]["attributes"] == 0x27
    assert manifest["diagnostics"]["attributes_pending"] == ["LAST.FIL"]
    assert manifest["diagnostics"]["files_copied"] == 0


def test_protected_deletion_is_preflighted_before_song_publication(save_session, mounted_drive, protected_target):
    attributes, operations = protected_target
    del save_session.prepared["LAST.FIL"]
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert not (mounted_drive / "LAST.FIL").exists()
    assert operations.index(("attributes", "LAST.FIL", 0x26)) < operations.index(("publish", "FIRST.FIL"))
    _, manifest = _package(save_session)
    assert manifest["attributes_pending"] == {}


def test_direct_save_preserves_unrelated_protected_file(save_session, mounted_drive, protected_target, tmp_path):
    attributes, operations = protected_target
    attributes["FIRST.FIL"] = 0x07
    job = _prepared_save(tmp_path, {"LAST.FIL": b"last replacement"}, overwrite=["LAST.FIL"])
    job.write_to_floppy_target("floppy_usb", "A:")
    assert (mounted_drive / "FIRST.FIL").read_bytes() == b"first original"
    assert attributes["FIRST.FIL"] == 0x07
    assert all(operation[1] != "FIRST.FIL" for operation in operations)


def test_disk_swap_after_publication_does_not_apply_attributes_to_substituted_media(
    save_session, mounted_drive, protected_target, monkeypatch,
):
    _, operations = protected_target
    copy = floppy_image._copy_prepared_floppy_file
    def copy_then_swap(source, destination, **kwargs):
        copy(source, destination, **kwargs)
        monkeypatch.setattr(floppy_image, "_windows_volume_identity", lambda _: {"serial": 999})
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", copy_then_swap)
    with pytest.raises(floppy_image.FloppyImageError, match="changed during save"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert operations == [("attributes", "LAST.FIL", 0x26), ("publish", "FIRST.FIL")]
    _, manifest = _package(save_session)
    assert set(manifest["attributes_pending"]) == {"FIRST.FIL"}
    assert not (mounted_drive / "LAST.FIL").exists()


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("mode", ["same_name", "rename", "direct_same_name"])
def test_full_floppy_reuses_original_allocation_and_directory_slot_without_disk_backups(
    save_session, mounted_drive, tmp_path, monkeypatch, backup, mode,
):
    name = "NEW.MID" if mode == "rename" else "OLD.MID"
    payload = b"x" * 1024
    save_session.prepared = {name: payload}
    direct = mode == "direct_same_name"
    job = _prepared_save(tmp_path, save_session.prepared, ["OLD.MID"]) if direct else save_session
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info",
                        lambda *a, **kw: _mounted_root_info(mounted_drive, 2))
    copy = floppy_image._copy_prepared_floppy_file
    destinations = []

    def copy_to_final(source, destination, **kwargs):
        destinations.append(Path(destination).name)
        assert Path(source).parent == Path(job.last_floppy_save_diagnostics["recovery_directory"])
        assert not list(mounted_drive.iterdir()), "Original space must be reclaimed before copying"
        directory, manifest = _package(job)
        if backup:
            assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
        else:
            assert manifest["originals"] == {}
        copy(source, destination, **kwargs)
        assert sum(floppy_image.allocated_size(path.stat().st_size, 1024)
                   for path in mounted_drive.iterdir()) == 1024

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", copy_to_final)
    job._sync_modified_image_files_to_windows_drive(
        None if direct else "prepared.img", "A:",
        backup_originals=backup, check_originals=False, verify_copies=False,
    )
    assert destinations == [name]
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {name: payload}
    diagnostics = job.last_floppy_save_diagnostics
    assert diagnostics["reclaimed_bytes"] == diagnostics["available_bytes"] == 1024
    assert diagnostics["root_directory_check"]["available_entries"] == 1
    assert diagnostics["files_staged"] == diagnostics["bytes_staged"] == 0
    assert diagnostics["bytes_copied"] == len(payload)


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_final_allocation_exceeding_reclaimed_space_is_rejected_without_deletion(
    save_session, mounted_drive, tmp_path, monkeypatch, backup, direct,
):
    save_session.prepared = {"OLD.MID": b"x" * 1025}
    job = _prepared_save(tmp_path, save_session.prepared, ["OLD.MID"]) if direct else save_session
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", lambda *a, **kw: pytest.fail("Insufficient final space"))
    with pytest.raises(floppy_image.FloppyImageError, match="2048 bytes; only 1024"):
        job._sync_modified_image_files_to_windows_drive(
            None if direct else "prepared.img", "A:", backup_originals=backup,
            check_originals=False, verify_copies=False,
        )
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == save_session.originals
    assert job.last_write_verification["confidence"] == "not_written"
    directory, manifest = _package(job)
    assert bool(manifest["originals"]) is backup
    assert manifest["actions"] == []
    assert (directory / manifest["replacements"]["OLD.MID"]["file"]).read_bytes() == b"x" * 1025
