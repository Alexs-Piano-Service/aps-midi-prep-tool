"""Generated output names remain readable and never claim an existing entry."""

from concurrent.futures import ThreadPoolExecutor
import re

import pytest

from aps_midi_prep_tool_app.helpers.output_names import available_output_path, create_output_directory


def test_random_suffixes_remain_the_default(tmp_path):
    folder = create_output_directory(tmp_path, "Album", separator=" - ")
    assert re.fullmatch(r"Album - [0-9a-f]{8}", folder.name)
    assert folder.is_dir()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "broken_symlink"])
def test_clean_directory_names_skip_existing_entries(tmp_path, kind):
    existing = tmp_path / "Album"
    if "symlink" in kind:
        target = tmp_path / "target"
        if kind == "symlink":
            target.mkdir()
        try:
            existing.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation unavailable")
    elif kind == "directory":
        existing.mkdir()
    else:
        existing.write_text("original")
    (tmp_path / "Album (2)").write_text("another original")
    folder = create_output_directory(tmp_path, "Album", random_suffixes=False)
    assert folder.name == "Album (3)"
    assert (tmp_path / "Album (2)").read_text() == "another original"
    if kind == "file":
        assert existing.read_text() == "original"
    if "symlink" in kind:
        assert existing.is_symlink()


def test_concurrent_clean_outputs_each_claim_their_own_directory(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: create_output_directory(tmp_path, "Album", random_suffixes=False), range(8)))
    assert {path.name for path in paths} == {"Album", *(f"Album ({n})" for n in range(2, 9))}
    assert all(path.is_dir() for path in paths)


def test_directory_creation_does_not_retry_unrelated_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_output_directory(tmp_path / "missing-parent", "Album", random_suffixes=False)


def test_file_names_use_numbers_only_when_the_clean_name_is_taken(tmp_path):
    assert available_output_path(tmp_path, "job", suffix=".json", random_suffixes=False).name == "job.json"
    (tmp_path / "job.json").write_text("original")
    assert available_output_path(tmp_path, "job", suffix=".json", random_suffixes=False).name == "job (2).json"
    assert (tmp_path / "job.json").read_text() == "original"


@pytest.mark.parametrize("name", ["", ".", "..", "../Album", "Album/Other", "Album\\Other"])
def test_output_name_cannot_escape_its_parent(tmp_path, name):
    with pytest.raises(ValueError):
        create_output_directory(tmp_path, name, random_suffixes=False)
