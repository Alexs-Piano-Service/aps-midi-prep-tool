"""Auto-open only a loaded Smart PianoSoft source, once the UI is ready."""

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest

from test_smart_pianosoft_loaded_list import _load_local_snapshot, _midi_bytes
from test_mute_workflows import window


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
