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
    monkeypatch.setattr(floppy_image, "_windows_free_root_directory_entries", lambda *a, **kw: 112)
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
        if stage == "post_write_listing" and len(queries) <= 3:
            return SimpleNamespace(free=700000)
        raise _windows_error(50)

    monkeypatch.setattr(floppy_image.shutil, "disk_usage", space)
    def legacy(_):
        if stage == "post_write_listing" and len(queries) <= 3:
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
    assert diagnostics["stage"] == "stage_file"
    assert diagnostics["file"] == "SONG.FIL"
    assert diagnostics["error"]["winerror"] == 112
    assert diagnostics["target_mutation_attempted"] is True
    assert diagnostics["files_removed"] == 0
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


@pytest.mark.parametrize("failed_copy", [1, 3])
def test_failed_staging_retains_every_original_and_durable_replacement(save_session, mounted_drive, failed_copy, monkeypatch):
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
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert save_session.last_floppy_save_diagnostics["files_removed"] == 0
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
    assert len(manifest["diagnostics"]["staging_cleanup"]["removed"]) == failed_copy - 1


def test_insufficient_space_stops_before_mutation_and_retains_recovery(save_session, mounted_drive, monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=1000))
    with pytest.raises(floppy_image.FloppyImageError, match="staging space"):
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
    assert manifest["diagnostics"]["staging_cleanup"]["status"] == "skipped"
    assert manifest["staging_files_remaining"] == sorted(path.name for path in mounted_drive.glob("APS*.TMP"))
    assert manifest["staging_files_remaining"]
    assert (mounted_drive / "SONG.FIL").read_bytes() == b"new song"


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


def test_staged_save_roundtrip_with_real_images_and_native_staging(mounted_drive, tmp_path, monkeypatch):
    """Exercise extraction, owned temporary files, publication, and readback together."""
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
    with pytest.raises(floppy_image.FloppyImageError, match="staging space"):
        job.write_to_floppy_target("floppy_usb", "A:")
    assert list(mounted_drive.iterdir()) == []
    assert not job.last_floppy_save_diagnostics["target_mutation_attempted"]


def test_direct_save_rechecks_existing_bytes_before_publication(mounted_drive, tmp_path, monkeypatch):
    original = mounted_drive / "OLD.MID"
    original.write_bytes(b"before")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, path: str(Path(root) / path))
    real_copy = floppy_image._copy_prepared_floppy_file
    def change_target(*args, **kwargs):
        real_copy(*args, **kwargs)
        original.write_bytes(b"another process changed this")
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", change_target)
    job = _prepared_save(tmp_path, {"NEW.MID": b"new"})
    with pytest.raises(floppy_image.FloppyImageError, match="changed during save"):
        job.write_to_floppy_target("floppy_usb", "A:")
    assert original.read_bytes() == b"another process changed this"
    assert not (mounted_drive / "NEW.MID").exists()
    assert list(mounted_drive.glob("APS*.TMP"))
    assert job.last_floppy_save_diagnostics["staging_cleanup"]["status"] == "skipped"


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("failure", ["third_copy", "partial_copy", "cancel", "flush", "verify", "publish"])
def test_failed_save_cleans_owned_stages_and_allows_retry(save_session, mounted_drive, tmp_path, monkeypatch, direct, failure):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) * 40 for i in range(3)}
    session = _prepared_save(tmp_path, save_session.prepared) if direct else save_session
    def save(**kwargs):
        session._sync_modified_image_files_to_windows_drive(None if direct else "prepared.img", "A:", **kwargs)
    copy = floppy_image._copy_prepared_floppy_file
    staged = []
    def fail(source, destination, **kwargs):
        staged.append(destination)
        if len(staged) == 3 and failure == "third_copy":
            raise _windows_error(21)
        if len(staged) == 3 and failure == "partial_copy":
            with open(destination, "xb") as handle:
                kwargs["created_callback"]()
                handle.write(b"partial")
            raise _windows_error(112)
        copy(source, destination, **kwargs)
        if len(staged) == 3 and failure == "verify":
            Path(destination).write_bytes(b"corrupt staging copy")
    with monkeypatch.context() as fault:
        fault.setattr(floppy_image, "_copy_prepared_floppy_file", fail)
        if failure == "flush":
            real_sync = floppy_image.floppy_save_recovery.sync_file
            def sync(path):
                if Path(path).parent == mounted_drive:
                    raise _windows_error(21)
                real_sync(path)
            fault.setattr(floppy_image.floppy_save_recovery, "sync_file", sync)
        if failure == "publish":
            fault.setattr(floppy_image.os, "replace", _fail(5))
        with pytest.raises((OSError, floppy_image.FloppyImageError)):
            save(cancel_callback=lambda: failure == "cancel" and len(staged) == 3)
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert list(mounted_drive.iterdir()) == [mounted_drive / "OLD.MID"]
    directory, manifest = _package(session)
    assert manifest["status"] == ("cancelled" if failure == "cancel" else "failed")
    assert manifest["staging_files_remaining"] == []
    assert manifest["diagnostics"]["staging_cleanup"]["status"] == "complete"
    assert (directory / manifest["originals"]["OLD.MID"]["file"]).read_bytes() == b"original song"
    save()
    assert session.last_write_verification["confidence"] == "contents_verified"


