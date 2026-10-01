"""Beta Smart PianoSoft: insert media, verify pairings, prepare an album."""

from pathlib import Path
import tempfile
import threading

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .icon_utils import apply_window_icon
from .message_catalog import normalize_language_code, tr, translate_text
from .responsive_button_box import ResponsiveDialogButtonBox
from .smart_pianosoft_workflow import LoadedAlbumSource, scan_album, scan_loaded_album, prepare_album
from .ui_utils import resize_dialog_to_screen


class _WrappingLabel(QLabel):
    """Keep wrapped text tall enough when a scroll bar narrows the viewport."""

    def _fit_text_height(self):
        if self.wordWrap():
            self.setMinimumHeight(0)
            self.setMinimumHeight(max(0, self.heightForWidth(self.width())))

    def setText(self, text):
        super().setText(text)
        self._fit_text_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit_text_height()


class SmartPianoSoftWorker(QThread):
    progressChanged = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancelled = threading.Event()

    def run(self):
        try:
            result = self.task(self.cancelled, self.progressChanged.emit)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit({"error": str(exc), "cancelled": self.cancelled.is_set(),
                              "output": getattr(exc, "output_directory", None)})


def _scan_source(source, destination, *, cancel=None, progress=None):
    scanner = scan_loaded_album if isinstance(source, LoadedAlbumSource) else scan_album
    return scanner(source, destination, cancel=cancel, progress=progress)


