"""File-level saves must fit the physical FAT12 directory before any write."""

import os
from pathlib import Path
import shutil

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


def _root_directory(path):
    data = Path(path).read_bytes()
    geometry = image._geometry_from_boot_sector(data[:512])
    return geometry, data[geometry.root_offset:geometry.root_offset + geometry.root_size]


def _delete_files(path, *names):
    image._run_command(
        [image._require_command("mdel"), "-i", str(path),
         *(image.mtools_path(name) for name in names)],
        "Could not prepare the root-directory test fixture",
    )


def _add_file(path, host_dir, name, payload=b"additional recording"):
    host_path = host_dir / name
    host_path.write_bytes(payload)
    image._copy_host_file_into_image(path, host_path, name)


@pytest.fixture(scope="module")
def full_root_images(tmp_path_factory):
    directory = tmp_path_factory.mktemp("full_root_images")
    target = directory / "720.img"
    source = directory / "1440.img"
    files = directory / "files"
    files.mkdir()
    image.create_blank_floppy_image(target, image.DISK_FORMAT_BY_KEY["ibm.720"])
    image.create_blank_floppy_image(source, image.DISK_FORMAT_BY_KEY["ibm.1440"])
    names = ["CHANGE.MID", *(f"KEEP{index:03}.MID" for index in range(110))]
    for name in names:
        (files / name).write_bytes(b"old recording" if name == "CHANGE.MID" else b"unchanged recording")
    for disk in (target, source):
        image._run_command(
            [image._require_command("mcopy"), "-i", str(disk),
             *(str(files / name) for name in names), "::/"],
            "Could not populate the root-directory test fixture",
        )
        if disk == target:
            (files / "CHANGE.MID").write_bytes(b"new recording")
    geometry, root = _root_directory(target)
    assert geometry.root_entries == 112
    assert len(image.read_image_listing(target).entries) == 111
    assert all(root[index * 32] not in (0, 0xE5) for index in range(112))
    assert root[11] == 0x08  # The volume label reserves the remaining slot.
    return source, target


@pytest.fixture
def root_capacity_save(full_root_images, tmp_path, monkeypatch):
    source, target = (tmp_path / name for name in ("prepared.img", "floppy.img"))
    for original, destination in zip(full_root_images, (source, target)):
        shutil.copyfile(original, destination)
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    monkeypatch.setattr(image, "_is_block_device_path", lambda path: os.fspath(path) == str(target))
    session = image.FloppyImageSession.load(source)
    try:
        yield session, source, target
    finally:
        session.cleanup()


