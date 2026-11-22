"""Keep disk progress visible until cancellation and worker cleanup finish."""

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QPushButton

from .write_safety_messages import localize_write_message


# A non-None worker owns the busy state, even before QThread.start() runs.
_OPERATIONS = (
    ("diskLoadWorker", "diskLoadProgressDialog", "_on_disk_load_finished", "Read Floppy"),
    ("diskRecoveryWorker", "diskRecoveryProgressDialog", "_on_disk_recovery_finished", "Disk recovery"),
    ("diskImageCaptureWorker", "diskImageCaptureProgressDialog", "_on_floppy_image_capture_finished", "Floppy imaging"),
    ("diskFormatWorker", "diskFormatProgressDialog", "_on_floppy_format_finished", "Floppy formatting"),
    ("diskCommitWorker", "diskCommitProgressDialog", "_on_floppy_commit_finished", "Saving floppy disk"),
    ("diskWriteTargetWorker", "diskWriteTargetProgressDialog", "_on_write_image_to_floppy_finished", "Writing floppy disk"),
    ("bulkExtractionWorker", "bulkExtractionProgressDialog", "_on_bulk_extraction_finished", "Bulk extraction"),
    ("emulatorImageWorker", "emulatorImageProgressDialog", "_on_emulator_image_finished", "Emulator image creation"),
)


def _translate(window, key, **values):
    return window._t(key, **values)


def _workers(window):
    return [(spec, worker) for spec in _OPERATIONS
            if (worker := getattr(window, spec[0], None)) is not None]


def _finished(worker):
    method = getattr(worker, "isFinished", None)
    if not callable(method):
        return False
    try:
        return bool(method())
    except RuntimeError:
        # A deleted QObject cannot still own a running operation.
        return True


@dataclass
class _Activity:
    worker: object
    started: float
    updated: float
    stage: str = ""
    cancelled: float | None = None
    finished_seen: float | None = None


class _DiskActivity(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.activities = {}
        self.reconciling = False
        self.last_status_text = None
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)

    def sync(self):
        now = time.monotonic()
        active = _workers(self.window)
        names = {spec[0] for spec, _worker in active}
        for name in set(self.activities) - names:
            del self.activities[name]
        for spec, worker in active:
            activity = self.activities.get(spec[0])
            if activity is None or activity.worker is not worker:
                dialog = getattr(self.window, spec[1], None)
                stage = str(getattr(dialog, "_aps_last_disk_stage", "") or "")
                if not stage and isinstance(dialog, QProgressDialog):
                    stage = dialog.labelText()
                self.activities[spec[0]] = _Activity(worker, now, now, stage)
        return active

    def title(self, spec, dialog):
        if dialog is not None:
            try:
                title = dialog.windowTitle().strip()
                if title:
                    return title
            except RuntimeError:
                pass
        translate = getattr(self.window, "_lt", str)
        return translate(spec[3])

    def message(self, spec, activity, dialog):
        now = time.monotonic()
        title = self.title(spec, dialog)
        if activity.cancelled is not None:
            lines = [
                _translate(self.window, "activity.stopping", operation=title,
                           elapsed=max(0, int(now - activity.cancelled))),
                _translate(self.window, "activity.cancel_wait"),
            ]
        else:
            lines = [_translate(self.window, "activity.active", operation=title,
                                elapsed=max(0, int(now - activity.started)))]
        if activity.stage:
            lines.append(_translate(self.window, "activity.last_stage", stage=activity.stage))
        if activity.cancelled is None and now - activity.updated >= 10:
            lines.append(_translate(self.window, "activity.no_progress",
                                    elapsed=max(0, int(now - activity.updated))))
        return "\n".join(lines)

    def show_cancellation(self, spec, activity, dialog):
        if dialog is None:
            return
        message = self.message(spec, activity, dialog)
        try:
            if isinstance(dialog, QProgressDialog):
                dialog.setAutoReset(False)
                dialog.setAutoClose(False)
                dialog.setRange(0, 0)
                dialog.setLabelText(message)
                label = getattr(dialog, "_aps_stable_progress_label", None)
                if label is not None:
                    label.setToolTip(message)
                for button in dialog.findChildren(QPushButton):
                    button.setEnabled(False)
            elif hasattr(dialog, "bulkActivityLabel"):
                dialog.bulkActivityLabel.setText(message)
                dialog.bulkCancelButton.setEnabled(False)
                dialog.bulkCurrentProgressBar.setRange(0, 0)
            if not dialog.isVisible():
                dialog.show()
        except RuntimeError:
            # Terminal callbacks can have disposed the dialog during a nested
            # event loop. Its worker still owns the lock until finished.
            return

    def clear_running_status(self):
        label = getattr(self.window, "status_label", None)
        if (label is not None and self.last_status_text is not None
                and label.text() == self.last_status_text):
            label.setText(_translate(self.window, "activity.idle"))
        self.last_status_text = None

    def restore_idle(self):
        if _workers(self.window):
            return False
        self.timer.stop()
        self.activities.clear()
        self.clear_running_status()
        choose = getattr(self.window, "choose_button", None)
        if choose is not None and not choose.isEnabled():
            self.window._set_disk_write_busy(False)
            label = getattr(self.window, "status_label", None)
            if label is not None and not label.text():
                label.setText(_translate(self.window, "activity.idle"))
        return True

    def refresh(self):
        if self.reconciling:
            return
        active = self.sync()
        if not active:
            self.restore_idle()
            return
        now = time.monotonic()
        # Allow queued result/finished signals to run first. Only reconcile a
        # definitely finished QThread; !isRunning() also describes a new one.
        for spec, worker in active:
            activity = self.activities[spec[0]]
            if _finished(worker):
                if activity.finished_seen is None:
                    activity.finished_seen = now
                elif now - activity.finished_seen >= 0.5:
                    handler = getattr(self.window, spec[2], None)
                    if callable(handler) and getattr(self.window, spec[0], None) is worker:
                        self.reconciling = True
                        try:
                            handler()
                        finally:
                            self.reconciling = False
            else:
                activity.finished_seen = None
        active = self.sync()
        if not active:
            self.restore_idle()
            return
        spec, _worker = active[0]
        activity = self.activities[spec[0]]
        dialog = getattr(self.window, spec[1], None)
        message = self.message(spec, activity, dialog)
        label = getattr(self.window, "status_label", None)
        if label is not None:
            label.setText(message)
            self.last_status_text = message
        for spec, _worker in active:
            activity = self.activities[spec[0]]
            dialog = getattr(self.window, spec[1], None)
            if activity.cancelled is not None:
                self.show_cancellation(spec, activity, dialog)
            elif now - activity.updated >= 10 and isinstance(dialog, QProgressDialog):
                # Explain a silent helper/device wait in the visible progress
                # dialog as well as the main window behind it.
                try:
                    dialog.setLabelText(self.message(spec, activity, dialog))
                except RuntimeError:
                    pass


