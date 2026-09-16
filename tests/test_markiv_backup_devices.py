# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from aps_midi_prep_tool_app.markiv_backup.devices import MountedDevice, parse_mountinfo, resolve_source


class DeviceTests(unittest.TestCase):
    def test_mountinfo_escaped_paths_and_readonly(self):
        devices = parse_mountinfo(
            "38 25 8:89 / /media/user/Piano\\040Music ro,nosuid,nodev - ext3 /dev/sdf9 ro\n"
            "39 25 0:28 / /run tmpfs rw - tmpfs tmpfs rw\n"
            "invalid line\n"
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].mountpoint, Path("/media/user/Piano Music"))
        self.assertTrue(devices[0].read_only)
        self.assertEqual(devices[0].device, "/dev/sdf9")

    @patch("aps_midi_prep_tool_app.markiv_backup.devices.discover_devices")
    def test_device_resolves_to_existing_mount(self, discover):
        discover.return_value = [MountedDevice("/dev/fake-markiv9", Path("/media/markiv"))]
        for source in ("/dev/fake-markiv9", Path("/dev/fake-markiv9")):
            with self.subTest(source=source):
                self.assertEqual(resolve_source(source), Path("/media/markiv").resolve())
        discover.assert_called_with(include_system=True)

    @patch("aps_midi_prep_tool_app.markiv_backup.devices.discover_devices", return_value=[])
    def test_unmounted_device_does_not_get_opened(self, discover):
        for source in ("/dev/fake-markiv9", Path("/dev/fake-markiv9")):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "not mounted"):
                    resolve_source(source)
        discover.assert_called_with(include_system=True)

    @patch("aps_midi_prep_tool_app.markiv_backup.devices.discover_devices")
    def test_directory_does_not_require_device_discovery(self, discover):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(resolve_source(directory), Path(directory).resolve())
        discover.assert_not_called()

    @patch("aps_midi_prep_tool_app.markiv_backup.devices.discover_devices")
    def test_missing_directory_is_not_treated_as_device(self, discover):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not an accessible directory"):
                resolve_source(Path(directory) / "missing")
        discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
