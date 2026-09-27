"""Boot/visibility repair preserves all bytes except intended metadata changes."""

from dataclasses import replace
import pytest

from aps_midi_prep_tool_app import boot_sector_repair, floppy_image
from aps_midi_prep_tool_app.boot_sector_repair import inspect_boot_sector_image, save_boot_sector_repair


def song_image(tmp_path, layout=None):
    layout = layout or floppy_image._PROTECTED_FAT12_LAYOUTS[0]
    source = tmp_path / "source.img"
    floppy_image._create_blank_fat12_image_from_layout(source, layout, "TEST")
    data = bytearray(source.read_bytes())
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    for index in range(geometry.num_fats):
        fat_start = geometry.fat_offset + index * geometry.fat_size
        data[fat_start + 3:fat_start + 5] = b"\xff\x0f"
    # Hidden/system flags are cleared; read-only/archive, a volume label,
    # arbitrary unused bytes, and FAT differences must otherwise survive.
    label = bytearray(32)
    label[:11] = b"TEST       "
    label[11] = 0x08
    data[geometry.root_offset:geometry.root_offset + 32] = label
    entry = bytearray(floppy_image._dos_directory_entry(b"SONG    FIL", 2, 4))
    entry[11] = 0x27
    data[geometry.root_offset + 32:geometry.root_offset + 64] = entry
    data[geometry.data_offset:geometry.data_offset + 4] = b"SONG"
    data[-512:] = bytes(range(256)) * 2
    return source, data, geometry


def visible_song_image(data, geometry):
    """Expected fixture change, independently of the production directory walk."""
    expected = bytearray(data)
    expected[geometry.root_offset + 32 + 11] &= ~0x06
    return bytes(expected)


def protect(data, geometry, kind):
    data = bytearray(data)
    if kind == "blank":
        data[:512] = bytes(512)
    elif kind == "f6":
        data[:512] = b"\xf6" * 512
    elif kind == "unsigned":
        data[510:512] = b"\x00\x00"
    elif kind == "omitted":
        data = data[512:]
    elif kind.startswith("relocated"):
        boot = data[:512]
        if kind == "relocated_unsigned":
            boot[28:] = bytes(512 - 28)
        fat2 = geometry.fat_offset + geometry.fat_size
        data[fat2:fat2 + 512] = boot
        data[:512] = bytes(512)
    elif kind == "different_fats":
        data[:512] = bytes(512)
        data[geometry.fat_offset + geometry.fat_size + 6] = 0xFF
    else:
        assert kind == "valid"
    return bytes(data)


@pytest.mark.parametrize("layout", floppy_image._PROTECTED_FAT12_LAYOUTS, ids=lambda layout: layout["label"])
@pytest.mark.parametrize("kind", ["valid", "blank", "f6", "unsigned", "omitted", "relocated", "relocated_unsigned", "different_fats"])
def test_repair_only_changes_boot_sector_and_hidden_system_flags(tmp_path, layout, kind):
    source, data, geometry = song_image(tmp_path, layout)
    original = protect(data, geometry, kind)
    source.write_bytes(original)
    output = tmp_path / "fixed.img"

    repair = inspect_boot_sector_image(source)
    save_boot_sector_repair(repair, output)

    assert source.read_bytes() == original
    assert repair.changed
    result = output.read_bytes()
    assert floppy_image._geometry_from_boot_sector(result[:512]) == geometry
    assert len(result) == geometry.total_size
    aligned_original = data[:512] + original if kind == "omitted" else original
    assert result[512:] == visible_song_image(aligned_original, geometry)[512:]
    assert result[geometry.root_offset + 32 + 11] == 0x21
    if kind in {"valid", "unsigned", "relocated"}:
        assert result[:512] == data[:512]
    assert not inspect_boot_sector_image(output).changed


@pytest.mark.parametrize("extension", [".img", ".IMA", ".bin", ".vfd"])
def test_raw_extensions_are_supported(tmp_path, extension):
    source, data, geometry = song_image(tmp_path)
    source = source.with_suffix(extension)
    source.write_bytes(protect(data, geometry, "blank"))
    output = tmp_path / ("fixed" + extension)
    save_boot_sector_repair(inspect_boot_sector_image(source), output)
    assert output.read_bytes()[512:] == visible_song_image(data, geometry)[512:]


@pytest.mark.parametrize("attributes", [0x23, 0x25, 0x27])
def test_valid_boot_image_clears_hidden_or_system_flags_without_changing_other_bytes(tmp_path, attributes):
    source, data, geometry = song_image(tmp_path)
    data[geometry.root_offset + 32 + 11] = attributes
    source.write_bytes(data)

    repair = inspect_boot_sector_image(source)

    assert repair.changed
    assert repair.repaired == visible_song_image(data, geometry)
    assert source.read_bytes() == data


