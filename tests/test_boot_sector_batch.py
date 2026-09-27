"""In-place repair, optional backups, conservative conversion, and batch safety."""

import os
from pathlib import Path
import stat

import pytest

from aps_midi_prep_tool_app import boot_sector_repair as repair, floppy_image
from test_boot_sector_repair import protect, song_image, visible_song_image


@pytest.mark.parametrize("kind", ["valid", "blank", "unsigned", "omitted", "relocated", "different_fats"])
@pytest.mark.parametrize("backup", [False, True])
def test_in_place_repair_makes_files_visible_and_only_backs_up_when_checked(tmp_path, kind, backup):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, kind)
    source.write_bytes(original)
    source.chmod(0o640)
    original_mode = stat.S_IMODE(source.stat().st_mode)
    result = repair.apply_boot_sector_repair(source, backup=backup)
    assert result.repaired and not result.converted and not result.error
    assert result.source_path == result.output_path == str(source)
    aligned_original = data[:512] + original if kind == "omitted" else original
    assert source.read_bytes()[512:] == visible_song_image(aligned_original, geometry)[512:]
    assert floppy_image._geometry_from_boot_sector(source.read_bytes()[:512]) == geometry
    assert stat.S_IMODE(source.stat().st_mode) == original_mode
    assert bool(result.backup_path) == backup
    if backup:
        assert Path(result.backup_path).read_bytes() == original
    assert {path.name for path in tmp_path.iterdir()} == ({"source.img", "source.img.bak"} if backup else {"source.img"})


def test_valid_image_is_untouched_and_needs_no_backup(tmp_path):
    source, data, geometry = song_image(tmp_path)
    data = visible_song_image(data, geometry)
    source.write_bytes(data)
    metadata = source.stat()
    result = repair.apply_boot_sector_repair(source, backup=True)
    assert not result.repaired and not result.converted and not result.backup_path
    assert source.read_bytes() == data
    assert source.stat().st_ino == metadata.st_ino
    assert source.stat().st_mtime_ns == metadata.st_mtime_ns
    assert list(tmp_path.iterdir()) == [source]


def test_existing_backups_are_never_overwritten(tmp_path):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    existing = tmp_path / "source.img.bak"
    existing.write_bytes(b"Older backup")
    result = repair.apply_boot_sector_repair(source, backup=True)
    assert existing.read_bytes() == b"Older backup"
    assert result.backup_path == str(tmp_path / "source.img.bak.1")
    assert Path(result.backup_path).read_bytes() == original


@pytest.mark.parametrize("kind", ["blank", "valid", "visible"])
@pytest.mark.parametrize("extension", ["ima", "bin", "vfd"])
def test_raw_conversion_keeps_repaired_original_and_converted_sibling(tmp_path, kind, extension):
    source, data, geometry = song_image(tmp_path)
    original = visible_song_image(data, geometry) if kind == "visible" else protect(data, geometry, kind)
    source.write_bytes(original)
    result = repair.apply_boot_sector_repair(source, target_format=extension)
    assert result.converted and result.repaired == (kind != "visible")
    assert source.exists() and source.with_suffix("." + extension).read_bytes() == source.read_bytes()
    assert source.read_bytes()[512:] == visible_song_image(original, geometry)[512:]
    assert not result.backup_path


