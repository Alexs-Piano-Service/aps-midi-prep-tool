import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import disk_session_worker, emulator_image_builder, floppy_image
from aps_midi_prep_tool_app.helpers import file_batch


@pytest.fixture
def prepared_image(tmp_path):
    disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    image = tmp_path / "prepared.img"
    song = tmp_path / "SONG.MID"
    song.write_bytes(b"original song payload")
    floppy_image.create_blank_floppy_image(image, disk_format)
    floppy_image._copy_host_file_into_image(image, song, "SONG.MID")
    return image, disk_format


def _damage_song(path):
    payload = bytearray(Path(path).read_bytes())
    index = payload.index(b"original song payload")
    payload[index] ^= 1
    Path(path).write_bytes(payload)


def test_verification_reopens_payload_and_rejects_same_name_same_size_damage(tmp_path, prepared_image):
    prepared, disk_format = prepared_image
    delivered = tmp_path / "delivered.img"
    shutil.copy2(prepared, delivered)

    result = floppy_image.verify_image_payloads(delivered, prepared, disk_format)

    assert result["confidence"] == "contents_verified"
    assert result["files_verified"] == 1
    assert result["hardware_tested"] is False
    _damage_song(delivered)
    with pytest.raises(floppy_image.FloppyImageError, match="contents differ for SONG.MID"):
        floppy_image.verify_image_payloads(delivered, prepared, disk_format)


def test_hfe_verification_decodes_delivered_container(tmp_path, prepared_image, monkeypatch):
    prepared, disk_format = prepared_image
    delivered = tmp_path / "delivered.hfe"
    delivered.write_bytes(b"HFE container")
    conversions = []

    def decode(source, output, format_key, **kwargs):
        assert Path(source).read_bytes() == b"HFE container"
        conversions.append((source, format_key))
        shutil.copy2(prepared, output)

    monkeypatch.setattr(floppy_image, "_gw_convert", decode)

    result = floppy_image.verify_image_payloads(delivered, prepared, disk_format)

    assert result["files_verified"] == 1
    assert conversions == [(delivered, disk_format.key)]


