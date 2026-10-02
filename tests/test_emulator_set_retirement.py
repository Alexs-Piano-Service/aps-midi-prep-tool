"""Rebuilding a recorded emulator set retires only its unchanged owned files."""

import hashlib
import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import emulator_image_builder as builder
from aps_midi_prep_tool_app.floppy_image import FloppyImageError, FloppyOperationCancelled
from aps_midi_prep_tool_app.helpers import file_batch
from test_emulator_image_builder import _midi_bytes


@pytest.fixture
def collection(tmp_path, monkeypatch):
    source = tmp_path / "songs"
    source.mkdir()
    for index in range(3):
        album = source / f"Album {index}"
        album.mkdir()
        (album / "song.mid").write_bytes(_midi_bytes(f"Song {index}"))
    monkeypatch.setattr(builder, "PIANODIR_MAX_TRACKS", 1)
    return source, tmp_path / "output"


def _build(collection, **options):
    options.setdefault("output_ext", "img")
    options.setdefault("disk_layout", "folders")
    return builder.build_emulator_disk_images(*collection, output_content="midi", **options)


def _contents(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.mark.parametrize("change", ["fewer_disks", "no_song_list", "hfe_to_img"])
def test_rebuild_retires_previous_set_artifacts_with_explicit_confirmation(collection, change):
    source, output = collection
    before = _build(collection, output_ext="hfe" if change == "hfe_to_img" else "img",
                    include_song_lists=change == "no_song_list")
    assert before.images_created == 3
    previous = set(before.output_paths)
    if before.song_list_path:
        previous.add(before.song_list_path)
    if change == "fewer_disks":
        (source / "Album 2" / "song.mid").unlink()
    unrelated = output / "unrelated.txt"
    unrelated.write_bytes(b"keep me")
    requests = []
    after = _build(collection, overwrite_callback=lambda changes: requests.append(changes) or True)
    retired = previous - set(after.output_paths)
    assert set(requests[0].retired_paths) == retired
    assert set(after.retired_paths) == retired
    assert all(not Path(path).exists() for path in retired)
    assert unrelated.read_bytes() == b"keep me"
    manifest = json.loads(Path(after.manifest_path).read_text())
    assert {item["filename"] for item in manifest["artifacts"]} == {Path(path).name for path in after.output_paths}
    for item in manifest["artifacts"]:
        assert hashlib.sha256((output / item["filename"]).read_bytes()).hexdigest() == item["sha256"]


def test_declining_retirement_keeps_the_entire_previous_set(collection):
    source, output = collection
    _build(collection, include_song_lists=True)
    before = _contents(output)
    (source / "Album 2" / "song.mid").unlink()
    with pytest.raises(FloppyOperationCancelled):
        _build(collection, overwrite_callback=lambda changes: False)
    assert _contents(output) == before


def test_retirement_failure_restores_images_song_list_and_manifest(collection, monkeypatch):
    source, output = collection
    previous = _build(collection, output_ext="hfe", include_song_lists=True)
    before = _contents(output)
    real_unlink = file_batch.os.unlink
    deleted = []

    def fail_later_retirement(path, *args, **kwargs):
        if str(path) in previous.output_paths:
            deleted.append(str(path))
            if len(deleted) == 2:
                raise OSError("retirement failed")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(file_batch.os, "unlink", fail_later_retirement)
    with pytest.raises(OSError, match="retirement failed"):
        _build(collection, overwrite_existing=True)
    assert len(deleted) == 2
    assert _contents(output) == before


def test_modified_obsolete_file_is_not_retired_even_with_overwrite_approval(collection):
    source, output = collection
    previous = _build(collection)
    Path(previous.output_paths[-1]).write_bytes(b"user's edited image")
    before = _contents(output)
    (source / "Album 2" / "song.mid").unlink()
    with pytest.raises(FloppyImageError, match="Cannot retire changed"):
        _build(collection, overwrite_existing=True)
    assert _contents(output) == before


def test_unrecorded_old_files_are_never_assumed_to_belong_to_the_set(collection):
    source, output = collection
    output.mkdir()
    unrelated = output / "DSKA0009.hfe"
    unrelated.write_bytes(b"unrecorded image")
    _build(collection)
    (source / "Album 2" / "song.mid").unlink()
    _build(collection, overwrite_existing=True)
    assert unrelated.read_bytes() == b"unrecorded image"


@pytest.mark.parametrize("bad_name", ["../outside.img", "/absolute.img", "personal.txt", "OTHER0000.img"])
def test_manifest_cannot_authorize_unrelated_paths(collection, bad_name):
    source, output = collection
    previous = _build(collection)
    manifest = Path(previous.manifest_path)
    value = json.loads(manifest.read_text())
    value["artifacts"][-1]["filename"] = bad_name
    manifest.write_text(json.dumps(value))
    before = _contents(output)
    (source / "Album 2" / "song.mid").unlink()
    with pytest.raises(FloppyImageError, match="Cannot verify emulator set ownership"):
        _build(collection, overwrite_existing=True)
    assert _contents(output) == before


def test_overlapping_recorded_sets_are_not_overwritten(collection):
    source, output = collection
    _build(collection)
    before = _contents(output)
    with pytest.raises(FloppyImageError, match="overlaps another recorded"):
        _build(collection, starting_number=1, overwrite_existing=True)
    assert _contents(output) == before


def test_manifest_changed_during_confirmation_aborts_before_publication(collection):
    source, output = collection
    previous = _build(collection)
    manifest = Path(previous.manifest_path)
    original_images = {path: Path(path).read_bytes() for path in previous.output_paths}
    changed = manifest.read_bytes() + b"\n"
    (source / "Album 2" / "song.mid").unlink()

    def approve(_changes):
        manifest.write_bytes(changed)
        return True

    with pytest.raises(OSError, match="changed after approval"):
        _build(collection, overwrite_callback=approve)
    assert manifest.read_bytes() == changed
    assert {path: Path(path).read_bytes() for path in original_images} == original_images


def test_new_destination_appearing_during_confirmation_is_not_overwritten(collection):
    source, output = collection
    _build(collection, output_ext="hfe")
    before = _contents(output)
    competing = output / "DSKA0000.img"

    def approve(_changes):
        competing.write_bytes(b"another application's output")
        return True

    with pytest.raises(OSError, match="appeared after approval"):
        _build(collection, overwrite_callback=approve)
    assert _contents(output) == {**before, competing.name: b"another application's output"}


def test_obsolete_symlink_does_not_delete_its_target(collection, tmp_path):
    source, output = collection
    previous = _build(collection)
    obsolete = Path(previous.output_paths[-1])
    target = tmp_path / "personal.img"
    target.write_bytes(obsolete.read_bytes())
    obsolete.unlink()
    try:
        obsolete.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks unavailable")
    (source / "Album 2" / "song.mid").unlink()
    with pytest.raises(FloppyImageError, match="Cannot retire changed"):
        _build(collection, overwrite_existing=True)
    assert obsolete.is_symlink()
    assert target.is_file()
