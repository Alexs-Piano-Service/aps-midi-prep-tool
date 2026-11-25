"""Smart PianoSoft: insert media, verify pairings, prepare an album."""

from pathlib import Path
import shutil
import tempfile
import threading

from PySide6.QtCore import QEvent, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QStyle, QStyleOptionButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .floppy_image import (
    DISK_FORMATS, FloppyDriveInfo, FloppyOperationCancelled, GreaseweazleDeviceInfo,
    GreaseweazleFloppySource,
)
from .icon_utils import apply_window_icon
from .localized_dialogs import QMessageBox
from .helpers.output_names import random_output_suffixes_enabled
from .message_catalog import normalize_language_code, tr, translate_text
from .audio_preparation import audio_playback_note_key, default_audio_output
from .preparation_profiles import (
    SETTING_PROFILE, get_preparation_profile, generate_yamaha_catalogs_enabled, preserves_yamaha_midi_catalogs,
)
from .responsive_button_box import ResponsiveDialogButtonBox
from .smart_pianosoft_workflow import LoadedAlbumSource, scan_album, scan_loaded_album, prepare_album
from .ui_utils import center_dialog_on_parent, resize_dialog_to_screen


class _PreparationButton(QPushButton):
    """Reserve native button space for both Start and Restart captions."""

    def __init__(self):
        super().__init__()
        self._caption_choices = ()

    def set_caption_choices(self, captions):
        captions = tuple(captions)
        if captions != self._caption_choices:
            self._caption_choices = captions
            self.updateGeometry()

    def sizeHint(self):
        size = super().sizeHint()
        option = QStyleOptionButton()
        self.initStyleOption(option)
        for caption in self._caption_choices:
            option.text = caption
            contents = self.fontMetrics().size(Qt.TextShowMnemonic, caption)
            if not option.icon.isNull():
                contents.setWidth(contents.width() + option.iconSize.width() + 4)
                contents.setHeight(max(contents.height(), option.iconSize.height()))
            size = size.expandedTo(self.style().sizeFromContents(QStyle.CT_PushButton, option, contents, self))
        return size


class _PairingButtonBox(ResponsiveDialogButtonBox):
    """Choose a row layout that still fits when progress adds a scrollbar."""

    def __init__(self, scroll_area):
        super().__init__()
        self._scroll_area = scroll_area

    def sizeHint(self):
        size = super().sizeHint()
        size.setWidth(size.width() + self._scroll_area.verticalScrollBar().sizeHint().width())
        return size

    def _fit_width(self, width):
        area = self._scroll_area
        # The viewport already gives up this width when the bar is present.
        # Reserve only the remainder, so showing/hiding it cannot change the
        # row's orientation at an otherwise unchanged window size.
        reserved = area.contentsRect().width() - area.viewport().width()
        remaining = max(0, area.verticalScrollBar().sizeHint().width() - reserved)
        super()._fit_width(max(0, width - remaining))


class _WrappingLabel(QLabel):
    """Use the actual wrapped height, without retaining a narrow size hint."""

    def _fit_text_height(self):
        if self.wordWrap():
            # Release the previous height before measuring changed text or a
            # wider layout. QLabel otherwise includes that minimum height.
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.setFixedHeight(max(0, self.heightForWidth(self.width())))

    def setText(self, text):
        super().setText(text)
        self._fit_text_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit_text_height()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_text_height()


class _WrappingForm(QWidget):
    """Keep wrapped form rows tall enough inside the scroll area's layout."""

    def _fit_rows(self):
        layout = self.layout()
        if layout is not None:
            height = layout.heightForWidth(self.width())
            if height >= 0:
                self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit_rows()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_rows()