def test_cancel_during_native_copy_removes_partial_stage(save_session, mounted_drive):
    save_session.prepared = {"SONG.MID": b"x" * 150000}
    def cancelled():
        return any(path.stat().st_size >= 65536 for path in mounted_drive.glob("APS*.TMP"))
    with pytest.raises(floppy_image.FloppyOperationCancelled):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", cancel_callback=cancelled)
    assert list(mounted_drive.iterdir()) == [mounted_drive / "OLD.MID"]
    _, manifest = _package(save_session)
    assert manifest["status"] == "cancelled"
    assert manifest["staging_files_remaining"] == []
    assert len(manifest["diagnostics"]["staging_cleanup"]["removed"]) == 1


@pytest.mark.parametrize("identity_failure", ["swap", "unreadable", "unavailable_from_start"])
def test_cleanup_skips_uncertain_disk_even_with_matching_originals(save_session, mounted_drive, monkeypatch, identity_failure):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) * 40 for i in range(3)}
    real_copy = floppy_image._copy_prepared_floppy_file
    stages = []
    def identity(_):
        if identity_failure == "unavailable_from_start" or (len(stages) == 3 and identity_failure == "unreadable"):
            raise _windows_error(21)
        return {"serial": 456 if len(stages) == 3 else 123}
    monkeypatch.setattr(floppy_image, "_windows_volume_identity", identity)
    def copy(source, destination, **kwargs):
        stages.append(Path(destination))
        if len(stages) == 3:
            raise _windows_error(21)
        real_copy(source, destination, **kwargs)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", copy)
    with pytest.raises(OSError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    _, manifest = _package(save_session)
    assert (mounted_drive / "OLD.MID").read_bytes() == b"original song"
    assert all(path.exists() for path in stages[:2])
    assert manifest["staging_files_remaining"] == sorted(path.name for path in stages[:2])
    assert manifest["diagnostics"]["staging_cleanup"]["status"] == "skipped"


def test_cleanup_does_not_claim_a_name_when_exclusive_creation_fails(save_session, mounted_drive, monkeypatch):
    copy = floppy_image._copy_prepared_floppy_file
    def collide(source, destination, **kwargs):
        Path(destination).write_bytes(b"another writer owns this")
        copy(source, destination, **kwargs)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", collide)
    with pytest.raises(FileExistsError):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    stages = list(mounted_drive.glob("APS*.TMP"))
    assert len(stages) == 1
    assert stages[0].read_bytes() == b"another writer owns this"
    assert save_session.last_floppy_save_diagnostics["staging_cleanup"]["status"] == "not_needed"


def test_cleanup_failure_keeps_original_error_and_reports_remaining_names(save_session, mounted_drive, monkeypatch):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) for i in range(3)}
    copy = floppy_image._copy_prepared_floppy_file
    paths = []
    def fail(source, destination, **kwargs):
        paths.append(Path(destination))
        if len(paths) == 3:
            raise _windows_error(112)
        copy(source, destination, **kwargs)
    remove = floppy_image.os.remove
    def remove_stage(path):
        if Path(path) == paths[0]:
            raise _windows_error(5)
        remove(path)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail)
    # Install after extraction, so the hook only faults disk cleanup.
    def progress(*args):
        monkeypatch.setattr(floppy_image.os, "remove", remove_stage)
    with pytest.raises(OSError) as caught:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:", progress_callback=progress)
    assert caught.value.winerror == 112
    _, manifest = _package(save_session)
    assert manifest["staging_files_remaining"] == [paths[0].name]
    assert paths[0].exists() and not paths[1].exists()
    assert manifest["diagnostics"]["staging_cleanup"]["status"] == "incomplete"


