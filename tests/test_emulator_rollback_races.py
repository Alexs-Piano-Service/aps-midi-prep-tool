"""Rollback must preserve competing files arriving at its mutation boundary."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import emulator_image_builder as builder
from aps_midi_prep_tool_app.floppy_image import FloppyImageError, FloppyOperationCancelled
from aps_midi_prep_tool_app.helpers import atomic_file, file_batch
from test_emulator_image_builder import _midi_bytes


@pytest.mark.parametrize("output_ext", ["img", "hfe"])
@pytest.mark.parametrize("existed", [False, True], ids=["remove-new", "restore-existing"])
def test_emulator_rollback_preserves_replacement_arriving_at_mutation(
    tmp_path, monkeypatch, output_ext, existed,
):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    output.mkdir()
    first, second = [output / f"RACE{number:04d}.{output_ext}" for number in (1, 2)]
    original = b"previous customer image"
    for number, destination in enumerate((first, second), start=1):
        album = source / f"Album {number}"
        album.mkdir()
        (album / "Song.mid").write_bytes(_midi_bytes(f"Song {number}"))
        if existed:
            destination.write_bytes(original)

    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir()
    real_mkdtemp = file_batch.tempfile.mkdtemp

    def make_recovery_directory(suffix=None, prefix=None, dir=None):
        return real_mkdtemp(suffix=suffix, prefix=prefix, dir=dir or recovery_root)

    monkeypatch.setattr(file_batch.tempfile, "mkdtemp", make_recovery_directory)
    replacement = tmp_path / "competing-image"
    competing_contents = b"another application's newer image"
    replacement.write_bytes(competing_contents)
    real_replace = os.replace
    real_write = file_batch.atomic_write_bytes
    state = {"rolling_back": False, "injected": False}
    published = {}

    def inject_at_mutation(operation):
        real_operation = getattr(os, operation)

        def mutate(*args, **kwargs):
            paths = [Path(path) for path in args[:2]]
            if state["rolling_back"] and not state["injected"] and first in paths:
                assert first.read_bytes() == published[first]
                real_replace(replacement, first)
                state["injected"] = True
            return real_operation(*args, **kwargs)

        return mutate

    # Keep the syscall interception local to the ownership helper; building,
    # conversion, and pytest cleanup retain their actual os functions.
    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(**(
        vars(os) | {name: inject_at_mutation(name) for name in ("unlink", "replace", "rename")}
    )))

    def fail_second_publication(destination, payload, **kwargs):
        if Path(destination) == second:
            state["rolling_back"] = True
            raise OSError("Second image publication failed")
        if Path(destination) == first and not state["rolling_back"]:
            published[first] = payload
        return real_write(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_second_publication)
    with pytest.raises((FloppyImageError, OSError)) as caught:
        builder.build_emulator_disk_images(
            source, output, prefix="RACE", starting_number=1,
            output_ext=output_ext, output_content="midi", disk_layout="folders",
            overwrite_existing=existed,
        )

    assert state["injected"]
    assert not replacement.exists()
    preserved = [
        path for path in tmp_path.rglob("*")
        if path.is_file() and path.read_bytes() == competing_contents
    ]
    assert preserved, "Rollback destroyed the competing image"
    if existed:
        assert second.read_bytes() == original
    else:
        assert not second.exists()

    error = caught.value
    assert isinstance(error, FloppyImageError)
    assert "Second image publication failed" in str(error)
    assert "could not be restored" in str(error)
    assert error.rollback_errors
    assert str(first) in "\n".join(error.rollback_errors)
    # A quarantine is acceptable only when the user is told where the
    # competing bytes were retained for manual recovery.
    assert any(str(path) in str(error) for path in preserved)
    recovery = Path(error.recovery_directory)
    assert recovery.is_dir()
    assert str(recovery) in str(error)
    manifest = json.loads((recovery / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rollback_errors"] == error.rollback_errors
    record = next(item for item in manifest["files"] if item["destination"] == str(first))
    assert record["published"] is True
    assert record["restored"] is False
    assert (recovery / record["prepared_file"]).read_bytes() == published[first]
    if existed:
        assert (recovery / record["original_file"]).read_bytes() == original
    else:
        assert record["original_file"] is None


@pytest.mark.parametrize("output_ext", ["img", "hfe"])
@pytest.mark.parametrize("existed", [False, True])
def test_cancelled_emulator_export_reports_retained_files_after_successful_rollback(
    tmp_path, monkeypatch, output_ext, existed,
):
    source = tmp_path / "songs"
    source.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Cancelled export"))
    output = tmp_path / "images"
    output.mkdir()
    destination = output / f"CANC0001.{output_ext}"
    original = b"previous customer image"
    if existed:
        destination.write_bytes(original)
    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir()
    monkeypatch.setattr(file_batch.tempfile, "tempdir", str(recovery_root))
    real_verify = builder.verify_image_payloads
    published = []

    def cancel_after_verification(delivered, prepared, disk_format, **kwargs):
        real_verify(delivered, prepared, disk_format, **kwargs)
        published.append(Path(delivered).read_bytes())
        raise FloppyOperationCancelled("Cancelled after first delivered image")

    monkeypatch.setattr(builder, "verify_image_payloads", cancel_after_verification)
    with pytest.raises(FloppyOperationCancelled, match="Cancelled after first delivered image") as caught:
        builder.build_emulator_disk_images(
            source, output, prefix="CANC", starting_number=1,
            output_ext=output_ext, output_content="midi", overwrite_existing=existed,
        )

    error = caught.value
    assert error.rollback_errors == []
    assert len(error.retained_paths) == 1
    retained = Path(error.retained_paths[0])
    assert retained.read_bytes() == published[0]
    assert str(retained) in str(error)
    recovery = Path(error.recovery_directory)
    assert recovery.parent == recovery_root
    assert str(recovery) in str(error)
    manifest = json.loads((recovery / "manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["files"] if item["destination"] == str(destination))
    assert record["restored"] is True
    assert str(retained) in record["retained_paths"]
    if existed:
        assert destination.read_bytes() == original
        assert (recovery / record["original_file"]).read_bytes() == original
    else:
        assert not destination.exists()
