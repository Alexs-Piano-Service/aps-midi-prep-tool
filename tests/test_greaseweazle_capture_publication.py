"""Saving complete or partial SCP captures must preserve previous captures."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


CAPTURE = b"SCP acquired flux capture" * 8
PREVIOUS_CAPTURE = b"previous complete SCP capture"


def _saved_capture(tmp_path, existing):
    output = tmp_path / "saved" / "capture.scp"
    output.parent.mkdir()
    if existing:
        output.write_bytes(PREVIOUS_CAPTURE)
    return output


def _assert_saved_capture_unchanged(output, existing):
    if existing:
        assert output.read_bytes() == PREVIOUS_CAPTURE
        assert list(output.parent.iterdir()) == [output]
    else:
        assert not list(output.parent.iterdir())


def _acquire_capture(monkeypatch, tmp_path, *, partial):
    real_mkdtemp = image.tempfile.mkdtemp
    monkeypatch.setattr(
        image.tempfile, "mkdtemp", lambda *args, **kwargs: real_mkdtemp(*args, dir=tmp_path, **kwargs),
    )
    reads = []

    def read(source, destination, **kwargs):
        reads.append(Path(destination))
        Path(destination).write_bytes(CAPTURE)
        if partial:
            raise OSError("Greaseweazle read interrupted")
        return {}

    monkeypatch.setattr(image, "_gw_read_floppy", read)
    return reads


@pytest.mark.parametrize("method", ["_recover_greaseweazle", "load_greaseweazle"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_capture_copy_failure_preserves_saved_destination(tmp_path, monkeypatch, method, partial, existing):
    output = _saved_capture(tmp_path, existing)
    reads = _acquire_capture(monkeypatch, tmp_path, partial=partial)
    source = image.GreaseweazleFloppySource(
        "mock-device", "A", image.DISK_FORMAT_BY_KEY["ibm.720"],
        archival_quality=True, capture_save_path=str(output),
    )
    copies = []

    def interrupted_copy(acquired, destination):
        assert Path(acquired).read_bytes() == CAPTURE
        destination = Path(destination)
        assert destination.parent == output.parent
        copies.append(destination)
        destination.write_bytes(CAPTURE[:len(CAPTURE) // 2])
        raise OSError("SCP copy interrupted")

    monkeypatch.setattr(image.shutil, "copy2", interrupted_copy)
    monkeypatch.setattr(image, "_gw_convert", lambda *_args, **_kwargs: pytest.fail("Incomplete copies must not be converted"))

    with pytest.raises(OSError, match="SCP copy interrupted"):
        getattr(image.FloppyImageSession, method)(source)

    assert len(reads) == len(copies) == 1
    _assert_saved_capture_unchanged(output, existing)
    assert not list(tmp_path.glob("aps_recover_gw_*"))
    assert not list(tmp_path.glob("aps_gw_floppy_*"))


@pytest.mark.parametrize("failure", ["short_copy", "same_size_corruption", "fsync", "replace"])
@pytest.mark.parametrize("existing", [False, True])
def test_capture_verification_or_publication_failure_keeps_previous_output(tmp_path, monkeypatch, failure, existing):
    output = _saved_capture(tmp_path, existing)
    acquired = tmp_path / "acquired.scp"
    acquired.write_bytes(CAPTURE)

    if failure in {"short_copy", "same_size_corruption"}:
        def corrupt_copy(source, destination):
            assert Path(source) == acquired
            payload = CAPTURE[:len(CAPTURE) // 2] if failure == "short_copy" else b"X" + CAPTURE[1:]
            Path(destination).write_bytes(payload)

        monkeypatch.setattr(image.shutil, "copy2", corrupt_copy)
        expected_error = image.FloppyImageError
        message = "did not match the acquired data"
    else:
        def fail(*args):
            raise OSError(f"SCP {failure} failed")

        monkeypatch.setattr(image.os, failure, fail)
        expected_error = OSError
        message = f"SCP {failure} failed"

    with pytest.raises(expected_error, match=message):
        image._save_greaseweazle_capture_copy(acquired, output)

    assert acquired.read_bytes() == CAPTURE
    _assert_saved_capture_unchanged(output, existing)


def test_saved_capture_is_synced_and_verified_before_replacement(tmp_path, monkeypatch):
    output = _saved_capture(tmp_path, existing=True)
    acquired = tmp_path / "acquired.scp"
    acquired.write_bytes(CAPTURE)
    real_fsync = image.os.fsync
    real_replace = image.os.replace
    synced = []

    def fsync(descriptor):
        assert output.read_bytes() == PREVIOUS_CAPTURE
        real_fsync(descriptor)
        synced.append(descriptor)

    def replace(staged, destination):
        assert synced
        assert Path(staged).parent == output.parent
        assert Path(staged).read_bytes() == CAPTURE
        assert Path(destination) == output
        return real_replace(staged, destination)

    monkeypatch.setattr(image.os, "fsync", fsync)
    monkeypatch.setattr(image.os, "replace", replace)

    assert image._save_greaseweazle_capture_copy(acquired, output) == str(output)
    assert acquired.read_bytes() == output.read_bytes() == CAPTURE
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("partial", [False, True])
def test_recovery_converts_the_complete_saved_capture_copy(tmp_path, monkeypatch, partial):
    output = _saved_capture(tmp_path, existing=True)
    reads = _acquire_capture(monkeypatch, tmp_path, partial=partial)
    source = image.GreaseweazleFloppySource(
        "mock-device", "A", image.DISK_FORMAT_BY_KEY["ibm.720"],
        archival_quality=True, capture_save_path=str(output),
    )

    def convert(capture, destination, disk_format, **kwargs):
        assert Path(capture) == output
        assert output.read_bytes() == CAPTURE
        assert list(output.parent.iterdir()) == [output]
        Path(destination).write_bytes(b"converted image")
        return ""

    def recover(raw_image, temp_dir, **kwargs):
        assert Path(raw_image).read_bytes() == b"converted image"
        assert ("partial SCP capture" in kwargs["extra_note"]) == partial
        return SimpleNamespace(temp_dir=temp_dir)

    monkeypatch.setattr(image, "_gw_convert", convert)
    monkeypatch.setattr(image.FloppyImageSession, "_recover_from_raw_image", recover)

    session = image.FloppyImageSession._recover_greaseweazle(source)

    assert len(reads) == 1
    assert Path(session.temp_dir).is_dir()
    assert output.read_bytes() == CAPTURE
