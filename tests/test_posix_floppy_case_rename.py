"""File-level saves preserve filename spelling independently of FAT identity."""

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX mtools save path")


@pytest.fixture(params=[
    ("SONG.MID", "Song.mid"),
    ("MY FAVORITE SONG.MID", "My Favorite Song.mid"),
], ids=["short-name", "long-name"])
def case_rename(request, tmp_path, monkeypatch):
    original, prepared = request.param
    payload = b"unchanged song recording"
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    target = str(tmp_path / "floppy.img")
    host_file = tmp_path / "song.mid"
    host_file.write_bytes(payload)
    image.create_blank_floppy_image(target, image.DISK_FORMAT_BY_KEY["ibm.720"])
    image._copy_host_file_into_image(target, host_file, original)
    session = image.FloppyImageSession.load(target)
    # Model a floppy opened into the session's private working-image snapshot.
    session.source_kind = "floppy_usb"
    session.source_path = target
    try:
        source = session.create_modified_image(renames={original: prepared})
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
            prepared=prepared, payload=payload, read_file=native_reader,
        )
    finally:
        session.cleanup()


def assert_saved(case, name):
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
