"""Physical file comparisons must not read unused floppy sectors."""

import os
import shutil

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux block-device save path")


@pytest.fixture
def floppy_save(tmp_path, monkeypatch):
    target = tmp_path / "floppy.img"
    source = tmp_path / "prepared.img"
    song = tmp_path / "song.mid"
    image.create_blank_floppy_image(target, image.DISK_FORMAT_BY_KEY["ibm.720"])
    for name, payload in (("CHANGE.MID", b"old recording"), ("KEEP.MID", b"unchanged recording")):
        song.write_bytes(payload)
        image._copy_host_file_into_image(target, song, name)
    shutil.copyfile(target, source)
    prepared = bytearray(source.read_bytes())
    offset = prepared.index(b"old recording")
    prepared[offset:offset + len(b"old recording")] = b"new recording"
    source.write_bytes(prepared)
    session = image.FloppyImageSession.load(source)
    native_reader = image._read_fat12_file_bytes

    def read_image(path, name):
        assert os.fspath(path) != str(target), "File comparison read the entire physical disk"
        return native_reader(path, name)

    monkeypatch.setattr(image, "_is_block_device_path", lambda path: os.fspath(path) == str(target))
    monkeypatch.setattr(image, "_read_fat12_file_bytes", read_image)
    try:
        yield session, str(source), str(target), native_reader
    finally:
        session.cleanup()


def test_save_compares_file_contents_without_reading_entire_device(floppy_save, monkeypatch):
    session, source, target, read_file = floppy_save
    commands = []
    progress = []
    run = session._run_mtools

    def record(args, *args_rest, **kwargs):
        commands.append(args)
        return run(args, *args_rest, **kwargs)

    monkeypatch.setattr(session, "_run_mtools", record)
    session._sync_modified_image_files_to_floppy_drive(
        source, target, progress_callback=lambda step, total, message: progress.append(message),
    )

    assert read_file(target, "CHANGE.MID") == b"new recording"
    assert read_file(target, "KEEP.MID") == b"unchanged recording"
    assert "Keeping unchanged KEEP.MID on floppy..." in progress
    assert "Removing old CHANGE.MID from floppy..." in progress
    assert not any(os.path.basename(args[0]) == "mdel" and args[-1] == "::/KEEP.MID"
                   for args in commands)


@pytest.mark.parametrize("error", [image.FloppyOperationCancelled, image.FloppyImageError])
def test_failed_or_cancelled_comparison_stops_before_writing(floppy_save, monkeypatch, error):
    session, source, target, _read_file = floppy_save
    with open(target, "rb") as handle:
        before = handle.read()
    commands = []

    def fail(args, message, cancel_callback=None):
        commands.append(args)
        raise error("Read stopped")

    monkeypatch.setattr(session, "_run_mtools", fail)
    with pytest.raises(error, match="Read stopped"):
        session._sync_modified_image_files_to_floppy_drive(source, target)

    assert len(commands) == 1
    assert os.path.basename(commands[0][0]) == "mcopy"
    assert commands[0][2] == target
    with open(target, "rb") as handle:
        assert handle.read() == before
