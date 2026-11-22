"""Media readers wait for consent without trapping cancellation or leaking sessions."""

import time
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import disk_session_worker as workers
from aps_midi_prep_tool_app.floppy_image import ImageEntry, ImageListing
from aps_midi_prep_tool_app.yamaha_consent import YamahaConsentRequest, session_requires_yamaha_consent
from test_smart_pianosoft import _psong_bytes
from test_smart_pianosoft_loaded_list import _midi_bytes


def _wait(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    QApplication.processEvents()
    assert predicate(), "Media worker did not reach the expected state"


def _session(tmp_path, *, smart=False, protected=False):
    payloads = {"PSONG.MNG": _psong_bytes([("SONG.MID", "Synthetic song")]),
                "SONG.MID": _midi_bytes(fingerprints=smart)}
    for name, payload in payloads.items():
        (tmp_path / name).write_bytes(payload)
    listing = ImageListing([ImageEntry(name, len(value), len(value)) for name, value in payloads.items()], 0, 512)
    cleaned = []
    session = SimpleNamespace(
        list_entries=lambda: listing,
        extract_file=lambda name: str(tmp_path / name),
        cleanup=lambda: cleaned.append(True),
        source_boot_sector_repaired=False,
        gw_sector_reports=[{"sector_map": {"expected_yamaha_protection": 1}}] if protected else [],
    )
    return session, listing, cleaned


def _worker(monkeypatch, kind, read):
    if kind == "load":
        monkeypatch.setattr(workers.FloppyImageSession, "load_greaseweazle", read)
        worker = workers.DiskSessionLoadWorker("floppy_gw", object())
        success_signal, failure_signal = worker.sessionLoaded, worker.loadFailed
    else:
        monkeypatch.setattr(workers.FloppyImageSession, "recover", read)
        worker = workers.DiskSessionRecoveryWorker("floppy_gw", object())
        success_signal, failure_signal = worker.sessionRecovered, worker.recoveryFailed
    worker.yamaha_consent_required = True
    record = SimpleNamespace(requests=[], successes=[], failures=[], cancellations=[], progress=[])
    worker.yamahaConsentRequested.connect(record.requests.append)
    success_signal.connect(lambda session, listing: record.successes.append((session, listing)))
    failure_signal.connect(record.failures.append)
    worker.operationCancelled.connect(record.cancellations.append)
    worker.progressChanged.connect(lambda step, total, message: record.progress.append((step, total, message)))
    return worker, record


@pytest.mark.parametrize("kind", ["load", "recover"])
def test_protected_reader_waits_for_agreement_and_prompts_once_per_operation(qt_application, tmp_path, monkeypatch, kind):
    session, listing, cleaned = _session(tmp_path, protected=True)
    continued = []

    def read(*_args, progress_callback, cancel_callback):
        assert not cancel_callback()
        try:
            progress_callback(1, 80, "Reading cylinder 0: Yamaha copy protection?")
            continued.append(True)
            progress_callback(2, 80, "Yamaha copy protection?")
            return session
        except Exception:
            session.cleanup()
            raise

    worker, record = _worker(monkeypatch, kind, read)
    worker.start()
    try:
        _wait(lambda: bool(record.requests))
        assert worker.isRunning()
        assert not continued and not record.successes and not record.progress
        assert not record.requests[0].wait(0)
        record.requests[0].resolve(True)
        _wait(lambda: not worker.isRunning())
        assert continued == [True]
        assert len(record.requests) == 1
        assert record.successes == [(session, listing)]
        assert len(record.progress) == 2
        assert session._yamaha_consent_granted is True
        assert not cleaned and not record.failures and not record.cancellations
    finally:
        worker.cancel()
        worker.wait(2000)


@pytest.mark.parametrize("kind", ["load", "recover"])
@pytest.mark.parametrize("decision", ["cancel", "decline", "interrupt"])
def test_pending_protection_consent_can_abort_without_delivering_or_leaking_session(
    qt_application, tmp_path, monkeypatch, kind, decision,
):
    session, _listing, cleaned = _session(tmp_path, protected=True)
    original = (tmp_path / "SONG.MID").read_bytes()
    worker, record = _worker(monkeypatch, kind, lambda *_a, **_k: session)
    worker.start()
    try:
        _wait(lambda: bool(record.requests))
        assert not record.successes and not cleaned
        if decision == "cancel":
            worker.cancel()
        elif decision == "interrupt":
            worker.requestInterruption()
        else:
            record.requests[0].resolve(False)
        _wait(lambda: not worker.isRunning())
        assert record.requests[0].wait(0) and record.requests[0].approved is False
        assert len(record.cancellations) == 1
        assert not record.successes and not record.failures
        assert cleaned == [True]
        assert (tmp_path / "SONG.MID").read_bytes() == original
        record.requests[0].resolve(True)
        assert record.requests[0].approved is False
    finally:
        worker.cancel()
        worker.wait(2000)


@pytest.mark.parametrize("kind", ["load", "recover"])
def test_smart_fingerprints_require_agreement_before_session_delivery(qt_application, tmp_path, monkeypatch, kind):
    session, listing, cleaned = _session(tmp_path, smart=True)
    worker, record = _worker(monkeypatch, kind, lambda *_a, **_k: session)
    worker.start()
    try:
        _wait(lambda: bool(record.requests))
        assert worker.isRunning()
        assert not record.successes
        assert not session.gw_sector_reports and not session.source_boot_sector_repaired
        record.requests[0].resolve(True)
        _wait(lambda: not worker.isRunning())
        assert record.successes == [(session, listing)]
        assert len(record.requests) == 1
        assert session._yamaha_consent_granted is True
        assert not record.failures and not cleaned
    finally:
        worker.cancel()
        worker.wait(2000)


@pytest.mark.parametrize("kind", ["load", "recover"])
def test_ordinary_yamaha_catalog_does_not_pause_reader(qt_application, tmp_path, monkeypatch, kind):
    session, listing, cleaned = _session(tmp_path)
    worker, record = _worker(monkeypatch, kind, lambda *_a, **_k: session)
    worker.start()
    try:
        _wait(lambda: not worker.isRunning())
        assert record.successes == [(session, listing)]
        assert not record.requests and not record.failures and not record.cancellations and not cleaned
    finally:
        worker.cancel()
        worker.wait(2000)


def test_preapproved_reader_does_not_pause_or_reread_smart_metadata(qt_application, tmp_path, monkeypatch):
    session, listing, cleaned = _session(tmp_path, smart=True, protected=True)
    session.extract_file = lambda _name: pytest.fail("Unnecessary metadata extraction after remembered agreement")

    def read(*_args, progress_callback, cancel_callback):
        progress_callback(1, 80, "Yamaha copy protection?")
        return session

    worker, record = _worker(monkeypatch, "load", read)
    worker.yamaha_consent_required = False
    worker.start()
    try:
        _wait(lambda: not worker.isRunning())
        assert record.successes == [(session, listing)]
        assert session._yamaha_consent_granted is True
        assert not record.requests and not record.failures and not cleaned
    finally:
        worker.cancel()
        worker.wait(2000)


def test_consent_request_keeps_first_answer():
    request = YamahaConsentRequest()
    assert not request.wait(0)
    request.resolve(False)
    assert request.wait(0)
    request.resolve(True)
    assert request.approved is False


def test_cancel_before_request_assignment_still_resolves_request(qt_application, monkeypatch):
    worker = workers.DiskSessionLoadWorker("floppy_gw", object())
    worker.yamaha_consent_required = True
    request = YamahaConsentRequest()

    def cancellation_race():
        worker.cancel()
        return request

    monkeypatch.setattr(workers, "YamahaConsentRequest", cancellation_race)
    with pytest.raises(workers.FloppyOperationCancelled):
        worker._request_yamaha_consent()
    assert request.wait(0) and request.approved is False
    assert worker._yamaha_consent_request is None


def test_directory_named_like_catalog_does_not_trigger_metadata_extraction():
    listing = ImageListing([ImageEntry("PSONG.MNG", 0, 0, is_directory=True)], 0, 512)
    session = SimpleNamespace(extract_file=lambda _name: pytest.fail("Tried to parse a directory as a catalog"))
    assert session_requires_yamaha_consent(session, listing) is False
