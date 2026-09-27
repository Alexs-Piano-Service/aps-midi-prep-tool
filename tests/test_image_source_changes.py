"""A stale editor must not overwrite repairs or edits made to its source."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.boot_sector_repair import apply_boot_sector_repair
from test_boot_sector_repair import song_image


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
