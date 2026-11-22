"""A newly named extraction checkpoint cannot overwrite a concurrent creator."""

import pytest

from aps_midi_prep_tool_app.bulk_extraction_job import ExtractionJob, read_extraction_job


def _job(path, root, *, resume=False):
    return ExtractionJob(path, str(root), str(root), {
        "convert_eseq": False, "include_eseq_sources": False,
        "long_midi_filenames": True, "trim_title_spaces": True,
        "use_album_names": True, "preserve_volume_controls": False,
    }, resume=resume)


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_first_checkpoint_save_keeps_late_name_collision(tmp_path, kind):
    path = tmp_path / "aps-extraction-job.json"
    job = _job(path, tmp_path)
    original = tmp_path / "original"
    original.write_bytes(b"another operation")
    if kind == "symlink":
        try:
            path.symlink_to(original)
        except OSError:
            pytest.skip("Symlink creation unavailable")
    else:
        path.write_bytes(original.read_bytes())
    with pytest.raises(FileExistsError):
        job.save()
    assert path.read_bytes() == original.read_bytes() == b"another operation"
    assert path.is_symlink() == (kind == "symlink")


def test_existing_owned_checkpoint_can_be_updated_and_resumed(tmp_path):
    path = tmp_path / "aps-extraction-job.json"
    job = _job(path, tmp_path)
    job.save()
    job.data["status"] = "incomplete"
    job.save()
    assert read_extraction_job(path)["status"] == "incomplete"
    resumed = _job(path, tmp_path, resume=True)
    resumed.data["status"] = "resumed"
    resumed.save()
    assert read_extraction_job(path)["status"] == "resumed"
