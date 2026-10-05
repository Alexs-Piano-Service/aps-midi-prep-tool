"""Image publication must preserve an existing destination on staging failure."""

import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.mark.parametrize("failure", ["copy", "fsync", "replace"])
def test_cross_filesystem_failure_preserves_existing_destination(tmp_path, monkeypatch, failure):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "destination"
    output_dir.mkdir()
    staged = source_dir / "prepared.img"
    staged.write_bytes(b"complete replacement image")
    output = output_dir / "existing.img"
    output.write_bytes(b"previous good image")
    real_copy = image.shutil.copy2
    real_fsync = image.os.fsync
    sync_calls = 0

    def replace(source, destination):
        assert Path(destination) == output
        if Path(source) == staged:
            raise OSError(errno.EXDEV, "simulated cross-filesystem rename")
        assert Path(source).parent == output_dir
        raise OSError(errno.EIO, "simulated replace failure")

    def copy(source, destination):
        assert Path(source) == staged
        assert Path(destination).parent == output_dir
        assert Path(destination) != output
        if failure == "copy":
            Path(destination).write_bytes(staged.read_bytes()[:7])
            raise OSError(errno.ENOSPC, "simulated partial copy failure")
        return real_copy(source, destination)

    def fsync(descriptor):
        nonlocal sync_calls
        sync_calls += 1
        if failure == "fsync" and sync_calls == 2:
            raise OSError(errno.EIO, "simulated fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(image.os, "replace", replace)
    monkeypatch.setattr(image.shutil, "copy2", copy)
    monkeypatch.setattr(image.os, "fsync", fsync)

    with pytest.raises(OSError, match=f"simulated .*{failure} failure"):
        image._finish_temp_output(staged, output)

    assert output.read_bytes() == b"previous good image"
    assert staged.read_bytes() == b"complete replacement image"
    assert list(output_dir.iterdir()) == [output]


@pytest.mark.parametrize("cross_filesystem", [False, True])
def test_image_is_synced_before_atomic_publication(tmp_path, monkeypatch, cross_filesystem):
    staged = tmp_path / "prepared.img"
    staged.write_bytes(b"complete replacement image")
    output = tmp_path / "existing.img"
    output.write_bytes(b"previous good image")
    real_replace = image.os.replace
    real_fsync = image.os.fsync
    actions = []

    def fsync(descriptor):
        assert output.read_bytes() == b"previous good image"
        real_fsync(descriptor)
        actions.append("fsync")

    def replace(source, destination):
        assert actions[-1] == "fsync"
        actions.append("replace")
        if cross_filesystem and Path(source) == staged:
            raise OSError(errno.EXDEV, "simulated cross-filesystem rename")
        assert Path(source).read_bytes() == b"complete replacement image"
        real_replace(source, destination)

    monkeypatch.setattr(image.os, "fsync", fsync)
    monkeypatch.setattr(image.os, "replace", replace)

    assert image._finish_temp_output(staged, output) == output

    assert actions == ["fsync", "replace"] * (2 if cross_filesystem else 1)
    assert output.read_bytes() == b"complete replacement image"
    assert list(tmp_path.iterdir()) == [output]


def test_initial_fsync_failure_does_not_publish_image(tmp_path, monkeypatch):
    staged = tmp_path / "prepared.img"
    staged.write_bytes(b"replacement")
    output = tmp_path / "existing.img"
    output.write_bytes(b"previous good image")

    def fail_sync(descriptor):
        raise OSError(errno.EIO, "simulated fsync failure")

    monkeypatch.setattr(image.os, "fsync", fail_sync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        image._finish_temp_output(staged, output)

    assert output.read_bytes() == b"previous good image"
    assert staged.read_bytes() == b"replacement"


@pytest.mark.parametrize("output_ext", ["img", "hfe"])
def test_drive_capture_stages_raw_and_converted_outputs_beside_destination(
    tmp_path, monkeypatch, output_ext,
):
    output = tmp_path / "new_directory" / f"captured.{output_ext}"
    stages = []
    disk_format = next(item for item in image.DISK_FORMATS if item.key == "ibm.720")

    def read(device, path, size, **kwargs):
        path = Path(path)
        assert path.parent == output.parent
        assert path.suffix == ".img"
        stages.append(path)
        path.write_bytes(b"captured floppy")

    def convert(source, destination, extension, selected_format):
        destination = Path(destination)
        assert destination.parent == output.parent
        assert destination.suffix == ".hfe"
        assert Path(source).read_bytes() == b"captured floppy"
        stages.append(destination)
        destination.write_bytes(b"converted floppy")

    def decode(source, destination, selected_format, **_kwargs):
        assert selected_format == disk_format.key
        assert Path(source).read_bytes() == b"converted floppy"
        Path(destination).write_bytes(b"captured floppy")

    monkeypatch.setattr(image, "_read_block_device", read)
    monkeypatch.setattr(image, "_write_image_direct", convert)
    monkeypatch.setattr(image, "_gw_convert", decode)

    assert image.capture_floppy_drive_image(
        image.FloppyDriveInfo("fake-drive", disk_format.size_bytes), output, disk_format,
    ) == os.fspath(output)

    assert output.read_bytes() == (b"captured floppy" if output_ext == "img" else b"converted floppy")
    assert len(stages) == (1 if output_ext == "img" else 2)
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("fail", [False, True])
def test_session_export_stages_beside_destination_and_cleans_up(tmp_path, fail):
    output = tmp_path / "destination" / "saved.img"
    output.parent.mkdir()
    output.write_bytes(b"previous good image")

    def write(source, destination, output_ext, **kwargs):
        assert Path(destination).parent == output.parent
        assert Path(destination).suffix == ".img"
        Path(destination).write_bytes(b"new image")
        if fail:
            raise OSError(errno.ENOSPC, "simulated image write failure")

    session = SimpleNamespace(temp_dir=tmp_path, _write_image_direct=write)
    if fail:
        with pytest.raises(OSError, match="simulated image write failure"):
            image.FloppyImageSession.write_image(session, "prepared.img", output, "img")
    else:
        image.FloppyImageSession.write_image(session, "prepared.img", output, "img")

    assert output.read_bytes() == (b"previous good image" if fail else b"new image")
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("cross_filesystem", [False, True])
def test_save_as_can_copy_read_only_image(tmp_path, monkeypatch, windows, cross_filesystem):
    source = tmp_path / "read-only.img"
    source.write_bytes(b"read-only source image")
    source.chmod(0o444)
    original_mode = stat.S_IMODE(source.stat().st_mode)
    output = tmp_path / "saved.img"
    session = image.FloppyImageSession.__new__(image.FloppyImageSession)
    real_replace = os.replace
    real_fsync = os.fsync
    replace_calls = 0
    synced = []

    def fsync(descriptor):
        if windows:
            # Model Windows' writable-descriptor requirement on POSIX too.
            position = os.lseek(descriptor, 0, os.SEEK_CUR)
            os.write(descriptor, b"")
            assert os.lseek(descriptor, 0, os.SEEK_CUR) == position
        real_fsync(descriptor)
        synced.append(descriptor)

    def replace(staged, destination):
        nonlocal replace_calls
        replace_calls += 1
        assert stat.S_IMODE(Path(staged).stat().st_mode) == original_mode
        if cross_filesystem and replace_calls == 1:
            raise OSError(errno.EXDEV, "simulated cross-filesystem rename")
        return real_replace(staged, destination)

    # Replace only this module's os reference; globally changing os.name would
    # make pathlib select WindowsPath on a POSIX test host.
    fake_os = SimpleNamespace(**vars(os))
    fake_os.name = "nt" if windows else os.name
    fake_os.fsync = fsync
    fake_os.replace = replace
    monkeypatch.setattr(image, "os", fake_os)
    try:
        session.write_image(source, output, "img")

        assert output.read_bytes() == source.read_bytes() == b"read-only source image"
        assert stat.S_IMODE(source.stat().st_mode) == original_mode
        assert stat.S_IMODE(output.stat().st_mode) == original_mode
        assert len(synced) == (2 if cross_filesystem else 1)
        assert set(tmp_path.iterdir()) == {source, output}
    finally:
        source.chmod(0o644)
        if output.exists():
            output.chmod(0o644)


def test_cross_filesystem_cleanup_failure_preserves_copy_error(tmp_path, monkeypatch):
    staged = tmp_path / "prepared.img"
    staged.write_bytes(b"complete replacement image")
    output = tmp_path / "existing.img"
    output.write_bytes(b"previous good image")
    copy_error = OSError(errno.ENOSPC, "simulated partial copy failure")

    def replace(source, destination):
        raise OSError(errno.EXDEV, "simulated cross-filesystem rename")

    def copy(source, destination):
        Path(destination).write_bytes(b"partial")
        raise copy_error

    def remove(path):
        raise OSError(errno.EIO, "simulated disconnected destination")

    monkeypatch.setattr(image.os, "replace", replace)
    monkeypatch.setattr(image.shutil, "copy2", copy)
    monkeypatch.setattr(image.os, "remove", remove)

    with pytest.raises(OSError) as error:
        image._finish_temp_output(staged, output)

    assert error.value is copy_error
    assert output.read_bytes() == b"previous good image"
