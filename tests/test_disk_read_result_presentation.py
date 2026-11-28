"""Acquisition completion must not uncover an unfinished song list."""

import time

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QProgressDialog

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.floppy_image import FloppyDriveInfo, FloppyImageSession
from test_preparation_automatic import destination_window, _make_image, _song


@pytest.mark.parametrize('operation,profile,convert_after_read', [
    ('load', 'custom', False),
    ('recover', 'custom', False),
    ('load', 'mark_ii', False),
    ('recover', 'mark_ii', False),
    ('load', 'custom', True),
])
def test_organizing_spans_row_build_and_automatic_conversion(
    destination_window, tmp_path, monkeypatch, operation, profile, convert_after_read,
):
    window = destination_window(profile, persisted=True)
    window.show()
    song = tmp_path / ('SONG.FIL' if convert_after_read else 'SONG.MID')
    song.write_bytes(convert_midi_bytes_to_eseq_bytes(_song()) if convert_after_read else _song())
    source = _make_image(tmp_path, [song])
    worker_name, dialog_name, finished_name = (
        ('diskLoadWorker', 'diskLoadProgressDialog', '_on_disk_load_finished') if operation == 'load'
        else ('diskRecoveryWorker', 'diskRecoveryProgressDialog', '_on_disk_recovery_finished')
    )
    observations = []
    dialogs = []
    errors = []

    def acquire(*_args, **kwargs):
        session = FloppyImageSession.load(
            source, progress_callback=kwargs['progress_callback'], cancel_callback=kwargs['cancel_callback'],
        )
        session.source_kind = 'floppy_usb'
        return session

    monkeypatch.setattr(FloppyImageSession, 'load_floppy' if operation == 'load' else 'recover', acquire)
    monkeypatch.setattr(window, '_offer_post_load_sequence_conversions', lambda: None)
    monkeypatch.setattr(window, '_offer_yamaha_media_workflow', lambda *_args: None)
    monkeypatch.setattr(window, '_information_with_optional_hide', lambda **_kwargs: None)
    monkeypatch.setattr(window, '_show_operation_error', lambda *args, **_kwargs: errors.append(args))
    original_probe = window._probe_image_file
    original_conversion = window._queue_image_format_conversion

    def assert_organizing(stage):
        dialog = getattr(window, dialog_name)
        assert dialog is not None and dialog.isVisible()
        assert dialog._aps_disk_read_stages.active_phase == 'organize'
        assert not dialog._aps_disk_read_stages.completed
        assert dialog._aps_disk_read_stages.animation.state() == QAbstractAnimation.Running
        observations.append(stage)
        if not dialogs:
            dialogs.append(dialog)
        assert dialog is dialogs[0]
        return dialog

    def probe(*args, **kwargs):
        dialog = assert_organizing('probe')
        worker = getattr(window, worker_name)
        if worker is not None:
            assert worker.wait(2000)
            # Exercise both the finished signal and the heartbeat's cleanup
            # path while title extraction is still building the table.
            QApplication.processEvents()
            getattr(window, finished_name)()
            assert getattr(window, worker_name) is worker
        QTest.qWait(60)
        assert_organizing('probe after finished')
        dialog.close()  # The local handoff cannot be cancelled like device I/O.
        assert dialog.isVisible()
        return original_probe(*args, **kwargs)

    def convert(*args, **kwargs):
        dialog = assert_organizing('convert')
        assert [item for item in window.findChildren(QProgressDialog) if item.isVisible()] == [dialog]
        QTest.qWait(60)
        assert_organizing('convert after events')
        return original_conversion(*args, **kwargs)

    monkeypatch.setattr(window, '_probe_image_file', probe)
    monkeypatch.setattr(window, '_queue_image_format_conversion', convert)
    window.pendingFloppyReadConvertToMidi = convert_after_read
    drive = FloppyDriveInfo('mock-floppy', source.stat().st_size)
    if operation == 'load':
        window._start_disk_load_worker(
            load_kind='floppy_usb', source=drive, progress_title='Reading Floppy', progress_total=100,
            initial_message='Reading floppy...', final_message='Opening floppy contents...', failure_title='Read failed',
        )
    else:
        window._start_disk_recovery_worker({'load_kind': 'floppy_usb', 'source': drive})
    deadline = time.monotonic() + 5
    while getattr(window, dialog_name) is not None and time.monotonic() < deadline:
        QTest.qWait(10)
    assert not errors
    assert getattr(window, worker_name) is None
    assert getattr(window, dialog_name) is None
    assert window.table.rowCount() >= 1
    assert dialogs and not dialogs[0].isVisible()
    assert dialogs[0]._aps_disk_read_stages.completed
    assert not getattr(window, '_destination_preparation_queued', False)
    assert 'probe after finished' in observations
    if profile != 'custom' or convert_after_read:
        assert 'convert after events' in observations


def test_failed_list_preparation_closes_without_completing(destination_window):
    window = destination_window('custom', persisted=True)
    dialog = QProgressDialog('Reading floppy...', 'Cancel', 0, 100, window)
    window._prepare_disk_read_progress_dialog(dialog)
    window.diskLoadProgressDialog = dialog
    dialog.show()
    with pytest.raises(RuntimeError, match='cannot prepare titles'):
        with window._organize_disk_read_result('diskLoadProgressDialog', 'diskLoadWorker', window._on_disk_load_finished):
            raise RuntimeError('cannot prepare titles')
    assert window.diskLoadProgressDialog is None
    assert not dialog.isVisible()
    assert not dialog._aps_disk_read_stages.completed
