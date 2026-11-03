"""Filename spelling survives file-level saves from real FAT12 images."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from test_windows_floppy_save import _package, _prepared_save, mounted_drive


@pytest.fixture
def case_save(mounted_drive, tmp_path, monkeypatch):
    sessions = []
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, name: str(Path(root) / name))

    def prepare(original, renamed, *, payload=b"unchanged song"):
        source = tmp_path / "source.img"
        floppy_image.create_blank_floppy_image(source, floppy_image.DISK_FORMAT_BY_KEY["ibm.720"])
        (mounted_drive / original).write_bytes(b"unchanged song")
        floppy_image._copy_host_file_into_image(source, mounted_drive / original, original)
        session = floppy_image.FloppyImageSession.load(source)
        sessions.append(session)
        session.source_kind, session.source_path = "floppy_usb", "A:"
        replacement = tmp_path / "replacement.bin"
        replacement.write_bytes(payload)
        prepared = session.create_modified_image(
            renames={original: renamed},
            replacements={original: str(replacement)} if payload != b"unchanged song" else None,
        )
        assert [entry.path for entry in floppy_image._read_fat12_image_listing(prepared).entries] == [renamed]
        return session, prepared

    yield prepare
    for session in sessions:
        session.cleanup()


@pytest.mark.parametrize("original,renamed", [
    ("SONG.MID", "Song.mid"),
    ("A LONG SONG TITLE.MID", "A Long Song Title.mid"),
    ("Song.mid", "SONG.MID"),
])
@pytest.mark.parametrize("payload", [b"unchanged song", b"updated song"])
@pytest.mark.parametrize("verify_copies", [False, True])
def test_fat12_case_rename_publishes_exact_prepared_name(
    case_save, mounted_drive, original, renamed, payload, verify_copies,
):
    session, prepared = case_save(original, renamed, payload=payload)

    session._sync_modified_image_files_to_windows_drive(prepared, "A:", verify_copies=verify_copies)

    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {renamed: payload}
    assert session.last_floppy_save_diagnostics["files_removed"] == 1
    assert session.last_floppy_save_diagnostics["files_copied"] == 1
    _, manifest = _package(session)
    assert manifest["expected"][floppy_image.dos_filename_key(original)]["path"] == renamed
    assert all(action["status"] == "complete" for action in manifest["actions"])


def test_direct_prepared_files_publish_case_change_with_unchanged_payload(mounted_drive, tmp_path, monkeypatch):
    (mounted_drive / "SONG.MID").write_bytes(b"unchanged song")
    monkeypatch.setattr(floppy_image, "_windows_drive_file_path", lambda root, name: str(Path(root) / name))
    job = _prepared_save(tmp_path, {"Song.mid": b"unchanged song"}, overwrite=["SONG.MID"])

    job._sync_modified_image_files_to_windows_drive(None, "A:")

    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {"Song.mid": b"unchanged song"}
    assert job.last_floppy_save_diagnostics["files_copied"] == 1


def _simulate_tunneled_case(monkeypatch, original):
    copy = floppy_image._copy_prepared_floppy_file
    rename = floppy_image.os.rename

    def recreate(source, destination, **kwargs):
        copy(source, destination, **kwargs)
        # FastFAT can restore a former mixed-case LFN when creating its short
        # uppercase/lowercase equivalent after deleting the old entry.
        rename(destination, str(Path(destination).with_name(original)))

    monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", recreate)


@pytest.mark.parametrize("renamed", ["SONG.MID", "song.mid"])
def test_tunneled_lfn_spelling_is_corrected_and_journaled(case_save, mounted_drive, tmp_path, monkeypatch, renamed):
    session, prepared = case_save("Song.mid", "SONG.MID")
    if renamed == "song.mid":
        session = _prepared_save(tmp_path, {renamed: b"unchanged song"}, overwrite=["Song.mid"])
        prepared = None
    _simulate_tunneled_case(monkeypatch, "Song.mid")

    session._sync_modified_image_files_to_windows_drive(prepared, "A:")

    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {renamed: b"unchanged song"}
    _, manifest = _package(session)
    assert manifest["actions"][-1]["filename_rename"] == {
        "source": "Song.mid", "destination": renamed, "status": "complete",
    }
    assert all(action["status"] == "complete" for action in manifest["actions"])


@pytest.mark.parametrize("failure", ["copy", "rename", "silent_rename"])
def test_failed_case_publication_keeps_original_and_prepared_recovery_files(
    case_save, mounted_drive, monkeypatch, failure,
):
    session, prepared = case_save("Song.mid", "SONG.MID")
    if failure == "copy":
        def fail_copy(*args, **kwargs):
            raise OSError("simulated publication failure")
        monkeypatch.setattr(floppy_image, "_copy_prepared_floppy_file", fail_copy)
    else:
        _simulate_tunneled_case(monkeypatch, "Song.mid")

        def fail_rename(*args, **kwargs):
            if failure == "rename":
                raise OSError("simulated rename failure")
        monkeypatch.setattr(floppy_image.os, "rename", fail_rename)

    with pytest.raises((OSError, floppy_image.FloppyImageError)):
        session._sync_modified_image_files_to_windows_drive(prepared, "A:")

    directory, manifest = _package(session)
    assert manifest["status"] == "failed"
    assert (directory / manifest["originals"]["Song.mid"]["file"]).read_bytes() == b"unchanged song"
    assert (directory / manifest["replacements"]["SONG.MID"]["file"]).read_bytes() == b"unchanged song"
    assert manifest["expected"]["SONG.MID"]["path"] == "SONG.MID"
    assert manifest["actions"][-1]["status"] == "started"
    assert session.last_write_verification["confidence"] == "partial_or_uncertain"
    if failure != "copy":
        assert manifest["actions"][-1]["filename_rename"]["status"] == "started"
        assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {"Song.mid": b"unchanged song"}


@pytest.mark.parametrize("verify_copies", [False, True])
def test_final_verification_rejects_correct_payload_with_wrong_case(
    case_save, mounted_drive, monkeypatch, verify_copies,
):
    session, prepared = case_save("SONG.MID", "Song.mid")

    def change_name_after_copy(path, attributes):
        Path(path).rename(mounted_drive / "SONG.MID")
    monkeypatch.setattr(floppy_image, "_set_windows_file_attributes", change_name_after_copy)

    with pytest.raises(floppy_image.FloppyImageError, match="filename spelling differs"):
        session._sync_modified_image_files_to_windows_drive(prepared, "A:", verify_copies=verify_copies)

    assert session.last_floppy_save_diagnostics["status"] == "failed"
    assert not session.last_floppy_save_diagnostics["file_contents_verified"]


def test_changed_target_spelling_is_rejected_before_mutation(case_save, mounted_drive):
    session, prepared = case_save("SONG.MID", "Song.mid")
    (mounted_drive / "SONG.MID").rename(mounted_drive / "song.mid")

    with pytest.raises(floppy_image.FloppyImageError, match="changed since it was opened"):
        session._sync_modified_image_files_to_windows_drive(prepared, "A:")

    assert not session.last_floppy_save_diagnostics["target_mutation_attempted"]
    assert {path.name: path.read_bytes() for path in mounted_drive.iterdir()} == {"song.mid": b"unchanged song"}
