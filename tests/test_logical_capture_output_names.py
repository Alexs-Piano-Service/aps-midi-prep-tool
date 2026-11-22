"""Readable recovery-report names still preserve other files on collision."""

import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import disk_session_worker, floppy_image as image
from aps_midi_prep_tool_app.helpers import atomic_file
from test_logical_floppy_capture import _windows_disk


def test_logical_capture_can_omit_random_report_suffix(tmp_path, monkeypatch):
    drive, source, songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch)
    original = source.read_bytes()
    output = tmp_path / "logical.img"

    result = image.capture_logical_floppy_image(drive, output, random_suffixes=False)

    assert Path(result["diagnostics_path"]) == tmp_path / "logical.img.recovery.json"
    report = json.loads(Path(result["diagnostics_path"]).read_text())
    assert report["output_path"] == str(output)
    for name, payload in songs.items():
        assert image._read_fat12_file_bytes(output, name) == payload
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".aps_capture_*"))


@pytest.mark.parametrize("collision_kind", ["file", "symlink", "dangling_symlink", "directory"])
def test_logical_report_collision_uses_number_without_replacing_existing_path(
    tmp_path, monkeypatch, collision_kind,
):
    drive, source, _songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch)
    original = source.read_bytes()
    collision = tmp_path / "logical.img.recovery.json"
    if collision_kind == "file":
        collision.write_bytes(b"existing report")
    elif collision_kind == "directory":
        collision.mkdir()
    else:
        try:
            collision.symlink_to(source if collision_kind == "symlink" else tmp_path / "missing")
        except OSError as exc:
            pytest.skip(f"Symbolic links are unavailable: {exc}")

    result = image.capture_logical_floppy_image(drive, tmp_path / "logical.img", random_suffixes=False)

    assert Path(result["diagnostics_path"]) == tmp_path / "logical.img.recovery (2).json"
    assert json.loads(Path(result["diagnostics_path"]).read_text())["capture_kind"] == "logical_recovery"
    if collision_kind == "file":
        assert collision.read_bytes() == b"existing report"
    elif collision_kind == "directory":
        assert collision.is_dir()
    else:
        assert collision.is_symlink()
    assert source.read_bytes() == original


def test_failed_image_publication_removes_only_its_numbered_report(tmp_path, monkeypatch):
    drive, _source, _songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch)
    output = tmp_path / "logical.img"
    output.write_bytes(b"existing image")
    report = tmp_path / "logical.img.recovery.json"
    report.write_bytes(b"existing report")

    def fail_publication(*_args):
        raise OSError("Destination disconnected")

    monkeypatch.setattr(image, "_finish_capture_output", fail_publication)
    with pytest.raises(OSError, match="disconnected"):
        image.capture_logical_floppy_image(drive, output, random_suffixes=False)

    assert output.read_bytes() == b"existing image"
    assert report.read_bytes() == b"existing report"
    assert not (tmp_path / "logical.img.recovery (2).json").exists()
    assert not list(tmp_path.glob(".aps_capture_*"))


@pytest.mark.parametrize("error_type", [PermissionError, FileExistsError])
def test_report_publication_does_not_retry_noncollision_errors(tmp_path, monkeypatch, error_type):
    drive, _source, _songs, _geometry, _devices = _windows_disk(tmp_path, monkeypatch)
    output = tmp_path / "logical.img"
    output.write_bytes(b"existing image")
    attempts = []

    def fail_report(_stage, destination, **_kwargs):
        attempts.append(destination)
        raise error_type("Unrelated publication failure")

    monkeypatch.setattr(atomic_file, "publish_new_file", fail_report)
    with pytest.raises(error_type, match="Unrelated publication failure"):
        image.capture_logical_floppy_image(drive, output, random_suffixes=False)

    assert len(attempts) == 1
    assert output.read_bytes() == b"existing image"
    assert not list(tmp_path.glob("*.recovery*.json"))
    assert not list(tmp_path.glob(".aps_capture_*"))


@pytest.mark.parametrize("random_suffixes", [False, True])
def test_capture_worker_passes_output_naming_preference(tmp_path, monkeypatch, random_suffixes):
    options = []

    def capture(_source, output, **kwargs):
        options.append(kwargs["random_suffixes"])
        return {"output_path": str(output), "capture_kind": "logical_recovery"}

    monkeypatch.setattr(disk_session_worker, "capture_logical_floppy_image", capture)
    worker = disk_session_worker.DiskImageCaptureWorker(
        "floppy_usb_recovery", image.FloppyDriveInfo("A:", 737280), tmp_path / "logical.img",
        random_suffixes=random_suffixes,
    )
    results, failures = [], []
    worker.captureFinished.connect(results.append)
    worker.captureFailed.connect(failures.append)
    worker.run()

    assert options == [random_suffixes]
    assert len(results) == 1
    assert failures == []
