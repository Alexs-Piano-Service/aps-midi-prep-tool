"""Use actual Windows file attributes and replacement on disposable host files."""

import json

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.mark.parametrize("deny_clear", [False, True])
def test_file_save_preserves_windows_protection_and_rolls_back_preflight(tmp_path, monkeypatch, deny_clear):
    root = tmp_path / "mounted"
    root.mkdir()
    names = ("FIRST.FIL", "LAST.FIL")
    originals = {name: ("original " + name).encode() for name in names}
    replacements = {name: ("replacement " + name).encode() for name in names}
    # The first file is ordinary on success, protected in the rollback case.
    attributes = {"FIRST.FIL": 0x07 if deny_clear else 0x20, "LAST.FIL": 0x27}
    specs = []
    real_set_attributes = image._set_windows_file_attributes
    volume_identity = image._windows_volume_identity(str(tmp_path.anchor))
    monkeypatch.setattr(image, "_windows_filesystem_root", lambda _: str(root))
    monkeypatch.setattr(image, "_windows_volume_identity", lambda _: volume_identity)
    monkeypatch.setattr(image, "_windows_floppy_disk_space", lambda *_: (1000000, 4096))
    monkeypatch.setattr(image, "_windows_free_root_directory_entries", lambda *a, **kw: 112)
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    try:
        for name in names:
            (root / name).write_bytes(originals[name])
            real_set_attributes(root / name, attributes[name])
            source = tmp_path / name
            source.write_bytes(replacements[name])
            specs.append({"host_path": str(source), "image_path": name})
        listing = image._read_windows_filesystem_drive_listing("A:")
        assert {entry.path: int(entry.attributes, 16) for entry in listing.entries} == attributes
        job = image.PreparedWindowsFileSave(specs, overwrite_names=names)
        if deny_clear:
            def set_attributes(path, value):
                if str(path).endswith("LAST.FIL") and not value & 0x01:
                    raise PermissionError("Injected attribute access denial")
                real_set_attributes(path, value)
            monkeypatch.setattr(image, "_set_windows_file_attributes", set_attributes)
            with pytest.raises(PermissionError):
                job.write_to_floppy_target("floppy_usb", "A:")
            assert job.last_floppy_save_diagnostics["files_copied"] == 0
        else:
            job.write_to_floppy_target("floppy_usb", "A:")
        assert {path.name: path.read_bytes() for path in root.iterdir()} == (originals if deny_clear else replacements)
        assert {name: image._windows_file_attributes(root / name) for name in names} == attributes
        manifest_path = tmp_path / "recovery"
        manifest = json.loads(next(manifest_path.glob("save-*/manifest.json")).read_text(encoding="utf-8"))
        assert not manifest["attributes_pending"]
        assert {name: manifest["originals"][name]["windows_attributes"] for name in names} == attributes
    finally:
        for path in root.iterdir():
            real_set_attributes(path, 0x80)
