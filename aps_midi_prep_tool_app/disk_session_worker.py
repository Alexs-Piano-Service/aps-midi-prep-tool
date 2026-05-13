from PySide6.QtCore import QThread, Signal

from .floppy_image import (
    FloppyImageSession,
    FloppyOperationCancelled,
    GreaseweazleConversionError,
    capture_floppy_drive_image,
    capture_greaseweazle_floppy_image,
    convert_greaseweazle_image_file,
)


class _CancellableDiskWorker(QThread):
    progressChanged = Signal(int, int, str)
    operationCancelled = Signal(str)
    CANCELLED_MESSAGE = "Operation cancelled."

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_was_requested = False

    def cancel(self):
        self._cancel_was_requested = True
        self.requestInterruption()

    def _cancel_requested(self):
        return self._cancel_was_requested or self.isInterruptionRequested()

    def _raise_if_cancelled(self):
        if self._cancel_requested():
            raise FloppyOperationCancelled(self.CANCELLED_MESSAGE)

    def _emit_progress(self, step, total, message):
        self._raise_if_cancelled()
        self.progressChanged.emit(int(step or 0), int(total or 0), str(message or ""))
        self._raise_if_cancelled()

    def _looks_cancelled(self, exc):
        text = str(exc or "").strip().lower()
        return "cancelled" in text or "canceled" in text

    def _should_treat_as_cancelled(self, exc):
        return (
            isinstance(exc, FloppyOperationCancelled)
            or self._cancel_requested()
            or self._looks_cancelled(exc)
        )

    def _emit_cancelled(self, exc=None):
        message = str(exc or "").strip() if exc is not None else ""
        self.operationCancelled.emit(message or self.CANCELLED_MESSAGE)


class DiskSessionLoadWorker(_CancellableDiskWorker):
    sessionLoaded = Signal(object, object)
    captureReady = Signal(object)
    loadFailed = Signal(str)
    loadFailedWithDetails = Signal(object)

    def __init__(self, load_kind, source, *, final_total=0, final_message="", parent=None):
        super().__init__(parent)
        self.load_kind = load_kind
        self.source = source
        self.final_total = int(final_total or 0)
        self.final_message = final_message or ""

    def run(self):
        session = None
        capture = None
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
            elif self.load_kind == "floppy_gw_capture_only":
                source = self.source
                if isinstance(source, dict):
                    source = source.get("gw_source")
                capture = FloppyImageSession.capture_greaseweazle_archival(
                    source,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
                if self.final_message:
                    self._emit_progress(self.final_total, self.final_total, self.final_message)
                self._raise_if_cancelled()
                self.captureReady.emit(
                    {
                        "capture": capture,
                        "recover_after_capture": bool(
                            isinstance(self.source, dict)
                            and self.source.get("recover_after_capture")
                        ),
                    }
                )
                capture = None
                return
            elif self.load_kind == "floppy_gw_capture":
                session = FloppyImageSession.load_greaseweazle_capture(
                    self.source["gw_source"],
                    self.source["capture_path"],
                    self.source["disk_format"],
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
            if capture is not None:
                capture.cleanup()
            self._emit_cancelled(exc)
        except Exception as exc:
            if session is not None:
                session.cleanup()
            if capture is not None:
                capture.cleanup()
            if self._should_treat_as_cancelled(exc):
                self._emit_cancelled(exc)
                return
            if isinstance(exc, GreaseweazleConversionError):
                details = {
                    "type": "greaseweazle_conversion",
                    "message": str(exc),
                    "sector_map": exc.sector_map,
                    "disk_format": exc.disk_format,
                    "capture_path": exc.capture_path,
                    "reason": exc.reason,
                    "suggested_format": exc.suggested_format,
                    "source": self.source,
                }
                details.update(getattr(exc, "details", {}) or {})
                self.loadFailedWithDetails.emit(details)
                return
            self.loadFailed.emit(str(exc))


class DiskImageCaptureWorker(_CancellableDiskWorker):
    captureFinished = Signal(object)
    captureFailed = Signal(str)

    def __init__(self, source_kind, source, output_path, *, disk_format=None, parent=None):
        super().__init__(parent)
        self.source_kind = source_kind
        self.source = source
        self.output_path = output_path
        self.disk_format = disk_format

    def run(self):
        try:
            if self.source_kind == "floppy_usb":
                output_path = capture_floppy_drive_image(
                    self.source,
                    self.output_path,
                    disk_format=self.disk_format,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            elif self.source_kind == "floppy_gw":
                capture_result = capture_greaseweazle_floppy_image(
                    self.source,
                    self.output_path,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
                if isinstance(capture_result, tuple):
                    output_path, sector_map = capture_result
                else:
                    output_path = capture_result
                    sector_map = {}
            elif self.source_kind == "image_convert":
                output_path, sector_map = convert_greaseweazle_image_file(
                    self.source,
                    self.output_path,
                    self.disk_format,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
            else:
                raise ValueError(f"Unsupported disk image capture source kind: {self.source_kind}")

            self._raise_if_cancelled()
            sector_map = locals().get("sector_map", {})
            self.captureFinished.emit(
                {
                    "output_path": output_path,
                    "source_kind": self.source_kind,
                    "source": self.source,
                    "disk_format": self.disk_format,
                    "sector_map": sector_map,
                }
            )
        except FloppyOperationCancelled as exc:
            self._emit_cancelled(exc)
        except Exception as exc:
            if self._should_treat_as_cancelled(exc):
                self._emit_cancelled(exc)
                return
            self.captureFailed.emit(str(exc))


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
            self._emit_cancelled(exc)
        except Exception as exc:
            if session is not None:
                session.cleanup()
            if self._should_treat_as_cancelled(exc):
                self._emit_cancelled(exc)
                return
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
            self._emit_cancelled(exc)
        except Exception as exc:
            if session is not None:
                session.cleanup()
            if self._should_treat_as_cancelled(exc):
                self._emit_cancelled(exc)
                return
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
            self._emit_cancelled(exc)
        except Exception as exc:
            if self._should_treat_as_cancelled(exc):
                self._emit_cancelled(exc)
                return
            self.commitFailed.emit(str(exc))


class DiskSessionWriteTargetWorker(_CancellableDiskWorker):
    writeFinished = Signal()
    writeFailed = Signal(str)

    def __init__(self, session, target_kind, target, operations, parent=None, file_level=False):
        super().__init__(parent)
        self.session = session
        self.target_kind = target_kind
        self.target = target
        self.operations = dict(operations or {})
        self.file_level = bool(file_level)

    def run(self):
        try:
            self.session.write_to_floppy_target(
                self.target_kind,
                self.target,
                **self.operations,
                file_level=self.file_level,
                progress_callback=self._emit_progress,
                cancel_callback=self._cancel_requested,
            )
            self.writeFinished.emit()
        except FloppyOperationCancelled as exc:
            self._emit_cancelled(exc)
        except Exception as exc:
            if self._should_treat_as_cancelled(exc):
                self._emit_cancelled(exc)
                return
            self.writeFailed.emit(str(exc))