@pytest.mark.parametrize("free_entries", [0, 2, 3])
def test_staging_preflights_root_slots_separately_from_clusters(save_session, mounted_drive, monkeypatch, free_entries):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) for i in range(3)}
    monkeypatch.setattr(floppy_image, "_windows_free_root_directory_entries", lambda *a, **kw: free_entries)
    if free_entries < 3:
        with pytest.raises(floppy_image.FloppyImageError, match="root-directory entries"):
            save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
        assert list(mounted_drive.iterdir()) == [mounted_drive / "OLD.MID"]
        assert not save_session.last_floppy_save_diagnostics["target_mutation_attempted"]
    else:
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
        assert not list(mounted_drive.glob("APS*.TMP"))


def test_unavailable_root_slot_query_does_not_require_raw_access(save_session, monkeypatch):
    monkeypatch.setattr(floppy_image, "_windows_free_root_directory_entries", _fail(5))
    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert save_session.last_floppy_save_diagnostics["root_directory_check"]["status"] == "unavailable"


@pytest.mark.parametrize("has_end_marker", [False, True])
def test_root_slot_count_includes_labels_long_names_and_deleted_entries(tmp_path, monkeypatch, has_end_marker):
    path = tmp_path / "test.img"
    disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    floppy_image.create_blank_floppy_image(path, disk_format)
    data = bytearray(path.read_bytes())
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    for index in range(geometry.root_entries):
        data[geometry.root_offset + index * 32] = 65
    data[geometry.root_offset + 11] = 0x08  # volume label occupies one slot
    data[geometry.root_offset + 32 + 11] = 0x0F  # long-name slot is also occupied
    data[geometry.root_offset + 64] = 0xE5
    if has_end_marker:
        data[geometry.root_offset + 96] = 0
    class Volume:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read_at(self, offset, size, label):
            return data[offset:offset + size]
    monkeypatch.setattr(floppy_image, "_WindowsRecoveryVolumeHandle", lambda *a, **kw: Volume())
    expected = 1 + geometry.root_entries - 3 if has_end_marker else 1
    assert floppy_image._windows_free_root_directory_entries("A:") == expected


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
    real_replace = floppy_image.os.replace
    def replace(source, destination):
        name = Path(destination).name
        if Path(destination).parent == mounted_drive:
            operations.append(("publish", name))
            if Path(destination).exists() and get_attributes(destination) & 0x01:
                raise _windows_error(5)
            real_replace(source, destination)
            attributes[name] = attributes.pop(Path(source).name, 0x20)
        else:
            real_replace(source, destination)
    real_remove = floppy_image.os.remove
    def remove(path):
        if Path(path).parent == mounted_drive and get_attributes(path) & 0x01:
            raise _windows_error(5)
        real_remove(path)
        if Path(path).parent == mounted_drive:
            attributes.pop(Path(path).name, None)
    monkeypatch.setattr(floppy_image, "_windows_file_attributes", get_attributes)
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", set_attributes)
    monkeypatch.setattr(floppy_image.os, "replace", replace)
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


@pytest.mark.parametrize("failure", ["query", "clear", "cancel", "publish"])
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
    elif failure == "publish":
        monkeypatch.setattr(floppy_image.os, "replace", _fail(5))
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
    save_session, protected_target, monkeypatch,
):
    _, operations = protected_target
    replace = floppy_image.os.replace
    def replace_then_swap(source, destination):
        replace(source, destination)
        monkeypatch.setattr(floppy_image, "_windows_volume_identity", lambda _: {"serial": 999})
    monkeypatch.setattr(floppy_image.os, "replace", replace_then_swap)
    with pytest.raises(floppy_image.FloppyImageError, match="changed during save"):
        save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")
    assert operations == [("attributes", "LAST.FIL", 0x26), ("publish", "FIRST.FIL")]
    _, manifest = _package(save_session)
    assert set(manifest["attributes_pending"]) == {"FIRST.FIL", "LAST.FIL"}
