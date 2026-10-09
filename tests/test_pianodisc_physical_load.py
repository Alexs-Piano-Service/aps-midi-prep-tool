"""PianoDisc raw fallback uses local fixture files, never physical devices."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from test_pianodisc_system3 import _simple_song_stream, _system3_image


def _device(tmp_path, *, damaged_song=False):
    songs = [("Physical-load fixture", _simple_song_stream())]
    if damaged_song:
        songs.append(("Incomplete", bytes((0, 0x90, 60, 100))))
    payload = _system3_image(songs)
    path = tmp_path / "regular-file-device.img"
    path.write_bytes(payload)
    return image.FloppyDriveInfo(str(path), len(payload), transport="usb", model="Test fixture"), payload


def _platform(monkeypatch, windows, payload):
    if not windows:
        return
    # Do not alter global os.name, which would affect pathlib and pytest.
    fake_os = SimpleNamespace(**vars(os))
    fake_os.name = "nt"
    monkeypatch.setattr(image, "os", fake_os)

    def no_fast_fat(*_args, **_kwargs):
        raise image.FastFloppyReadError("Not a FAT disk", fallback_allowed=True)

    def read_windows(_path, _size, *, diagnostics, **_kwargs):
        diagnostics.update(exact_capture_completed=True)
        return payload

    monkeypatch.setattr(image, "_read_floppy_device_fast_image", no_fast_fat)
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", read_windows)


@pytest.mark.parametrize("windows", [False, True], ids=["posix-regular-file", "windows-raw-capture"])
@pytest.mark.parametrize("damaged_song", [False, True], ids=["valid-songs", "retained-warning"])
def test_full_raw_floppy_fallback_exposes_pianodisc_midi_without_modifying_source(
    tmp_path, monkeypatch, windows, damaged_song,
):
    drive, payload = _device(tmp_path, damaged_song=damaged_song)
    expected = image.pianodisc_system3.convert_pianodisc_system3_image(payload)
    _platform(monkeypatch, windows, payload)

    session = image.FloppyImageSession.load_floppy(drive)
    temporary = Path(session.temp_dir)
    try:
        assert session.source_path == drive.path
        assert session.source_kind == "floppy_usb"
        assert session.source_name == drive.display_name
        assert session.drive_info is drive
        assert session.read_only_format == "pianodisc_system3"
        assert session.disk_format.key == "pianodisc.system3"
        assert session.disk_format.size_bytes == len(payload)
        assert session.conversion_warnings == expected.errors
        assert bool(session.conversion_warnings) is damaged_song
        assert Path(session.working_img_path).read_bytes() == payload
        assert session.read_diagnostics["stage"] == "complete"
        assert session.read_diagnostics["read_method"] == "exact_raw"
        assert session.read_diagnostics["exact_raw_copy"] is True
        assert session.read_diagnostics["raw_fallback"]["exact_capture_completed"] is True
        assert session.read_diagnostics["selected_capacity_bytes"] == len(payload)
        assert session.read_diagnostics["detected_capacity_bytes"] == len(payload)
        assert session.read_diagnostics["fallback_reason"]
        actual = {entry.path: Path(session.extract_file(entry.path)).read_bytes()
                  for entry in session.list_entries().entries}
        assert actual == {item.filename: item.data for item in expected.files}
    finally:
        session.cleanup()
    assert not temporary.exists()
    assert Path(drive.path).read_bytes() == payload


@pytest.mark.parametrize("windows", [False, True], ids=["posix-regular-file", "windows-raw-capture"])
@pytest.mark.parametrize("cancel_message", ["Decoding PianoDisc", "Opening floppy contents"])
def test_pianodisc_floppy_cancellation_cleans_private_capture(
    tmp_path, monkeypatch, windows, cancel_message,
):
    drive, payload = _device(tmp_path)
    _platform(monkeypatch, windows, payload)
    temporary = []
    original_mkdtemp = image.tempfile.mkdtemp

    def stage(*args, **kwargs):
        kwargs["dir"] = tmp_path
        result = original_mkdtemp(*args, **kwargs)
        temporary.append(Path(result))
        return result

    monkeypatch.setattr(image.tempfile, "mkdtemp", stage)
    cancelled = False

    def progress(_step, _total, message):
        nonlocal cancelled
        if message.startswith(cancel_message):
            cancelled = True

    with pytest.raises(image.FloppyOperationCancelled) as failure:
        image.FloppyImageSession.load_floppy(
            drive, progress_callback=progress, cancel_callback=lambda: cancelled,
        )
    assert failure.value.read_diagnostics["cancelled"] is True
    assert failure.value.read_diagnostics["exact_raw_copy"] is True
    assert temporary and all(not path.exists() for path in temporary)
    assert Path(drive.path).read_bytes() == payload


def test_failed_raw_floppy_capture_never_publishes_pianodisc_session(tmp_path, monkeypatch):
    drive, payload = _device(tmp_path)
    captures = []

    def incomplete(_source, destination, _size, *, diagnostics, **_kwargs):
        path = Path(destination)
        path.write_bytes(payload[:4096])
        captures.append(path)
        diagnostics.update(exact_capture_completed=False)
        raise image.FloppyImageError("Required sector could not be read")

    monkeypatch.setattr(image, "_read_block_device", incomplete)
    monkeypatch.setattr(image.FloppyImageSession, "_from_pianodisc_system3_image",
                        lambda *_args, **_kwargs: pytest.fail("Incomplete capture must not become a session"))
    with pytest.raises(image.FloppyImageError, match="Required sector") as failure:
        image.FloppyImageSession.load_floppy(drive)
    assert failure.value.read_diagnostics["exact_raw_copy"] is False
    assert failure.value.read_diagnostics["stage"] == "raw_read"
    assert captures and all(not path.parent.exists() for path in captures)
    assert Path(drive.path).read_bytes() == payload


def test_pianodisc_sniff_does_not_bypass_required_fat_acquisition_failure(tmp_path, monkeypatch):
    drive, payload = _device(tmp_path)

    def required_fat_failure(*_args, **_kwargs):
        raise image.FastFloppyReadError("Unreadable allocated song sector", fallback_allowed=False)

    monkeypatch.setattr(image, "_read_floppy_device_fast_image", required_fat_failure)
    monkeypatch.setattr(image, "_read_block_device",
                        lambda *_args, **_kwargs: pytest.fail("Non-recoverable FAT errors must not raw-fallback"))
    with pytest.raises(image.FloppyImageError, match="could not finish without losing data"):
        image.FloppyImageSession.load_floppy(drive)
    assert Path(drive.path).read_bytes() == payload