def test_visibility_repair_rechecks_original_before_creating_backup(tmp_path, monkeypatch):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(data)
    updated = visible_song_image(data, geometry)
    real_encode = repair._encode_verified

    def encode_then_change_source(*args, **kwargs):
        encoded = real_encode(*args, **kwargs)
        source.write_bytes(updated)
        return encoded

    monkeypatch.setattr(repair, "_encode_verified", encode_then_change_source)
    with pytest.raises(floppy_image.FloppyImageError, match="source image changed"):
        repair.apply_boot_sector_repair(source, backup=True)

    assert source.read_bytes() == updated
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("layout", floppy_image._PROTECTED_FAT12_LAYOUTS, ids=lambda layout: layout["label"])
@pytest.mark.parametrize("kind", ["blank", "valid"])
def test_real_hfe_round_trip_keeps_both_formats_and_clears_visibility_flags(tmp_path, layout, kind):
    if not floppy_image._find_gw():
        pytest.skip("Greaseweazle is required")
    source, data, geometry = song_image(tmp_path, layout)
    original = protect(data, geometry, kind)
    source.write_bytes(original)
    result = repair.apply_boot_sector_repair(source, target_format="hfe")
    hfe = source.with_suffix(".hfe")
    assert result.repaired and result.converted
    assert source.read_bytes()[512:] == visible_song_image(original, geometry)[512:]
    disk_format = floppy_image.DISK_FORMAT_BY_SIZE[len(data)]
    decoded = tmp_path / "decoded.img"
    floppy_image._gw_convert(str(hfe), str(decoded), disk_format.key)
    assert decoded.read_bytes() == source.read_bytes()
    header = hfe.read_bytes()[:32]
    assert header[floppy_image.HFE_TRACK_ENCODING_OFFSET] == floppy_image.HFE_ENCODING_ISOIBM_MFM
    assert header[floppy_image.HFE_FLOPPY_INTERFACE_OFFSET] == floppy_image.HFE_IBMPC_INTERFACE_BY_FORMAT[disk_format.key]

    # Both protected and visibility-only HFE repairs update the input in place.
    source.write_bytes(original)
    floppy_image._gw_convert(str(source), str(hfe), disk_format.key)
    original_hfe = hfe.read_bytes()
    source.unlink()
    result = repair.apply_boot_sector_repair(hfe, target_format="img", backup=True)
    assert result.repaired and result.converted
    assert Path(result.backup_path).read_bytes() == original_hfe
    assert source.read_bytes()[512:] == visible_song_image(original, geometry)[512:]
    floppy_image._gw_convert(str(hfe), str(decoded), disk_format.key)
    assert decoded.read_bytes() == source.read_bytes()
    assert len(decoded.read_bytes()) == geometry.total_size
    unchanged_hfe = hfe.read_bytes()
    assert not repair.apply_boot_sector_repair(hfe).repaired
    assert hfe.read_bytes() == unchanged_hfe


def test_existing_conversion_conflict_does_not_repair_source_or_overwrite_output(tmp_path):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    output = source.with_suffix(".ima")
    output.write_bytes(b"Existing output")
    with pytest.raises(floppy_image.FloppyImageError, match="already exists"):
        repair.apply_boot_sector_repair(source, target_format="ima", backup=True)
    assert source.read_bytes() == original
    assert output.read_bytes() == b"Existing output"
    assert not list(tmp_path.glob("*.bak*"))


