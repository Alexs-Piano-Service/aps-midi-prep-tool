"""Disk reading keeps one geometry through status, range and visibility changes."""

import pytest
from PySide6.QtCore import QAbstractAnimation, QSize, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QProgressBar, QProgressDialog, QPushButton, QStyleFactory

from aps_midi_prep_tool_app import main_window
from test_ui_font_scaling import make_window
from test_save_as_overwrite import window


def _settle_native_placement(dialog):
    if QApplication.platformName() != 'offscreen':
        assert QTest.qWaitForWindowExposed(dialog)
        # Native title-bar margins arrive asynchronously on X11/Windows.
        QTest.qWait(150)


@pytest.mark.parametrize('style', ['Fusion', 'Windows'])
@pytest.mark.parametrize('point_size,scale,language', [(9, 'compact', 'en'), (12, 'regular', 'de'), (16, 'large', 'ja')])
def test_read_progress_geometry_is_fixed_across_stages(make_window, style, point_size, scale, language):
    if style not in QStyleFactory.keys():
        pytest.skip(f'{style} is unavailable')
    parent = make_window(point_size, density=scale)
    QApplication.setStyle(style)
    parent.settings.setValue('language', language)
    dialog = QProgressDialog('Reading floppy...', 'Cancel', 0, 0, parent)
    parent._prepare_disk_read_progress_dialog(dialog)
    parent._show_centered_progress_dialog(dialog)
    _settle_native_placement(dialog)
    original = dialog.geometry()
    label = dialog._aps_stable_progress_label
    bar = dialog.findChild(QProgressBar)
    button = dialog.findChild(QPushButton)
    assert dialog.minimumSize() == dialog.maximumSize() == original.size()
    assert not dialog.autoClose() and not dialog.autoReset()
    assert label.textFormat() == Qt.PlainText
    assert label.alignment() & Qt.AlignLeft
    assert label.geometry().bottom() < bar.geometry().top()
    assert bar.geometry().bottom() < button.geometry().top()
    assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())
    assert dialog.size().width() <= dialog.screen().availableGeometry().width()
    stages = dialog._aps_disk_read_stages
    assert stages.geometry().bottom() < label.contentsRect().top()
    for caption in stages.captions:
        required = caption.fontMetrics().boundingRect(caption.rect(), Qt.TextWordWrap, caption.text())
        assert required.height() <= caption.height()
        assert required.width() <= caption.width()

    for step, total, message in [
        (0, 100, 'Reading floppy image: 32 KB of 720 KB...'),
        (50, 100, 'Reading floppy...\n' + 'A long device description / ' * 60),
        (100, 100, 'Finishing capture...'),
        (0, 4, 'Converting raw SCP capture...'),
        (0, 0, 'Opening floppy contents...'),
        (0, 0, 'Cancelling...\nWaiting for the floppy drive to stop.'),
    ]:
        parent._apply_stage_progress(dialog, step, total, message)
        QApplication.processEvents()
        assert dialog.isVisible()
        assert dialog.geometry() == original
        assert len(label.text().splitlines()) <= 4
        assert all(label.fontMetrics().horizontalAdvance(line) <= label.width() for line in label.text().splitlines())
        assert label.geometry().bottom() < bar.geometry().top()
        assert bar.geometry().bottom() < button.geometry().top()

    full_message = '<b>literal filename</b> & ' + 'long-name-𝄞🎹-' * 100
    parent._set_progress_dialog_message(dialog, full_message)
    tooltip = QTextDocument()
    tooltip.setHtml(label.toolTip())
    assert tooltip.toPlainText() == full_message
    assert label.accessibleDescription() == full_message
    assert '\u2026' in label.text()
    dialog.hide()
    parent._set_progress_dialog_message(dialog, 'Reading floppy...')
    parent._show_centered_progress_dialog(dialog)
    assert dialog.geometry() == original
    dialog.resize(QSize(100, 100))
    assert dialog.geometry() == original
    dialog.close()


