"""Disk phase events follow acquisition boundaries, including failed retries."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.disk_session_worker import DiskSessionLoadWorker
from aps_midi_prep_tool_app.read_progress import ReadProgressMessage
from test_floppy_targeted_read import _blank, _device, _set_fat


@pytest.mark.parametrize('unreadable', [False, True])
def test_fast_read_phases_follow_physical_io(tmp_path, monkeypatch, unreadable):
    _source, data, geometry = _blank(tmp_path)
    _set_fat(data, geometry, 2, 0xFFF)
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b'SONG    MID', 2, 4)
    start = image._cluster_offset(geometry, 2)
    data[start:start + 4] = b'SONG'
    device = _device(monkeypatch, data, {start} if unreadable else set())
    events = []

    def progress(_step, _total, message):
        assert isinstance(message, str)
        if phase := getattr(message, 'read_phase', None):
            events.append((phase, list(device.calls)))

    def read():
        image._read_floppy_device_fast_image('mock', tmp_path / 'working.img', len(data), progress_callback=progress)

    if unreadable:
        with pytest.raises(image.FastFloppyReadError):
            read()
        assert [phase for phase, _ in events] == ['inspect', 'read']
    else:
        read()
        assert [phase for phase, _ in events] == ['inspect', 'read', 'organize']
        assert events[-1][1] == device.calls
        assert any(offset == start for offset, _size in events[-1][1])
    assert events[0][1] == []
    assert not any(offset == start for offset, _size in events[1][1])


def test_worker_preserves_explicit_phase_before_converting_message_to_qt_string(qt_application):
    worker = DiskSessionLoadWorker('floppy_usb', 'A:')
    events = []
    worker.readPhaseChanged.connect(lambda phase: events.append(('phase', phase)))
    worker.progressChanged.connect(lambda _step, _total, message: events.append(('message', message)))
    worker._emit_progress(25, 100, ReadProgressMessage('File data...', 'read'))
    worker._emit_progress(100, 100, 'Scanning contents...')
    assert events == [('phase', 'read'), ('message', 'File data...'), ('message', 'Scanning contents...')]
    worker.cancel()
    with pytest.raises(image.FloppyOperationCancelled):
        worker._emit_progress(0, 0, ReadProgressMessage('Organizing...', 'organize'))
    assert len(events) == 3


def test_greaseweazle_recovery_does_not_finish_reading_before_retry(tmp_path, monkeypatch):
    events = []
    attempts = []
    recovered = SimpleNamespace()
    capture_dir = tmp_path / 'capture'
    capture_dir.mkdir()
    monkeypatch.setattr(image.tempfile, 'mkdtemp', lambda **_kwargs: str(capture_dir))

    def capture(source, output_path, progress_callback, **_kwargs):
        attempts.append(source)
        image._notify_progress(progress_callback, 0, 100, 'Acquiring...', read_phase='read')
        Path(output_path).write_bytes(b'capture')
        return {}

    def recover(*_args, progress_callback, **_kwargs):
        image._notify_progress(progress_callback, 70, 100, 'Repairing candidate...', read_phase='organize')
        if len(attempts) == 1:
            raise image.FloppyImageError('Try the raw capture')
        return recovered

    monkeypatch.setattr(image, '_gw_read_floppy', capture)
    monkeypatch.setattr(image, '_gw_convert', lambda *_args, **_kwargs: '')
    monkeypatch.setattr(image.FloppyImageSession, '_recover_from_raw_image', recover)
    source = image.GreaseweazleFloppySource(device_path='', drive='A', disk_format=image.DISK_FORMATS[0])
    def progress(_step, _total, message):
        if phase := getattr(message, 'read_phase', None):
            events.append((phase, len(attempts)))

    result = image.FloppyImageSession._recover_greaseweazle(source, progress_callback=progress)
    assert result is recovered
    assert events == [('read', 1), ('read', 2), ('organize', 2)]