class SmartPianoSoftWorker(QThread):
    progressChanged = Signal(object)
    consentRequested = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, task, parent=None, *, require_consent=False):
        super().__init__(parent)
        self.task = task
        self.cancelled = threading.Event()
        self.consent_granted = not require_consent
        self._preparation_complete = False

    def _check_cancel(self):
        if self.cancelled.is_set():
            raise FloppyOperationCancelled("Smart PianoSoft preparation cancelled.")

    def _progress(self, detail):
        if detail.get("phase") == "complete":
            # The manifest and all outputs are committed. A late Cancel must
            # not turn this result into an unreported output folder.
            self._preparation_complete = True
            self.progressChanged.emit(detail)
            return
        self._check_cancel()
        smart_track = detail.get("phase") == "scan" and detail.get("completed", 0) > 0
        protected_read = "yamaha copy protection?" in str(detail.get("message", "")).casefold()
        if not self.consent_granted and (smart_track or protected_read):
            from .yamaha_consent import YamahaConsentRequest

            request = YamahaConsentRequest()
            self.consentRequested.emit(request)
            try:
                while not request.wait(0.05):
                    self._check_cancel()
            finally:
                if self.cancelled.is_set():
                    request.resolve(False)
            if not request.approved:
                self.cancelled.set()
            self._check_cancel()
            self.consent_granted = True
        self.progressChanged.emit(detail)

    def run(self):
        try:
            result = self.task(self.cancelled, self._progress)
            if not self._preparation_complete:
                self._check_cancel()
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit({"error": str(exc), "cancelled": self.cancelled.is_set(),
                              "output": getattr(exc, "output_directory", None),
                              "cd_read_error": getattr(exc, "cd_read_error", False),
                              "album": getattr(exc, "album_snapshot", None)})


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
        self._completed_cd_device = None
        self._workspace = None
        self._floppy_source = None
        self._closing = False
        self._operation = ""
        self._operation_done = False
        self._incomplete_output = None
        self._loaded_source = loaded_source
        self._using_loaded_source = loaded_source is not None
        self._loaded_source_consent_granted = loaded_source is not None and bool(
            getattr(getattr(parent, "image_session", None), "_yamaha_consent_granted", False)
        )
        self._source_consent_granted = self._loaded_source_consent_granted
        self._discover_after_scan = False
        self.profile = get_preparation_profile(settings.value(SETTING_PROFILE, "unsure"))
        self.setWindowTitle(self.text("action"))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)
        description = self._label("description")
        layout.addWidget(description)

        self.options = _WrappingForm()
        form = QFormLayout(self.options)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(8)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.target_label = _WrappingLabel(translate_text(self.profile.label, self.language))
        self.target_label.setTextFormat(Qt.PlainText)
        self.target_label.setWordWrap(True)
        form.addRow(self._label("preparing_for", wrap=False), self.target_label)
        self.format_combo = QComboBox()
        self.format_combo.setObjectName("smartPianoSoftOutputFormat")
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        # Native font metrics can make a caption wider than the whole viewport.
        # Let the closed selector shrink; its popup retains the full choices.
        self.format_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for key in ("encoded_wav", "midi_mp3"):
            self.format_combo.addItem(self.text("output_" + key), key)
        self.format_combo.setCurrentIndex(self.format_combo.findData(default_audio_output(self.profile.key)))
        form.addRow(self._label("output_format", wrap=False), self.format_combo)
        source_text = (self.text("current_list_source", source=loaded_source.label)
                       if loaded_source is not None else str(settings.value("sps_source", "") or ""))
        self.source_edit = QLineEdit(source_text)
        self.source_edit.setCursorPosition(0)
        self.source_edit.setToolTip(source_text)
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
        form.addRow(self._label("source", wrap=False), source_row)

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
        form.addRow(self._label("cd", wrap=False), cd_row)

        self.output_edit = QLineEdit(str(settings.value("sps_output", "") or ""))
        self.output_edit.setObjectName("smartPianoSoftOutput")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        browse = QPushButton(translate_text("Browse...", self.language))
        browse.setAutoDefault(False)
        browse.clicked.connect(self.choose_output)
        output_row.addWidget(browse)
        form.addRow(self._label("output", wrap=False), output_row)
        # WrapLongRows can omit column spacing when deciding whether a field
        # fits. Keep that gap inside the label so it counts toward row width.
        label_gap = max(12, self.fontMetrics().horizontalAdvance("  "))
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
        # Leave room for target/output controls and rip progress at large font
        # sizes; additional songs remain scrollable inside the table.
        self.table.setMinimumHeight(self.fontMetrics().height() * 4)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.pair_audio())
        layout.addWidget(self.table, 1)
        self.pairing_buttons = _PairingButtonBox(self.scroll_area)
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
        self.format_combo.currentIndexChanged.connect(self._update_format_help)
        self._update_format_help()
        self.status = self._label("ready")
        self.status.hide()
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(400)
        self.details.setMinimumHeight(self.fontMetrics().height() * 3)
        self.details.setMaximumHeight(115)
        self.details.hide()
        self.details.textChanged.connect(lambda: self.details.setVisible(bool(self.details.toPlainText())))
        layout.addWidget(self.details)
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area, 1)

        self.buttons = ResponsiveDialogButtonBox()
        self.open_button = QPushButton(self.text("open"))
        self.open_button.clicked.connect(self.open_output)
        self.start_button = _PreparationButton()
        self.start_button.setObjectName("smartPianoSoftStart")
        self.start_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        start_font = self.start_button.font()
        start_font.setBold(True)
        self.start_button.setFont(start_font)
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
        self.source_edit.textChanged.connect(self.source_edit.setToolTip)
        self.cd_combo.currentTextChanged.connect(self._update_controls)
        self.format_combo.currentIndexChanged.connect(self._update_controls)
        self.table.itemSelectionChanged.connect(self._update_controls)
        self._update_controls()
        # Size for the completed footer from the beginning. Smaller screens
        # keep its stacked layout throughout the run instead of switching at
        # completion when Restart and Open output folder become available.
        footer_width = self.buttons.sizeHint().width() + 64
        resize_dialog_to_screen(self, width=max(940, footer_width), height=700)
        if loaded_source is not None:
            self._discover_after_scan = discover_on_open
            QTimer.singleShot(0, self.start_scan)
        elif discover_on_open:
            QTimer.singleShot(0, self.discover_cd)

    def text(self, key, **values):
        return tr("sps." + key, self.language, **values)

    def _update_format_help(self):
        note = self.text(audio_playback_note_key(self.profile.key, self.format_combo.currentData()))
        self.format_combo.setToolTip(note)
        self.format_combo.setAccessibleDescription(note)

    def _label(self, key, *, wrap=True):
        label_type = _WrappingLabel if wrap else QLabel
        label = label_type(self.text(key))
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(wrap)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        return label

    @property
    def is_busy(self):
        return self.worker is not None

    def _update_controls(self):
        busy = self.is_busy
        self.options.setEnabled(not busy)
        self.scan_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        cd_device = self._cd_device()
        cd_selected = bool(cd_device)
        restarting = cd_selected and cd_device == self._completed_cd_device
        output_format = "MIDI/MP3" if self.format_combo.currentData() == "midi_mp3" else "WAV"
        self.start_button.set_caption_choices(
            self.text(key, format=output_format) for key in ("start_files", "start", "restart")
        )
        self.start_button.setText(self.text(
            "restart" if restarting else "start" if cd_selected else "start_files", format=output_format,
        ))
        self.start_button.setIcon(self.style().standardIcon(
            QStyle.SP_BrowserReload if restarting else QStyle.SP_MediaPlay,
        ))
        self.start_button.setToolTip(self.text("start_hint" if cd_selected else "start_files_hint", format=output_format))
        self.start_button.setAccessibleDescription(self.start_button.toolTip())
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
        self._operation_done = False
        if operation != "discover":
            self.status.show()
        self.progress.show()
        self.progress.setRange(0, 0)
        require_consent = (operation != "discover" and not self._source_consent_granted
                           and callable(getattr(self.parent(), "_show_yamaha_personal_use_notice", None)))
        self.worker = SmartPianoSoftWorker(task, self, require_consent=require_consent)
        self.worker.progressChanged.connect(self._progress)
        self.worker.consentRequested.connect(self._request_personal_use_consent)
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
        if self.is_busy or self._closing:
            return
        result = discover_floppy_devices(self, include_greaseweazle=True,
                                        translate=lambda value, **kw: translate_text(value, self.language, **kw))
        if result is None:
            return
        drives, greaseweazles, issues = result
        drives = [*drives, *greaseweazles]
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
        if isinstance(selected, GreaseweazleDeviceInfo):
            selected = self._choose_greaseweazle_source(selected)
            if selected is None:
                return
        self._select_external_source(selected)

    def _choose_greaseweazle_source(self, device):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Greaseweazle")
        form = QFormLayout(dialog)
        form.addRow(QLabel(device.display_name))
        drive = QComboBox(dialog)
        drive.setObjectName("smartPianoSoftGreaseweazleDrive")
        drive.addItems(["A", "B", "0", "1", "2"])
        saved_drive = str(self.settings.value("greaseweazle_drive", "A") or "A")
        drive.setCurrentIndex(max(0, drive.findText(saved_drive)))
        formats = QComboBox(dialog)
        formats.setObjectName("smartPianoSoftGreaseweazleFormat")
        for disk_format in DISK_FORMATS:
            formats.addItem(disk_format.label, disk_format)
        saved_format = str(self.settings.value("read_floppy_gw_format", "ibm.720") or "ibm.720")
        formats.setCurrentIndex(next((i for i, item in enumerate(DISK_FORMATS) if item.key == saved_format), 0))
        form.addRow(translate_text("Drive:", self.language), drive)
        form.addRow(translate_text("Disk format:", self.language), formats)
        spins = []
        for label, setting, default in (("Read revs:", "read_floppy_gw_revs", 0),
                                        ("Read retries:", "read_floppy_gw_retries", 3)):
            spin = QSpinBox(dialog)
            spin.setRange(0, 20)
            spin.setValue(self.settings.value(setting, default, type=int))
            if not default:
                spin.setSpecialValueText(translate_text("CLI default", self.language))
            form.addRow(translate_text(label, self.language), spin)
            spins.append(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setText(translate_text("OK", self.language))
        buttons.button(QDialogButtonBox.Cancel).setText(translate_text("Cancel", self.language))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        try:
            center_dialog_on_parent(dialog, self)
            if dialog.exec() != QDialog.Accepted:
                return None
            selected = GreaseweazleFloppySource(device.path, drive.currentText(), formats.currentData(),
                                               revs=spins[0].value(), retries=spins[1].value())
            for key, value in (("greaseweazle_device_path", selected.device_path),
                               ("greaseweazle_drive", selected.drive),
                               ("read_floppy_gw_format", selected.disk_format.key),
                               ("read_floppy_gw_revs", selected.revs),
                               ("read_floppy_gw_retries", selected.retries)):
                self.settings.setValue(key, value)
            return selected
        finally:
            dialog.deleteLater()

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
        self._source_consent_granted = False
        self._floppy_source = source if isinstance(source, (FloppyDriveInfo, GreaseweazleFloppySource)) else None
        self.source_edit.setReadOnly(self._floppy_source is not None)
        self.source_edit.setText(source.display_name if self._floppy_source is not None else str(source))
        self.source_edit.setCursorPosition(0)

    def use_current_list(self):
        if self.is_busy or self._closing or self._loaded_source is None:
            return
        self._using_loaded_source = True
        self._source_consent_granted = self._loaded_source_consent_granted
        self._floppy_source = None
        self.source_edit.setReadOnly(True)
        self.source_edit.setText(self.text("current_list_source", source=self._loaded_source.label))
        self.source_edit.setCursorPosition(0)
        self.start_scan()

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, self.text("output"), self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def invalidate_scan(self):
        self.album = None
        self._completed_cd_device = None
        self.audio_paths.clear()
        self.table.setRowCount(0)
        self.album_label.clear()
        self.album_label.hide()
        self._update_controls()

    def _new_scan(self):
        if self._workspace is not None:
            self._workspace.cleanup()
        self._workspace = tempfile.TemporaryDirectory(prefix="aps-smart-pianosoft-")
        self._source_consent_granted = self._using_loaded_source and self._loaded_source_consent_granted
        self.invalidate_scan()
        return Path(self._workspace.name) / "source"

    def _source(self):
        if self._using_loaded_source:
            return self._loaded_source
        if self._floppy_source is not None:
            return self._floppy_source
        source = self.source_edit.text().strip()
        if not source:
            self.choose_floppy()
            if self._floppy_source is not None:
                return self._floppy_source
            source = self.source_edit.text().strip()
        return source

    def start_scan(self):
        if self.is_busy or self._closing:
            return
        source = self._source()
        if not source:
            return
        target = self._new_scan()
        if isinstance(source, (str, Path)):
            self.settings.setValue("sps_source", str(source))
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
        if isinstance(source, (str, Path)):
            self.settings.setValue("sps_source", str(source))
        self.settings.setValue("sps_output", destination)
        album, audio, cd_device = self.album, dict(self.audio_paths), self._cd_device()
        random_suffixes = random_output_suffixes_enabled(self.settings)
        output_format = self.format_combo.currentData()
        generate_yamaha_catalogs = generate_yamaha_catalogs_enabled(self.settings)
        preserve_yamaha_catalogs = preserves_yamaha_midi_catalogs(self.settings.value(SETTING_PROFILE, "unsure"))
        target = self._new_scan() if album is None else None
        def prepare(cancel, progress):
            snapshot = album or _scan_source(source, target, cancel=cancel, progress=progress)
            try:
                output = prepare_album(snapshot, destination, cd_device=cd_device, audio_paths=audio,
                                       output_format=output_format, cancel=cancel, progress=progress,
                                       random_suffixes=random_suffixes,
                                       generate_yamaha_catalogs=generate_yamaha_catalogs,
                                       preserve_yamaha_catalogs=preserve_yamaha_catalogs)
            except Exception as exc:
                # Retain the verified snapshot even when Start also performed
                # the scan, so a CD retry does not need the original floppy.
                exc.album_snapshot = snapshot
                raise
            return snapshot, output
        self._start("prepare", prepare)

    def _populate(self):
        self.album_label.setText(self.text("album", title=self.album.title))
        self.album_label.show()
        self.table.setRowCount(len(self.album.tracks))
        for row, track in enumerate(self.album.tracks):
            audio = self.audio_paths.get(track.number)
            cd_reference = (self.text("cd_reference", number=track.metadata.cd_track)
                            if track.metadata.cd_track is not None else self.text("cd_hint"))
            values = (str(track.number), track.title, track.display_filename or track.filename,
                      str(audio) if audio else cd_reference)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(self.text("cd_hint_details") if column == 3 and not audio else value)
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
        if self._closing or (self.worker is not None and
                             (self.worker.cancelled.is_set() or self._operation_done)):
            return
        self.status.show()
        self.progress.show()
        total = detail.get("total", 0)
        if total:
            self.progress.setRange(0, 100)
            maximum = 99 if self._operation == "prepare" else 100
            self.progress.setValue(min(maximum, round(100 * detail.get("completed", 0) / total)))
        else:
            self.progress.setRange(0, 0)
        phase = detail.get("phase", "scan")
        message = str(detail.get("message", ""))
        if phase == "rip":
            message = translate_text(message, self.language)
        if phase == "rip" and "cd_track" in detail:
            self.status.setText(self.text("cd_track_progress", number=detail["cd_track"],
                                          index=detail["track_index"], count=detail["track_count"],
                                          message=message))
        else:
            self.status.setText(self.text("phase_" + phase) + (": " + message if message else ""))

    def _request_personal_use_consent(self, request):
        approved = False
        try:
            if self.worker is None or self.worker.cancelled.is_set() or self._closing:
                return
            self.status.setText(self.text("waiting_for_consent"))
            notice = getattr(self.parent(), "_show_yamaha_personal_use_notice", None)
            approved = bool(notice()) if callable(notice) else True
            if self.worker is None or self.worker.cancelled.is_set() or self._closing:
                approved = False
            if approved:
                self._source_consent_granted = True
        finally:
            request.resolve(approved)

    def _success(self, result):
        self._operation_done = True
        if self._operation == "discover":
            previous = self._cd_device()
            self.cd_combo.clear()
            self.cd_combo.addItem(self.text("paired_files"), "")
            for device, label in result:
                self.cd_combo.addItem(f"{label} ({device})", device)
            index = self.cd_combo.findData(previous) if previous else (1 if result else 0)
            if index < 0 and len(result) == 1:
                index = 1
            self.cd_combo.setCurrentIndex(max(0, index))
        elif self._operation == "scan":
            self.album = result
            self._populate()
            self.status.setText(self.text("scanned", count=len(result.tracks)))
        else:
            self.album, self.result_folder = result
            self._completed_cd_device = self._cd_device() or None
            self._populate()
            self.status.setText(self.text("complete"))
            self.details.appendPlainText(str(self.result_folder))
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self._operation != "discover" else 0)

    def _failure(self, result):
        self._operation_done = True
        self.status.show()
        if result["cancelled"]:
            self._discover_after_scan = False
        if self._operation == "prepare":
            self._completed_cd_device = None
            if result.get("album") is not None:
                self.album = result["album"]
                self._populate()
        cd_read_error = result.get("cd_read_error", False) and not result["cancelled"]
        status = "cancelled" if result["cancelled"] else "cd_read_failed" if cd_read_error else "failed"
        self.status.setText(self.text(status))
        self.details.appendPlainText(result["error"])
        if result.get("output"):
            self._incomplete_output = Path(result["output"])
        if cd_read_error:
            self.details.appendPlainText(self.text("cd_read_retry"))
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def _finished(self):
        # Keep the busy guard during the modal prompt, including when closing.
        # No worker may still be writing when the user chooses Discard.
        if self._incomplete_output is not None:
            self._resolve_incomplete_output()
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.progress.hide()
        self._update_controls()
        if self._closing:
            self.close()
        elif self._discover_after_scan:
            self._discover_after_scan = False
            QTimer.singleShot(0, self.discover_cd)

    def _resolve_incomplete_output(self):
        folder, self._incomplete_output = self._incomplete_output, None
        box = QMessageBox(self)
        apply_window_icon(box)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(self.text("incomplete_title"))
        box.setTextFormat(Qt.PlainText)
        box.setText(self.text("incomplete_prompt", folder=str(folder)))
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard)
        box.button(QMessageBox.Save).setText(self.text("save_incomplete"))
        box.button(QMessageBox.Discard).setText(self.text("discard_incomplete"))
        box.setDefaultButton(QMessageBox.Save)
        box.setEscapeButton(QMessageBox.Save)
        choice = box.exec()
        box.deleteLater()
        if choice == QMessageBox.Discard:
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                message = self.text("discard_failed", folder=str(folder), error=str(exc))
                self.details.appendPlainText(message)
                QMessageBox.warning(self, self.text("incomplete_title"), message)
            else:
                self.details.appendPlainText(self.text("discarded"))
                return
        self.result_folder = folder
        self.details.appendPlainText(self.text("retained", folder=str(folder)))

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
