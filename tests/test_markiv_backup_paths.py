"""Windows junctions must be skipped like source symbolic links."""

from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.markiv_backup.backup import _contained
from aps_midi_prep_tool_app.markiv_backup.library import scan_library
from aps_midi_prep_tool_app.markiv_backup.metadata import load_metadata
from aps_midi_prep_tool_app.markiv_backup.paths import is_link


def _junction(monkeypatch, path):
    original = Path.lstat

    def lstat(self, *args, **kwargs):
        if self == path:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_reparse_tag=0xA0000003)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', lstat)


def test_scan_skips_junction_instead_of_entering_it(tmp_path, monkeypatch):
    album = tmp_path / 'songs/user/1'
    junction = album / 'junction'
    junction.mkdir(parents=True)
    (album / 'SONG.MID').write_bytes(b'music')
    (junction / 'OUTSIDE.MID').write_bytes(b'outside source')
    _junction(monkeypatch, junction)

    assert is_link(junction)
    plan = scan_library(tmp_path)

    assert [item.source for item in plan.files] == ['songs/user/1/SONG.MID']
    assert any('junction' in warning for warning in plan.warnings)


def test_containment_rejects_junction_even_when_it_resolves_within_root(tmp_path, monkeypatch):
    junction = tmp_path / 'junction'
    junction.mkdir()
    _junction(monkeypatch, junction)

    with pytest.raises(ValueError, match='Symbolic links'):
        _contained(tmp_path, 'junction/song.mid')


def test_metadata_does_not_follow_a_junction_to_cluster(tmp_path, monkeypatch):
    local = tmp_path / 'local'
    cluster = local / 'pgsql/data'
    cluster.mkdir(parents=True)
    (cluster / 'PG_VERSION').write_text('7.3')
    _junction(monkeypatch, local)

    catalog = load_metadata(tmp_path)

    assert catalog.evidence_files == []
    assert any('No readable' in warning for warning in catalog.warnings)


def test_regular_folders_and_files_are_not_links(tmp_path):
    file = tmp_path / 'song.mid'
    file.write_bytes(b'music')

    assert not is_link(tmp_path)
    assert not is_link(file)
    assert not is_link(tmp_path / 'missing')
