"""Auto-open only a loaded Smart PianoSoft source, once the UI is ready."""

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest

from aps_midi_prep_tool_app.main_window import QMessageBox
from test_smart_pianosoft_loaded_list import _load_local_snapshot, _midi_bytes
from test_mute_workflows import window


@pytest.fixture(autouse=True)
def accept_disk_offer(window, monkeypatch):
    # Main already offers synchronization after disk-save/consent dialogs;
    # deferred opening must use that same offer, rather than bypass it.
    monkeypatch.setattr(window, '_show_yamaha_personal_use_notice', lambda: True)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.Yes)


def test_loaded_sps_opens_once_after_disk_worker_and_modal_finish(window, tmp_path, monkeypatch):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(window, 'show_smart_pianosoft_utility', lambda: opened.append(True))
    window.show()
    window._set_disk_load_busy(True)
    window._schedule_smart_pianosoft_from_loaded_media()
    QApplication.processEvents()
    assert opened == []
    window._set_disk_load_busy(False)
    modal = QDialog(window)
    modal.setModal(True)
    modal.show()
    QTest.qWait(120)
    assert opened == []
    modal.close()
    QTest.qWait(130)
    assert opened == [True]
    QTest.qWait(120)
    assert opened == [True]


def test_plain_catalog_does_not_open_synchronization(window, tmp_path, monkeypatch):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    for name in ('FIRST.MID', 'SECOND.MID', 'THIRD.MID'):
        replacement = tmp_path / name
        replacement.write_bytes(_midi_bytes(fingerprints=False))
        window.pendingImageReplacements[name] = str(replacement)
    opened = []
    monkeypatch.setattr(window, 'show_smart_pianosoft_utility', lambda: opened.append(True))
    window.show()
    window._schedule_smart_pianosoft_from_loaded_media()
    QApplication.processEvents()
    assert opened == []


def test_replaced_source_and_closed_window_cancel_queued_open(window, tmp_path, monkeypatch):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(window, 'show_smart_pianosoft_utility', lambda: opened.append(True))
    window.show()
    window._schedule_smart_pianosoft_from_loaded_media()
    window.table.setRowCount(0)
    QApplication.processEvents()
    assert opened == []
    _load_local_snapshot(window, tmp_path, monkeypatch)
    window._schedule_smart_pianosoft_from_loaded_media()
    window.hide()
    QApplication.processEvents()
    assert opened == []


def test_duplicate_load_scheduling_opens_only_latest_source(window, tmp_path, monkeypatch):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(window, 'show_smart_pianosoft_utility', lambda: opened.append(True))
    window.show()
    window._schedule_smart_pianosoft_from_loaded_media()
    window._schedule_smart_pianosoft_from_loaded_media()
    QApplication.processEvents()
    assert opened == [True]
    # A second load callback for the same session cannot repeat its offer.
    window._schedule_smart_pianosoft_from_loaded_media()
    QApplication.processEvents()
    assert opened == [True]


def test_disk_activation_leaves_offer_to_completed_read_handler(window, tmp_path, monkeypatch):
    _payloads, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    window.image_session = None
    scheduled = []
    monkeypatch.setattr(window, '_schedule_smart_pianosoft_from_loaded_media', lambda: scheduled.append(True))
    monkeypatch.setattr(window, '_schedule_destination_preparation', lambda: None)
    window._activate_disk_session(session, session.list_entries())
    assert scheduled == []


def test_declined_disk_offer_is_not_bypassed_by_auto_open(window, tmp_path, monkeypatch):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(window, 'show_smart_pianosoft_utility', lambda: opened.append(True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.No)
    window.show()
    window._schedule_smart_pianosoft_from_loaded_media()
    QApplication.processEvents()
    window._schedule_smart_pianosoft_from_loaded_media()
    QApplication.processEvents()
    assert opened == []


@pytest.mark.parametrize('fingerprints', [True, False])
def test_regular_folder_load_opens_only_confirmed_smart_source(window, tmp_path, monkeypatch, fingerprints):
    from test_smart_pianosoft_loaded_list import _album_payloads

    paths = []
    for name, payload in _album_payloads().items():
        target = tmp_path / name
        target.write_bytes(_midi_bytes(fingerprints=fingerprints) if name.endswith('.MID') else payload)
        if name.endswith('.MID'):
            paths.append(str(target))
    opened = []
    monkeypatch.setattr(window, 'show_smart_pianosoft_utility', lambda: opened.append(True))
    monkeypatch.setattr(window, '_schedule_destination_preparation', lambda: None)
    window.show()
    window._load_regular_files(paths, 'Extracted Smart album')
    QApplication.processEvents()
    assert opened == ([True] if fingerprints else [])