def _assert_capacity_rejected_before_writes(root_capacity_save, monkeypatch, *, backup_originals=True):
    session, source, target = root_capacity_save
    before = target.read_bytes()
    mutations = []
    run = session._run_mtools

    def record(args, *rest, **kwargs):
        command = os.path.basename(args[0])
        if (str(target) in args and
                (command == "mdel" or command == "mcopy" and args[-1].startswith("::/"))):
            mutations.append(args)
        return run(args, *rest, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    with pytest.raises(image.FloppyImageError, match="root-directory") as failure:
        session._sync_modified_image_files_to_floppy_drive(
            str(source), str(target), backup_originals=backup_originals,
        )

    assert "no files were changed" in str(failure.value).lower()
    assert mutations == []
    assert target.read_bytes() == before
    diagnostics = session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "failed"
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
    assert "restoration" not in diagnostics
    assert session.last_write_verification["confidence"] == "not_written"


@pytest.mark.parametrize("backup_originals", [False, True])
@pytest.mark.parametrize("additional_files", [1, 2], ids=["volume_label_reserves_slot", "113_source_files"])
def test_full_root_rejected_before_any_target_mutation(
    root_capacity_save, tmp_path, monkeypatch, backup_originals, additional_files,
):
    _session, source, target = root_capacity_save
    for index in range(additional_files):
        _add_file(source, tmp_path, f"NEW{index:03}.MID")
    assert len(image.read_image_listing(source).entries) == 111 + additional_files
    assert image.read_image_listing(target).free_space > 500_000
    _assert_capacity_rejected_before_writes(
        root_capacity_save, monkeypatch, backup_originals=backup_originals,
    )


def test_save_counts_free_deleted_and_reclaimed_root_slots(root_capacity_save, tmp_path):
    session, source, target = root_capacity_save
    # Leave one deleted slot and one end-of-directory slot on the target.
    _delete_files(target, "KEEP108.MID", "KEEP109.MID")
    geometry, root = _root_directory(target)
    assert root[-64] == root[-32] == 0xE5
    with target.open("r+b") as handle:
        handle.seek(geometry.root_offset + geometry.root_size - 32)
        handle.write(bytes(32))
    # The other two slots come from one deletion and the changed song.
    _delete_files(source, "KEEP107.MID", "KEEP108.MID", "KEEP109.MID")
    for index in range(3):
        _add_file(source, tmp_path, f"NEW{index:03}.MID")

    session._sync_modified_image_files_to_floppy_drive(str(source), str(target))

    assert image._read_fat12_file_bytes(target, "CHANGE.MID") == b"new recording"
    assert {entry.path for entry in image.read_image_listing(target).entries} == {
        entry.path for entry in image.read_image_listing(source).entries
    }
    _, root = _root_directory(target)
    assert all(root[index * 32] not in (0, 0xE5) for index in range(112))
    assert root[11] == 0x08
    diagnostics = session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "contents_verified"
    assert diagnostics["files_removed"] == 2
    assert diagnostics["files_copied"] == 4


@pytest.mark.parametrize("backup_originals", [False, True])
def test_apostrophe_filename_requires_long_name_slot_before_writes(
    root_capacity_save, tmp_path, monkeypatch, backup_originals,
):
    _session, source, target = root_capacity_save
    for path in (source, target):
        _delete_files(path, "KEEP109.MID")
    # Although this fits DOS 8.3 syntax, mcopy stores an apostrophe in an LFN
    # record. The free slot plus CHANGE.MID's reclaimed slot cannot fit both.
    _add_file(source, tmp_path, "A'B.MID")
    _assert_capacity_rejected_before_writes(
        root_capacity_save, monkeypatch, backup_originals=backup_originals,
    )


@pytest.mark.parametrize("fragmented", [False, True], ids=["too_few_slots", "nonadjacent_slots"])
def test_new_long_filename_requires_adjacent_directory_slots(
    root_capacity_save, tmp_path, monkeypatch, fragmented,
):
    _session, source, target = root_capacity_save
    removed = ("KEEP010.MID", "KEEP020.MID", "KEEP030.MID") if fragmented else ("KEEP109.MID",)
    for path in (source, target):
        _delete_files(path, *removed)
    # Two LFN records plus the short-name record must be adjacent. In the
    # fragmented case enough individual slots exist, but they are isolated.
    _add_file(source, tmp_path, "A long song title.mid")
    _assert_capacity_rejected_before_writes(root_capacity_save, monkeypatch)


@pytest.mark.parametrize("replace_long_name", [False, True], ids=["delete_long_name", "replace_long_name"])
def test_save_reclaims_long_filename_slots(root_capacity_save, tmp_path, replace_long_name):
    session, source, target = root_capacity_save
    removed = ("KEEP107.MID", "KEEP108.MID", "KEEP109.MID")
    _delete_files(target, *removed)
    name = "A long song title.mid"
    _add_file(target, tmp_path, name, b"old long-name recording")
    if replace_long_name:
        _delete_files(source, *removed)
        _add_file(source, tmp_path, name, b"new long-name recording")
    _, root = _root_directory(target)
    assert all(root[index * 32] not in (0, 0xE5) for index in range(112))

    session._sync_modified_image_files_to_floppy_drive(str(source), str(target))

    assert {entry.path for entry in image.read_image_listing(target).entries} == {
        entry.path for entry in image.read_image_listing(source).entries
    }
    assert image._read_fat12_file_bytes(target, "CHANGE.MID") == b"new recording"
    if replace_long_name:
        assert image._read_fat12_file_bytes(target, name) == b"new long-name recording"
    diagnostics = session.last_floppy_save_diagnostics
    assert diagnostics["status"] == "contents_verified"
    assert diagnostics["files_removed"] == 2
    assert diagnostics["files_copied"] == (2 if replace_long_name else 4)


