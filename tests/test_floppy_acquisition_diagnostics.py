"""Ordinary floppy failures retain both targeted and exact-read evidence."""

import ctypes
import os
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


def test_raw_fallback_error_retains_fast_failure_and_read_ranges(monkeypatch):
    drive = image.FloppyDriveInfo("A:", 720 * 1024)

    def fast_read(*_args, diagnostics=None, **_kwargs):
        if diagnostics is not None:
            diagnostics.update(
                boot_probe={"status": "read_success", "offset_bytes": 0, "length_bytes": 512},
                successful_ranges=[{"offset_bytes": 0, "length_bytes": 512}],
                layout_recognized=False,
            )
        raise image.FastFloppyReadError("Readable boot sector has no supported layout", fallback_allowed=True)

    def raw_read(*_args, diagnostics=None, **_kwargs):
        error = image.FloppyImageError("Le média disque n’est pas reconnu.", winerror=1785)
        error.read_diagnostics = {
            "failed_read_offset_bytes": 65536,
            "failed_read_length_bytes": 512,
            "successful_ranges": [{"offset_bytes": 0, "length_bytes": 65536}],
            "final_failed_read": {
                "offset_bytes": 65536, "length_bytes": 512, "winerror": 1785,
                "message": str(error),
            },
        }
        raise error

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_read_floppy_device_fast_image", fast_read)
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", raw_read)
    with pytest.raises(image.FloppyImageError) as caught:
        image.FloppyImageSession.load_floppy(drive)
    record = caught.value.read_diagnostics
    assert record["selected_capacity_bytes"] == drive.size_bytes
    assert record["source_path"] == drive.path
    assert record["first_error"]["message"] == "Readable boot sector has no supported layout"
    assert record["fallback_reason"] == record["first_error"]["message"]
    assert record["fast_path"]["boot_probe"]["status"] == "read_success"
    assert record["fast_path"]["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": 512}]
    assert record["final_error"]["winerror"] == 1785
    assert record["raw_fallback"]["final_failed_read"]["offset_bytes"] == 65536
    assert record["raw_fallback"]["failed_read_length_bytes"] == 512
    assert record["raw_fallback"]["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": 65536}]


@pytest.mark.parametrize("readable", [False, True])
def test_boot_probe_distinguishes_failed_io_from_readable_unknown_bytes(readable):
    class Device:
        def read_at(self, *_args):
            if readable:
                return bytes(512)
            error = OSError("Localized disk error")
            error.winerror = 1785
            raise error

    diagnostics = {}
    data = image._try_read_device_exact(Device(), 0, 512, diagnostics=diagnostics)
    assert diagnostics["boot_probe"]["status"] == ("read_success" if readable else "read_failed")
    assert image._geometry_from_boot_sector(data or bytes(512)) is None
    if readable:
        assert data == bytes(512)
    else:
        assert data is None
        assert diagnostics["boot_probe"]["error"]["winerror"] == 1785


def test_diagnostic_device_records_only_actual_partial_bytes():
    class Device:
        def read_at(self, offset, size, _label):
            if offset == 0:
                return b"A" * 512
            raise OSError(5, "input/output error")

    diagnostics = {}
    device = image._DiagnosticReadDevice(Device(), diagnostics)
    with pytest.raises(image.FloppyImageError):
        image._read_device_exact(device, 0, 1024, "song")
    assert diagnostics["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": 512}]
    failed = diagnostics["failed_requests"][0]
    assert (failed["offset_bytes"], failed["length_bytes"], failed["errno"]) == (512, 512, 5)
    assert diagnostics["read_calls"] == 2


@pytest.mark.parametrize("completed", [False, True], ids=["submission", "completion"])
@pytest.mark.parametrize("error_code", [1785, 38], ids=["unrecognized-media", "native-eof"])
def test_native_windows_errors_retain_codes_with_localized_messages(monkeypatch, completed, error_code):
    class Overlapped(ctypes.Structure):
        _fields_ = [("Offset", ctypes.c_ulong), ("OffsetHigh", ctypes.c_ulong), ("hEvent", ctypes.c_void_p)]

    closed = []
    handle = object.__new__(image._WindowsRecoveryVolumeHandle)
    handle.handle = 10
    handle._ctypes = SimpleNamespace(**{
        **vars(ctypes), "get_last_error": lambda: error_code,
        "FormatError": lambda _code: "Le média disque n’est pas reconnu.",
    })
    handle._kernel32 = SimpleNamespace(
        CreateEventW=lambda *_args: 17,
        ReadFile=lambda *_args: completed,
        CloseHandle=closed.append,
    )
    monkeypatch.setattr(image._WindowsRecoveryVolumeHandle, "_overlapped_type", Overlapped)
    monkeypatch.setattr(handle, "_overlapped_result", lambda _overlapped: (True, False, 0, error_code))
    diagnostics = {}
    device = image._DiagnosticReadDevice(handle, diagnostics)
    with pytest.raises(image.FloppyImageError, match="Le média disque") as caught:
        image._read_device_exact(device, 1024, 512, "floppy image")
    assert caught.value.winerror == error_code
    assert diagnostics["failed_requests"][0]["winerror"] == error_code
    assert closed == [17]


def test_raw_eof_does_not_inherit_the_fast_reader_windows_error(monkeypatch):
    def fast_read(*_args, **_kwargs):
        try:
            raise image.FloppyImageError("Fast Windows request failed", winerror=23)
        except image.FloppyImageError as exc:
            raise image.FastFloppyReadError("No supported layout", fallback_allowed=True) from exc

    def raw_read(*_args, **_kwargs):
        raise image.FloppyImageError("The drive stopped returning bytes")

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_read_floppy_device_fast_image", fast_read)
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", raw_read)
    with pytest.raises(image.FloppyImageError) as caught:
        image.FloppyImageSession.load_floppy(image.FloppyDriveInfo("A:", 720 * 1024))
    diagnostics = caught.value.read_diagnostics
    assert diagnostics["first_error"]["winerror"] == 23
    assert diagnostics["final_error"]["message"] == "The drive stopped returning bytes"
    assert "winerror" not in diagnostics["final_error"]


@pytest.mark.parametrize("stage", ["fast", "raw"])
def test_cancelled_acquisition_retains_read_evidence_without_reporting_failure(monkeypatch, stage):
    def fast_read(*_args, diagnostics=None, **_kwargs):
        diagnostics["successful_ranges"] = [{"offset_bytes": 0, "length_bytes": 512}]
        if stage == "fast":
            raise image.FloppyOperationCancelled("Operation cancelled.")
        raise image.FastFloppyReadError("Unsupported layout", fallback_allowed=True)

    def raw_read(*_args, diagnostics=None, **_kwargs):
        diagnostics["successful_ranges"] = [{"offset_bytes": 0, "length_bytes": 65536}]
        error = image.FloppyOperationCancelled("Operation cancelled.")
        error.read_diagnostics = diagnostics
        raise error

    monkeypatch.setattr(image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(image, "_read_floppy_device_fast_image", fast_read)
    monkeypatch.setattr(image, "_read_windows_block_device_bytes", raw_read)
    with pytest.raises(image.FloppyOperationCancelled) as caught:
        image.FloppyImageSession.load_floppy(image.FloppyDriveInfo("A:", 720 * 1024))
    record = caught.value.read_diagnostics
    assert record["cancelled"]
    assert record["selected_capacity_bytes"] == 720 * 1024
    assert record["fast_path"]["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": 512}]
    assert "final_error" not in record
    if stage == "raw":
        assert record["first_error"]["message"] == "Unsupported layout"
        assert record["raw_fallback"]["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": 65536}]
