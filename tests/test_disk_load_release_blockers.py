"""Consent must survive unavailable audio dependencies; recovery needs a media error."""

import errno
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import disk_session_worker as workers, floppy_image as image
from test_smart_pianosoft import _psong_bytes
from test_smart_pianosoft_loaded_list import _midi_bytes
from test_save_as_overwrite import window


@pytest.mark.parametrize('smart,protected', [(False, False), (False, True), (True, False)])
def test_disk_consent_and_protected_normal_reader_without_numpy(tmp_path, smart, protected):
    payloads = {'PSONG.MNG': _psong_bytes([('SONG.MID', 'Synthetic song')]),
                'SONG.MID': _midi_bytes(fingerprints=smart)}
    specs = []
    for name, data in payloads.items():
        path = tmp_path / name
        path.write_bytes(data)
        specs.append({'host_path': str(path), 'image_path': name})
    source = tmp_path / 'protected.img'
    image.create_floppy_images_from_files(specs, str(source), 'img', image.DISK_FORMAT_BY_KEY['ibm.720'])
    if protected:
        data = bytearray(source.read_bytes())
        data[:512] = bytes(512)
        source.write_bytes(data)
    # A new process is essential: importing the test helpers above loads NumPy.
    script = r'''
import importlib.abc, sys
class NoAudio(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'numpy':
            raise ImportError('NumPy native runtime unavailable')
sys.meta_path.insert(0, NoAudio())
from pathlib import Path
from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.disk_session_worker import DiskSessionLoadWorker
source = Path(sys.argv[1])
# Exercise load_floppy's existing targeted/protected read path against a
# deterministic read-only block-device fixture; never enter recovery.
class Device:
    def read_at(self, offset, size, label):
        with source.open('rb') as stream:
            stream.seek(offset)
            return stream.read(size)
    def close(self): pass
image._open_block_device_for_read = lambda path: Device()
image.FloppyImageSession.recover = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Recovery selected'))
worker = DiskSessionLoadWorker('floppy_usb', image.FloppyDriveInfo(str(source), source.stat().st_size))
worker.yamaha_consent_required = True
consents, successes, failures = [], [], []
def approve(request):
    consents.append(request)
    request.resolve(True)
worker.yamahaConsentRequested.connect(approve)
worker.sessionLoaded.connect(lambda s, l: successes.append((s, l)))
worker.loadFailedWithDetails.connect(failures.append)
worker.loadFailed.connect(failures.append)
worker.run()
assert not failures, failures
assert len(consents) == int(sys.argv[2])
assert len(successes) == 1
session, listing = successes[0]
try:
    assert {e.path for e in listing.entries} >= {'SONG.MID', 'PSONG.MNG'}
    for name in ['SONG.MID', 'PSONG.MNG']:
        assert Path(session.extract_file(name)).read_bytes() == (source.parent / name).read_bytes()
    assert session.source_boot_sector_repaired == bool(int(sys.argv[3]))
finally:
    session.cleanup()
assert 'numpy' not in sys.modules
assert 'aps_midi_prep_tool_app.smart_pianosoft_sync' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', script, str(source), str(int(smart or protected)), str(int(protected))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('stage', ['acquisition', 'listing', 'consent'])
@pytest.mark.parametrize('error,category', [
    (ModuleNotFoundError("No module named 'numpy'"), 'dependency'),
    (image.FloppyDependencyError('Missing mcopy helper'), 'dependency'),
    (OSError(errno.ENOSPC, 'No space left'), 'local_storage'),
    (PermissionError(errno.EACCES, 'Access denied'), 'local_storage'),
    (RuntimeError('Application invariant failed'), 'application'),
])
def test_application_failures_keep_stage_and_do_not_offer_recovery(window, monkeypatch, stage, error, category):
    cleaned = []
    def fail(*args, **kwargs): raise error
    session = SimpleNamespace(list_entries=lambda: object(), cleanup=lambda: cleaned.append(True),
                              read_diagnostics={'fast_path': {'completed': True}})
    worker = workers.DiskSessionLoadWorker('floppy_usb', image.FloppyDriveInfo('A:', 737280))
    monkeypatch.setattr(image.FloppyImageSession, 'load_floppy', fail if stage == 'acquisition' else lambda *a, **k: session)
    if stage == 'listing': session.list_entries = fail
    if stage == 'consent': monkeypatch.setattr(worker, '_confirm_yamaha_session', fail)
    details, plain = [], []
    worker.loadFailedWithDetails.connect(details.append)
    worker.loadFailed.connect(plain.append)
    worker.run()
    assert not plain
    failure, = details
    assert failure['type'] == category
    assert failure['exception_type'] == type(error).__name__
    assert failure['failure_stage'] == stage
    assert failure['recoverable'] is False
    assert failure['traceback'] and failure['message'] == str(error)
    assert cleaned == ([] if stage == 'acquisition' else [True])
    window.diskLoadContext = {'load_kind': 'floppy_usb', 'source': worker.source}
    window._on_disk_load_failure_with_details(failure)
    assert window.pendingDiskRecoveryRequest is None
    assert window._test_errors[-1][0][0] == 'Application Error'


def test_real_media_failure_still_offers_recovery(window, monkeypatch):
    def fail(*a, **k): raise image.FloppyImageError('Unreadable FAT sector', winerror=23)
    monkeypatch.setattr(image.FloppyImageSession, 'load_floppy', fail)
    worker = workers.DiskSessionLoadWorker('floppy_usb', image.FloppyDriveInfo('A:', 737280))
    failures = []
    worker.loadFailedWithDetails.connect(failures.append)
    worker.run()
    failure, = failures
    assert failure['recoverable'] is True
    assert failure['failure_stage'] == 'acquisition'
    window.diskLoadContext = {'load_kind': 'floppy_usb', 'source': worker.source}
    window._on_disk_load_failure_with_details(failure)
    assert window.pendingDiskRecoveryRequest['message'] == str('Unreadable FAT sector')
    assert not window._test_errors
