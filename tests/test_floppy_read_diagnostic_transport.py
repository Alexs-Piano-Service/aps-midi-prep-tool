"""Normal floppy acquisition records survive worker signals and support reports."""

from copy import deepcopy
import os
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QCheckBox, QDialog

from aps_midi_prep_tool_app import disk_session_worker, floppy_image, main_window
from aps_midi_prep_tool_app.floppy_image import (
    BlankDiskImageError, FloppyImageError, GreaseweazleConversionError,
)
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_inspection_staging import window


@pytest.fixture
def diagnostics():
    return {
        "source_path": "A:", "selected_capacity_bytes": 737280,
        "fast_path": {
            "boot_probe": {"status": "read_success", "offset_bytes": 0, "length_bytes": 512},
            "detected_capacity_bytes": 737280,
            "successful_ranges": [{"offset_bytes": 0, "length_bytes": 512}],
        },
        "fallback_reason": "Root contains an unsupported subdirectory: SONGS",
        "first_error": {"message": "Root contains an unsupported subdirectory: SONGS"},
        "raw_fallback": {
            "successful_ranges": [{"offset_bytes": 0, "length_bytes": 65536}],
            "failed_requests": [{
                "offset_bytes": 65536, "length_bytes": 65536,
                "message": "Le média disque n’est pas reconnu", "winerror": 1785,
            }],
            "exact_capture_completed": False,
        },
        "final_error": {"message": "Could not finish reading floppy image", "winerror": 1785},
    }


@pytest.mark.parametrize("load_kind, method", [("floppy_usb", "load_floppy"), ("floppy_gw", "load_greaseweazle")])
def test_load_worker_emits_structured_acquisition_failure(monkeypatch, diagnostics, load_kind, method):
    error = FloppyImageError("Could not finish reading floppy image", winerror=1785)
    error.read_diagnostics = diagnostics

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(disk_session_worker.FloppyImageSession, method, fail)
    worker = disk_session_worker.DiskSessionLoadWorker(load_kind, "A:")
    detailed, plain = [], []
    worker.loadFailedWithDetails.connect(detailed.append)
    worker.loadFailed.connect(plain.append)
    worker.run()
    assert plain == []
    assert detailed == [{
        "type": "floppy_read", "message": str(error), "source": "A:",
        "read_diagnostics": diagnostics,
    }]
    assert worker.read_diagnostics == diagnostics


@pytest.mark.parametrize("special", ["blank", "greaseweazle"])
def test_read_diagnostics_do_not_change_specialized_failure_type(monkeypatch, diagnostics, special):
    if special == "blank":
        error = BlankDiskImageError("Blank image", source_path="A:")
        expected_type = "blank_disk_image"
    else:
        error = GreaseweazleConversionError("Conversion failed", reason="format_mismatch")
        expected_type = "greaseweazle_conversion"
    error.read_diagnostics = diagnostics

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", fail)
    worker = disk_session_worker.DiskSessionLoadWorker("floppy_usb", "A:")
    detailed = []
    worker.loadFailedWithDetails.connect(detailed.append)
    worker.run()
    assert detailed[0]["type"] == expected_type
    assert detailed[0]["read_diagnostics"] == diagnostics
    if special == "greaseweazle":
        assert detailed[0]["reason"] == "format_mismatch"


@pytest.mark.parametrize("result", ["success", "listing_failure", "cancelled"])
def test_load_worker_retains_session_record_through_listing_and_cancellation(monkeypatch, diagnostics, result):
    listing = object()
    cleaned = []

    def list_entries():
        if result == "listing_failure":
            raise FloppyImageError("Could not list the acquired image")
        return listing

    session = SimpleNamespace(
        read_diagnostics=diagnostics, list_entries=list_entries,
        cleanup=lambda: cleaned.append(True),
    )
    monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", lambda *_a, **_k: session)
    worker = disk_session_worker.DiskSessionLoadWorker("floppy_usb", "A:", final_message="Opening acquired image")
    loaded, failed, cancelled = [], [], []
    worker.sessionLoaded.connect(lambda value, entries: loaded.append((value, entries)))
    worker.loadFailedWithDetails.connect(failed.append)
    worker.operationCancelled.connect(cancelled.append)
    if result == "cancelled":
        worker.progressChanged.connect(lambda *_args: worker.cancel())
    worker.run()
    assert worker.read_diagnostics == diagnostics
    if result == "success":
        assert loaded == [(session, listing)] and not cleaned
    elif result == "listing_failure":
        assert failed[0]["type"] == "floppy_read"
        assert failed[0]["read_diagnostics"] == diagnostics
        assert cleaned == [True]
    else:
        assert cancelled and not loaded and not failed
        assert cleaned == [True]


