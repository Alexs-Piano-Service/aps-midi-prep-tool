"""A stale editor must not overwrite repairs or edits made to its source."""

import errno
import os
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.boot_sector_repair import apply_boot_sector_repair
from test_boot_sector_repair import song_image


def _with_external_file(data, geometry):
    """Add a FAT12 file independently of the session's image writer."""
    payload = b"Added outside the app while the image was open"
    external = bytearray(data)
    for index in range(geometry.num_fats):
        fat_start = geometry.fat_offset + index * geometry.fat_size
        external[fat_start + 4] |= 0xF0
        external[fat_start + 5] = 0xFF
    entry = bytearray(32)
    entry[:11] = b"EXTERNALTXT"
    entry[11] = 0x20
    entry[26:28] = (3).to_bytes(2, "little")
    entry[28:32] = len(payload).to_bytes(4, "little")
    external[geometry.root_offset + 64:geometry.root_offset + 96] = entry
    offset = geometry.data_offset + geometry.cluster_size
    external[offset:offset + len(payload)] = payload
    return external, payload


@pytest.mark.parametrize("when", ("before_save", "during_save"))
def test_save_refuses_changed_source_and_preserves_working_copy(tmp_path, monkeypatch, when):
    source, data, _ = song_image(tmp_path)
    source.write_bytes(data)
    session = image.FloppyImageSession.load(source)
    baseline = Path(session.working_img_path).read_bytes()
    modified = bytearray(source.read_bytes())
    modified[-1] ^= 1
    try:
        if when == "before_save":
            source.write_bytes(modified)
        else:
            write = session._write_image_direct

            def change_source(*args, **kwargs):
                result = write(*args, **kwargs)
                source.write_bytes(modified)
                return result

            monkeypatch.setattr(session, "_write_image_direct", change_source)
        with pytest.raises(image.FloppyImageError, match="Source image changed"):
            session.commit_to_source(renames={"SONG.FIL": "RENAMED.FIL"})
        assert source.read_bytes() == modified
        assert Path(session.working_img_path).read_bytes() == baseline
        assert not list(Path(session.temp_dir).glob("modified_*.img"))
        assert not list(Path(session.temp_dir).glob(".aps_image_*"))
        # Pending changes can still be recovered through a separate export.
        output = tmp_path / "saved_edits.img"
        session.export_to(str(output), "img", renames={"SONG.FIL": "RENAMED.FIL"})
        assert image._read_fat12_file_bytes(output, "RENAMED.FIL") == b"SONG"
        assert source.read_bytes() == modified
    finally:
        session.cleanup()


