from PySide6.QtCore import QThread, Signal

from .floppy_image import FloppyImageSession


class DiskSessionLoadWorker(QThread):
    progressChanged = Signal(int, int, str)
    sessionLoaded = Signal(object, object)
    loadFailed = Signal(str)

    def __init__(self, load_kind, source, *, final_total=0, final_message="", parent=None):
        super().__init__(parent)
        self.load_kind = load_kind
        self.source = source
        self.final_total = int(final_total or 0)
        self.final_message = final_message or ""

    def _emit_progress(self, step, total, message):
        self.progressChanged.emit(int(step or 0), int(total or 0), str(message or ""))

    def run(self):
        session = None
        try:
            if self.load_kind == "image":
                session = FloppyImageSession.load(self.source, progress_callback=self._emit_progress)
            elif self.load_kind == "floppy_usb":
                session = FloppyImageSession.load_floppy(self.source, progress_callback=self._emit_progress)
            elif self.load_kind == "floppy_gw":
                session = FloppyImageSession.load_greaseweazle(self.source, progress_callback=self._emit_progress)
            else:
                raise ValueError(f"Unsupported disk session load kind: {self.load_kind}")

            if self.final_message:
                self._emit_progress(self.final_total, self.final_total, self.final_message)

            listing = session.list_entries()
            self.sessionLoaded.emit(session, listing)
            session = None
        except Exception as exc:
            if session is not None:
                session.cleanup()
            self.loadFailed.emit(str(exc))
