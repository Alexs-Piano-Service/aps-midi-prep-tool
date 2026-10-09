"""Reject an oversized prepared image before any physical USB write."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _prepared_session(tmp_path, size, *, declared_format="ibm.1440"):
    modified = tmp_path / "prepared.img"
    modified.write_bytes(b"P" * size)
    session = SimpleNamespace(
        create_modified_image=lambda **_kwargs: str(modified),
        disk_format=image.DISK_FORMAT_BY_KEY[declared_format],
    )
    return session, modified


@pytest.mark.parametrize("verify", [False, True])
@pytest.mark.parametrize("declared_format", ["ibm.1440", "ibm.720"])
def test_oversized_prepared_bytes_never_reach_writer_or_verifier(tmp_path, monkeypatch, verify, declared_format):
    session, modified = _prepared_session(tmp_path, 1_474_560, declared_format=declared_format)
    target = image.FloppyDriveInfo("NO_PHYSICAL_DEVICE", 737_280)
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        pytest.fail("Oversized prepared image reached physical I/O")

    monkeypatch.setattr(image, "_write_block_device", forbidden)
    monkeypatch.setattr(image, "_verify_physical_floppy_contents", forbidden)

    with pytest.raises(image.FloppyImageError, match="exceeds.*reported.*capacity") as caught:
        image.FloppyImageSession.write_to_floppy_target(
            session, "floppy_usb", target, file_level=False, verify_after_write=verify,
        )

    assert not calls
    assert "1,474,560" in str(caught.value) and "737,280" in str(caught.value)
    assert session.last_write_verification["confidence"] == "not_written"
    assert session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    assert session.last_floppy_save_diagnostics["stage"] == "preflight"
    assert not modified.exists()


@pytest.mark.parametrize("verify", [False, True])
@pytest.mark.parametrize("prepared_size,capacity", [
    (737_280, 737_280),
    (737_280, 1_474_560),
    (1_474_560, 1_474_560),
    (1_474_560, 0),
    (1_474_560, -1),
])
def test_equal_smaller_and_unknown_capacity_raw_writes_retain_existing_behavior(
    tmp_path, monkeypatch, verify, prepared_size, capacity,
):
    session, modified = _prepared_session(tmp_path, prepared_size)
    target = image.FloppyDriveInfo("NO_PHYSICAL_DEVICE", capacity)
    calls = []

    def write(path, destination, **_kwargs):
        assert Path(path).stat().st_size == prepared_size
        assert destination == target.path
        calls.append("write")

    def readback(path, kind, selected, disk_format, **_kwargs):
        assert Path(path).stat().st_size == prepared_size
        assert kind == "floppy_usb" and selected == target and disk_format == session.disk_format
        calls.append("verify")
        return {"confidence": "contents_verified", "hardware_tested": False}

    monkeypatch.setattr(image, "_write_block_device", write)
    monkeypatch.setattr(image, "_verify_physical_floppy_contents", readback)

    image.FloppyImageSession.write_to_floppy_target(
        session, "floppy_usb", target, file_level=False, verify_after_write=verify,
    )

    assert calls == (["write", "verify"] if verify else ["write"])
    assert session.last_write_verification["confidence"] == ("contents_verified" if verify else "written")
    assert not modified.exists()


@pytest.fixture(params=[False, True], ids=["native-stat", "stat-without-rdev"])
def file_level_save(tmp_path, monkeypatch, request):
    monkeypatch.setenv("APS_FLOPPY_SAVE_RECOVERY_DIR", str(tmp_path / "recovery"))
    source = tmp_path / "source-hd.img"
    destination = tmp_path / "target-dd.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[2], "SOURCE")
    image._create_blank_fat12_image_from_layout(destination, image._PROTECTED_FAT12_LAYOUTS[0], "TARGET")
    payload = b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk\0\0\0\x04\0\xff\x2f\0"
    song = tmp_path / "SONG.MID"
    song.write_bytes(payload)
    image._copy_host_file_into_image(source, song, "SONG.MID")
    target = image.FloppyDriveInfo(str(destination), 737_280)
    monkeypatch.setattr(image, "_write_block_device", lambda *_a, **_k: pytest.fail("File-level save must not use raw writing"))
    if request.param:
        fstat = image.os.fstat

        def without_rdev(descriptor):
            info = fstat(descriptor)
            return SimpleNamespace(**{
                name: getattr(info, name) for name in dir(info)
                if name.startswith("st_") and name != "st_rdev"
            })

        # Exercise Windows stat fields on Linux too, without changing global os.
        monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(image.os), "fstat": without_rdev}))
    session = image.FloppyImageSession.load(source)
    try:
        yield session, source, destination, target, payload
    finally:
        session.cleanup()


@pytest.mark.parametrize("verify", [False, True])
def test_file_level_hd_source_fits_dd_target_without_changing_geometry(file_level_save, verify):
    session, source, destination, target, payload = file_level_save
    before = source.read_bytes()

    session.write_to_floppy_target("floppy_usb", target, file_level=True, verify_after_write=verify)

    assert destination.stat().st_size == 737_280
    assert image._geometry_from_boot_sector(destination.read_bytes()[:512]).total_size == 737_280
    assert image._read_fat12_file_bytes(destination, "SONG.MID") == payload
    assert source.read_bytes() == before
    assert session.last_write_verification["confidence"] == ("contents_verified" if verify else "written")


def test_file_level_save_stops_before_writing_if_target_media_changes(file_level_save, monkeypatch):
    session, source, destination, target, _payload = file_level_save
    before = source.read_bytes()
    changed_disk = []
    extract = session._extract_from_image

    def change_media_after_extraction(*args, **kwargs):
        result = extract(*args, **kwargs)
        if not changed_disk:
            data = bytearray(destination.read_bytes())
            data[39] ^= 1  # Change the FAT volume serial while keeping a valid filesystem.
            destination.write_bytes(data)
            changed_disk.append(bytes(data))
        return result

    monkeypatch.setattr(session, "_extract_from_image", change_media_after_extraction)
    with pytest.raises(image.FloppyImageError, match="target floppy changed"):
        session.write_to_floppy_target("floppy_usb", target, file_level=True)

    assert changed_disk
    assert destination.read_bytes() == changed_disk[0]
    assert source.read_bytes() == before
    assert session.last_floppy_save_diagnostics["target_mutation_attempted"] is False
    assert session.last_write_verification["confidence"] == "not_written"
