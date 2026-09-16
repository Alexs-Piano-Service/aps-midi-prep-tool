# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
import unittest
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
        self.assertEqual(resolve_source("/dev/fake-markiv9"), Path("/media/markiv"))

    @patch("aps_midi_prep_tool_app.markiv_backup.devices.discover_devices", return_value=[])
    def test_unmounted_device_does_not_get_opened(self, discover):
        with self.assertRaisesRegex(ValueError, "not mounted"):
            resolve_source("/dev/fake-markiv9")


if __name__ == "__main__":
    unittest.main()
