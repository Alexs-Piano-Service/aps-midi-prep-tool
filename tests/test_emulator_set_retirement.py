"""Rebuilds retire recorded output and warn about preserved legacy disks."""

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
    result = _build(collection, overwrite_existing=True)
    assert unrelated.read_bytes() == b"unrecorded image"
    assert str(unrelated) not in result.retired_paths
    assert result.untracked_paths == (str(unrelated),)
    assert any(unrelated.name in warning for warning in result.warnings)
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert unrelated.name not in {item["filename"] for item in manifest["artifacts"]}


@pytest.mark.parametrize("extension", ["img", "hfe"])
def test_legacy_warning_finds_preserved_stale_disks_after_a_numbering_gap(collection, extension):
    source, output = collection
    previous = _build(collection, output_ext=extension)
    Path(previous.manifest_path).unlink()
    Path(previous.output_paths[1]).unlink()
    stale = Path(previous.output_paths[2])
    stale_bytes = stale.read_bytes()
    for index in (1, 2):
        (source / f"Album {index}" / "song.mid").unlink()
    previews = []

    def review(preview):
        previews.append(preview)
        return {"action": "build"}

    result = _build(collection, output_ext=extension, overwrite_existing=True,
                    review_callback=review)

    assert result.images_created == 1
    assert result.untracked_paths == (str(stale),)
    assert result.retired_paths == ()
    assert stale.read_bytes() == stale_bytes
    assert any(stale.name in warning for warning in result.warnings)
    assert any(stale.name in warning for warning in previews[0].warnings)
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert stale.name not in {item["filename"] for item in manifest["artifacts"]}


def test_legacy_warning_ignores_disks_below_the_starting_number(collection):
    _source, output = collection
    previous = _build(collection)
    Path(previous.manifest_path).unlink()
    assert builder._untracked_legacy_images(output, "DSKA", 2, ()) == (
        previous.output_paths[2],
    )


@pytest.mark.parametrize("include_song_lists", [False, True])
def test_rebuilding_pre_manifest_set_warns_about_preserved_stale_disks(collection, include_song_lists):
    source, output = collection
    previous = _build(collection, include_song_lists=include_song_lists)
    Path(previous.manifest_path).unlink()  # Output from an older APS version.
    stale = Path(previous.output_paths[-1])
    stale_bytes = stale.read_bytes()
    (source / "Album 2" / "song.mid").unlink()
    previews = []

    def review(preview):
        previews.append(preview)
        return {"action": "build"}

    result = _build(collection, overwrite_existing=True, review_callback=review,
                    include_song_lists=include_song_lists)
    assert result.images_created == 2
    assert result.retired_paths == ()
    assert result.untracked_paths == (str(stale),)
    assert stale.read_bytes() == stale_bytes
    assert any("DSKA0002.img" in warning and "untracked" in warning.lower()
               for warning in result.warnings)
    assert any("DSKA0002.img" in warning for warning in previews[0].warnings)
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert stale.name not in {item["filename"] for item in manifest["artifacts"]}
    if include_song_lists:
        assert "DSKA0002.img" in Path(result.song_list_path).read_text()

    # The new manifest must not hide the leftover disk on subsequent builds.
    rebuilt = _build(collection, overwrite_existing=True)
    assert any("DSKA0002.img" in warning for warning in rebuilt.warnings)
    assert stale.read_bytes() == stale_bytes


def test_legacy_warning_excludes_disks_owned_by_a_neighboring_recorded_set(collection):
    source, output = collection
    legacy = _build(collection)
    Path(legacy.manifest_path).unlink()
    neighbor = _build(collection, starting_number=3)
    neighbor_bytes = {path: Path(path).read_bytes() for path in neighbor.output_paths}
    (source / "Album 2" / "song.mid").unlink()
    rebuilt = _build(collection, overwrite_existing=True)
    assert rebuilt.untracked_paths == (legacy.output_paths[-1],)
    assert len(rebuilt.warnings) == 1
    assert {path: Path(path).read_bytes() for path in neighbor.output_paths} == neighbor_bytes


@pytest.mark.parametrize("legacy_filename_case", ["generated", "upper_extension", "lower_prefix"])
def test_legacy_format_change_warns_about_all_untracked_images_without_removing_them(collection, legacy_filename_case):
    source, output = collection
    previous = _build(collection, output_ext="hfe")
    Path(previous.manifest_path).unlink()
    legacy_paths = []
    for path in previous.output_paths:
        image = Path(path)
        if legacy_filename_case == "upper_extension":
            image = image.rename(image.with_suffix(".HFE"))
        elif legacy_filename_case == "lower_prefix":
            image = image.rename(image.with_name(image.name.lower()))
        legacy_paths.append(str(image))
    legacy_paths = tuple(legacy_paths)
    legacy_bytes = {path: Path(path).read_bytes() for path in legacy_paths}
    (source / "Album 2" / "song.mid").unlink()
    rebuilt = _build(collection)
    assert rebuilt.untracked_paths == legacy_paths
    assert rebuilt.retired_paths == ()
    assert all(Path(path).name in warning
               for path, warning in zip(legacy_paths, rebuilt.warnings))
    assert {path: Path(path).read_bytes() for path in legacy_paths} == legacy_bytes


def test_legacy_warning_scan_does_not_trust_or_fail_on_corrupt_foreign_manifest(collection):
    _source, output = collection
    previous = _build(collection)
    Path(previous.manifest_path).unlink()
    foreign = output / ".aps-emulator-DSKA-0009.json"
    foreign.write_bytes(b"damaged ownership record")
    before = _contents(output)
    assert builder._untracked_legacy_images(output, "DSKA", 0, previous.output_paths[:2]) == (
        previous.output_paths[-1],
    )
    assert _contents(output) == before


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