def _monitor(window):
    monitor = getattr(window, "_disk_activity_monitor", None)
    if monitor is None:
        monitor = window._disk_activity_monitor = _DiskActivity(window)
    return monitor


def start_disk_activity(window):
    monitor = _monitor(window)
    if monitor.sync():
        monitor.timer.start()
        monitor.refresh()


def stop_disk_activity(window):
    """Stop observing when no worker remains; never unlock a live operation."""
    monitor = getattr(window, "_disk_activity_monitor", None)
    if monitor is None:
        return
    if _workers(window):
        monitor.timer.start()
    else:
        monitor.timer.stop()
        monitor.activities.clear()
        monitor.clear_running_status()


def update_disk_activity(window, dialog, message):
    if dialog is None:
        return
    language = getattr(window, "_language_code", lambda: None)()
    stage = localize_write_message(window._lt(str(message or "")), language)
    dialog._aps_last_disk_stage = stage
    monitor = getattr(window, "_disk_activity_monitor", None)
    if monitor is None:
        return
    for spec, _worker in monitor.sync():
        if getattr(window, spec[1], None) is dialog:
            activity = monitor.activities[spec[0]]
            activity.stage = stage
            activity.updated = time.monotonic()
            if activity.cancelled is not None:
                monitor.show_cancellation(spec, activity, dialog)


def cancel_disk_activity(window, worker, dialog):
    monitor = _monitor(window)
    for spec, active_worker in monitor.sync():
        if active_worker is worker:
            activity = monitor.activities[spec[0]]
            if activity.cancelled is None:
                activity.cancelled = time.monotonic()
                worker.cancel()
            monitor.timer.start()
            monitor.refresh()
            # QProgressDialog hides itself when Cancel is clicked. Reopen on
            # the next event turn after its internal cancellation slot ends.
            QTimer.singleShot(0, monitor.refresh)
            return


def show_busy_operation(window):
    """Reveal the actual operation, or repair stale controls and allow action."""
    monitor = _monitor(window)
    monitor.refresh()
    active = monitor.sync()
    if not active:
        return False
    monitor.timer.start()
    spec, _worker = active[0]
    dialog = getattr(window, spec[1], None)
    if dialog is not None:
        try:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return True
        except RuntimeError:
            pass
    message = monitor.message(spec, monitor.activities[spec[0]], None)
    QMessageBox.information(window, window._lt("Busy"), message)
    return True