@pytest.mark.parametrize("include_diagnostics", [False, True])
def test_final_report_retains_both_acquisition_failures_and_numeric_ranges(
    window, monkeypatch, diagnostics, include_diagnostics,
):
    error = FloppyImageError("Le média disque n’est pas reconnu", winerror=1785)
    error.read_diagnostics = {**diagnostics, "raw_sector_bytes": b"private disk contents"}

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(disk_session_worker.FloppyImageSession, "load_floppy", fail)
    worker = disk_session_worker.DiskSessionLoadWorker("floppy_usb", "A:")
    window.diskLoadWorker = worker
    window.diskLoadContext = {"load_kind": "floppy_usb", "source": "A:", "failure_title": "Floppy Load Failed"}
    window.diskLoadFailureTitle = "Floppy Load Failed"
    worker.loadFailedWithDetails.connect(window._on_disk_load_failure_with_details)
    worker.run()
    errors = []
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_a, **_k: main_window.QMessageBox.No)
    monkeypatch.setattr(window, "_show_operation_error", lambda *args: errors.append(args))
    window._offer_disk_recovery(window.pendingDiskRecoveryRequest)
    assert errors == [("Floppy Load Failed", "The disk or image could not be opened", str(error))]
    assert window.diskLoadContext == {}
    payload = window._build_bug_report_payload(
        summary="Floppy Load Failed", description=str(error), contact="", include_logs=False,
        include_floppy_recovery_diagnostics=include_diagnostics,
    )
    if include_diagnostics:
        assert payload["context"]["floppy_read"] == diagnostics
        record = payload["context"]["floppy_read"]
        assert "subdirectory" in record["first_error"]["message"]
        assert record["final_error"]["winerror"] == 1785
        assert record["raw_fallback"]["failed_requests"][0]["offset_bytes"] == 65536
        assert record["raw_fallback"]["failed_requests"][0]["length_bytes"] == 65536
        assert record["selected_capacity_bytes"] == record["fast_path"]["detected_capacity_bytes"] == 737280
    else:
        assert "floppy_read" not in payload["context"]
    assert "floppy_read" not in window._build_feedback_payload(
        summary="Feedback", description="", contact="", include_logs=False,
    )["context"]
    window.diskLoadWorker = None


