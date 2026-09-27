"""A damaged Yamaha directory needs more than boot-only repair."""

import pytest

from aps_midi_prep_tool_app import boot_sector_repair as repair, floppy_image
from aps_midi_prep_tool_app.eseq_pianodir import PIANODIR_HEADER, PIANODIR_TARGET_FILE_SIZE
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_boot_sector_repair import song_image
from test_dos_filename_encoding import _song


def damaged_directory_image(tmp_path):
    source, data, geometry = song_image(tmp_path)
    song = bytearray(_song("Directory preservation", 60))
    song[0x27:0x32] = b"SONG    FIL"
    assert len(song) <= geometry.cluster_size
    catalog = bytearray(PIANODIR_TARGET_FILE_SIZE)
    catalog[:len(PIANODIR_HEADER)] = PIANODIR_HEADER
    catalog[len(PIANODIR_HEADER):len(PIANODIR_HEADER) + 0x50] = song[0x27:0x77]
    fat = bytearray(geometry.fat_size)
    fat[:3] = b"\xf9\xff\xff"
    for cluster, following in [(2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 0xFFF), (8, 0xFFF)]:
        offset = cluster + cluster // 2
        if cluster & 1:
            fat[offset] = (fat[offset] & 0x0F) | ((following & 0x0F) << 4)
            fat[offset + 1] = following >> 4
        else:
            fat[offset] = following & 0xFF
            fat[offset + 1] = (fat[offset + 1] & 0xF0) | (following >> 8)
    for index in range(geometry.num_fats):
        start = geometry.fat_offset + index * geometry.fat_size
        data[start:start + geometry.fat_size] = fat
    root = geometry.root_offset
    data[root:root + geometry.root_size] = b"\xe5" * geometry.root_size
    data[root:root + 32] = floppy_image._dos_directory_entry(b"PIANODIRFIL", 2, len(catalog))
    data[root + 32:root + 64] = floppy_image._dos_directory_entry(b"SONG    FIL", 8, len(song))
    # Match the failure pattern: valid entries followed by deleted entries and
    # malformed bytes later in the root, with no zero end-of-directory marker.
    data[root + 87 * 32:root + 88 * 32] = b"\xf2" * 32
    data[geometry.data_offset:geometry.data_offset + len(catalog)] = catalog
    song_offset = geometry.data_offset + 6 * geometry.cluster_size
    data[song_offset:song_offset + len(song)] = song
    data[:512] = b"\xe5" * 512
    source.write_bytes(data)
    return source, bytes(data), geometry


@pytest.mark.parametrize("target", ["", "hfe"])
@pytest.mark.parametrize("backup", [False, True])
def test_directory_damage_is_identified_without_creating_copies(tmp_path, monkeypatch, target, backup):
    source, original, _ = damaged_directory_image(tmp_path)
    assert floppy_image._detect_protected_fat12_layout(original) is None
    assert floppy_image._reconstruct_yamaha_root_dir_from_pianodir(original) is not None
    def no_scratch_copy(*_args, **_kwargs):
        pytest.fail("Rejected raw images must be identified before creating a working folder")
    monkeypatch.setattr(repair.tempfile, "TemporaryDirectory", no_scratch_copy)
    result = repair.repair_boot_sector_batch(source, target_format=target, backup=backup)
    assert len(result.results) == 1
    item = result.results[0]
    assert item.error == repair._DAMAGED_DIRECTORY
    assert not item.repaired and not item.converted
    assert not item.output_path and not item.backup_path
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("missing_evidence", ["catalog", "song", "fat"])
def test_directory_diagnosis_requires_matching_catalog_and_fat_chains(tmp_path, missing_evidence):
    source, original, geometry = damaged_directory_image(tmp_path)
    data = bytearray(original)
    if missing_evidence == "catalog":
        data[geometry.data_offset:geometry.data_offset + len(PIANODIR_HEADER)] = bytes(len(PIANODIR_HEADER))
    elif missing_evidence == "song":
        start = geometry.data_offset + 6 * geometry.cluster_size
        data[start + 0x27:start + 0x32] = b"OTHER   FIL"
    else:
        data[geometry.fat_offset:geometry.fat_offset + geometry.fat_size] = bytes(geometry.fat_size)
    source.write_bytes(data)
    with pytest.raises(floppy_image.FloppyImageError, match="Could not identify"):
        repair.inspect_boot_sector_image(source)
    assert source.read_bytes() == data


def test_existing_recovery_can_rebuild_directory_and_preserve_song_data(tmp_path):
    source, original, geometry = damaged_directory_image(tmp_path)
    output = tmp_path / "recovered.img"
    result = floppy_image.prepare_yamaha_bytes(original, output)
    assert result.boot_sector_repaired
    assert "root directory" in result.note
    assert output.read_bytes()[geometry.data_offset:] == original[geometry.data_offset:]
    assert {entry.path for entry in floppy_image.read_image_listing(output).entries} == {"PIANODIR.FIL", "SONG.FIL"}
    assert source.read_bytes() == original


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
def test_directory_damage_guidance_is_translated(language):
    message = translate_text(repair._DAMAGED_DIRECTORY, language)
    assert message.strip()
    if language != "en":
        assert message != repair._DAMAGED_DIRECTORY
