"""Consent is resolved before a capture replaces its selected output file."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import disk_session_worker as workers, floppy_image as image
from test_smart_pianosoft import _psong_bytes
from test_smart_pianosoft_loaded_list import _midi_bytes


@pytest.fixture
def catalog_image(tmp_path):
    def make(*, smart=False, protected=False):
        source = tmp_path / "source.img"
        image.create_blank_floppy_image(source, image.DISK_FORMAT_BY_KEY["ibm.720"])
        for name, payload in {
            "PSONG.MNG": _psong_bytes([("SONG.MID", "Synthetic song")]),
            "SONG.MID": _midi_bytes(fingerprints=smart),
        }.items():
            host = tmp_path / name
            host.write_bytes(payload)
            image._copy_host_file_into_image(source, host, name)
        clean = source.read_bytes()
        if protected:
            source.write_bytes(bytes(512) + clean[512:])
        return source, clean
    return make


# Exercise the Windows guard on POSIX runners too, but keep Windows file
# synchronization native: it requires a writable handle.
@pytest.mark.parametrize("source_kind", ["floppy_usb", "floppy_usb_recovery", "floppy_gw", "image_convert"])
@pytest.mark.parametrize("approve", [False, True])
@pytest.mark.parametrize("platform", sorted({os.name, "nt"}))
def test_protected_capture_waits_before_replacing_output(
    qt_application, tmp_path, monkeypatch, catalog_image, source_kind, approve, platform,
):
    source, clean = catalog_image(protected=True)
    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": platform}))
    original = source.read_bytes()
    output = tmp_path / ("capture.scp" if source_kind == "floppy_gw" else "capture.img")
    output.write_bytes(b"previous good image")
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    # Readers are mocked, but Windows still requires a drive-letter source
    # separate from the temporary output volume.
    device_path = "A:" if platform == "nt" else str(source)
    selected_source = image.FloppyDriveInfo(device_path, disk_format.size_bytes)

    def read_usb(_source, stage, _size, **_kwargs):
        Path(stage).write_bytes(original)

    def logical_read(_source, stage, _size, **_kwargs):
        Path(stage).write_bytes(clean)
        return image.YamahaRepairResult("Reconstructed boot", True, boot_sector_repaired=True)

    def read_gw(_source, stage, **_kwargs):
        Path(stage).write_bytes(b"Synthetic flux capture")
        # Deliberately no intermediate protection progress marker.
        return {"expected_yamaha_protection": 1}

    def convert(_source, stage, _format, **_kwargs):
        Path(stage).write_bytes(original)
        return "Decoded protected track"

    monkeypatch.setattr(image, "_read_block_device", read_usb)
    monkeypatch.setattr(image, "_read_floppy_device_fast_image", logical_read)
    monkeypatch.setattr(image, "_gw_read_floppy", read_gw)
    monkeypatch.setattr(image, "_gw_convert", convert)
    monkeypatch.setattr(image, "_parse_gw_sector_map", lambda *_: {"expected_yamaha_protection": 1})
    if source_kind == "floppy_gw":
        selected_source = image.GreaseweazleFloppySource("fake-device", "A", disk_format, archival_quality=True)
    elif source_kind == "image_convert":
        selected_source = tmp_path / "source.scp"
        selected_source.write_bytes(b"Synthetic flux source")

    worker = workers.DiskImageCaptureWorker(source_kind, selected_source, output, disk_format=disk_format)
    worker.yamaha_consent_required = True
    requests, successes, cancellations, failures = [], [], [], []

    def answer(request):
        requests.append(request)
        assert output.read_bytes() == b"previous good image"
        assert not list(tmp_path.glob("*.recovery-*.json"))
        request.resolve(approve)

    worker.yamahaConsentRequested.connect(answer)
    worker.captureFinished.connect(successes.append)
    worker.operationCancelled.connect(cancellations.append)
    worker.captureFailed.connect(failures.append)

    worker.run()

    assert not failures, failures
    assert len(requests) == 1
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".aps_capture_*"))
    if approve:
        assert len(successes) == 1 and not cancellations
        assert output.read_bytes() != b"previous good image"
        if source_kind == "floppy_usb_recovery":
            assert Path(successes[0]["diagnostics_path"]).is_file()
    else:
        assert len(cancellations) == 1 and not successes
        assert output.read_bytes() == b"previous good image"
        assert not list(tmp_path.glob("*.recovery-*.json"))


@pytest.mark.parametrize("source_kind", ["floppy_usb", "image_convert"])
@pytest.mark.parametrize("smart", [False, True])
def test_raw_smart_fingerprints_gate_before_conversion_but_ordinary_catalog_does_not(
    qt_application, tmp_path, monkeypatch, catalog_image, source_kind, smart,
):
    source, original = catalog_image(smart=smart)
    output = tmp_path / "capture.img"
    output.write_bytes(b"previous good image")
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    conversions = []

    def read(_source, stage, _size, **_kwargs):
        Path(stage).write_bytes(original)

    def convert(_source, stage, _format, **_kwargs):
        conversions.append(True)
        Path(stage).write_bytes(original)
        return ""

    monkeypatch.setattr(image, "_read_block_device", read)
    monkeypatch.setattr(image, "_gw_convert", convert)
    selected = source if source_kind == "image_convert" else image.FloppyDriveInfo(str(source), len(original))
    worker = workers.DiskImageCaptureWorker(source_kind, selected, output, disk_format=disk_format)
    worker.yamaha_consent_required = True
    requests, successes, cancellations, failures = [], [], [], []

    def decline(request):
        requests.append(request)
        assert not conversions
        assert output.read_bytes() == b"previous good image"
        request.resolve(False)

    worker.yamahaConsentRequested.connect(decline)
    worker.captureFinished.connect(successes.append)
    worker.operationCancelled.connect(cancellations.append)
    worker.captureFailed.connect(failures.append)
    worker.run()

    assert not failures
    assert source.read_bytes() == original
    if smart:
        assert len(requests) == len(cancellations) == 1
        assert not successes and not conversions
        assert output.read_bytes() == b"previous good image"
    else:
        assert not requests and not cancellations
        assert len(successes) == 1
        assert output.read_bytes() == original


@pytest.mark.parametrize("approve", [False, True])
def test_archival_capture_only_checks_sector_map_before_delivery(qt_application, tmp_path, monkeypatch, approve):
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    source = image.GreaseweazleFloppySource("fake-device", "A", disk_format, archival_quality=True)
    capture_dir = tmp_path / "private-capture"
    capture_dir.mkdir()
    capture_path = capture_dir / "source.scp"
    capture_path.write_bytes(b"Synthetic flux capture")
    capture = image.GreaseweazleCapture(source, str(capture_path), str(capture_dir),
                                      sector_map={"expected_yamaha_protection": 1})
    monkeypatch.setattr(image.FloppyImageSession, "capture_greaseweazle_archival", lambda *_a, **_k: capture)
    worker = workers.DiskSessionLoadWorker("floppy_gw_capture_only", source)
    worker.yamaha_consent_required = True
    requests, deliveries, cancellations = [], [], []

    def answer(request):
        requests.append(request)
        assert not deliveries
        assert capture_path.is_file()
        request.resolve(approve)

    worker.yamahaConsentRequested.connect(answer)
    worker.captureReady.connect(deliveries.append)
    worker.operationCancelled.connect(cancellations.append)
    worker.run()

    assert len(requests) == 1
    if approve:
        assert len(deliveries) == 1 and not cancellations
        assert capture_path.is_file()
        capture.cleanup()
    else:
        assert len(cancellations) == 1 and not deliveries
        assert not capture_dir.exists()