def test_valid_visible_image_needs_no_output(tmp_path):
    source, data, geometry = song_image(tmp_path)
    visible = visible_song_image(data, geometry)
    source.write_bytes(visible)
    output = tmp_path / "fixed.img"

    repair = inspect_boot_sector_image(source)
    save_boot_sector_repair(repair, output)

    assert not repair.changed
    assert source.read_bytes() == visible
    assert not output.exists()


@pytest.mark.parametrize("damage", ["unknown", "truncated", "extra_bytes", "invalid_root", "oversized", "encoded"])
def test_unrecognized_images_are_rejected_without_recovery(tmp_path, damage):
    source, data, geometry = song_image(tmp_path)
    data = protect(data, geometry, "blank")
    if damage == "unknown":
        data = bytes(len(data))
    elif damage == "truncated":
        data = data[:-512]
    elif damage == "extra_bytes":
        data += b"trailer"
    elif damage == "invalid_root":
        data = bytearray(data)
        data[geometry.root_offset:geometry.root_offset + geometry.root_size] = b"\xff" * geometry.root_size
    elif damage == "oversized":
        data += bytes(3 * 1024 * 1024)
    elif damage == "encoded":
        source = source.with_suffix(".hfe")
    source.write_bytes(data)

    with pytest.raises(floppy_image.FloppyImageError):
        inspect_boot_sector_image(source)
    assert source.read_bytes() == data
    assert not list(tmp_path.glob(".aps_boot_*"))


@pytest.mark.parametrize("alias", ["same", "relative", "symlink", "hardlink"])
def test_source_cannot_be_overwritten_through_any_alias(tmp_path, monkeypatch, alias):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    repair = inspect_boot_sector_image(source)
    output = source
    if alias == "relative":
        monkeypatch.chdir(tmp_path)
        output = "source.img"
    elif alias in {"symlink", "hardlink"}:
        output = tmp_path / "alias.img"
        try:
            if alias == "symlink":
                output.symlink_to(source)
            else:
                output.hardlink_to(source)
        except OSError:
            pytest.skip(f"{alias} is unavailable on this platform")

    with pytest.raises(floppy_image.FloppyImageError, match="different output"):
        save_boot_sector_repair(repair, output)
    assert source.read_bytes() == original


def test_changed_source_is_rejected(tmp_path):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(protect(data, geometry, "blank"))
    repair = inspect_boot_sector_image(source)
    source.write_bytes(data)
    output = tmp_path / "fixed.img"

    with pytest.raises(floppy_image.FloppyImageError, match="source image changed"):
        save_boot_sector_repair(repair, output)
    assert source.read_bytes() == data
    assert not output.exists()


@pytest.mark.parametrize("failure", ["payload_changed", "other_attribute_changed", "visibility_not_cleared",
                                     "write_error", "readback_mismatch", "replace_error", "encoded_output"])
def test_failed_save_preserves_existing_destination_and_cleans_temporary_file(tmp_path, monkeypatch, failure):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    repair = inspect_boot_sector_image(source)
    output = tmp_path / ("fixed.hfe" if failure == "encoded_output" else "fixed.img")
    output.write_bytes(b"Existing output")
    if failure == "payload_changed":
        repair = replace(repair, repaired=repair.repaired[:-1] + b"!")
    elif failure in {"other_attribute_changed", "visibility_not_cleared"}:
        changed = bytearray(repair.repaired)
        changed[geometry.root_offset + 32 + 11] = 0x20 if failure == "other_attribute_changed" else 0x27
        repair = replace(repair, repaired=bytes(changed))
    elif failure == "readback_mismatch":
        real_fsync = boot_sector_repair.os.fsync

        def corrupt_temp(fd):
            real_fsync(fd)
            temporary = next(tmp_path.glob(".aps_boot_*"))
            with temporary.open("r+b") as handle:
                handle.seek(geometry.data_offset)
                handle.write(b"FAIL")

        monkeypatch.setattr(boot_sector_repair.os, "fsync", corrupt_temp)
    elif failure in {"write_error", "replace_error"}:
        def fail(*_args):
            raise OSError("Disk failure")

        monkeypatch.setattr(boot_sector_repair.os, "fsync" if failure == "write_error" else "replace", fail)

    with pytest.raises((floppy_image.FloppyImageError, OSError)):
        save_boot_sector_repair(repair, output)
    assert output.read_bytes() == b"Existing output"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".aps_boot_*"))


def test_successful_save_can_atomically_replace_a_separate_output(tmp_path):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(protect(data, geometry, "blank"))
    output = tmp_path / "fixed.img"
    output.write_bytes(b"Old output")

    save_boot_sector_repair(inspect_boot_sector_image(source), output)

    assert output.read_bytes()[512:] == visible_song_image(data, geometry)[512:]
    assert not list(tmp_path.glob(".aps_boot_*"))
