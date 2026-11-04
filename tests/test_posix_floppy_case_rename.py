"""File-level saves preserve filename spelling independently of FAT identity."""

from dataclasses import replace
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX mtools save path")


SHORT_NAME_CASE_FLAGS = {
    "SONG.MID": 0x00,
    "SONG.mid": 0x10,
    "song.MID": 0x08,
    "song.mid": 0x18,
}


def assert_short_name_case_flags(path, name):
    if name not in SHORT_NAME_CASE_FLAGS:
        return
    with open(path, "rb") as handle:
        geometry = image._geometry_from_boot_sector(handle.read(512))
        handle.seek(geometry.root_offset)
        root = handle.read(geometry.root_size)
    entries = []
    for offset in range(0, len(root), 32):
        entry = root[offset:offset + 32]
        if entry[0] == 0:
            break
        if entry[0] == 0xE5:
            continue
        # Keep LFN slots in the assertion, but ignore the volume label.
        if entry[11] == 0x0F or not entry[11] & 0x08:
            entries.append(entry)
    assert len(entries) == 1, "Case flags must preserve spelling without LFN slots"
    assert entries[0][:11] == b"SONG    MID"
    assert entries[0][12] & 0x18 == SHORT_NAME_CASE_FLAGS[name]


def replace_name_using_case_flags(path, before, after, payload_file):
    # mcopy writes case bits for uniform-case components. Some mren versions
    # retain the old bits, which would fail to prepare the requested spelling.
    image._run_command(
        [image._require_command("mdel"), "-i", os.fspath(path), image.mtools_path(before)],
        "Could not remove the original filename",
    )
    image._copy_host_file_into_image(path, payload_file, after)


@pytest.fixture(params=[
    pytest.param(("SONG.MID", "Song.mid"), id="short-name"),
    pytest.param(("MY FAVORITE SONG.MID", "My Favorite Song.mid"), id="long-name"),
    pytest.param(("SONG.MID", "SONG.mid"), id="lower-extension"),
    pytest.param(("SONG.mid", "SONG.MID"), id="restore-upper-extension"),
    pytest.param(("SONG.MID", "song.MID"), id="lower-basename"),
    pytest.param(("song.MID", "SONG.MID"), id="restore-upper-basename"),
    pytest.param(("SONG.MID", "song.mid"), id="lower-both"),
    pytest.param(("song.mid", "SONG.MID"), id="restore-upper-both"),
])
def case_rename(request, tmp_path, monkeypatch):
    original, prepared = request.param
    payload = b"unchanged song recording"
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    target = str(tmp_path / "floppy.img")
    host_file = tmp_path / "song.mid"
    host_file.write_bytes(payload)
    image.create_blank_floppy_image(target, image.DISK_FORMAT_BY_KEY["ibm.720"])
    image._copy_host_file_into_image(target, host_file, original)
    assert_short_name_case_flags(target, original)
    session = image.FloppyImageSession.load(target)
    # Model a floppy opened into the session's private working-image snapshot.
    session.source_kind = "floppy_usb"
    session.source_path = target
    try:
        if original in SHORT_NAME_CASE_FLAGS and prepared in SHORT_NAME_CASE_FLAGS:
            # Isolate saves of valid case-flag images from mren's case-bit
            # behavior while preserving the unchanged file payload.
            source = os.path.join(session.temp_dir, "prepared_case.img")
            shutil.copyfile(session.working_img_path, source)
            replace_name_using_case_flags(source, original, prepared, host_file)
        else:
            source = session.create_modified_image(renames={original: prepared})
        assert_short_name_case_flags(source, prepared)
        assert [entry.path for entry in image.read_image_listing(source).entries] == [prepared]
        assert image._read_fat12_file_bytes(source, prepared) == payload
        native_reader = image._read_fat12_file_bytes

        def read_image(path, name):
            assert os.fspath(path) != target, "Save read the entire physical floppy"
            return native_reader(path, name)

        monkeypatch.setattr(image, "_is_block_device_path", lambda path: os.fspath(path) == target)
        monkeypatch.setattr(image, "_read_fat12_file_bytes", read_image)
        yield SimpleNamespace(
            session=session, source=source, target=target, original=original,
            prepared=prepared, payload=payload, host_file=host_file, read_file=native_reader,
        )
    finally:
        session.cleanup()


def assert_saved(case, name):
    assert_short_name_case_flags(case.target, name)
    assert [entry.path for entry in image.read_image_listing(case.target).entries] == [name]
    assert case.read_file(case.target, name) == case.payload


@pytest.mark.parametrize("verify_copies", [False, True])
@pytest.mark.parametrize("backup_originals,check_originals", [(True, True), (False, False)])
def test_case_only_rename_publishes_exact_spelling(
    case_rename, verify_copies, backup_originals, check_originals,
):
    case = case_rename

    case.session._sync_modified_image_files_to_floppy_drive(
        case.source, case.target, verify_copies=verify_copies,
        backup_originals=backup_originals, check_originals=check_originals,
    )

    assert_saved(case, case.prepared)
    assert case.session.last_floppy_save_diagnostics["files_copied"] == 1


