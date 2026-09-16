"""Keep read-only device discovery off the Qt event thread.

Some removable-media drivers can block even a capacity or volume-label query.
Python cannot safely interrupt those calls. A bounded, cancellable wait lets the
UI recover, while one daemon probe per device kind prevents retries from piling
up threads or delaying application exit. These probes must never perform writes.
"""

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QProgressDialog


DISCOVERY_TIMEOUT_SECONDS = 10.0
_probes = {}
_probe_lock = threading.Lock()


@dataclass
class _DeviceProbe:
    done: threading.Event = field(default_factory=threading.Event)
    devices: list = field(default_factory=list)
    error: str = ""
    collected: bool = False

    def run(self, callback):
        try:
            self.devices = list(callback())
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
        finally:
            self.done.set()


def _start_probe(kind, callback):
    with _probe_lock:
        probe = _probes.get(kind)
        if probe is None or (probe.done.is_set() and probe.collected):
            probe = _DeviceProbe()
            _probes[kind] = probe
            threading.Thread(
                target=probe.run,
                args=(callback,),
                name=f"aps-{kind}-discovery",
                daemon=True,
            ).start()
        return probe


def discover_floppy_devices(
    parent,
    *,
    floppy_probe,
    greaseweazle_probe=None,
    prepare_dialog=None,
    translate=None,
    timeout=DISCOVERY_TIMEOUT_SECONDS,
):
    """Return (floppy drives, Greaseweazles, issues), or None on cancellation.

    The wait runs a Qt event loop, so painting, timers, and Cancel still work.
    Background callbacks hold no widgets and never touch Qt. After a timeout,
    any other probe's completed results remain available to the drive chooser.
    """
    if translate is None:
        translate = lambda text, **kwargs: text.format(**kwargs)
    pending = {"floppy": (_start_probe("floppy", floppy_probe), "Floppy Drive")}
    if greaseweazle_probe is not None:
        pending["greaseweazle"] = (
            _start_probe("greaseweazle", greaseweazle_probe), "Greaseweazle",
        )
    deadline = time.monotonic() + timeout
    dialog = QProgressDialog(translate("Detecting floppy drives..."), translate("Cancel"), 0, 0, parent)
    dialog.setWindowTitle(translate("Detecting Floppy Drives"))
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.canceled.connect(dialog.reject)
    if prepare_dialog is not None:
        prepare_dialog(dialog)
    timer = QTimer(dialog)
    timer.setInterval(50)

    def check_finished():
        if all(probe.done.is_set() for probe, _label in pending.values()) or time.monotonic() >= deadline:
            dialog.done(QDialog.Accepted)

    timer.timeout.connect(check_finished)
    # Always complete inside exec(), including instant empty-device results.
    QTimer.singleShot(0, check_finished)
    timer.start()
    try:
        result = dialog.exec()
    finally:
        timer.stop()
        dialog.deleteLater()
    if result != QDialog.Accepted or dialog.wasCanceled():
        return None

    devices = {"floppy": [], "greaseweazle": []}
    issues = []
    for kind, (probe, label) in pending.items():
        if not probe.done.is_set():
            issues.append(translate(
                "{device}: detection did not finish within {seconds} seconds.",
                device=translate(label), seconds=f"{timeout:g}",
            ))
        else:
            probe.collected = True
            if probe.error:
                issues.append(f"{translate(label)}: {probe.error}")
            else:
                devices[kind] = probe.devices
    return devices["floppy"], devices["greaseweazle"], issues
