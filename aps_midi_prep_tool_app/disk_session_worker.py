from PySide6.QtCore import QThread, Signal

from .floppy_image import FloppyImageSession, FloppyOperationCancelled


class _CancellableDiskWorker(QThread):
    progressChanged = Signal(int, int, str)
    operationCancelled = Signal(str)

    def cancel(self):
        self.requestInterruption()

    def _cancel_requested(self):
        return self.isInterruptionRequested()

    def _raise_if_cancelled(self):
        if self._cancel_requested():
            raise FloppyOperationCancelled("Operation cancelled.")

    def _emit_progress(self, step, total, message):
        self._raise_if_cancelled()
        self.progressChanged.emit(int(step or 0), int(total or 0), str(message or ""))
        self._raise_if_cancelled()


class DiskSessionLoadWorker(_CancellableDiskWorker):
    sessionLoaded = Signal(object, object)
    loadFailed = Signal(str)

    def __init__(self, load_kind, source, *, final_total=0, final_message="", parent=None):
        super().__init__(parent)
        self.load_kind = load_kind
        self.source = source
        self.final_total = int(final_total or 0)
        self.final_message = final_message or ""

    def run(self):
        session = None
        try:
            if self.load_kind == "image":
                session = FloppyImageSession.load(
                    self.source,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            elif self.load_kind == "floppy_usb":
                session = FloppyImageSession.load_floppy(
                    self.source,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            elif self.load_kind == "floppy_gw":
                session = FloppyImageSession.load_greaseweazle(
                    self.source,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            else:
                raise ValueError(f"Unsupported disk session load kind: {self.load_kind}")

            if self.final_message:
                self._emit_progress(self.final_total, self.final_total, self.final_message)

            self._raise_if_cancelled()
            listing = session.list_entries()
            self._raise_if_cancelled()
            self.sessionLoaded.emit(session, listing)
            session = None
        except FloppyOperationCancelled as exc:
            if session is not None:
                session.cleanup()
            self.operationCancelled.emit(str(exc) or "Operation cancelled.")
        except Exception as exc:
            if session is not None:
                session.cleanup()
            self.loadFailed.emit(str(exc))


class DiskSessionRecoveryWorker(_CancellableDiskWorker):
    sessionRecovered = Signal(object, object)
    recoveryFailed = Signal(str)

    def __init__(self, load_kind, source, *, final_total=100, final_message="", parent=None):
        super().__init__(parent)
        self.load_kind = load_kind
        self.source = source
        self.final_total = int(final_total or 100)
        self.final_message = final_message or ""

    def run(self):
        session = None
        try:
            session = FloppyImageSession.recover(
                self.load_kind,
                self.source,
                progress_callback=self._emit_progress,
                cancel_callback=self._cancel_requested,
            )
            if self.final_message:
                self._emit_progress(self.final_total, self.final_total, self.final_message)
            self._raise_if_cancelled()
            listing = session.list_entries()
            self._raise_if_cancelled()
            self.sessionRecovered.emit(session, listing)
            session = None
        except FloppyOperationCancelled as exc:
            if session is not None:
                session.cleanup()
            self.operationCancelled.emit(str(exc) or "Operation cancelled.")
        except Exception as exc:
            if session is not None:
                session.cleanup()
            self.recoveryFailed.emit(str(exc))


class DiskSessionFormatWorker(_CancellableDiskWorker):
    sessionFormatted = Signal(object, object)
    formatFailed = Signal(str)

    def __init__(
        self,
        source_kind,
        source,
        *,
        disk_format=None,
        eseq_disk=False,
        volume_label="YAMAHA",
        parent=None,
    ):
        super().__init__(parent)
        self.source_kind = source_kind
        self.source = source
        self.disk_format = disk_format
        self.eseq_disk = bool(eseq_disk)
        self.volume_label = volume_label or "YAMAHA"

    def run(self):
        session = None
        try:
            if self.source_kind == "floppy_usb":
                session = FloppyImageSession.format_usb_floppy(
                    self.source,
                    self.disk_format,
                    eseq_disk=self.eseq_disk,
                    volume_label=self.volume_label,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            elif self.source_kind == "floppy_gw":
                session = FloppyImageSession.format_greaseweazle_floppy(
                    self.source,
                    eseq_disk=self.eseq_disk,
                    volume_label=self.volume_label,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            else:
                raise ValueError(f"Unsupported disk session format kind: {self.source_kind}")

            listing = session.list_entries()
            self.sessionFormatted.emit(session, listing)
            session = None
        except FloppyOperationCancelled as exc:
            if session is not None:
                session.cleanup()
            self.operationCancelled.emit(str(exc) or "Operation cancelled.")
        except Exception as exc:
            if session is not None:
                session.cleanup()
            self.formatFailed.emit(str(exc))


class DiskSessionCommitWorker(_CancellableDiskWorker):
    commitFinished = Signal(object)
    commitFailed = Signal(str)

    def __init__(self, session, operations, parent=None):
        super().__init__(parent)
        self.session = session
        self.operations = dict(operations or {})

    def run(self):
        try:
            self.session.commit_to_source(
                **self.operations,
                progress_callback=self._emit_progress,
                cancel_callback=self._cancel_requested,
            )
            listing = self.session.list_entries()
            self.commitFinished.emit(listing)
        except FloppyOperationCancelled as exc:
            self.operationCancelled.emit(str(exc) or "Operation cancelled.")
        except Exception as exc:
            self.commitFailed.emit(str(exc))


class DiskSessionWriteTargetWorker(_CancellableDiskWorker):
    writeFinished = Signal()
    writeFailed = Signal(str)

    def __init__(self, session, target_kind, target, operations, parent=None):
        super().__init__(parent)
        self.session = session
        self.target_kind = target_kind
        self.target = target
        self.operations = dict(operations or {})

    def run(self):
        try:
            self.session.write_to_floppy_target(
                self.target_kind,
                self.target,
                **self.operations,
                progress_callback=self._emit_progress,
                cancel_callback=self._cancel_requested,
            )
            self.writeFinished.emit()
        except FloppyOperationCancelled as exc:
            self.operationCancelled.emit(str(exc) or "Operation cancelled.")
        except Exception as exc:
            self.writeFailed.emit(str(exc))