@pytest.mark.parametrize('operation', ['load', 'recover', 'capture'])
def test_disk_read_entrypoints_use_fixed_progress_and_keep_cancel_wired(window, tmp_path, monkeypatch, operation):
    worker_type, worker_attr, dialog_attr = {
        'load': (main_window.DiskSessionLoadWorker, 'diskLoadWorker', 'diskLoadProgressDialog'),
        'recover': (main_window.DiskSessionRecoveryWorker, 'diskRecoveryWorker', 'diskRecoveryProgressDialog'),
        'capture': (main_window.DiskImageCaptureWorker, 'diskImageCaptureWorker', 'diskImageCaptureProgressDialog'),
    }[operation]
    monkeypatch.setattr(worker_type, 'start', lambda _worker: None)
    monkeypatch.setattr(window, '_set_disk_load_busy', lambda _busy: None)
    if operation == 'load':
        window._start_disk_load_worker(
            load_kind='floppy_usb', source='A:', progress_title='Reading floppy', progress_total=100,
            initial_message='Reading floppy...', final_message='Opening floppy contents...', failure_title='Read failed',
        )
    elif operation == 'recover':
        window._start_disk_recovery_worker({'load_kind': 'floppy_usb', 'source': 'A:'})
    else:
        window._start_floppy_image_capture_worker('floppy_usb', 'A:', str(tmp_path / 'disk.img'))
    worker, dialog = getattr(window, worker_attr), getattr(window, dialog_attr)
    _settle_native_placement(dialog)
    original = dialog.geometry()
    assert dialog.minimumSize() == dialog.maximumSize()
    stages = dialog._aps_disk_read_stages
    worker.readPhaseChanged.emit('read')
    assert stages.active_phase == 'read'
    worker.progressChanged.emit(70, 100, 'Reading floppy...\n' + 'Read diagnostics ' * 30)
    assert dialog.geometry() == original
    dialog.findChild(QPushButton).click()
    QApplication.processEvents()
    assert worker._cancel_requested()
    assert stages.stopped and not stages.completed
    assert stages.animation.state() == QAbstractAnimation.Stopped
    assert dialog.geometry() == original


def test_read_stage_animation_tracks_work_not_percentage(window):
    from aps_midi_prep_tool_app.disk_session_worker import DiskSessionLoadWorker

    dialog = QProgressDialog('Reading floppy...', 'Cancel', 0, 100, window)
    window._prepare_disk_read_progress_dialog(dialog)
    worker = DiskSessionLoadWorker('floppy_usb', 'A:')
    window._connect_disk_read_stages(dialog, worker, worker.sessionLoaded)
    window._show_centered_progress_dialog(dialog)
    _settle_native_placement(dialog)
    stages = dialog._aps_disk_read_stages
    original = dialog.geometry()
    assert stages.active_phase == 'inspect'
    assert stages.animation.state() == QAbstractAnimation.Running
    worker.readPhaseChanged.emit('read')
    stages.animation.setCurrentTime(0)
    dim = stages.grab().toImage()
    stages.animation.setCurrentTime(1300)
    bright = stages.grab().toImage()
    assert bright != dim
    # The completed inspection graphic stays lit while the read graphic pulses.
    assert bright.copy(0, 0, bright.width() // 3, bright.height()) == dim.copy(0, 0, dim.width() // 3, dim.height())
    window._apply_stage_progress(dialog, 100, 100, 'Read completed; decoding capture...')
    window._apply_stage_progress(dialog, 0, 0, 'Preparing files...')
    assert stages.active_phase == 'read' and not stages.completed
    worker.readPhaseChanged.emit('organize')
    worker.readPhaseChanged.emit('read')  # A queued older event cannot regress the strip.
    assert stages.active_phase == 'organize'
    dialog.hide()
    assert stages.animation.state() == QAbstractAnimation.Stopped
    dialog.show()
    assert stages.animation.state() == QAbstractAnimation.Running
    worker.sessionLoaded.emit(object(), object())
    assert stages.completed
    assert stages.animation.state() == QAbstractAnimation.Stopped
    worker.readPhaseChanged.emit('inspect')
    assert stages.active_phase == 'organize'
    assert dialog.geometry() == original
    dialog.close()


def test_failed_read_stops_pulsing_without_claiming_completion(window):
    from aps_midi_prep_tool_app.disk_session_worker import DiskSessionLoadWorker

    dialog = QProgressDialog('Reading floppy...', 'Cancel', 0, 100, window)
    window._prepare_disk_read_progress_dialog(dialog)
    worker = DiskSessionLoadWorker('floppy_usb', 'A:')
    window._connect_disk_read_stages(dialog, worker, worker.sessionLoaded)
    dialog.show()
    stages = dialog._aps_disk_read_stages
    worker.readPhaseChanged.emit('read')
    worker.finished.emit()
    dialog.hide()
    dialog.show()
    worker.readPhaseChanged.emit('organize')
    worker.sessionLoaded.emit(object(), object())
    assert stages.active_phase == 'read'
    assert stages.stopped and not stages.completed
    assert stages.animation.state() == QAbstractAnimation.Stopped
    dialog.close()


def test_drive_discovery_has_no_disk_read_stages(window):
    dialog = QProgressDialog('Finding drives...', 'Cancel', 0, 0, window)
    window._prepare_disk_read_progress_dialog(dialog, show_stages=False)
    assert not hasattr(dialog, '_aps_disk_read_stages')
    dialog.close()

