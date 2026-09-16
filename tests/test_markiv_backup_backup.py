# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import threading
import unittest

from aps_midi_prep_tool_app.markiv_backup.backup import run_backup, verify_backup
from aps_midi_prep_tool_app.markiv_backup.library import Album, BackupPlan, PlannedFile, safe_name


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.target = self.root / 'target'
        self.originals = {'recording.MID': b'MThd\x00\xff\x00',
                          'recording.WAV': b'RIFF\x00\x01\xff',
                          'old.FIL': b'original E-SEQ bytes',
                          'song.mp3': b'ID3original audio'}
        for name, value in self.originals.items():
            (self.source / name).write_bytes(value)
        self.plan = BackupPlan(self.source, [
            PlannedFile(name, f'User/My Album/{name}', len(value), 'music', {},
                        (self.source / name).stat().st_mtime_ns)
            for name, value in self.originals.items()],
            [Album('My Album', 'User', 4, sum(map(len, self.originals.values())))], [],
            sum(map(len, self.originals.values())))

    def test_copy_preserves_bytes_and_verifies_all_formats(self):
        result = run_backup(self.plan, self.target)
        self.assertEqual((result.status, result.verified), ('complete', 4))
        self.assertEqual(verify_backup(result.folder), [])
        for name, value in self.originals.items():
            self.assertEqual((result.folder / 'User/My Album' / name).read_bytes(), value)
            self.assertEqual((self.source / name).read_bytes(), value)
        second = run_backup(self.plan, self.target)
        self.assertNotEqual(result.folder, second.folder)

    def test_verify_detects_same_length_corruption(self):
        result = run_backup(self.plan, self.target)
        p = result.folder / self.plan.files[0].destination
        p.write_bytes(b'x' * p.stat().st_size)
        self.assertTrue(any('checksum' in e for e in verify_backup(result.folder)))

    def test_rejects_destination_inside_source_and_symlink_alias(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        for target in [self.source, self.source / 'backup', alias / 'backup']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                run_backup(self.plan, target)

    def test_source_changed_since_scan_is_not_success(self):
        (self.source / 'old.FIL').write_bytes(b'changed')
        result = run_backup(self.plan, self.target)
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.verified, 3)
        self.assertTrue((result.folder / '.incomplete').exists())
        self.assertTrue(verify_backup(result.folder))

    def test_scan_coverage_error_prevents_complete_backup(self):
        self.plan.scan_errors = ['Could not scan songs/pianosoft/101: Permission denied']
        result = run_backup(self.plan, self.target)
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.verified, len(self.plan.files))
        self.assertTrue((result.folder / '.incomplete').exists())
        self.assertTrue(any('songs/pianosoft/101' in error for error in result.errors))
        manifest = json.loads((result.folder / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'incomplete')
        self.assertTrue(any('not complete' in error for error in verify_backup(result.folder)))

    def test_cancellation_leaves_honest_manifest(self):
        cancel = threading.Event()
        def progress(event):
            if event.get('completed', 0) >= 1:
                cancel.set()
        result = run_backup(self.plan, self.target, progress=progress, cancel=cancel)
        self.assertEqual(result.status, 'cancelled')
        manifest = json.loads((result.folder / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'cancelled')
        self.assertTrue((result.folder / '.incomplete').exists())
        self.assertEqual(len(list(result.folder.rglob('*.partial'))), 0)

    def test_rejects_source_symlink(self):
        (self.source / 'old.FIL').unlink()
        (self.source / 'old.FIL').symlink_to(self.root / 'outside')
        with self.assertRaises(ValueError):
            run_backup(self.plan, self.target)

    def test_rejects_destination_escape(self):
        self.plan.files[0].destination = '../../outside.mid'
        with self.assertRaises(ValueError):
            run_backup(self.plan, self.target)

    def test_verify_rejects_manifest_traversal(self):
        result = run_backup(self.plan, self.target)
        path = result.folder / 'manifest.json'
        data = json.loads(path.read_text())
        data['files'][0]['destination'] = '../../outside.mid'
        path.write_text(json.dumps(data))
        self.assertTrue(any('Unsafe' in e for e in verify_backup(result.folder)))

    def test_portable_names(self):
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertNotIn('/', safe_name('a/b'))
        self.assertLessEqual(len(safe_name('音' * 300).encode()), 150)


if __name__ == '__main__':
    unittest.main()
