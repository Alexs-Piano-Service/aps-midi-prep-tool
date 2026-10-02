import pytest

from aps_midi_prep_tool_app import disk_session_worker


def test_emulator_build_worker_keeps_retirement_details_in_confirmation(qt_application):
    from aps_midi_prep_tool_app.emulator_image_builder import EmulatorOutputChanges

    worker = disk_session_worker.EmulatorImageBuildWorker(
        "source", "output", prefix="DSKA", starting_number=0,
        safety_margin_bytes=0, album_title="", disk_format=object(), output_ext="img",
    )
    changes = EmulatorOutputChanges(["DSKA0000.img"], ["DSKA0002.img"])
    requests = []

    def approve(value):
        requests.append(value)
        worker.resolve_overwrite_request(True)

    worker.overwriteRequested.connect(approve)
    assert worker._request_overwrite_confirmation(changes) is True
    assert requests == [changes]
    assert requests[0].retired_paths == ("DSKA0002.img",)


@pytest.mark.parametrize("disk_layout", ["fill", "folders"])
def test_emulator_build_worker_forwards_layout_and_scan_options(monkeypatch, disk_layout):
    calls = []
    result = object()

    def fake_build_emulator_disk_images(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(
        disk_session_worker,
        "build_emulator_disk_images",
        fake_build_emulator_disk_images,
    )
    worker = disk_session_worker.EmulatorImageBuildWorker(
        "source",
        "output",
        prefix="DSKA",
        starting_number=1,
        safety_margin_bytes=0,
        album_title="Album",
        disk_format=object(),
        output_ext="img",
        include_subfolders=False,
        disk_layout=disk_layout,
        include_song_lists=True,
    )
    finished = []
    worker.buildFinished.connect(finished.append)

    worker.run()

    assert calls[0][1]["include_subfolders"] is False
    assert calls[0][1]["disk_layout"] == disk_layout
    assert calls[0][1]["include_song_lists"] is True
    assert finished == [result]


class _RecoveredSession:
    def __init__(self, diagnostics, listing):
        self.recovery_diagnostics = diagnostics
        self._listing = listing

    def list_entries(self):
        return self._listing


def test_recovery_worker_retains_successful_session_diagnostics(monkeypatch):
    listing = object()
    session = _RecoveredSession(
        {"readable_sectors": 1439},
        listing,
    )
    monkeypatch.setattr(
        disk_session_worker.FloppyImageSession,
        "recover",
        lambda *_args, **_kwargs: session,
    )
    worker = disk_session_worker.DiskSessionRecoveryWorker(
        "floppy_usb",
        object(),
    )
    recovered = []
    worker.sessionRecovered.connect(
        lambda value, value_listing: recovered.append((value, value_listing))
    )

    worker.run()

    assert recovered == [(session, listing)]
    assert worker.recovery_diagnostics == {"readable_sectors": 1439}


def test_recovery_worker_retains_exception_diagnostics(monkeypatch):
    class RecoveryFailure(Exception):
        def __init__(self):
            super().__init__("Recovery stopped after the time limit.")
            self.diagnostics = {
                "attempted_sectors": 24,
                "unattempted_sectors": 1416,
                "stop_reason": "soft_deadline",
            }

    def fail_recovery(*_args, **_kwargs):
        raise RecoveryFailure()

    monkeypatch.setattr(
        disk_session_worker.FloppyImageSession,
        "recover",
        fail_recovery,
    )
    worker = disk_session_worker.DiskSessionRecoveryWorker(
        "floppy_usb",
        object(),
    )
    failures = []
    worker.recoveryFailed.connect(failures.append)

    worker.run()

    assert failures == ["Recovery stopped after the time limit."]
    assert worker.recovery_diagnostics == {
        "attempted_sectors": 24,
        "unattempted_sectors": 1416,
        "stop_reason": "soft_deadline",
    }


def test_recovery_worker_retains_cancellation_diagnostics(monkeypatch):
    def cancel_recovery(*_args, **_kwargs):
        error = disk_session_worker.FloppyOperationCancelled("Operation cancelled.")
        error.diagnostics = {
            "attempted_sectors": 32,
            "unresolved_sectors": 4,
            "stop_reason": "cancelled",
        }
        raise error

    monkeypatch.setattr(
        disk_session_worker.FloppyImageSession,
        "recover",
        cancel_recovery,
    )
    worker = disk_session_worker.DiskSessionRecoveryWorker(
        "floppy_usb",
        object(),
    )
    cancellations = []
    worker.operationCancelled.connect(cancellations.append)

    worker.run()

    assert cancellations == ["Operation cancelled."]
    assert worker.recovery_diagnostics == {
        "attempted_sectors": 32,
        "unresolved_sectors": 4,
        "stop_reason": "cancelled",
    }
