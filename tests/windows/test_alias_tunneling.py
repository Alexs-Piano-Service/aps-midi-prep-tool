"""Native FAT regression, opt in with APS_WINDOWS_FAT_TEST_ROOT=<existing dir>.

Run ``python -m pytest tests/windows/test_alias_tunneling.py`` on Windows with
a FAT12/16 volume mounted. Tests use a disposable child directory only; they
never format a volume or change its tunneling settings. The warm-cache cases
require enabled filename tunneling and demonstrate it before testing APS.
"""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _native_directory_info(root):
    return image.windows_fat_directory.query_root_directory(root, image._windows_ctypes())


@pytest.fixture
def native_fat_directory():
    configured = os.environ.get("APS_WINDOWS_FAT_TEST_ROOT")
    if not configured:
        pytest.skip("Set APS_WINDOWS_FAT_TEST_ROOT to an existing FAT12/16 directory")
    try:
        parent = Path(configured).expanduser().resolve(strict=True)
        if not parent.is_dir():
            pytest.fail("APS_WINDOWS_FAT_TEST_ROOT must name an existing directory")
        identity = image._windows_volume_identity(parent.anchor)
        if identity["filesystem"].upper() != "FAT":
            pytest.fail("APS_WINDOWS_FAT_TEST_ROOT must be on FAT12/16, not FAT32 or NTFS")
    except OSError as exc:
        pytest.fail(f"Cannot use APS_WINDOWS_FAT_TEST_ROOT: {exc}")

    # Hold the native setter for cleanup even if a test replaces the wrapper.
    set_attributes = image._set_windows_file_attributes
    with tempfile.TemporaryDirectory(prefix="aps_alias_", dir=parent) as temporary:
        root = Path(temporary)
        try:
            # Validate the configured filesystem's BPB and directory API too.
            _native_directory_info(root)
            yield root, parent.anchor, identity
        finally:
            for path in root.iterdir():
                set_attributes(path, 0x80)


@pytest.mark.parametrize("warm_cache", [False, True], ids=["fresh", "tunneled"])
@pytest.mark.parametrize("optional_checks", [False, True], ids=["checks-off", "checks-on"])
def test_alias_of_deleted_long_name_is_rejected_before_native_fat_mutation(
    native_fat_directory, tmp_path, monkeypatch, warm_cache, optional_checks,
):
    root, volume_root, identity = native_fat_directory
    original_name = "A long song title.mid"
    original_bytes = b"original recording"
    original = root / original_name
    original.write_bytes(original_bytes)

    def original_metadata():
        return next(entry for entry in _native_directory_info(root)["entries"]
                    if entry["name"] == original_name)

    alias = original_metadata()["short_name"]
    assert alias and image.dos_filename_key(alias) != image.dos_filename_key(original_name)
    assert (root / alias).read_bytes() == original_bytes

    if warm_cache:
        # Exercise the actual cache association, without changing machine-wide
        # settings. Recreating this fixture-owned short name must restore its
        # long name; otherwise this environment cannot run the warmed case.
        original.unlink()
        (root / alias).write_bytes(original_bytes)
        assert {path.name for path in root.iterdir()} == {original_name}, (
            "Native FAT filename tunneling did not restore the long name; "
            "the warm-cache regression requires enabled tunneling"
        )
        assert original_metadata()["short_name"] == alias

    image._set_windows_file_attributes(original, 0x27)
    before_attributes = image._windows_file_attributes(original)
    before_metadata = original_metadata()
    attribute_writes = []
    native_set_attributes = image._set_windows_file_attributes

    def record_attribute_write(path, attributes):
        attribute_writes.append((str(path), attributes))
        native_set_attributes(path, attributes)

    monkeypatch.setattr(image, "_set_windows_file_attributes", record_attribute_write)
    monkeypatch.setattr(image, "_windows_filesystem_root", lambda _: str(root))
    monkeypatch.setattr(image, "_windows_volume_identity", lambda _: identity)
    # GetDiskFreeSpaceW requires the volume root; all space values are native.
    native_disk_space = image._windows_legacy_disk_space
    monkeypatch.setattr(image, "_windows_legacy_disk_space", lambda _: native_disk_space(volume_root))
    # This directory fixture tests the filesystem, not physical media changes.
    monkeypatch.setattr(image, "_windows_media_change_guard", lambda _: SimpleNamespace(
        check=lambda: None, close=lambda: None,
    ))
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))

    prepared_path = tmp_path / "prepared.img"
    prepared_path.write_bytes(b"retained prepared image")
    baseline_path = str(tmp_path / "baseline.img")
    replacement_bytes = b"replacement recording"

    def listing(path, **kwargs):
        if path == str(prepared_path):
            contents = {alias: replacement_bytes}
        elif path == baseline_path:
            contents = {original_name: original_bytes}
        else:
            assert path == "A:"
            return image._read_windows_filesystem_drive_listing(path, **kwargs)
        return image.ImageListing(
            entries=[image.ImageEntry(path=name, size=len(data), packed_size=512, attributes="")
                     for name, data in contents.items()],
            free_space=0, cluster_size=512,
        )

    monkeypatch.setattr(image, "read_image_listing", listing)
    monkeypatch.setattr(image, "_read_fat12_file_bytes", lambda *_: original_bytes)
    session = object.__new__(image.FloppyImageSession)
    session.temp_dir = str(tmp_path)
    session.source_kind = "floppy_usb"
    session.source_path = "A:"
    session.working_img_path = baseline_path
    session._extract_from_image = lambda _, name, destination, **kwargs: Path(destination).write_bytes(replacement_bytes)

    with pytest.raises(image.FloppyImageError, match="short alias for another floppy file"):
        session._sync_modified_image_files_to_windows_drive(
            str(prepared_path), "A:", backup_originals=optional_checks,
            check_originals=optional_checks, verify_copies=optional_checks,
        )

    assert {path.name: path.read_bytes() for path in root.iterdir()} == {original_name: original_bytes}
    assert image._windows_file_attributes(original) == before_attributes
    assert original_metadata() == before_metadata
    assert not attribute_writes
    diagnostics = session.last_floppy_save_diagnostics
    assert not diagnostics["target_mutation_attempted"]
    assert not diagnostics["original_content_mutation_attempted"]
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
    assert session.last_write_verification["confidence"] == "not_written"
    recovery = Path(diagnostics["recovery_directory"])
    manifest = json.loads((recovery / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert not manifest["actions"]
    if optional_checks:
        assert (recovery / manifest["originals"][original_name]["file"]).read_bytes() == original_bytes
    else:
        assert not manifest["originals"]
    assert (recovery / manifest["replacements"][alias]["file"]).read_bytes() == replacement_bytes
    assert (recovery / "prepared.img").read_bytes() == prepared_path.read_bytes()