@pytest.mark.parametrize("kind", ["blank", "valid"])
def test_hfe_verification_failure_leaves_source_untouched(tmp_path, monkeypatch, kind):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, kind)
    source.write_bytes(original)

    def corrupt_round_trip(input_path, output_path, _disk_format, **_kwargs):
        if Path(output_path).suffix == ".hfe":
            Path(output_path).write_bytes(b"Encoded HFE")
        else:
            # A successful decoder exit is insufficient if a sector changed.
            staged_raw = Path(input_path).parent / "repaired.img"
            payload = staged_raw.read_bytes()
            Path(output_path).write_bytes(payload[:-1] + b"!")

    monkeypatch.setattr(repair, "_gw_convert", corrupt_round_trip)
    with pytest.raises(floppy_image.FloppyImageError, match="verification failed"):
        repair.apply_boot_sector_repair(source, target_format="hfe", backup=True)
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("failure", ["encode", "verification", "cancel", "changed_source", "replace", "output_write"])
@pytest.mark.parametrize("kind", ["blank", "valid"])
def test_failure_before_commit_leaves_original_and_no_converted_file(tmp_path, monkeypatch, failure, kind):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, kind)
    source.write_bytes(original)
    real_encode = repair._encode_verified
    cancelled = []

    def encode(raw_path, raw_bytes, output_path, cancel_callback):
        encoded = real_encode(raw_path, raw_bytes, output_path, cancel_callback)
        if Path(output_path).name.startswith("converted"):
            if failure == "encode":
                raise floppy_image.FloppyImageError("Encoding failed")
            if failure == "cancel":
                cancelled.append(True)
            if failure == "changed_source":
                source.write_bytes(visible_song_image(data, geometry))
        return encoded

    monkeypatch.setattr(repair, "_encode_verified", encode)
    if failure == "verification":
        real_copy = repair.shutil.copyfile

        def corrupt_copy(input_path, output_path):
            real_copy(input_path, output_path)
            with open(output_path, "r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                handle.write(b"!")

        monkeypatch.setattr(repair.shutil, "copyfile", corrupt_copy)
    elif failure == "replace":
        def fail_replace(*_args):
            raise OSError("Replace failed")

        monkeypatch.setattr(repair.os, "replace", fail_replace)
    elif failure == "output_write":
        real_fsync = repair.os.fsync

        def fail_output(fd):
            if source.with_suffix(".ima").exists():
                raise OSError("Disk full")
            real_fsync(fd)

        monkeypatch.setattr(repair.os, "fsync", fail_output)
    with pytest.raises((floppy_image.FloppyImageError, OSError)):
        repair.apply_boot_sector_repair(source, target_format="ima", cancel_callback=lambda: bool(cancelled))
    assert source.read_bytes() == (visible_song_image(data, geometry) if failure == "changed_source" else original)
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("failure", ["partial_large_disk", "sparse_large_disk", "complete_invalid_disk"])
def test_hfe_detection_never_discards_sectors_by_falling_back_to_smaller_layout(tmp_path, monkeypatch, failure):
    source = tmp_path / "protected.hfe"
    source.write_bytes(b"An encoded disk")
    calls = []

    def convert(_input, output, disk_format, **_kwargs):
        calls.append(disk_format)
        if disk_format == "ibm.1440":
            raise floppy_image.GreaseweazleConversionError("Wrong density", sector_map={"found": 0})
        if disk_format == "ibm.800":
            if failure == "partial_large_disk":
                raise floppy_image.GreaseweazleConversionError("Missing sector", sector_map={"found": 1599})
            if failure == "sparse_large_disk":
                raise floppy_image.GreaseweazleConversionError("Missing sectors", sector_map={
                    "found": 1400, "rows": [
                        {"head": 0, "sector": 0, "statuses": "." * 80},
                        {"head": 0, "sector": 9, "statuses": "." + "x" * 79},
                    ],
                })
            Path(output).write_bytes(bytes(800 * 1024))
            return
        pytest.fail("A smaller layout would discard sectors")

    monkeypatch.setattr(repair, "_gw_convert", convert)
    with pytest.raises(floppy_image.FloppyImageError):
        repair.apply_boot_sector_repair(source)
    assert calls == ["ibm.1440", "ibm.800"]
    assert source.read_bytes() == b"An encoded disk"


@pytest.mark.parametrize("recursive", [False, True])
def test_directory_snapshot_skips_backups_and_outputs_and_continues_after_failure(tmp_path, recursive):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    (tmp_path / "a_bad.img").write_bytes(b"Invalid")
    (tmp_path / "source.img.bak").write_bytes(b"Existing backup")
    subdir = tmp_path / "nested"
    subdir.mkdir()
    nested = subdir / "second.IMG"
    nested.write_bytes(original)
    temporary = tmp_path / ".aps_boot_unfinished"
    temporary.mkdir()
    (temporary / "staged.img").write_bytes(original)
    progress, reported = [], []
    batch = repair.repair_boot_sector_batch(
        tmp_path, directory=True, recursive=recursive, target_format="ima", backup=True,
        progress_callback=lambda *values: progress.append(values), result_callback=reported.append,
    )
    assert not batch.cancelled
    assert len(batch.results) == (3 if recursive else 2)
    assert reported == list(batch.results)
    assert batch.results[0].error
    assert all(result.repaired and result.converted for result in batch.results[1:])
    assert progress[-1] == (len(batch.results), len(batch.results), "")
    assert source.read_bytes()[512:] == visible_song_image(original, geometry)[512:]
    assert source.with_suffix(".ima").read_bytes() == source.read_bytes()
    assert nested.with_suffix(".ima").exists() == recursive
    assert (tmp_path / "source.img.bak").read_bytes() == b"Existing backup"
    assert (tmp_path / "source.img.bak.1").read_bytes() == original
    assert (temporary / "staged.img").read_bytes() == original


def test_batch_cancellation_keeps_completed_repairs_and_leaves_later_images_untouched(tmp_path):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    later = tmp_path / "z.img"
    later.write_bytes(original)
    completed = []
    batch = repair.repair_boot_sector_batch(tmp_path, directory=True, result_callback=completed.append,
                                           cancel_callback=lambda: bool(completed))
    assert batch.cancelled and len(batch.results) == 1
    assert batch.results[0].repaired
    assert source.read_bytes() != original
    assert later.read_bytes() == original


def test_symlink_images_are_not_modified_or_followed_by_batch(tmp_path):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    directory = tmp_path / "links"
    directory.mkdir()
    alias = directory / "alias.img"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(floppy_image.FloppyImageError, match="regular"):
        repair.apply_boot_sector_repair(alias)
    assert not repair.repair_boot_sector_batch(directory, directory=True, recursive=True).results
    assert source.read_bytes() == original
