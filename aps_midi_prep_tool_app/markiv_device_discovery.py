"""Discover mounted Mark IV sources in a disposable, read-only helper process."""

from pathlib import Path
import sys
import threading
import time

from .markiv_backup.devices import MountedDevice, discover_devices
from .markiv_backup.library import check_cancel


DISCOVERY_TIMEOUT_SECONDS = 10.0


def mounted_sources():
    """Run potentially blocking volume and filesystem queries only in a helper."""
    if sys.platform.startswith("linux"):
        return discover_devices()

    from PySide6.QtCore import QStorageInfo

    devices = []
    for volume in QStorageInfo.mountedVolumes():
        if not volume.isValid() or not volume.isReady():
            continue
        root = Path(volume.rootPath())
        devices.append(MountedDevice(
            device=bytes(volume.device()).decode(errors="replace"),
            mountpoint=root,
            filesystem=bytes(volume.fileSystemType()).decode(errors="replace"),
            label=volume.displayName(),
            read_only=volume.isReadOnly(),
            is_mark_iv=(root / "songs").is_dir(),
        ))
    return sorted(devices, key=lambda device: (not device.is_mark_iv, str(device.mountpoint)))


def discover_mounted_sources(*, cancel=None, timeout=None):
    """Bound the entire discovery attempt, including filesystem queries.

    The caller may be a QThread, but it only polls this helper and waits on
    its cancellation event. Process cleanup is asynchronous, so a driver
    that also stalls termination cannot keep the dialog or a retry waiting.
    """
    from .disk_device_discovery import _DeviceProbe

    if cancel is None:
        cancel = threading.Event()
    if timeout is None:
        timeout = DISCOVERY_TIMEOUT_SECONDS
    check_cancel(cancel)
    deadline = time.monotonic() + timeout
    probe = _DeviceProbe("markiv")
    try:
        while True:
            check_cancel(cancel)
            if probe.poll():
                if probe.error:
                    raise OSError(probe.error)
                return probe.devices
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Drive detection did not finish within {timeout:g} seconds.")
            cancel.wait(min(0.05, remaining))
    finally:
        probe.close()
