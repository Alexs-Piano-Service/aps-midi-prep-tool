"""Only explicitly completed floppy recovery packages have bounded retention."""

import datetime
import json
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_save_recovery as recovery


@pytest.fixture
def root(tmp_path, monkeypatch):
    directory = tmp_path / "recovery"
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(directory))
    directory.mkdir()
    return directory


def package_at(root, name, *, status="complete", days=0, **fields):
    directory = root / ("save-" + name)
    directory.mkdir()
    timestamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    manifest = {"schema": 1, "status": status, "created_at": timestamp.isoformat(), **fields}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "originals-0000.bin").write_bytes(b"retained song")
    return directory


def test_pruning_bounds_complete_packages_by_age_and_count(root):
    recent = [package_at(root, str(index), days=index) for index in range(8)]
    expired = package_at(root, "expired", days=31)
    recovery.prune_completed_packages()
    assert all(path.exists() for path in recent[:5])
    assert all(not path.exists() for path in recent[5:] + [expired])


def test_pruning_expires_old_packages_even_below_count_limit(root):
    old = package_at(root, "old", days=31)
    # Completion time takes priority over preparation time.
    current = package_at(root, "current", days=60,
                         completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    recovery.prune_completed_packages()
    assert not old.exists()
    assert current.exists()


@pytest.mark.parametrize("status", ["preparing", "ready", "writing", "contents_verified", "failed", "cancelled", "unknown"])
def test_incomplete_packages_are_never_pruned(root, status):
    pending = [package_at(root, str(index), status=status, days=365) for index in range(8)]
    recovery.prune_completed_packages()
    for directory in pending:
        assert (directory / "originals-0000.bin").read_bytes() == b"retained song"


@pytest.mark.parametrize("manifest", [None, "{", "[]", '{"status": "complete"}',
    '{"schema": 1, "status": "complete", "created_at": "invalid"}',
    '{"schema": 1, "status": "complete", "created_at": "2000-01-01T00:00:00"}',
    '{"schema": 99, "status": "complete", "created_at": "2000-01-01T00:00:00+00:00"}',
])
def test_ambiguous_packages_are_preserved(root, manifest):
    directory = package_at(root, "ambiguous", days=365)
    path = directory / "manifest.json"
    path.unlink()
    if manifest is not None:
        path.write_text(manifest, encoding="utf-8")
    recovery.prune_completed_packages()
    assert (directory / "originals-0000.bin").read_bytes() == b"retained song"


def test_pruning_does_not_follow_package_or_manifest_symlinks(root, tmp_path):
    outside = package_at(tmp_path, "outside", days=365)
    try:
        (root / "save-link").symlink_to(outside, target_is_directory=True)
        directory = root / "save-linked-manifest"
        directory.mkdir()
        (directory / "manifest.json").symlink_to(outside / "manifest.json")
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    recovery.prune_completed_packages()
    assert (outside / "originals-0000.bin").read_bytes() == b"retained song"
    assert directory.exists()
    assert (root / "save-link").is_symlink()


def test_locked_package_does_not_block_pruning_or_saving(root, monkeypatch):
    locked = package_at(root, "locked", days=90)
    expired = package_at(root, "expired", days=60)
    real_unlink = Path.unlink
    def remove(path, *args, **kwargs):
        if path == locked / "originals-0000.bin":
            raise PermissionError("Package is open elsewhere")
        real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", remove)
    package = recovery.SaveRecoveryPackage("A:")
    package.complete({"file_contents_verified": True})
    assert locked.exists()
    assert json.loads((locked / "manifest.json").read_text())["status"] == "complete"
    assert not expired.exists()
    assert json.loads((package.directory / "manifest.json").read_text())["status"] == "complete"
    monkeypatch.setattr(Path, "unlink", real_unlink)
    recovery.prune_completed_packages()
    assert not locked.exists()


def test_creation_and_completion_automatically_prune(root):
    expired = package_at(root, "expired", days=31)
    failed = package_at(root, "failed", status="failed", days=365)
    for index in range(6):
        package = recovery.SaveRecoveryPackage("A:")
        package.complete({"file_contents_verified": True})
    assert not expired.exists()
    assert (failed / "originals-0000.bin").read_bytes() == b"retained song"
    complete = [path for path in root.iterdir()
                if json.loads((path / "manifest.json").read_text())["status"] == "complete"]
    assert len(complete) == 5
