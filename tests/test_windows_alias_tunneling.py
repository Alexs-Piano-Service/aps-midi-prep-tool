"""Reject different LFN/SFN identities before Windows can tunnel a name."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from test_windows_floppy_save import _package, mounted_drive, save_session


LONG_NAME = "A long song title.mid"
SHORT_NAME = "ALONGS~1.MID"


def _configure_target(session, root, monkeypatch, *, name=LONG_NAME, alias=SHORT_NAME, slots=3):
    (root / "OLD.MID").rename(root / name)
    session.originals = {name: b"original song"}
    metadata = {
        "root_entries": slots + 1,
        "entries": [{"name": name, "short_name": alias, "attributes": 0x27, "slots": slots}],
    }
    monkeypatch.setattr(floppy_image, "_windows_root_directory_info", lambda *a, **kw: metadata)
    return metadata


@pytest.mark.parametrize("replacement_name", [SHORT_NAME, SHORT_NAME.lower()])
@pytest.mark.parametrize("cache_populated", [False, True])
@pytest.mark.parametrize("backup_originals", [False, True])
@pytest.mark.parametrize("check_originals", [False, True])
@pytest.mark.parametrize("verify_copies", [False, True])
def test_deleted_owner_short_alias_is_rejected_before_any_mutation(
    save_session, mounted_drive, monkeypatch, replacement_name, cache_populated,
    backup_originals, check_originals, verify_copies,
):
    _configure_target(save_session, mounted_drive, monkeypatch)
    save_session.prepared = {replacement_name: b"replacement song"}
    attributes = {LONG_NAME: 0x27}
    mutations = []
    # Model the name association remembered by an earlier delete/recreate,
    # independently of what the current directory enumeration reports.
    tunnel_cache = {SHORT_NAME: LONG_NAME} if cache_populated else {}
    remove = floppy_image.os.remove

    def set_attributes(path, flags):
        mutations.append(("attributes", Path(path).name, flags))
        attributes[Path(path).name] = flags

    def delete(path, *args, **kwargs):
        if Path(path).parent == mounted_drive:
            mutations.append(("delete", Path(path).name))
            tunnel_cache[SHORT_NAME] = LONG_NAME
        return remove(path, *args, **kwargs)

    def recreate(_source, destination, **kwargs):
        requested = Path(destination).name
        mutations.append(("create", requested))
        restored = tunnel_cache.get(floppy_image.dos_filename_key(requested), requested)
        pytest.fail(f"Creating {requested} would restore the tunneled filename {restored}")

    monkeypatch.setattr(floppy_image, "_windows_file_attributes", lambda path: attributes[Path(path).name])
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", set_attributes)
    monkeypatch.setattr(floppy_image.os, "remove", delete)
    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", recreate)

    with pytest.raises(floppy_image.FloppyImageError, match="short alias"):
        save_session._sync_modified_image_files_to_windows_drive(
            "prepared.img", "A:", backup_originals=backup_originals,
            check_originals=check_originals, verify_copies=verify_copies,
        )

    assert mutations == []
    assert attributes == {LONG_NAME: 0x27}
    assert tunnel_cache == ({SHORT_NAME: LONG_NAME} if cache_populated else {})
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == save_session.originals
    diagnostics = save_session.last_floppy_save_diagnostics
    assert diagnostics["stage"] == "check_final_directory_space"
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["original_content_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
    assert save_session.last_write_verification["confidence"] == "not_written"
    directory, manifest = _package(save_session)
    assert manifest["status"] == "failed"
    assert manifest["actions"] == []
    if backup_originals:
        record = manifest["originals"][LONG_NAME]
        assert record["windows_attributes"] == 0x27
        assert (directory / record["file"]).read_bytes() == b"original song"
    else:
        assert manifest["originals"] == {}


@pytest.mark.parametrize("name,alias,slots,replacement_name", [
    (LONG_NAME, SHORT_NAME, 3, LONG_NAME),
    ("OLD.MID", "OLD.MID", 2, "OLD.MID"),
    ("Old.mid", "OLD.MID", 2, "old.mid"),
])
def test_replacing_same_visible_name_preserves_valid_long_name_association(
    save_session, mounted_drive, monkeypatch, name, alias, slots, replacement_name,
):
    _configure_target(save_session, mounted_drive, monkeypatch, name=name, alias=alias, slots=slots)
    save_session.prepared = {replacement_name: b"replacement song"}

    save_session._sync_modified_image_files_to_windows_drive("prepared.img", "A:")

    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {name: b"replacement song"}
    check = save_session.last_floppy_save_diagnostics["root_directory_check"]
    assert check["free_entries"] == 0
    assert check["reclaimed_entries"] == check["required_entries"] == slots
    assert save_session.last_write_verification["confidence"] == "contents_verified"