@pytest.mark.parametrize("when", ("before_save", "during_save"))
def test_external_file_addition_survives_failed_save_and_pending_edits_can_be_exported(
    tmp_path, monkeypatch, when,
):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(data)
    source_stat = source.stat()
    session = image.FloppyImageSession.load(source)
    baseline = Path(session.working_img_path).read_bytes()
    pending = tmp_path / "pending.txt"
    pending.write_bytes(b"Pending addition from the open session")
    edits = {
        "renames": {"SONG.FIL": "RENAMED.FIL"},
        "additions": {"PENDING.TXT": str(pending)},
    }

    # Simulate another program adding a real file to unused cluster 3 and the
    # next directory slot, independently of this session's image writer.
    external, external_payload = _with_external_file(data, geometry)

    def add_external_file():
        source.write_bytes(external)
        # Content changes must be detected even when size and mtime match.
        os.utime(source, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        assert source.stat().st_size == source_stat.st_size
        assert source.stat().st_mtime_ns == source_stat.st_mtime_ns
        assert image._read_fat12_file_bytes(source, "EXTERNAL.TXT") == external_payload

    try:
        if when == "before_save":
            add_external_file()
        else:
            write = session._write_image_direct

            def write_then_add_external_file(*args, **kwargs):
                result = write(*args, **kwargs)
                add_external_file()
                return result

            monkeypatch.setattr(session, "_write_image_direct", write_then_add_external_file)

        with pytest.raises(image.FloppyImageError, match="Source image changed"):
            session.commit_to_source(**edits)

        assert source.read_bytes() == external
        assert image._read_fat12_file_bytes(source, "SONG.FIL") == b"SONG"
        assert image._read_fat12_file_bytes(source, "EXTERNAL.TXT") == external_payload
        assert Path(session.working_img_path).read_bytes() == baseline
        assert not list(Path(session.temp_dir).glob("modified_*.img"))
        assert not list(tmp_path.glob(".aps_capture_*"))

        output = tmp_path / "recovered_edits.img"
        session.export_to(str(output), "img", **edits)
        assert image._read_fat12_file_bytes(output, "RENAMED.FIL") == b"SONG"
        assert image._read_fat12_file_bytes(output, "PENDING.TXT") == pending.read_bytes()
        assert source.read_bytes() == external
    finally:
        session.cleanup()


@pytest.mark.parametrize("export", ("direct", "same_size", "resized"))
@pytest.mark.parametrize("alias", ("same", "relative", "symlink", "hardlink"))
def test_save_as_cannot_overwrite_changed_source_through_alias(tmp_path, monkeypatch, export, alias):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(data)
    session = image.FloppyImageSession.load(source)
    output = source
    if alias == "relative":
        monkeypatch.chdir(tmp_path)
        output = Path("source.img")
    elif alias in {"symlink", "hardlink"}:
        output = tmp_path / "alias.img"
        try:
            if alias == "symlink":
                output.symlink_to(source)
            else:
                output.hardlink_to(source)
        except OSError:
            session.cleanup()
            pytest.skip(f"{alias} is unavailable on this platform")
    external, external_payload = _with_external_file(data, geometry)
    source.write_bytes(external)
    try:
        with pytest.raises(image.FloppyImageError, match="Source image changed"):
            if export == "direct":
                session.export_to(output, "img", renames={"SONG.FIL": "RENAMED.FIL"})
            else:
                disk_format = session.disk_format if export == "same_size" else image.DISK_FORMAT_BY_KEY["ibm.1440"]
                session.export_to_images(output, "img", disk_format, renames={"SONG.FIL": "RENAMED.FIL"})
        assert source.read_bytes() == external
        assert output.read_bytes() == external
        assert image._read_fat12_file_bytes(source, "EXTERNAL.TXT") == external_payload
        assert not list(tmp_path.glob(".aps_capture_*"))
    finally:
        session.cleanup()


@pytest.mark.parametrize("operation", ("save", "direct", "same_size", "resized"))
@pytest.mark.parametrize("when", ("during_write", "during_sync", "during_cross_filesystem_sync"))
def test_save_and_export_recheck_source_at_publication(tmp_path, monkeypatch, operation, when):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(data)
    session = image.FloppyImageSession.load(source)
    baseline = Path(session.working_img_path).read_bytes()
    external, external_payload = _with_external_file(data, geometry)
    edits = {"renames": {"SONG.FIL": "RENAMED.FIL"}}

    def export_to(path):
        if operation == "direct":
            session.export_to(path, "img", **edits)
        else:
            disk_format = image.DISK_FORMAT_BY_KEY["ibm.1440"] if operation == "resized" else session.disk_format
            session.export_to_images(path, "img", disk_format, **edits)

    try:
        with monkeypatch.context() as patch:
            if when == "during_write":
                writer = image if operation == "resized" else session
                original_write = writer._write_image_direct

                def write_then_change(*args, **kwargs):
                    result = original_write(*args, **kwargs)
                    source.write_bytes(external)
                    return result

                patch.setattr(writer, "_write_image_direct", write_then_change)
            else:
                original_sync = image.os.fsync
                sync_count = 0

                def sync_then_change(descriptor):
                    nonlocal sync_count
                    original_sync(descriptor)
                    sync_count += 1
                    target_sync = 2 if when == "during_cross_filesystem_sync" else 1
                    if sync_count == target_sync:
                        source.write_bytes(external)

                patch.setattr(image.os, "fsync", sync_then_change)
                if when == "during_cross_filesystem_sync":
                    original_replace = image.os.replace

                    def cross_filesystem_replace(staged, destination):
                        if Path(destination) == source and Path(staged).name.startswith(".aps_capture_"):
                            raise OSError(errno.EXDEV, "simulated cross-filesystem rename")
                        return original_replace(staged, destination)

                    patch.setattr(image.os, "replace", cross_filesystem_replace)

            with pytest.raises(image.FloppyImageError, match="Source image changed"):
                if operation == "save":
                    session.commit_to_source(**edits)
                else:
                    export_to(source)

        assert source.read_bytes() == external
        assert image._read_fat12_file_bytes(source, "EXTERNAL.TXT") == external_payload
        assert Path(session.working_img_path).read_bytes() == baseline
        assert not list(tmp_path.glob(".aps_capture_*"))
        assert not list(tmp_path.glob(".aps_image_*"))
        assert not list(Path(session.temp_dir).glob("modified_*.img"))

        output = tmp_path / "separate.img"
        export_to(output)
        assert image._read_fat12_file_bytes(output, "RENAMED.FIL") == b"SONG"
        assert source.read_bytes() == external
    finally:
        session.cleanup()


def test_split_export_cannot_overwrite_changed_source_with_generated_filename(tmp_path):
    original, data, geometry = song_image(tmp_path)
    source = original.with_name("export_02.img")
    source.write_bytes(data)
    session = image.FloppyImageSession.load(source)
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"A" * (200 * 1024))
    second.write_bytes(b"B" * (200 * 1024))
    previous_output = tmp_path / "export_01.img"
    previous_output.write_bytes(b"Previous exported image must survive")
    external, external_payload = _with_external_file(data, geometry)
    source.write_bytes(external)
    try:
        with pytest.raises(image.FloppyImageError, match="Source image changed"):
            session.export_to_images(
                tmp_path / "export.img", "img", image.DISK_FORMAT_BY_KEY["ibm.360"],
                additions={"FIRST.BIN": str(first), "SECOND.BIN": str(second)},
            )
        assert source.read_bytes() == external
        assert previous_output.read_bytes() == b"Previous exported image must survive"
        assert image._read_fat12_file_bytes(source, "EXTERNAL.TXT") == external_payload
        assert not list(tmp_path.glob(".aps_capture_*"))
    finally:
        session.cleanup()


