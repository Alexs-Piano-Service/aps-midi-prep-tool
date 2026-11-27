"""Yamaha consent controls reads while Smart CD offers follow completed reads."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.main_window import QMessageBox

from aps_midi_prep_tool_app.smart_pianosoft_workflow import is_smart_pianosoft_source
from test_mute_workflows import window
from test_smart_pianosoft_loaded_list import _load_local_snapshot, _midi_bytes


def test_personal_use_notice_persists_across_settings_reload_and_message_reset(window, monkeypatch):
    shown = []

    def agree(box):
        shown.append(box.text())
        assert box.standardButtons() == QMessageBox.Yes | QMessageBox.Cancel
        assert box.button(QMessageBox.Yes).text() == "Agree"
        assert box.defaultButton() is box.button(QMessageBox.Yes)
        assert box.escapeButton() is box.button(QMessageBox.Cancel)
        assert box.checkBox().text() == "Never show this again"
        assert box.checkBox().isChecked()
        return QMessageBox.Yes

    monkeypatch.setattr(window, "_exec_child_dialog", agree)
    assert window._show_yamaha_personal_use_notice()
    assert len(shown) == 1
    assert 'href="https://www.alexanderpeppe.com/disclaimer/"' in shown[0]
    assert "personal use only" in shown[0]
    assert "continue reading" in shown[0]
    key = window.SETTING_YAMAHA_PERSONAL_USE_AGREED
    assert key not in window._hidden_dialog_setting_keys()
    filename = window.settings.fileName()
    window.settings = QSettings(filename, QSettings.IniFormat)
    window.reset_hidden_dialog_settings()
    assert window._show_yamaha_personal_use_notice()
    assert len(shown) == 1
    assert window.settings.value(key, False, type=bool)


@pytest.mark.parametrize("reply", [QMessageBox.Cancel, 0])
def test_cancelling_or_closing_notice_does_not_remember_agreement(window, monkeypatch, reply):
    shown = []
    monkeypatch.setattr(window, "_exec_child_dialog", lambda box: shown.append(box.text()) or reply)
    assert not window._show_yamaha_personal_use_notice()
    assert not window.settings.value(window.SETTING_YAMAHA_PERSONAL_USE_AGREED, False, type=bool)
    assert not window._yamaha_notice_open
    assert not window._show_yamaha_personal_use_notice()
    assert len(shown) == 2


def test_unchecked_remember_option_asks_for_next_operation(window, monkeypatch):
    shown = []

    def agree_without_remembering(box):
        shown.append(box.text())
        assert box.checkBox().isChecked()
        box.checkBox().setChecked(False)
        return QMessageBox.Yes

    monkeypatch.setattr(window, "_exec_child_dialog", agree_without_remembering)
    assert window._show_yamaha_personal_use_notice()
    assert window._show_yamaha_personal_use_notice()
    assert len(shown) == 2
    assert not window.settings.value(window.SETTING_YAMAHA_PERSONAL_USE_AGREED, False, type=bool)


def test_previously_shown_notice_does_not_count_as_agreement(window, monkeypatch):
    window.settings.setValue("yamaha_personal_use_notice_shown", True)
    shown = []
    monkeypatch.setattr(window, "_exec_child_dialog", lambda box: shown.append(box.text()) or QMessageBox.Cancel)
    assert not window._show_yamaha_personal_use_notice()
    assert len(shown) == 1
    assert not window.settings.value(window.SETTING_YAMAHA_PERSONAL_USE_AGREED, False, type=bool)


def test_protected_sector_report_does_not_repeat_worker_consent(window, monkeypatch):
    monkeypatch.setattr(window, "_exec_child_dialog", lambda box: pytest.fail("Report reopened consent"))
    monkeypatch.setattr(window, "_show_greaseweazle_sector_report", lambda report: False)
    report = {"sector_map": {"expected_yamaha_protection": 1}}
    window._show_greaseweazle_sector_reports([report, report])


def test_first_protection_detection_not_generic_check_shows_notice(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, "_exec_child_dialog", lambda box: shown.append(box.text()) or QMessageBox.Yes)
    window._note_yamaha_disk_read_progress(0, 80, "Reading first track; checking possible Yamaha copy protection...")
    assert not shown
    window._note_yamaha_disk_read_progress(1, 80, "Reading T0.0 (1/80)... Yamaha copy protection?")
    window._note_yamaha_disk_read_progress(1, 80, "Reading T0.0 (1/80)... Yamaha copy protection?")
    assert len(shown) == 1


def test_cd_offer_waits_until_the_first_notice_is_closed(window, tmp_path, monkeypatch):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    calls = []

    def notice_dialog(box):
        calls.append("notice open")
        # Simulate the reader completing while the notice runs its event loop.
        window._pending_yamaha_media_session = session
        window._on_disk_load_finished()
        QApplication.processEvents()
        assert calls == ["notice open"]
        calls.append("notice closed")
        return QMessageBox.Yes

    monkeypatch.setattr(window, "_exec_child_dialog", notice_dialog)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: calls.append("question") or QMessageBox.Yes)
    monkeypatch.setattr(window, "show_smart_pianosoft_utility", lambda: calls.append("dialog"))
    window._note_yamaha_disk_read_progress(1, 80, "Yamaha copy protection?")
    QApplication.processEvents()
    assert calls == ["notice open", "notice closed", "question", "dialog"]


@pytest.mark.parametrize("reply", [QMessageBox.Yes, QMessageBox.No])
def test_confirmed_smart_disk_offers_once_and_yes_opens_existing_dialog(window, tmp_path, monkeypatch, reply):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(window, "_show_yamaha_personal_use_notice", lambda: calls.append("notice") or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: calls.append("question") or reply)
    monkeypatch.setattr(window, "show_smart_pianosoft_utility", lambda: calls.append("dialog"))
    window._offer_yamaha_media_workflow(session)
    window._offer_yamaha_media_workflow(session)
    assert calls == ["notice", "question"] + (["dialog"] if reply == QMessageBox.Yes else [])


@pytest.mark.parametrize("protected", [False, True])
def test_ordinary_yamaha_catalog_never_offers_cd(window, tmp_path, monkeypatch, protected):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    source = window._smart_pianosoft_loaded_source()
    source = replace(source, tracks=tuple(replace(t, midi_bytes=_midi_bytes(fingerprints=False)) for t in source.tracks))
    monkeypatch.setattr(window, "_smart_pianosoft_loaded_source", lambda: source)
    session.source_boot_sector_repaired = protected
    notices = []
    monkeypatch.setattr(window, "_show_yamaha_personal_use_notice", lambda: notices.append(True) or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: pytest.fail("Ordinary disk offered CD rip"))
    window.imageEntriesByPath = {"PSONG.MNG": object()}
    window._offer_yamaha_media_workflow(session)
    assert notices == ([True] if protected else [])


def test_declining_consent_does_not_offer_cd_or_open_smart_dialog(window, tmp_path, monkeypatch):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    monkeypatch.setattr(window, "_show_yamaha_personal_use_notice", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: pytest.fail("Declined source offered CD rip"))
    monkeypatch.setattr(window, "show_smart_pianosoft_utility", lambda: pytest.fail("Declined source opened Smart"))
    window._offer_yamaha_media_workflow(session)
    assert not getattr(session, "_yamaha_consent_granted", False)


@pytest.mark.parametrize("worker_name", ["diskLoadWorker", "diskRecoveryWorker", "diskImageCaptureWorker"])
@pytest.mark.parametrize("agreed", [False, True])
def test_active_worker_consent_resolves_reply_and_cancels_when_declined(
    window, monkeypatch, worker_name, agreed,
):
    worker = SimpleNamespace(_cancel_requested=lambda: False)
    setattr(window, worker_name, worker)
    updates, resolved, cancellations = [], [], []
    monkeypatch.setattr(main_window, "update_disk_activity", lambda *_a: updates.append(_a[-1]))
    monkeypatch.setattr(main_window, "cancel_disk_activity", lambda _w, owner, dialog: cancellations.append(owner))

    def show_notice():
        assert updates == [window._t("sps.waiting_for_consent")]
        return agreed

    monkeypatch.setattr(window, "_show_yamaha_personal_use_notice", show_notice)
    try:
        window._on_yamaha_consent_requested(worker, SimpleNamespace(resolve=resolved.append))
        assert resolved == [agreed]
        assert cancellations == ([] if agreed else [worker])
    finally:
        setattr(window, worker_name, None)


@pytest.mark.parametrize("inactive_reason", ["replaced", "cancelled"])
def test_inactive_worker_consent_is_rejected_without_dialog(window, monkeypatch, inactive_reason):
    worker = SimpleNamespace(_cancel_requested=lambda: inactive_reason == "cancelled")
    if inactive_reason == "cancelled":
        window.diskLoadWorker = worker
    resolved = []
    monkeypatch.setattr(window, "_show_yamaha_personal_use_notice", lambda: pytest.fail("Inactive read asked consent"))
    try:
        window._on_yamaha_consent_requested(worker, SimpleNamespace(resolve=resolved.append))
        assert resolved == [False]
    finally:
        window.diskLoadWorker = None


def test_read_cancelled_during_consent_does_not_resume(window, monkeypatch):
    cancelled, resolved = [], []
    worker = SimpleNamespace(_cancel_requested=lambda: bool(cancelled))
    window.diskLoadWorker = worker
    monkeypatch.setattr(main_window, "update_disk_activity", lambda *_a: None)

    def finish_dialog():
        cancelled.append(True)
        return True

    monkeypatch.setattr(window, "_show_yamaha_personal_use_notice", finish_dialog)
    try:
        window._on_yamaha_consent_requested(worker, SimpleNamespace(resolve=resolved.append))
        assert resolved == [False]
    finally:
        window.diskLoadWorker = None


@pytest.mark.parametrize("fault", ["truncated", "ordinary", "missing_catalog", "uncataloged", "error"])
def test_incomplete_or_generic_sources_are_not_clearly_smart(window, tmp_path, monkeypatch, fault):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    source = window._smart_pianosoft_loaded_source()
    assert is_smart_pianosoft_source(source)
    if fault in {"truncated", "ordinary", "uncataloged"}:
        track = replace(source.tracks[0], **(
            {"catalog_filename": "OTHER.MID"} if fault == "uncataloged" else
            {"midi_bytes": b"MThd" if fault == "truncated" else _midi_bytes(fingerprints=False)}))
        source = replace(source, tracks=(track,) + source.tracks[1:])
    elif fault == "missing_catalog":
        source = replace(source, song_catalog=b"")
    else:
        source = replace(source, error="Unreadable source")
    assert not is_smart_pianosoft_source(source)


def test_replaced_session_cannot_open_a_stale_cd_offer(window, tmp_path, monkeypatch):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    window.image_session = None
    monkeypatch.setattr(window, "_smart_pianosoft_loaded_source", lambda: pytest.fail("Read stale source"))
    window._offer_yamaha_media_workflow(session)
    session.cleanup()


@pytest.mark.parametrize("finish_during_report", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
def test_success_waits_for_worker_cleanup_even_when_report_runs_nested_loop(
    window, tmp_path, monkeypatch, finish_during_report, recovery,
):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    listing = session.list_entries()
    window.image_session = None
    monkeypatch.setattr(window, "_activate_disk_session", lambda session, listing: setattr(window, "image_session", session))
    monkeypatch.setattr(window, "_apply_pending_floppy_read_title_trim", lambda: None)
    monkeypatch.setattr(window, "_apply_pending_floppy_read_long_filenames", lambda: None)
    monkeypatch.setattr(window, "_information_with_optional_hide", lambda **kwargs: None)
    finish = window._on_disk_recovery_finished if recovery else window._on_disk_load_finished
    success = window._on_disk_recovery_success if recovery else window._on_disk_load_success
    worker_attribute = "diskRecoveryWorker" if recovery else "diskLoadWorker"
    if finish_during_report:
        monkeypatch.setattr(window, "_show_greaseweazle_sector_reports", lambda reports: finish())
    else:
        monkeypatch.setattr(window, "_show_greaseweazle_sector_reports", lambda reports: None)
    setattr(window, worker_attribute, SimpleNamespace(isRunning=lambda: True, deleteLater=lambda: None))
    window._set_disk_load_busy(True)
    offers = []
    monkeypatch.setattr(window, "_offer_yamaha_media_workflow", lambda source: offers.append(
        (source, getattr(window, worker_attribute), window.choose_button.isEnabled())))
    success(session, listing)
    if not finish_during_report:
        QApplication.processEvents()
        assert not offers
        finish()
    QApplication.processEvents()
    assert offers == [(session, None, True)]


@pytest.mark.parametrize("capture_ext", ["hfe", "scp"])
@pytest.mark.parametrize("finish_stage,outcome", [
    ("report", "saved"),
    ("question", "saved"),
    ("picker", "saved"),
    ("export", "saved"),
    ("complete", "saved"),
    ("after", "saved"),
    ("question", "declined"),
    ("after", "declined"),
    ("question", "cancelled"),
    ("picker", "cancelled"),
    ("export", "failed"),
    ("error", "failed"),
])
def test_capture_save_finishes_before_smart_pianosoft_offer(
    window, tmp_path, monkeypatch, capture_ext, finish_stage, outcome,
):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch, source_kind="floppy_gw")
    listing = session.list_entries()
    session.gw_source = SimpleNamespace(
        drive="A", archival_quality=capture_ext == "scp", capture_output_ext=capture_ext,
    )
    capture_path = tmp_path / f"capture.{capture_ext}"
    capture_path.write_bytes(b"captured disk")
    session.capture_path = str(capture_path)
    session.capture_ext = capture_ext
    session._yamaha_consent_granted = True
    window.image_session = None
    window.diskLoadShouldOfferCapture = True
    window.diskLoadWorker = SimpleNamespace(isRunning=lambda: True, deleteLater=lambda: None)
    window._set_disk_load_busy(True)
    monkeypatch.setattr(window, "_activate_disk_session", lambda session, listing: setattr(window, "image_session", session))
    monkeypatch.setattr(window, "_apply_pending_floppy_read_title_trim", lambda: None)
    monkeypatch.setattr(window, "_apply_pending_floppy_read_long_filenames", lambda: None)
    calls = []

    def stage(name):
        calls.append(name)
        if finish_stage == name:
            window._on_disk_load_finished()
        # Modal dialogs and export progress can dispatch worker.finished and
        # queued Smart offers while the save flow is still on the stack.
        QApplication.processEvents()
        assert "smart question" not in calls

    def question(_parent, title, *_args, **_kwargs):
        if title == "Save Greaseweazle Capture":
            stage("question")
            return QMessageBox.No if outcome == "declined" else QMessageBox.Yes
        assert title == window._t("sps.detected_title")
        calls.append("smart question")
        assert window.diskLoadWorker is None
        assert window.choose_button.isEnabled()
        return QMessageBox.Yes

    def choose_path(*_args):
        stage("picker")
        return ("" if outcome == "cancelled" else str(tmp_path / f"saved.{capture_ext}")), ""

    def export(*_args, **_kwargs):
        stage("export")
        if outcome == "failed":
            raise OSError("Destination is unavailable")

    monkeypatch.setattr(window, "_show_greaseweazle_sector_reports", lambda reports: stage("report"))
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", choose_path)
    monkeypatch.setattr(session, "write_image", export)
    if capture_ext == "scp":
        copy2 = main_window.shutil.copy2

        def copy_capture(source, destination, *args, **kwargs):
            if source == str(capture_path):
                export(source, destination)
            return copy2(source, destination, *args, **kwargs)

        monkeypatch.setattr(main_window.shutil, "copy2", copy_capture)
    monkeypatch.setattr(window, "_fill_blank_image_catalog_from_source", lambda path: False)
    monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *_a, **_k: stage("complete"))
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: stage("complete"))
    monkeypatch.setattr(window, "_show_operation_error", lambda *_a, **_k: stage("error"))
    monkeypatch.setattr(window, "show_smart_pianosoft_utility", lambda: calls.append("smart dialog"))

    window._on_disk_load_success(session, listing)
    assert "smart question" not in calls
    if finish_stage == "after":
        QApplication.processEvents()
        assert "smart question" not in calls
        window._on_disk_load_finished()
    QApplication.processEvents()
    expected = ["report", "question"]
    if outcome != "declined":
        expected.append("picker")
    if outcome in {"saved", "failed"}:
        expected.extend(["export", "complete" if outcome == "saved" else "error"])
    assert calls == expected + ["smart question", "smart dialog"]
    assert getattr(window, "_pending_yamaha_media_session", None) is None


def test_recovery_complete_closes_before_smart_pianosoft_offer(window, tmp_path, monkeypatch):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    listing = session.list_entries()
    window.image_session = None
    window.diskRecoveryWorker = SimpleNamespace(isRunning=lambda: True, deleteLater=lambda: None)
    window._set_disk_load_busy(True)
    monkeypatch.setattr(window, "_activate_disk_session", lambda session, listing: setattr(window, "image_session", session))
    monkeypatch.setattr(window, "_apply_pending_floppy_read_title_trim", lambda: None)
    monkeypatch.setattr(window, "_apply_pending_floppy_read_long_filenames", lambda: None)
    calls = []

    def complete(**kwargs):
        calls.append("recovery complete open")
        window._on_disk_recovery_finished()
        QApplication.processEvents()
        assert calls == ["recovery complete open"]
        calls.append("recovery complete closed")

    monkeypatch.setattr(window, "_information_with_optional_hide", complete)
    monkeypatch.setattr(window, "_offer_yamaha_media_workflow", lambda source: calls.append("smart offer"))
    window._on_disk_recovery_success(session, listing)
    QApplication.processEvents()
    assert calls == ["recovery complete open", "recovery complete closed", "smart offer"]
    assert getattr(window, "_pending_yamaha_media_session", None) is None
