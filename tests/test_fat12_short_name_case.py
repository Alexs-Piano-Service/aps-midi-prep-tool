"""FAT short-name case bits affect display without changing DOS identity."""

import pytest

from aps_midi_prep_tool_app import floppy_image as image


CASE_VARIANTS = [(0, "SONG.MID"), (0x08, "song.MID"),
                 (0x10, "SONG.mid"), (0x18, "song.mid")]


def _short_entry(raw=b"SONG    MID", *, flags=0, attributes=0x20):
    entry = bytearray(image._dos_directory_entry(raw, 0, 0, attributes))
    entry[12] = flags
    return bytes(entry)


@pytest.mark.parametrize("flags,expected", CASE_VARIANTS)
@pytest.mark.parametrize("attributes", [0x20, 0x10], ids=["file", "directory"])
def test_short_name_case_flags_preserve_display_and_alias(flags, expected, attributes):
    directory = _short_entry(flags=flags, attributes=attributes)

    entry, = image._iter_fat_directory_entries(directory)

    assert entry["name"] == expected
    assert entry["short_name"] == "SONG.MID"
    assert entry["short_name_bytes"] == b"SONG    MID"
    for name in (expected, "SONG.MID"):
        assert image._find_fat12_entry(directory, name, original_path=name) == entry


@pytest.mark.parametrize("raw,flags,expected", [
    (b"SONG       ", 0x18, "song"),
    (b"1234    567", 0x18, "1234.567"),
    (b"SONG    MID", 0xE7, "SONG.MID"),
    (b"CAF\x90    FIL", 0x18, "cafÉ.fil"),
    (b"\x05FILE   FIL", 0x10, "ÕFILE.fil"),
    (b"\x05FILE   FIL", 0x18, "Õfile.fil"),
])
def test_short_name_flags_handle_missing_extension_oem_and_reserved_bits(raw, flags, expected):
    entry, = image._iter_fat_directory_entries(_short_entry(raw, flags=flags))

    assert entry["name"] == expected
    assert entry["short_name"] == image._decode_dos_directory_name(raw)
    assert entry["short_name_bytes"] == raw


def test_volume_label_ignores_filename_case_flags():
    entry, = image._iter_fat_directory_entries(_short_entry(flags=0x18, attributes=0x08))
    assert entry["name"] == "SONG.MID"


@pytest.mark.parametrize("valid_checksum", [False, True])
@pytest.mark.parametrize("flags,short_display", CASE_VARIANTS)
def test_lfn_takes_precedence_and_checksum_uses_raw_alias(flags, short_display, valid_checksum):
    raw = b"SONG    MID"
    lfn = bytearray(32)
    lfn[0], lfn[11] = 0x41, 0x0F
    lfn[13] = image._fat_lfn_checksum(raw) ^ (0 if valid_checksum else 1)
    encoded = ("Song.Mid".encode("utf-16-le") + b"\x00\x00").ljust(26, b"\xff")
    lfn[1:11], lfn[14:26], lfn[28:32] = encoded[:10], encoded[10:22], encoded[22:]

    entry, = image._iter_fat_directory_entries(bytes(lfn) + _short_entry(raw, flags=flags))

    assert entry["name"] == ("Song.Mid" if valid_checksum else short_display)
    assert entry["short_name"] == "SONG.MID"
    assert entry["short_name_bytes"] == raw


@pytest.mark.parametrize("fail_command", [False, True])
def test_lowercase_oem_short_name_still_uses_reversible_ascii_staging(tmp_path, fail_command):
    path = tmp_path / "oem.img"
    image._create_blank_fat12_image_from_layout(path, image._PROTECTED_FAT12_LAYOUTS[0], "TEST")
    data = bytearray(path.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    raw = b"CAF\x90    FIL"
    data[geometry.root_offset:geometry.root_offset + 32] = _short_entry(raw, flags=0x18)
    path.write_bytes(data)

    def stage_command():
        with image._ascii_mtools_image_paths(path, ["cafÉ.fil"]) as paths:
            assert paths[0].isascii()
            directory, _ = image._fat12_directory_slots(path, "")
            entry, = (entry for entry in image._iter_fat_directory_entries(directory)
                      if not entry["attr"] & 0x08)
            assert entry["name"] == paths[0]
            assert directory[entry["offset"] + 12] == 0
            if fail_command:
                raise image.FloppyImageError("Injected command failure")

    if fail_command:
        with pytest.raises(image.FloppyImageError, match="Injected command failure"):
            stage_command()
    else:
        stage_command()

    assert path.read_bytes() == data
