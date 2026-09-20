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


def test_copy_failure_reports_partial_changes(save_session, mounted_drive):
    save_session._run_mtools = _fail(112)
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
def test_failed_staging_retains_every_original_and_durable_replacement(save_session, mounted_drive, failed_copy):
    save_session.prepared = {f"SONG{i}.MID": bytes([i]) * 40 for i in range(3)}
    copy = save_session._run_mtools
    calls = []

    def fail_copy(args, *a, **kw):
        calls.append(args)
        if len(calls) == failed_copy:
            raise _windows_error(21)
        return copy(args, *a, **kw)

    save_session._run_mtools = fail_copy
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


def test_staged_save_roundtrip_with_real_images_and_mcopy(mounted_drive, tmp_path, monkeypatch):
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
    session._sync_modified_image_files_to_windows_drive(str(prepared), "A:")
    assert [path.name for path in mounted_drive.iterdir()] == ["NEW.MID"]
    assert (mounted_drive / "NEW.MID").read_bytes() == replacement.read_bytes()
    assert session.last_floppy_save_diagnostics["file_contents_verified"] is True