@pytest.mark.parametrize("verify_copies", [False, True])
@pytest.mark.parametrize("backup_originals,check_originals", [(True, True), (False, False)])
def test_unchanged_filename_is_not_republished(
    case_rename, verify_copies, backup_originals, check_originals,
):
    case = case_rename

    case.session._sync_modified_image_files_to_floppy_drive(
        case.session.working_img_path, case.target, verify_copies=verify_copies,
        backup_originals=backup_originals, check_originals=check_originals,
    )

    assert_saved(case, case.original)
    diagnostics = case.session.last_floppy_save_diagnostics
    assert diagnostics["target_mutation_attempted"] is False
    assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0


def test_external_case_rename_is_rejected_before_target_mutation(case_rename):
    case = case_rename
    if case.original in SHORT_NAME_CASE_FLAGS and case.prepared in SHORT_NAME_CASE_FLAGS:
        replace_name_using_case_flags(
            case.target, case.original, case.prepared, case.host_file,
        )
    else:
        mren = image._require_command("mren")
        intermediate = "APSTEMP.MID"
        for before, after in ((case.original, intermediate), (intermediate, case.prepared)):
            image._run_command(
                [mren, "-i", case.target, image.mtools_path(before), image.mtools_path(after)],
                "Could not simulate an external filename change",
            )
    assert_saved(case, case.prepared)
    before_save = Path(case.target).read_bytes()

    with pytest.raises(image.FloppyImageError, match="contents changed since it was opened"):
        case.session._sync_modified_image_files_to_floppy_drive(case.source, case.target)

    assert Path(case.target).read_bytes() == before_save
    assert case.session.last_floppy_save_diagnostics["target_mutation_attempted"] is False


@pytest.mark.parametrize("failure", ["before_publication", "after_publication"])
def test_case_rename_publication_failure_restores_original_spelling(case_rename, monkeypatch, failure):
    case = case_rename
    run = case.session._run_mtools
    failed = []

    def fail_publication(args, message, **kwargs):
        if (not failed and os.path.basename(args[0]) == "mcopy"
                and case.target in args and args[-1] == image.mtools_path(case.prepared)):
            # Originals have already been backed up and removed. Recovery
            # must handle both an absent file and a completed case-only copy.
            assert image.read_image_listing(case.target).entries == []
            failed.append(args[-1])
            if failure == "after_publication":
                run(args, message, **kwargs)
                assert_saved(case, case.prepared)
            raise image.FloppyImageError("Injected case rename publication failure")
        return run(args, message, **kwargs)

    monkeypatch.setattr(case.session, "_run_mtools", fail_publication)
    with pytest.raises(image.FloppyImageError, match="Injected case rename publication failure"):
        case.session._sync_modified_image_files_to_floppy_drive(case.source, case.target)

    assert failed == [image.mtools_path(case.prepared)]
    assert_saved(case, case.original)
    restoration = case.session.last_floppy_save_diagnostics["restoration"]
    assert restoration["status"] == "complete"
    assert restoration["restored"] == [case.original]
    assert restoration["unchanged"] == []


@pytest.mark.parametrize("verify_copies", [False, True])
def test_copy_verification_rejects_original_spelling(case_rename, monkeypatch, verify_copies):
    case = case_rename
    run = case.session._run_mtools
    injected = []

    def preserve_original_spelling(args, message, **kwargs):
        if (not injected and os.path.basename(args[0]) == "mcopy"
                and case.target in args and args[-1] == image.mtools_path(case.prepared)):
            injected.append(True)
            args = [*args[:-1], image.mtools_path(case.original)]
        return run(args, message, **kwargs)

    monkeypatch.setattr(case.session, "_run_mtools", preserve_original_spelling)
    with pytest.raises(image.FloppyImageError, match="filename differs"):
        case.session._sync_modified_image_files_to_floppy_drive(
            case.source, case.target, verify_copies=verify_copies,
        )

    assert injected == [True]
    assert_saved(case, case.original)
    diagnostics = case.session.last_floppy_save_diagnostics
    assert diagnostics["files_copied"] == 0
    assert diagnostics["restoration"]["status"] == "complete"
    assert diagnostics["restoration"]["unchanged"] == [case.original]


@pytest.mark.parametrize("verify_copies", [False, True])
def test_final_verification_rejects_original_spelling(case_rename, monkeypatch, verify_copies):
    case = case_rename
    read_listing = image.read_image_listing
    injected = []

    def original_spelling_at_verification(path, **kwargs):
        listing = read_listing(path, **kwargs)
        diagnostics = case.session.last_floppy_save_diagnostics
        if (os.fspath(path) == case.target and not injected
                and diagnostics.get("stage") in {"verify_file_contents", "check_file_listing"}):
            injected.append(True)
            # Model a driver returning the original spelling despite matching
            # case-folded identity, size, and bytes at the final verification.
            return replace(listing, entries=[
                replace(entry, path=case.original) for entry in listing.entries
            ])
        return listing

    monkeypatch.setattr(image, "read_image_listing", original_spelling_at_verification)
    with pytest.raises(image.FloppyImageError, match="filename differs"):
        case.session._sync_modified_image_files_to_floppy_drive(
            case.source, case.target, verify_copies=verify_copies,
        )

    assert injected == [True]
    assert_saved(case, case.original)
    assert case.session.last_floppy_save_diagnostics["restoration"]["status"] == "complete"
