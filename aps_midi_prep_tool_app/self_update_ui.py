"""Explicit, cancellable self-update UI and shutdown coordination."""

import sys

from PySide6.QtCore import QProcess, QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QProgressBar, QVBoxLayout

from .icon_utils import apply_window_icon
from .localized_dialogs import QMessageBox
from .self_update import UpdateCancelled, cleanup_staged_update, detect_update_target, prepare_update
from .update_installer import prepare_handoff


class SelfUpdateWorker(QThread):
    progressChanged = Signal(int)
    preparingRestart = Signal()

    def __init__(self, version, parent=None):
        super().__init__(parent)
        self.version = version
        self.handoff = None
        self.error = ""

    def run(self):
        staged = None
        handoff_started = False
        try:
            staged = prepare_update(
                self.version,
                progress_callback=lambda done, total: self.progressChanged.emit(
                    min(100, int(100 * done / total)) if total else 0
                ),
                cancel_callback=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                raise UpdateCancelled()
            self.preparingRestart.emit()
            handoff_started = True
            self.handoff = prepare_handoff(staged, cancel_callback=self.isInterruptionRequested)
            if self.isInterruptionRequested():
                self.handoff.cancel()
                self.handoff = None
        except UpdateCancelled:
            if staged is not None and not handoff_started:
                cleanup_staged_update(staged)
        except Exception as exc:
            self.error = str(exc)
            if staged is not None and not handoff_started:
                cleanup_staged_update(staged)


class SelfUpdateDialog(QDialog):
    def __init__(self, parent, version):
        super().__init__(parent)
        self._t = parent._t
        self.handoff = None
        self.error = ""
        self._cancelled = False
        self.setWindowTitle(self._t("update.install.title"))
        self.setMinimumWidth(420)
        apply_window_icon(self)
        layout = QVBoxLayout(self)
        self.label = QLabel(self._t("update.install.download"))
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.worker = SelfUpdateWorker(version, self)
        self.worker.progressChanged.connect(self.progress.setValue)
        self.worker.preparingRestart.connect(self._preparing_restart)
        # Only finished releases the dialog: success from run() is too early
        # to destroy the QThread or close the application's main window.
        self.worker.finished.connect(self._finished)
        QTimer.singleShot(0, self._start_worker)

    def _start_worker(self):
        if self._cancelled:
            self._finished()
        else:
            self.worker.start()

    @property
    def is_running(self):
        return self.worker is not None

    def _preparing_restart(self):
        if not self._cancelled:
            self.label.setText(self._t("update.install.restart"))
            self.progress.setRange(0, 0)

    def reject(self):
        if self.worker is not None:
            self._cancelled = True
            self.worker.requestInterruption()
            self.buttons.setEnabled(False)
            self.label.setText(self._t("update.install.cancel"))
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_running:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

    def _finished(self):
        worker, self.worker = self.worker, None
        self.error = worker.error
        self.handoff = worker.handoff
        worker.deleteLater()
        if self._cancelled and self.handoff is not None:
            self.handoff.cancel()
            self.handoff = None
        if self.handoff is not None:
            self.accept()
        else:
            super().reject()


class SelfUpdateMixin:
    def _self_update_supported(self):
        try:
            return detect_update_target() is not None
        except (OSError, ValueError):
            return False

    def _self_update_busy(self):
        if self._disk_worker_busy():
            return True
        if getattr(self, "_staging_depth", 0) or getattr(self.table, "_zip_import_use_depth", 0):
            return True
        if any(getattr(self, name, None) is not None for name in
               ("updateCheckWorker", "bugReportWorker", "feedbackWorker")):
            return True
        markiv = getattr(self, "markivBackupDialog", None)
        if markiv is not None and markiv.is_busy:
            return True
        if any(worker.isRunning() for worker in self.findChildren(QThread)):
            return True
        if any(process.state() != QProcess.NotRunning for process in self.findChildren(QProcess)):
            return True
        for dialog in self.findChildren(QDialog):
            for name in ("live_synth_process", "midi_output_process"):
                process = getattr(dialog, name, None)
                if isinstance(process, QProcess):
                    if process.state() != QProcess.NotRunning:
                        return True
                elif callable(getattr(process, "poll", None)) and process.poll() is None:
                    return True
        # Tool dialogs can own their own render/download/device workers. Do
        # not close them from the main window while they are still in use.
        if any(dialog.isVisible() for dialog in self.findChildren(QDialog)):
            return True
        modal = QApplication.activeModalWidget()
        return modal is not None and modal is not self

    def _self_update_has_pending_changes(self):
        return bool(
            self._pending_changes_to_discard()
            or getattr(self, "pendingRegularOrderKeyEdits", {})
            or (self.is_image_mode() and self._has_pending_image_changes())
        )

    def _self_update_resolve_pending(self):
        """Return permission to continue; discard only when shutdown succeeds."""
        if not self._self_update_has_pending_changes():
            return True
        choice = QMessageBox.question(
            self, self._t("update.install.title"), self._t("update.install.pending"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if choice == QMessageBox.Discard:
            return True
        if choice != QMessageBox.Save:
            return False
        self.save_pending_changes()
        # Asynchronous disk saves must finish before a subsequent update try.
        return not self._self_update_busy() and not self._self_update_has_pending_changes()

    def _self_update_show_error(self, error):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle(self._t("update.install.error_title"))
        dialog.setTextFormat(Qt.PlainText)
        dialog.setText(self._t("update.install.failed"))
        dialog.setDetailedText(str(error))
        self._exec_child_dialog(dialog)

    def _start_self_update(self, version):
        if getattr(self, "_self_update_requested", False):
            return
        if self._self_update_busy():
            QMessageBox.information(self, self._t("update.install.title"), self._t("update.install.busy"))
            return
        self._self_update_requested = True
        handoff = None
        try:
            if not self._self_update_resolve_pending():
                return
            before = self._staged_signature()
            dialog = SelfUpdateDialog(self, version)
            self.selfUpdateDialog = dialog
            try:
                self._exec_child_dialog(dialog, resize_to_contents=False)
                handoff = dialog.handoff
                error = dialog.error
            finally:
                self.selfUpdateDialog = None
                dialog.deleteLater()
            if error:
                self._self_update_show_error(error)
                return
            if handoff is None:
                return
            if self._self_update_busy():
                QMessageBox.information(self, self._t("update.install.title"), self._t("update.install.busy"))
                return
            if before != self._staged_signature() and not self._self_update_resolve_pending():
                return
            self.settings.sync()
            if self.settings.status() != QSettings.NoError:
                raise OSError("Could not save application preferences before restarting.")
            # Permission above includes image changes, so closeEvent must not
            # display a second discard question. It still applies busy guards.
            self._self_update_discard_authorized = True
            self._self_update_close_pending = True
            app = QApplication.instance()
            quit_on_close = app.quitOnLastWindowClosed()
            app.setQuitOnLastWindowClosed(False)
            try:
                if self.close():
                    try:
                        handoff.commit()
                    except Exception:
                        # closeEvent accepted the request but has kept session
                        # resources alive until authorization is durable.
                        self.show()
                        raise
                    handoff = None
                    try:
                        self._cleanup_for_close()
                    except Exception as exc:
                        # The restart is already authorized. Do not turn a
                        # scratch-file cleanup error into a second modal dialog
                        # that prevents shutdown while the helper waits.
                        print(f"Update shutdown cleanup: {exc}", file=sys.stderr)
                    finally:
                        QApplication.quit()
            finally:
                app.setQuitOnLastWindowClosed(quit_on_close)
        except Exception as exc:
            self._self_update_show_error(exc)
        finally:
            self._self_update_discard_authorized = False
            self._self_update_close_pending = False
            self._self_update_requested = False
            if handoff is not None:
                handoff.cancel()
