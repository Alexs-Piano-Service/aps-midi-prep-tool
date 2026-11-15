"""A failed Unicode-path alias copy must clean up its own temporary files."""

import errno
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image


@pytest.mark.parametrize("source_name", ["café.mid", "Música/song.mid"])
def test_partial_alias_copy_failure_removes_staging_directory(tmp_path, monkeypatch, source_name):
    source = tmp_path / source_name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"complete source contents")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    monkeypatch.setattr(floppy_image.tempfile, "tempdir", str(staging_root))
    aliases = []
    failure = OSError(errno.ENOSPC, "No space left on device")

    def partial_copy(host_path, alias_path):
        assert Path(host_path) == source
        alias = Path(alias_path)
        alias.write_bytes(b"partial")
        aliases.append(alias)
        raise failure

    monkeypatch.setattr(floppy_image.shutil, "copy2", partial_copy)
    with pytest.raises(OSError) as caught:
        floppy_image._mtools_host_source_path(source, "SONG.MID")

    assert caught.value is failure
    assert len(aliases) == 1
    assert aliases[0].parent.name.startswith("aps_mtools_host_")
    assert not aliases[0].parent.exists()
    assert list(staging_root.iterdir()) == []
    assert source.read_bytes() == b"complete source contents"
