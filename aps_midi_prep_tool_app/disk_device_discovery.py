"""Keep read-only device discovery outside the Qt process.

A removable-media driver can block even a capacity or volume-label query.
Each attempt owns fresh helper processes, which are terminated on cancellation
or timeout. A stuck OS call therefore cannot be reused by later retries.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QProgressDialog

from .disk_device_probe import probe_command as _probe_command
from .floppy_image import FloppyDriveInfo, GreaseweazleDeviceInfo, _terminate_process
from .subprocess_utils import windows_subprocess_kwargs


DISCOVERY_TIMEOUT_SECONDS = 10.0


class _DeviceProbe:
    def __init__(self, kind):
        self.kind = kind
        self.devices = []
        self.error = ""
        self.done = False
        self.closed = False
        self.stopped = threading.Event()
        self.process = None
        self._folder = tempfile.TemporaryDirectory(prefix="aps-device-probe-")
        self._result_path = Path(self._folder.name) / "result.json"
        try:
            self.process = subprocess.Popen(
                _probe_command(kind, self._result_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                **windows_subprocess_kwargs(),
            )
        except OSError as exc:
            self.error = str(exc)
            self.done = True

    def poll(self):
        if self.done:
            return True
        if self.process.poll() is None:
            return False
        try:
            result = json.loads(self._result_path.read_text(encoding="utf-8"))
            self.error = result["error"]
            if self.process.returncode and not self.error:
                raise ValueError(f"Device discovery exited with code {self.process.returncode}")
            for values in result["devices"]:
                if self.kind == "floppy":
                    values["mountpoints"] = tuple(values.get("mountpoints", ()))
                    self.devices.append(FloppyDriveInfo(**values))
                else:
                    self.devices.append(GreaseweazleDeviceInfo(**values))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.devices = []
            self.error = str(exc) or type(exc).__name__
        self.done = True
        return True

    def _reap(self):
        try:
            if os.name == "nt":
                _terminate_process(self.process)
            else:
                # The helper owns a session, including any discovery utilities.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            # Even a kernel-level failure to exit must not block the UI or retry.
            pass
        finally:
            try:
                self._folder.cleanup()
            finally:
                self.stopped.set()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process is not None and self.process.poll() is None:
            # Windows process-tree termination can itself wait for a driver.
            threading.Thread(target=self._reap, name="aps-discovery-cleanup", daemon=True).start()
        else:
            self._folder.cleanup()
            self.stopped.set()


def _start_probe(kind):
    return _DeviceProbe(kind)


def discover_floppy_devices(
    parent,
    *,
    include_greaseweazle=True,
    prepare_dialog=None,
    translate=None,
    timeout=DISCOVERY_TIMEOUT_SECONDS,
):
    """Return (floppy drives, Greaseweazles, issues), or None on cancellation.

    The wait runs a Qt event loop, so painting, timers, and Cancel still work.
    After a timeout, completed results remain available to the drive chooser;
    unfinished helpers are terminated and the next attempt starts fresh.
    """
    if translate is None:
        translate = lambda text, **kwargs: text.format(**kwargs)
    pending = {"floppy": (_start_probe("floppy"), "Floppy Drive")}
    if include_greaseweazle:
        pending["greaseweazle"] = (_start_probe("greaseweazle"), "Greaseweazle")
    deadline = time.monotonic() + timeout
    dialog = QProgressDialog(translate("Detecting floppy drives..."), translate("Cancel"), 0, 0, parent)
    dialog.setWindowTitle(translate("Detecting Floppy Drives"))
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.canceled.connect(dialog.reject)
    timer = QTimer(dialog)
    timer.setInterval(50)

    def check_finished():
        finished = [probe.poll() for probe, _label in pending.values()]
        if all(finished) or time.monotonic() >= deadline:
            dialog.done(QDialog.Accepted)

    timer.timeout.connect(check_finished)
    try:
        if prepare_dialog is not None:
            prepare_dialog(dialog)
        # Always complete inside exec(), including instant empty-device results.
        QTimer.singleShot(0, check_finished)
        timer.start()
        result = dialog.exec()
        if result != QDialog.Accepted or dialog.wasCanceled():
            return None

        devices = {"floppy": [], "greaseweazle": []}
        issues = []
        for kind, (probe, label) in pending.items():
            if not probe.poll():
                issues.append(translate(
                    "{device}: detection did not finish within {seconds} seconds.",
                    device=translate(label), seconds=f"{timeout:g}",
                ))
            elif probe.error:
                issues.append(f"{translate(label)}: {probe.error}")
            else:
                devices[kind] = probe.devices
        return devices["floppy"], devices["greaseweazle"], issues
    finally:
        timer.stop()
        dialog.deleteLater()
        for probe, _label in pending.values():
            probe.close()
