"""Save failures retain physical captures and share a writable user default."""

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from aps_midi_prep_tool_app import capture_save, floppy_image as image, main_window
from test_save_as_overwrite import window


@pytest.mark.parametrize('failure', [PermissionError(errno.EACCES, 'Denied'), OSError(errno.ENOSPC, 'Full')])
def test_scp_save_failure_retries_captured_bytes_without_reread(window, tmp_path, monkeypatch, failure):
    scratch = tmp_path / 'capture'; scratch.mkdir()
    source = scratch / 'raw.scp'; source.write_bytes(b'SCP captured data')
    existing = tmp_path / 'existing.scp'; existing.write_bytes(b'previous capture')
    saved = tmp_path / 'alternate' / 'saved.scp'; saved.parent.mkdir()
    capture = image.GreaseweazleCapture(image.GreaseweazleFloppySource('test', 'A', image.DISK_FORMAT_BY_KEY['ibm.720']),
                                      str(source), str(scratch), {})
    choices = iter([str(existing), str(saved)])
    defaults, loads = [], []
    def choose(*args):
        assert source.read_bytes() == b'SCP captured data'
        assert existing.read_bytes() == b'previous capture'
        defaults.append(args[2])
        return next(choices), ''
    real_copy = image.shutil.copy2
    calls = []
    def copy(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            Path(dst).write_bytes(b'partial')
            # A full or disconnected user volume can invalidate every probed
            # default before the user chooses a different destination.
            monkeypatch.setattr(capture_save, '_writable_directory', lambda _: False)
            raise failure
        return real_copy(src, dst)
    monkeypatch.setattr(main_window.QFileDialog, 'getSaveFileName', choose)
    monkeypatch.setattr(image.shutil, 'copy2', copy)
    monkeypatch.setattr(window, '_show_greaseweazle_sector_reports', lambda *_: None)
    monkeypatch.setattr(window, '_start_disk_load_worker', lambda **kwargs: loads.append(kwargs))
    window._handle_greaseweazle_capture_ready({'capture': capture})
    assert len(defaults) == 2 and all(Path(p).is_absolute() for p in defaults)
    assert len(window._test_errors) == 1
    assert saved.read_bytes() == b'SCP captured data'
    assert existing.read_bytes() == b'previous capture'
    assert not scratch.exists()
    assert len(loads) == 1 and loads[0]['source']['capture_path'] == str(saved)
    assert window.settings.value(capture_save.SETTING_CAPTURE_DIRECTORY) == str(saved.parent)


def test_cancel_save_needs_explicit_discard(window, tmp_path, monkeypatch):
    scratch = tmp_path / 'capture'; scratch.mkdir()
    source = scratch / 'raw.scp'; source.write_bytes(b'SCP bytes')
    capture = image.GreaseweazleCapture(image.GreaseweazleFloppySource('test', 'A', image.DISK_FORMAT_BY_KEY['ibm.720']),
                                      str(source), str(scratch), {})
    decisions = iter([QMessageBox.No, QMessageBox.Yes])
    prompts = []
    def choose(*args):
        assert source.read_bytes() == b'SCP bytes'
        prompts.append(args)
        return '', ''
    monkeypatch.setattr(main_window.QFileDialog, 'getSaveFileName', choose)
    monkeypatch.setattr(main_window.QMessageBox, 'question', lambda *args: next(decisions))
    window._handle_greaseweazle_capture_ready({'capture': capture})
    assert len(prompts) == 2
    assert not scratch.exists()
    assert not window.settings.value(capture_save.SETTING_CAPTURE_DIRECTORY)


@pytest.mark.parametrize('saved_writable,documents_writable', [(True, True), (False, True), (False, False)])
def test_capture_default_is_absolute_and_ignores_program_files_cwd(tmp_path, monkeypatch, saved_writable, documents_writable):
    program = tmp_path / 'Program Files'; program.mkdir()
    monkeypatch.chdir(program)
    saved, documents, home = [tmp_path / name for name in ['Saved', 'Documents', 'Home']]
    for p in [saved, documents, home]: p.mkdir()
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    settings.setValue(capture_save.SETTING_CAPTURE_DIRECTORY, str(saved))
    qpaths = capture_save.QStandardPaths
    monkeypatch.setattr(qpaths, 'writableLocation', lambda kind: str(documents if kind == qpaths.DocumentsLocation else home))
    real_probe = capture_save._writable_directory
    def probe(path):
        if path == str(saved) and not saved_writable: return False
        if path == str(documents) and not documents_writable: return False
        return real_probe(path)
    monkeypatch.setattr(capture_save, '_writable_directory', probe)
    expected = saved if saved_writable else documents if documents_writable else home
    assert Path(capture_save.capture_save_path(settings, 'partial-recovery.img')) == expected / 'partial-recovery.img'
    assert list(program.iterdir()) == []


def test_no_writable_default_still_allows_browsing_another_drive(tmp_path, monkeypatch):
    program = tmp_path / 'Program Files'; program.mkdir()
    documents = tmp_path / 'Documents'; documents.mkdir()
    monkeypatch.chdir(program)
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr(capture_save.QStandardPaths, 'writableLocation', lambda _: str(documents))
    monkeypatch.setattr(capture_save, '_writable_directory', lambda _: False)
    def no_temp():
        raise OSError(errno.ENOSPC, 'No writable temporary directory')
    monkeypatch.setattr(capture_save.tempfile, 'gettempdir', no_temp)
    assert Path(capture_save.capture_save_path(settings, 'raw.scp')) == documents / 'raw.scp'
    assert list(program.iterdir()) == []
    assert not settings.value(capture_save.SETTING_CAPTURE_DIRECTORY)


def test_partial_save_remembers_only_success(window, tmp_path, monkeypatch):
    source = tmp_path / 'retained.img'; source.write_bytes(b'partial')
    output = tmp_path / 'chosen' / 'saved.img'; output.parent.mkdir()
    window.lastPartialRecoveryDiagnostics = {'partial_capture_path': str(source)}
    defaults = []
    monkeypatch.setattr(main_window.QFileDialog, 'getSaveFileName', lambda *a: (defaults.append(a[2]) or str(output), ''))
    def fail(*a): raise PermissionError('denied')
    monkeypatch.setattr(image, 'save_floppy_recovery_capture', fail)
    window.save_partial_recovery_capture()
    assert not window.settings.value(capture_save.SETTING_CAPTURE_DIRECTORY)
    assert source.read_bytes() == b'partial'
    monkeypatch.setattr(image, 'save_floppy_recovery_capture', lambda *a: (str(output), str(output)+'.json'))
    window.save_partial_recovery_capture()
    assert window.settings.value(capture_save.SETTING_CAPTURE_DIRECTORY) == str(output.parent)
    assert all(Path(p).is_absolute() for p in defaults)