def test_failed_final_verification_restores_existing_delivery(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    title = b"Delivery Song"
    track = b"\x00\xff\x03" + bytes([len(title)]) + title + b"\x00\xff\x2f\x00"
    (source / "SONG.MID").write_bytes(
        b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )
    output = tmp_path / "delivery"
    output.mkdir()
    previous = output / "DSKA0000.img"
    previous.write_bytes(b"previous customer delivery")
    real_write = file_batch.atomic_write_bytes

    def corrupt_delivery(output_path, prepared_payload, **kwargs):
        real_write(output_path, prepared_payload, **kwargs)
        if title in prepared_payload:
            payload = bytearray(Path(output_path).read_bytes())
            payload[payload.index(title)] ^= 1
            Path(output_path).write_bytes(payload)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", corrupt_delivery)

    with pytest.raises(floppy_image.FloppyImageError, match="contents differ"):
        emulator_image_builder.build_emulator_disk_images(
            source, output, output_content="midi", output_ext="img", overwrite_existing=True,
        )

    assert previous.read_bytes() == b"previous customer delivery"
    assert list(output.iterdir()) == [previous]


@pytest.mark.parametrize("verify", [False, True])
def test_physical_write_readback_is_optional_and_reports_confidence(tmp_path, prepared_image, monkeypatch, verify):
    prepared, disk_format = prepared_image
    modified = tmp_path / "modified.img"
    shutil.copy2(prepared, modified)
    target = floppy_image.FloppyDriveInfo(str(tmp_path / "fake-drive"), disk_format.size_bytes)
    session = SimpleNamespace(create_modified_image=lambda **kwargs: str(modified), disk_format=disk_format)
    reads = []
    monkeypatch.setattr(floppy_image, "_write_block_device", lambda source, target, **kwargs: shutil.copy2(source, target))

    def read(source, output, size, **kwargs):
        reads.append(source)
        shutil.copy2(source, output)

    monkeypatch.setattr(floppy_image, "_read_block_device", read)

    floppy_image.FloppyImageSession.write_to_floppy_target(
        session, "floppy_usb", target, verify_after_write=verify,
    )

    assert reads == ([target.path] if verify else [])
    assert session.last_write_verification["confidence"] == ("contents_verified" if verify else "written")
    assert session.last_write_verification["hardware_tested"] is False


def test_physical_readback_failure_does_not_report_verified(tmp_path, prepared_image, monkeypatch):
    prepared, disk_format = prepared_image
    modified = tmp_path / "modified.img"
    shutil.copy2(prepared, modified)
    session = SimpleNamespace(create_modified_image=lambda **kwargs: str(modified), disk_format=disk_format)
    target = floppy_image.FloppyDriveInfo("fake-drive", disk_format.size_bytes)
    monkeypatch.setattr(floppy_image, "_write_block_device", lambda *args, **kwargs: None)

    def bad_read(source, output, size, **kwargs):
        shutil.copy2(prepared, output)
        _damage_song(output)

    monkeypatch.setattr(floppy_image, "_read_block_device", bad_read)

    with pytest.raises(floppy_image.FloppyImageError, match="Files were written, but readback verification failed"):
        floppy_image.FloppyImageSession.write_to_floppy_target(
            session, "floppy_usb", target, verify_after_write=True,
        )

    assert session.last_write_verification["confidence"] == "written"
    assert not modified.exists()


def test_write_worker_forwards_readback_choice():
    calls = []
    session = SimpleNamespace(write_to_floppy_target=lambda *args, **kwargs: calls.append(kwargs))
    worker = disk_session_worker.DiskSessionWriteTargetWorker(session, "floppy_usb", object(), {}, verify_after_write=True)

    worker.run()

    assert calls[0]["verify_after_write"] is True


@pytest.mark.parametrize("verify", [False, True])
def test_commit_to_open_physical_floppy_honors_readback_choice(tmp_path, prepared_image, monkeypatch, verify):
    prepared, disk_format = prepared_image
    modified = tmp_path / "commit-modified.img"
    working = tmp_path / "working.img"
    target = floppy_image.FloppyDriveInfo(str(tmp_path / "fake-device"), disk_format.size_bytes)
    shutil.copy2(prepared, modified)
    session = SimpleNamespace(
        source_kind="floppy_usb", source_path=target.path, drive_info=target,
        disk_format=disk_format, working_img_path=str(working), _extracted_files={},
        create_modified_image=lambda **kwargs: str(modified),
        _sync_modified_image_files_to_floppy_drive=lambda source, target, **kwargs: shutil.copy2(source, target),
    )
    reads = []

    def read(source, output, size, **kwargs):
        reads.append(source)
        shutil.copy2(source, output)

    monkeypatch.setattr(floppy_image, "_read_block_device", read)

    floppy_image.FloppyImageSession.commit_to_source(session, verify_after_write=verify)

    assert reads == ([target.path] if verify else [])
    assert working.read_bytes() == prepared.read_bytes()
    assert session.last_write_verification["confidence"] == ("contents_verified" if verify else "written")


@pytest.mark.parametrize("corrupt", [False, True])
def test_unsupported_flush_forces_independent_readback(tmp_path, prepared_image, monkeypatch, corrupt):
    prepared, disk_format = prepared_image
    modified = tmp_path / "modified.img"
    shutil.copyfile(prepared, modified)
    session = SimpleNamespace(create_modified_image=lambda **kw: str(modified), disk_format=disk_format)
    target = floppy_image.FloppyDriveInfo("fake-drive", disk_format.size_bytes)
    monkeypatch.setattr(floppy_image, "_write_block_device", lambda *a, **kw: {"confidence": "flush_unconfirmed", "winerror": 1})
    reads = []
    def read(source, output, size, **kw):
        reads.append(source)
        shutil.copyfile(prepared, output)
        if corrupt:
            _damage_song(output)
    monkeypatch.setattr(floppy_image, "_read_block_device", read)
    if corrupt:
        with pytest.raises(floppy_image.FloppyImageError, match="readback verification failed"):
            floppy_image.FloppyImageSession.write_to_floppy_target(session, "floppy_usb", target)
        assert session.last_write_verification["confidence"] == "flush_unconfirmed"
        assert session.last_floppy_save_diagnostics["stage"] == "readback"
    else:
        floppy_image.FloppyImageSession.write_to_floppy_target(session, "floppy_usb", target)
        assert session.last_write_verification["confidence"] == "contents_verified"
    assert reads == [target.path]


def test_partial_raw_failure_updates_session_write_state(tmp_path, prepared_image, monkeypatch):
    prepared, disk_format = prepared_image
    modified = tmp_path / "modified.img"
    shutil.copyfile(prepared, modified)
    session = SimpleNamespace(create_modified_image=lambda **kw: str(modified), disk_format=disk_format)
    target = floppy_image.FloppyDriveInfo("fake-drive", disk_format.size_bytes)
    def write(*a, **kw):
        error = floppy_image.FloppyImageError("[WinError 21] not ready")
        error.diagnostics = {"stage": "flush", "target_mutation_attempted": True, "bytes_written": disk_format.size_bytes, "winerror": 21}
        raise error
    monkeypatch.setattr(floppy_image, "_write_block_device", write)
    with pytest.raises(floppy_image.FloppyImageError, match="21"):
        floppy_image.FloppyImageSession.write_to_floppy_target(session, "floppy_usb", target)
    assert session.last_write_verification["confidence"] == "partial_or_uncertain"
    assert session.last_floppy_save_diagnostics["stage"] == "flush"
    assert session.last_floppy_save_diagnostics["bytes_written"] == disk_format.size_bytes


@pytest.mark.parametrize("corrupt", [False, True])
def test_usb_format_verifies_physical_bytes_even_when_prepared_image_is_valid(tmp_path, monkeypatch, corrupt):
    disk_format = next(item for item in floppy_image.DISK_FORMATS if item.key == "ibm.720")
    target_path = tmp_path / "target.img"
    target = floppy_image.FloppyDriveInfo(str(target_path), disk_format.size_bytes)
    monkeypatch.setattr(floppy_image.FloppyImageSession, "_try_prepare_existing_usb_floppy", classmethod(lambda *a, **kw: None))
    def write(source, destination, **kw):
        shutil.copyfile(source, destination)
        if corrupt:
            data = bytearray(Path(destination).read_bytes())
            data[100] ^= 1  # Still a readable blank FAT image; bytes differ.
            Path(destination).write_bytes(data)
    monkeypatch.setattr(floppy_image, "_write_block_device", write)
    reads = []
    def read(source, destination, size, **kw):
        reads.append(source)
        shutil.copyfile(source, destination)
    monkeypatch.setattr(floppy_image, "_read_block_device", read)
    if corrupt:
        with pytest.raises(floppy_image.FloppyImageError, match="physical floppy differs"):
            floppy_image.FloppyImageSession.format_usb_floppy(target, disk_format)
    else:
        session = floppy_image.FloppyImageSession.format_usb_floppy(target, disk_format)
        try:
            assert session.last_write_verification["confidence"] == "contents_verified"
        finally:
            session.cleanup()
    assert reads == [target.path]