def test_repair_cannot_be_undone_by_old_session_and_reloaded_session_can_save(tmp_path):
    source, data, geometry = song_image(tmp_path)
    source.write_bytes(data)
    old = image.FloppyImageSession.load(source)
    try:
        apply_boot_sector_repair(source)
        repaired = source.read_bytes()
        assert repaired[geometry.root_offset + 32 + 11] == 0x21
        with pytest.raises(image.FloppyImageError, match="Source image changed"):
            old.commit_to_source()
        assert source.read_bytes() == repaired
        refreshed = image.FloppyImageSession.load(source)
        try:
            refreshed.commit_to_source(renames={"SONG.FIL": "RENAMED.FIL"})
            refreshed.commit_to_source(renames={"RENAMED.FIL": "SONG.FIL"})
            assert source.read_bytes()[geometry.root_offset + 32 + 11] == 0x21
            assert image._read_fat12_file_bytes(source, "SONG.FIL") == b"SONG"
        finally:
            refreshed.cleanup()
    finally:
        old.cleanup()


def test_source_change_during_load_is_rejected(tmp_path, monkeypatch):
    source, data, _ = song_image(tmp_path)
    source.write_bytes(data)
    original_load = image.FloppyImageSession._load_raw
    loaded = []

    def load_then_change(*args, **kwargs):
        session = original_load(*args, **kwargs)
        loaded.append(session)
        source.write_bytes(source.read_bytes()[:-1] + b"X")
        return session

    monkeypatch.setattr(image.FloppyImageSession, "_load_raw", load_then_change)
    with pytest.raises(image.FloppyImageError, match="Source image changed"):
        image.FloppyImageSession.load(source)
    assert not Path(loaded[0].temp_dir).exists()