def test_real_load_fallback_record_reaches_support_report(window, monkeypatch):
    drive = floppy_image.FloppyDriveInfo("A:", 737280)
    first_message = "Readable boot sector has no supported layout"

    def fast_read(*_args, diagnostics, **_kwargs):
        diagnostics.update(
            boot_probe={"status": "read_success", "offset_bytes": 0, "length_bytes": 512},
            successful_ranges=[{"offset_bytes": 0, "length_bytes": 512}],
            detected_capacity_bytes=737280,
        )
        raise floppy_image.FastFloppyReadError(first_message, fallback_allowed=True)

    def raw_read(*_args, **_kwargs):
        error = FloppyImageError("Le média disque n’est pas reconnu", winerror=1785)
        error.read_diagnostics = {
            "successful_ranges": [{"offset_bytes": 0, "length_bytes": 65536}],
            "final_failed_read": {"offset_bytes": 65536, "length_bytes": 512, "winerror": 1785},
        }
        raise error

    monkeypatch.setattr(floppy_image, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(floppy_image, "_read_floppy_device_fast_image", fast_read)
    monkeypatch.setattr(floppy_image, "_read_windows_block_device_bytes", raw_read)
    worker = disk_session_worker.DiskSessionLoadWorker("floppy_usb", drive)
    window.diskLoadWorker = worker
    window.diskLoadContext = {"load_kind": "floppy_usb", "source": drive}
    worker.loadFailedWithDetails.connect(window._on_disk_load_failure_with_details)
    worker.run()
    record = window._build_bug_report_payload(
        summary="Floppy Load Failed", description="", contact="", include_logs=False,
    )["context"]["floppy_read"]
    assert record["first_error"]["message"] == record["fallback_reason"] == first_message
    assert record["final_error"]["winerror"] == 1785
    assert record["selected_capacity_bytes"] == record["detected_capacity_bytes"] == 737280
    assert record["fast_path"]["boot_probe"]["status"] == "read_success"
    assert record["raw_fallback"]["final_failed_read"] == {
        "offset_bytes": 65536, "length_bytes": 512, "winerror": 1785,
    }
    assert record["raw_fallback"]["successful_ranges"] == [{"offset_bytes": 0, "length_bytes": 65536}]
    window.diskLoadWorker = None


@pytest.mark.parametrize("failure_type", ["blank_disk_image", "greaseweazle_conversion"])
def test_detailed_ui_failure_keeps_specialized_routing_and_read_record(window, diagnostics, failure_type):
    window.diskLoadContext = {"load_kind": "floppy_gw", "source": "A:"}
    details = {"type": failure_type, "message": "Specialized failure", "read_diagnostics": diagnostics}
    window._on_disk_load_failure_with_details(details)
    assert window.pendingGwConversionDetails == details
    assert window.pendingDiskRecoveryRequest is None
    assert window.lastDiskReadDiagnostics == diagnostics


def test_new_normal_floppy_attempt_clears_previous_read_and_recovery_context(window, monkeypatch, diagnostics):
    window.lastDiskReadDiagnostics = diagnostics
    window.lastDiskReadContext = {"load_kind": "floppy_usb", "source": "B:"}
    window.lastDiskRecoveryDiagnostics = {"source_path": "B:", "readable_sectors": 10}
    window.diskRecoveryContext = {"load_kind": "floppy_usb", "source": "B:"}
    monkeypatch.setattr(main_window.DiskSessionLoadWorker, "start", lambda _worker: None)
    assert window._start_disk_load_worker(
        load_kind="floppy_usb", source="A:", progress_title="Reading Floppy", progress_total=100,
        initial_message="Reading", final_message="Opening", failure_title="Floppy Load Failed",
    )
    assert window.lastDiskReadDiagnostics == {}
    assert window.lastDiskReadContext == {}
    assert window.lastDiskRecoveryDiagnostics == {}
    assert window.diskRecoveryContext == {}


def test_direct_recovery_attempt_clears_previous_normal_read_record(window, monkeypatch, diagnostics):
    window.lastDiskReadDiagnostics = diagnostics
    window.lastDiskReadContext = {"load_kind": "floppy_usb", "source": "B:"}
    monkeypatch.setattr(window, "_prepare_for_disk_load", lambda _label: True)
    monkeypatch.setattr(window, "_choose_floppy_read_options", lambda **_kwargs: {
        "load_kind": "floppy_usb", "source": "A:", "recover": True,
    })
    monkeypatch.setattr(window, "_start_disk_recovery_worker", lambda _request: None)
    window.load_floppy_drive()
    assert window.lastDiskReadDiagnostics == {}
    assert window.lastDiskReadContext == {}


def test_successful_read_retains_acquisition_diagnostics_for_reporting(window, monkeypatch, diagnostics):
    listing = SimpleNamespace(entries=[])
    record = deepcopy(diagnostics)
    record.pop("final_error")
    record["read_method"] = "exact_raw"
    record["exact_raw_copy"] = True
    session = SimpleNamespace(read_diagnostics=record, source_kind="floppy_usb", source_name="A:")
    window.diskLoadContext = {"load_kind": "floppy_usb", "source": "A:"}
    monkeypatch.setattr(window, "_activate_disk_session", lambda *_a: None)
    monkeypatch.setattr(window, "_offer_post_load_sequence_conversions", lambda: None)
    window._on_disk_load_success(session, listing)
    assert window.lastDiskReadDiagnostics == record
    assert window._bug_report_context()["floppy_read"] == record


def test_bug_report_form_exposes_read_diagnostic_opt_out_without_recovery(window, diagnostics):
    window.lastDiskReadDiagnostics = diagnostics
    window.lastDiskReadContext = {"load_kind": "floppy_usb", "source": "A:"}
    seen = []

    def inspect(dialog):
        checkboxes = {box.text(): box for box in dialog.findChildren(QCheckBox)}
        label = window._lt("Include floppy read and recovery diagnostics")
        assert label in checkboxes
        assert checkboxes[label].isChecked()
        assert checkboxes[window._lt("Floppy context (optional)")].isChecked()
        seen.append(dialog)
        return QDialog.Rejected

    window._exec_child_dialog = inspect
    window.show_bug_report_dialog(include_logs=False)
    assert seen


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES if item.code != "en"])
def test_read_diagnostic_opt_out_and_scope_are_localized(language):
    for source in (
        "Include floppy read and recovery diagnostics",
        "Includes drive details, read failures, requested offsets and sizes, sector counts, "
        "format and scan results, and recovery timing. Raw floppy image bytes are never included.",
    ):
        assert translate_text(source, language) != source