class SmartPianoSoftDialog(QDialog):
    def __init__(self, settings, parent=None, *, discover_on_open=True, loaded_source=None):
        super().__init__(parent)
        apply_window_icon(self)
        self.settings = settings
        self.language = normalize_language_code(settings.value("language", "en"))
        self.worker = None
        self.album = None
        self.audio_paths = {}
        self.result_folder = None
        self._workspace = None
        self._closing = False
        self._operation = ""
        self._loaded_source = loaded_source
        self._using_loaded_source = loaded_source is not None
        self._discover_after_scan = False
        self.setWindowTitle(self.text("action"))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        outer = QVBoxLayout(self)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        description = self._label("description")
        layout.addWidget(description)

        self.options = QWidget()
        form = QFormLayout(self.options)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        source_text = (self.text("current_list_source", source=loaded_source.label)
                       if loaded_source is not None else str(settings.value("sps_source", "") or ""))
        self.source_edit = QLineEdit(source_text)
        self.source_edit.setReadOnly(self._using_loaded_source)
        self.source_edit.setPlaceholderText(self.text("source_hint"))
        self.source_edit.setObjectName("smartPianoSoftSource")
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        self.source_button = QPushButton(self.text("choose_source"))
        # These secondary actions must not replace the primary default when
        # focused or reserve the native style's extra default-button margins.
        self.source_button.setAutoDefault(False)
        menu = QMenu(self.source_button)
        if loaded_source is not None:
            menu.addAction(self.text("current_list"), self.use_current_list)
        menu.addAction(self.text("usb_floppy"), self.choose_floppy)
        menu.addAction(self.text("image"), self.choose_image)
        menu.addAction(self.text("folder"), self.choose_folder)
        self.source_button.setMenu(menu)
        source_row.addWidget(self.source_button)
        form.addRow(self._label("source"), source_row)

        self.cd_combo = QComboBox()
        self.cd_combo.setObjectName("smartPianoSoftCD")
        self.cd_combo.setEditable(True)
        self.cd_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cd_combo.setMinimumContentsLength(12)
        self.cd_combo.addItem(self.text("paired_files"), "")
        self.cd_combo.setInsertPolicy(QComboBox.NoInsert)
        self.refresh_button = QPushButton(translate_text("Refresh", self.language))
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self.discover_cd)
        cd_row = QHBoxLayout()
        cd_row.addWidget(self.cd_combo, 1)
        cd_row.addWidget(self.refresh_button)
        form.addRow(self._label("cd"), cd_row)

        self.output_edit = QLineEdit(str(settings.value("sps_output", "") or ""))
        self.output_edit.setObjectName("smartPianoSoftOutput")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        browse = QPushButton(translate_text("Browse...", self.language))
        browse.setAutoDefault(False)
        browse.clicked.connect(self.choose_output)
        output_row.addWidget(browse)
        form.addRow(self._label("output"), output_row)
        # WrapLongRows can omit column spacing when deciding whether a field
        # fits. Keep that gap inside the label so it counts toward row width.
        label_gap = max(0, form.horizontalSpacing())
        form.setHorizontalSpacing(0)
        for row in range(form.rowCount()):
            form.itemAt(row, QFormLayout.LabelRole).widget().setContentsMargins(0, 0, label_gap, 0)
        layout.addWidget(self.options)

        self.album_label = _WrappingLabel()
        self.album_label.setObjectName("smartPianoSoftAlbum")
        self.album_label.setTextFormat(Qt.PlainText)
        self.album_label.setWordWrap(True)
        self.album_label.hide()
        layout.addWidget(self.album_label)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("smartPianoSoftPairings")
        self.table.setHorizontalHeaderLabels([self.text(k) for k in ("track", "song", "midi", "audio")])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(self.fontMetrics().height() * 6)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.pair_audio())
        layout.addWidget(self.table, 1)
        self.pairing_buttons = ResponsiveDialogButtonBox()
        self.scan_button = QPushButton(self.text("scan"))
        self.scan_button.clicked.connect(self.start_scan)
        self.pair_button = QPushButton(self.text("pair"))
        self.pair_button.clicked.connect(self.pair_audio)
        self.automatic_button = QPushButton(self.text("automatic"))
        self.automatic_button.clicked.connect(self.clear_pairing)
        for button in (self.scan_button, self.pair_button, self.automatic_button):
            button.setAutoDefault(False)
            self.pairing_buttons.addButton(button, QDialogButtonBox.ActionRole)
        layout.addWidget(self.pairing_buttons)
        note = self._label("format_note")
        layout.addWidget(note)
        self.status = self._label("ready")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(400)
        self.details.setMinimumHeight(self.fontMetrics().height() * 3)
        self.details.setMaximumHeight(115)
        layout.addWidget(self.details)
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area, 1)

        self.buttons = ResponsiveDialogButtonBox()
        self.open_button = QPushButton(self.text("open"))
        self.open_button.clicked.connect(self.open_output)
        self.start_button = QPushButton(self.text("start"))
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start_preparation)
        self.cancel_button = QPushButton(translate_text("Close", self.language))
        self.cancel_button.clicked.connect(self.cancel_or_close)
        self.open_button.setAutoDefault(False)
        self.cancel_button.setAutoDefault(False)
        self.buttons.addButton(self.open_button, QDialogButtonBox.ActionRole)
        self.buttons.addButton(self.start_button, QDialogButtonBox.ActionRole)
        self.buttons.addButton(self.cancel_button, QDialogButtonBox.RejectRole)
        outer.addWidget(self.buttons)
        self.source_edit.textChanged.connect(self.invalidate_scan)
        self.table.itemSelectionChanged.connect(self._update_controls)
        self._update_controls()
        resize_dialog_to_screen(self, width=940, height=700)
        if loaded_source is not None:
            self._discover_after_scan = discover_on_open
            QTimer.singleShot(0, self.start_scan)
        elif discover_on_open:
            QTimer.singleShot(0, self.discover_cd)

    def text(self, key, **values):
        return tr("sps." + key, self.language, **values)

    def _label(self, key):
        label = _WrappingLabel(self.text(key))
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        return label

    @property
    def is_busy(self):
        return self.worker is not None

    def _update_controls(self):
        busy = self.is_busy
        self.options.setEnabled(not busy)
        self.scan_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        selected = self.table.currentRow() >= 0 and self.album is not None
        self.pair_button.setEnabled(not busy and selected)
        self.automatic_button.setEnabled(not busy and selected)
        self.open_button.setEnabled(not busy and self.result_folder is not None)
        self.cancel_button.setText(translate_text("Cancel" if busy else "Close", self.language))

    def _start(self, operation, task):
        if self.is_busy or self._closing:
            return
        self._operation = operation
        self.progress.setRange(0, 0)
        self.worker = SmartPianoSoftWorker(task, self)
        self.worker.progressChanged.connect(self._progress)
        self.worker.succeeded.connect(self._success)
        self.worker.failed.connect(self._failure)
        self.worker.finished.connect(self._finished)
        self._update_controls()
        self.worker.start()

    def discover_cd(self):
        if self.is_busy or self._closing:
            return
        def discover(_cancel, _progress):
            from .smart_pianosoft_media import discover_cd_drives
            return discover_cd_drives()
        self._start("discover", discover)

    def choose_floppy(self):
        from .disk_device_discovery import discover_floppy_devices
        from PySide6.QtWidgets import QInputDialog
        result = discover_floppy_devices(self, include_greaseweazle=False,
                                        translate=lambda value, **kw: translate_text(value, self.language, **kw))
        if result is None:
            return
        drives, _greaseweazles, issues = result
        for issue in issues:
            self.details.appendPlainText(issue)
        if not drives:
            self.status.setText(self.text("no_floppy"))
            return
        if len(drives) == 1:
            selected = drives[0]
        else:
            labels = [str(getattr(d, "display_name", "") or d.path) for d in drives]
            label, accepted = QInputDialog.getItem(self, self.text("usb_floppy"), self.text("source"), labels, 0, False)
            if not accepted:
                return
            selected = drives[labels.index(label)]
        self._select_external_source(selected.path)

    def choose_image(self):
        filename, _ = QFileDialog.getOpenFileName(self, self.text("image"), self.source_edit.text(),
                                                "Disk images (*.img *.ima *.dsk *.hfe)")
        if filename:
            self._select_external_source(filename)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.text("folder"), self.source_edit.text())
        if folder:
            self._select_external_source(folder)

    def _select_external_source(self, source):
        self._using_loaded_source = False
        self.source_edit.setReadOnly(False)
        self.source_edit.setText(str(source))

    def use_current_list(self):
        if self.is_busy or self._closing or self._loaded_source is None:
            return
        self._using_loaded_source = True
        self.source_edit.setReadOnly(True)
        self.source_edit.setText(self.text("current_list_source", source=self._loaded_source.label))
        self.start_scan()

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, self.text("output"), self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def invalidate_scan(self):
        self.album = None
        self.audio_paths.clear()
        self.table.setRowCount(0)
        self.album_label.clear()
        self.album_label.hide()
        self._update_controls()

    def _new_scan(self):
        if self._workspace is not None:
            self._workspace.cleanup()
        self._workspace = tempfile.TemporaryDirectory(prefix="aps-smart-pianosoft-")
        self.invalidate_scan()
        return Path(self._workspace.name) / "source"

    def _source(self):
        if self._using_loaded_source:
            return self._loaded_source
        source = self.source_edit.text().strip()
        if not source:
            self.choose_floppy()
            source = self.source_edit.text().strip()
        return source

    def start_scan(self):
        if self.is_busy or self._closing:
            return
        source = self._source()
        if not source:
            return
        target = self._new_scan()
        if not isinstance(source, LoadedAlbumSource):
            self.settings.setValue("sps_source", source)
        self._start("scan", lambda cancel, progress: _scan_source(source, target, cancel=cancel, progress=progress))

    def _cd_device(self):
        if self.cd_combo.currentText() == self.cd_combo.itemText(self.cd_combo.currentIndex()):
            return self.cd_combo.currentData() or ""
        return self.cd_combo.currentText().strip()

    def start_preparation(self):
        if self.is_busy or self._closing:
            return
        source = self._source()
        if not source:
            return
        if not self.output_edit.text().strip():
            self.choose_output()
        destination = self.output_edit.text().strip()
        if not destination:
            return
        if not isinstance(source, LoadedAlbumSource):
            self.settings.setValue("sps_source", source)
        self.settings.setValue("sps_output", destination)
        album, audio, cd_device = self.album, dict(self.audio_paths), self._cd_device()
        target = self._new_scan() if album is None else None
        def prepare(cancel, progress):
            snapshot = album or _scan_source(source, target, cancel=cancel, progress=progress)
            output = prepare_album(snapshot, destination, cd_device=cd_device, audio_paths=audio,
                                   cancel=cancel, progress=progress)
            return snapshot, output
        self._start("prepare", prepare)

    def _populate(self):
        self.album_label.setText(self.text("album", title=self.album.title))
        self.album_label.show()
        self.table.setRowCount(len(self.album.tracks))
        for row, track in enumerate(self.album.tracks):
            audio = self.audio_paths.get(track.number)
            values = (str(track.number), track.title, track.display_filename or track.filename,
                      str(audio) if audio else self.text("cd_hint", number=track.number))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        if self.album.tracks:
            self.table.selectRow(0)

    def pair_audio(self):
        row = self.table.currentRow()
        if self.is_busy or self.album is None or row < 0:
            return
        filename, _ = QFileDialog.getOpenFileName(self, self.text("pair"), "", "PCM WAV (*.wav)")
        if filename:
            self.audio_paths[self.album.tracks[row].number] = Path(filename)
            self._populate()
            self.table.selectRow(row)

    def clear_pairing(self):
        row = self.table.currentRow()
        if self.is_busy or self.album is None or row < 0:
            return
        self.audio_paths.pop(self.album.tracks[row].number, None)
        self._populate()
        self.table.selectRow(row)

    def _progress(self, detail):
        total = detail.get("total", 0)
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(round(100 * detail.get("completed", 0) / total))
        else:
            self.progress.setRange(0, 0)
        message = str(detail.get("message", ""))
        phase = detail.get("phase", "scan")
        self.status.setText(self.text("phase_" + phase) + (": " + message if message else ""))

    def _success(self, result):
        if self._operation == "discover":
            previous = self._cd_device()
            self.cd_combo.clear()
            self.cd_combo.addItem(self.text("paired_files"), "")
            for device, label in result:
                self.cd_combo.addItem(f"{label} ({device})", device)
            index = self.cd_combo.findData(previous) if previous else (1 if result else 0)
            self.cd_combo.setCurrentIndex(max(0, index))
        elif self._operation == "scan":
            self.album = result
            self._populate()
            self.status.setText(self.text("scanned", count=len(result.tracks)))
        else:
            self.album, self.result_folder = result
            self._populate()
            self.status.setText(self.text("complete"))
            self.details.appendPlainText(str(self.result_folder))
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self._operation != "discover" else 0)

    def _failure(self, result):
        self.status.setText(self.text("cancelled" if result["cancelled"] else "failed"))
        self.details.appendPlainText(result["error"])
        if result.get("output"):
            self.result_folder = Path(result["output"])
            self.details.appendPlainText(self.text("retained", folder=str(self.result_folder)))
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self._update_controls()
        if self._closing:
            self.close()
        elif self._discover_after_scan:
            self._discover_after_scan = False
            QTimer.singleShot(0, self.discover_cd)

    def cancel_or_close(self):
        if self.is_busy:
            self.worker.cancelled.set()
            self.status.setText(self.text("cancelling"))
        else:
            self.close()

    def open_output(self):
        if self.result_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_folder)))

    def reject(self):
        if self.is_busy:
            self._closing = True
            self.cancel_or_close()
        else:
            self._closing = True
            self._cleanup_workspace()
            super().reject()

    def _cleanup_workspace(self):
        if self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None

    def closeEvent(self, event):
        self._closing = True
        if self.is_busy:
            self.cancel_or_close()
            event.ignore()
            return
        self._cleanup_workspace()
        event.accept()
        super().closeEvent(event)
