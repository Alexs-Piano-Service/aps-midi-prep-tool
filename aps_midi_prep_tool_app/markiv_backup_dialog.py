"""Mark IV library backup and optional MIDI copies, with cancellable workers."""

from pathlib import Path
import threading

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .icon_utils import apply_window_icon
from .markiv_backup.backup import run_backup, verify_backup
from .markiv_backup.devices import resolve_source
from .markiv_backup.library import CancelledError, check_cancel, scan_library
from .markiv_device_discovery import discover_mounted_sources
from .message_catalog import normalize_language_code, tr, translate_text


SETTING_SOURCE = "markiv_backup_source"
SETTING_OUTPUT = "markiv_backup_output"
SETTING_CONVERT = "markiv_backup_convert_eseq"
SETTING_KEEP_ORIGINALS = "markiv_backup_keep_originals"
SETTING_LAST_FOLDER = "markiv_backup_last_folder"


def _format_size(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024


class MarkIVBackupWorker(QThread):
    progressChanged = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, operation, *, source="", target="", plan=None,
                 convert_eseq=False, keep_originals=True, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.source = source
        self.target = target
        self.plan = plan
        self.convert_eseq = convert_eseq
        self.keep_originals = keep_originals
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            check_cancel(self._cancel)
            if self.operation == "discover":
                result = discover_mounted_sources(cancel=self._cancel)
            elif self.operation == "scan":
                result = scan_library(resolve_source(self.source),
                                      progress=self.progressChanged.emit, cancel=self._cancel)
            elif self.operation == "backup":
                result = run_backup(self.plan, Path(self.target),
                                    progress=self.progressChanged.emit, cancel=self._cancel,
                                    convert_eseq=self.convert_eseq,
                                    keep_originals=self.keep_originals)
            elif self.operation == "verify":
                result = verify_backup(Path(self.target),
                                       progress=self.progressChanged.emit, cancel=self._cancel)
            else:
                raise ValueError(f"Unknown Mark IV backup operation: {self.operation}")
            # A cancelled backup returns its partial result and output folder.
            if self.operation != "backup":
                check_cancel(self._cancel)
            self.succeeded.emit(result)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class MarkIVBackupDialog(QDialog):
    def __init__(self, settings, parent=None, *, refresh_on_open=True):
        super().__init__(parent)
        apply_window_icon(self)
        self.settings = settings
        self.language_code = normalize_language_code(
            parent._language_code() if parent is not None and hasattr(parent, "_language_code")
            else settings.value("language", "en")
        )
        self.plan = None
        self._report_messages = []
        self.worker = None
        self._closing = False
        self._cancelling = False
        self._operation = ""
        saved_folder = str(settings.value(SETTING_LAST_FOLDER, "") or "")
        self.result_folder = Path(saved_folder) if saved_folder else None
        self.setWindowTitle(self._t("markiv.title"))
        self.resize(850, 690)
        layout = QVBoxLayout(self)
        layout.addWidget(self._label("markiv.description"))

        form = QFormLayout()
        self.source_combo = QComboBox()
        self.source_combo.setEditable(True)
        self.source_combo.setInsertPolicy(QComboBox.NoInsert)
        self.source_combo.setObjectName("markivBackupSource")
        self.source_combo.setEditText(str(settings.value(SETTING_SOURCE, "") or ""))
        self.source_browse = QPushButton(self.lt("Browse..."))
        self.refresh_button = QPushButton(self._t("markiv.refresh"))
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_combo, 1)
        source_row.addWidget(self.source_browse)
        source_row.addWidget(self.refresh_button)
        form.addRow(self._t("markiv.source"), source_row)
        form.addRow(self._label("markiv.source_note"))

        self.destination_edit = QLineEdit(str(settings.value(SETTING_OUTPUT, "") or ""))
        self.destination_edit.setObjectName("markivBackupDestination")
        self.destination_browse = QPushButton(self.lt("Browse..."))
        destination_row = QHBoxLayout()
        destination_row.addWidget(self.destination_edit, 1)
        destination_row.addWidget(self.destination_browse)
        form.addRow(self._t("markiv.destination"), destination_row)
        form.addRow(self._label("markiv.destination_note"))
        layout.addLayout(form)

        self.convert_checkbox = QCheckBox(self._t("markiv.convert"))
        self.convert_checkbox.setObjectName("markivBackupConvertEseq")
        self.convert_checkbox.setChecked(settings.value(SETTING_CONVERT, False, type=bool))
        layout.addWidget(self.convert_checkbox)
        self.keep_originals_checkbox = QCheckBox(self._t("markiv.conversion_note"))
        self.keep_originals_checkbox.setObjectName("markivBackupKeepOriginals")
        self.keep_originals_checkbox.setChecked(settings.value(SETTING_KEEP_ORIGINALS, True, type=bool))
        self.keep_originals_checkbox.setToolTip(self._t("markiv.keep_originals_tooltip"))
        keep_row = QHBoxLayout()
        keep_row.addSpacing(20)
        keep_row.addWidget(self.keep_originals_checkbox)
        layout.addLayout(keep_row)

        preview_row = QHBoxLayout()
        self.summary_label = self._label("markiv.ready")
        self.scan_button = QPushButton(self._t("markiv.scan"))
        preview_row.addWidget(self.summary_label, 1)
        preview_row.addWidget(self.scan_button)
        layout.addLayout(preview_row)
        self.album_table = QTreeWidget()
        self.album_table.setObjectName("markivBackupAlbums")
        self.album_table.setToolTip(self._t("markiv.select_album"))
        self.album_table.setRootIsDecorated(False)
        self.album_table.setAlternatingRowColors(True)
        self.album_table.setHeaderLabels([
            self._t("markiv." + key) for key in ("album", "collection", "files", "size")
        ])
        self.album_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.album_table.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.album_splitter = QSplitter(Qt.Vertical)
        self.album_splitter.setObjectName("markivBackupAlbumSplitter")
        self.album_splitter.setChildrenCollapsible(False)
        self.album_splitter.setHandleWidth(8)
        self.album_table.setMinimumHeight(80)
        self.album_splitter.addWidget(self.album_table)
        details_panel = QWidget()
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.addWidget(self._label("markiv.details"))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText(self._t("markiv.select_album"))
        self.details.setMinimumHeight(60)
        details_layout.addWidget(self.details, 1)
        self.album_splitter.addWidget(details_panel)
        self.album_splitter.setStretchFactor(0, 2)
        self.album_splitter.setStretchFactor(1, 1)
        self.album_splitter.setSizes([220, 130])
        layout.addWidget(self.album_splitter, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = self._label("markiv.ready")
        self.progress_label = QLabel()
        self.result_label = QLabel(saved_folder)
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.PlainText)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        self.report_button = QPushButton()
        self.report_button.setObjectName("markivBackupReport")
        status_row.addWidget(self.report_button)
        layout.addLayout(status_row)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        self.verify_button = QPushButton(self._t("markiv.verify"))
        self.open_button = QPushButton(self._t("markiv.open"))
        self.backup_button = QPushButton(self._t("markiv.backup"))
        self.cancel_button = QPushButton(self.lt("Cancel"))
        self.close_button = QPushButton(self.lt("Close"))
        for button in (self.verify_button, self.open_button):
            buttons.addWidget(button)
        buttons.addStretch()
        for button in (self.backup_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        # Enter in a path field must not unexpectedly start a backup.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.source_combo.editTextChanged.connect(self._source_changed)
        self.destination_edit.textChanged.connect(self._update_buttons)
        self.convert_checkbox.toggled.connect(self._update_buttons)
        self.convert_checkbox.toggled.connect(self._show_details)
        self.keep_originals_checkbox.toggled.connect(self._show_details)
        self.album_table.currentItemChanged.connect(self._show_details)
        self.album_table.itemDoubleClicked.connect(self._open_album_details)
        self.report_button.clicked.connect(self._open_report)
        self.source_browse.clicked.connect(self._browse_source)
        self.destination_browse.clicked.connect(self._browse_destination)
        self.refresh_button.clicked.connect(self.refresh_drives)
        self.scan_button.clicked.connect(self.start_scan)
        self.backup_button.clicked.connect(self.start_backup)
        self.verify_button.clicked.connect(self.verify_existing_backup)
        self.open_button.clicked.connect(self._open_result)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.close_button.clicked.connect(self.reject)
        self._set_report([])
        self._update_buttons()
        if refresh_on_open:
            QTimer.singleShot(0, self.refresh_drives)

    def _t(self, key, **kwargs):
        return tr(key, self.language_code, **kwargs)

    def lt(self, text):
        return translate_text(text, self.language_code)

    def _label(self, key):
        label = QLabel(self._t(key))
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        return label

    @property
    def is_busy(self):
        # Retain the worker until its finished signal is handled, even if the
        # result signal has arrived. Destroying a running QThread is unsafe.
        return self.worker is not None

    def _save_settings(self):
        self.settings.setValue(SETTING_SOURCE, self.source_combo.currentText().strip())
        self.settings.setValue(SETTING_OUTPUT, self.destination_edit.text().strip())
        self.settings.setValue(SETTING_CONVERT, self.convert_checkbox.isChecked())
        self.settings.setValue(SETTING_KEEP_ORIGINALS, self.keep_originals_checkbox.isChecked())
        if self.result_folder is not None:
            self.settings.setValue(SETTING_LAST_FOLDER, str(self.result_folder))

    def _source_changed(self, _text=None):
        self.plan = None
        self._set_report([])
        self.album_table.clear()
        self.summary_label.setText(self._t("markiv.ready"))
        if not self.is_busy:
            self.status_label.setText(self._t("markiv.ready"))
            self.details.clear()
        self._update_buttons()

    def _show_details(self, *_args):
        self.details.setPlainText(self._album_text(self.album_table.currentItem()))

    def _album_text(self, item, *, expanded=False):
        if self.plan is None or item is None:
            return ""
        album = self.plan.albums[item.data(0, Qt.UserRole)]
        sections = [f"{album.name} · {self.lt(album.category)} · "
                    f"{self._t('markiv.files')}: {album.file_count} · {_format_size(album.total_bytes)}"]
        sections.extend(self._metadata_lines(album.metadata, expanded=expanded))
        if expanded:
            sections.extend([
                f"{self._t('markiv.source_path')}: {self.plan.source / album.source}",
                f"{self._t('markiv.backup_path')}: {album.destination}",
            ])
        folder = Path(album.destination)
        files = [file for file in self.plan.files
                 if album.destination and folder in Path(file.destination).parents]

        def track_order(file):
            number = str(file.metadata.get("track_number", ""))
            return (not number.isdigit(), int(number) if number.isdigit() else 0, file.source.casefold())

        tracks, assets = [], []
        for file in files:
            is_track = file.convertible_eseq or file.kind in {
                "MIDI", "E-SEQ", "WAV", "MP3", "PianoSoft package",
            }
            (tracks if is_track else assets).append(file)
        for file in sorted(tracks, key=track_order):
            number = str(file.metadata.get("track_number", ""))
            prefix = f"{int(number):02d}. " if number.isdigit() else ""
            title = file.metadata.get("title") or Path(file.source).name
            kind = "E-SEQ" if file.convertible_eseq else file.kind
            lines = [f"{prefix}{title} · {self.lt(kind)} · {_format_size(file.size)}"]
            if self.convert_checkbox.isChecked() and file.convertible_eseq:
                key = "markiv.will_convert_keep" if self.keep_originals_checkbox.isChecked() else "markiv.will_convert_replace"
                lines.append(self._t(key))
            lines.extend(self._metadata_lines(file.metadata, expanded=expanded))
            if expanded:
                lines.extend([
                    f"{self._t('markiv.source_path')}: {file.source}",
                    f"{self._t('markiv.backup_path')}: {file.destination}",
                ])
            sections.append("\n".join(lines))
        if assets:
            sections.append(f"{self._t('markiv.album_assets')}: {len(assets)}")
            if expanded:
                sections.extend(f"{file.source} → {file.destination} · {_format_size(file.size)}"
                                for file in assets)
        return "\n".join(sections)

    def _metadata_lines(self, metadata, *, expanded):
        fields = ("subtitle", "artist", "composer", "genre")
        if expanded:
            fields += ("copyright", "metadata_source", "album_id", "song_id")
        return [f"{self._t('markiv.' + field)}: {metadata[field]}"
                for field in fields if metadata.get(field) not in (None, "")]

    def _open_text_dialog(self, title, text, *, object_name):
        child = QDialog(self)
        child.setObjectName(object_name)
        apply_window_icon(child)
        child.setWindowTitle(title)
        child.resize(800, 600)
        layout = QVBoxLayout(child)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(text)
        layout.addWidget(content, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton(self.lt("Close"))
        close.clicked.connect(child.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        child.exec()
        child.deleteLater()

    def _open_album_details(self, item, _column=0):
        text = self._album_text(item, expanded=True)
        if text:
            album = self.plan.albums[item.data(0, Qt.UserRole)]
            self._open_text_dialog(f"{album.name} — {self._t('markiv.details')}", text,
                                   object_name="markivAlbumDetails")

    def _set_report(self, messages):
        self._report_messages = list(messages)
        self.report_button.setText(self._t("markiv.report_button", count=len(self._report_messages)))
        self.report_button.setVisible(bool(self._report_messages))

    def _open_report(self):
        if self._report_messages:
            self._open_text_dialog(self._t("markiv.report_title"), "\n\n".join(self._report_messages),
                                   object_name="markivBackupReportDetails")

    def _update_buttons(self, *_args):
        idle = not self.is_busy
        for widget in (self.source_combo, self.source_browse, self.refresh_button,
                       self.destination_edit, self.destination_browse, self.convert_checkbox,
                       self.verify_button):
            widget.setEnabled(idle)
        self.keep_originals_checkbox.setEnabled(idle and self.convert_checkbox.isChecked())
        self.scan_button.setEnabled(idle and bool(self.source_combo.currentText().strip()))
        self.backup_button.setEnabled(
            idle and self.plan is not None and bool(self.destination_edit.text().strip())
        )
        self.cancel_button.setEnabled(not idle and not self._cancelling)
        self.open_button.setEnabled(idle and self.result_folder is not None)

    def _browse_source(self):
        path = QFileDialog.getExistingDirectory(
            self, self._t("markiv.choose_source"), self.source_combo.currentText()
        )
        if path:
            self.source_combo.setEditText(path)

    def _browse_destination(self):
        path = QFileDialog.getExistingDirectory(
            self, self._t("markiv.choose_destination"), self.destination_edit.text()
        )
        if path:
            self.destination_edit.setText(path)

    def _start(self, operation, **kwargs):
        if self.is_busy or self._closing:
            return
        self._save_settings()
        self._operation = operation
        self._cancelling = False
        self.progress_bar.setRange(0, 0)
        self.progress_label.clear()
        status = {"discover": "refresh", "scan": "scanning", "backup": "backing_up", "verify": "verifying"}
        self.status_label.setText(self._t("markiv." + status[operation]))
        self.worker = MarkIVBackupWorker(operation, parent=self, **kwargs)
        self.worker.progressChanged.connect(self._progress)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.finished.connect(self._finished)
        self._update_buttons()
        self.worker.start()

    def refresh_drives(self):
        self._start("discover")

    def start_scan(self):
        if not self.is_busy and self.source_combo.currentText().strip():
            self._source_changed()
            self._start("scan", source=self.source_combo.currentText().strip())

    def start_backup(self):
        if self.plan is not None and self.destination_edit.text().strip():
            self._start("backup", plan=self.plan, target=self.destination_edit.text().strip(),
                        convert_eseq=self.convert_checkbox.isChecked(),
                        keep_originals=self.keep_originals_checkbox.isChecked())

    def verify_existing_backup(self):
        if self.is_busy:
            return
        path = QFileDialog.getExistingDirectory(
            self, self._t("markiv.choose_backup"),
            str(self.result_folder or self.destination_edit.text())
        )
        if path:
            self.result_folder = Path(path)
            self.result_label.setText(path)
            self._set_report([])
            self._start("verify", target=path)

    def _progress(self, progress):
        if self._cancelling:
            return
        if progress.get("event") == "error" and progress.get("message"):
            self._set_report([*self._report_messages, progress["message"]])
        completed, total = progress.get("completed", 0), progress.get("total", 0)
        if progress.get("event") in ("conversion", "convert", "converting"):
            self.status_label.setText(self._t("markiv.converting"))
            completed = progress.get("converted", 0)
            total = progress.get("conversion_total", 0)
            done, size = completed, total
        else:
            done = progress.get("bytes_done", completed)
            size = progress.get("bytes_total", total)
        if size:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(min(1000, int(done * 1000 / size)))
        if total:
            self.progress_label.setText(self._t("markiv.file_progress", completed=completed, total=total))

    def _succeeded(self, result):
        if self._operation == "discover":
            selected = self.source_combo.currentText()
            self.source_combo.blockSignals(True)
            self.source_combo.clear()
            for device in result:
                self.source_combo.addItem(str(device.mountpoint))
                self.source_combo.setItemData(self.source_combo.count() - 1,
                                              device.display_name, Qt.ToolTipRole)
            if not selected:
                selected = next((str(device.mountpoint) for device in result if device.is_mark_iv), "")
            self.source_combo.setEditText(selected)
            self.source_combo.blockSignals(False)
            self.status_label.setText(self._t("markiv.ready"))
        elif self._operation == "scan":
            self.plan = result
            self.album_table.clear()
            for index, album in enumerate(result.albums):
                item = QTreeWidgetItem([album.name, self.lt(album.category),
                                       str(album.file_count), _format_size(album.total_bytes)])
                item.setData(0, Qt.UserRole, index)
                for column in (2, 3):
                    item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
                self.album_table.addTopLevelItem(item)
            self.summary_label.setText(self._t("markiv.summary", albums=len(result.albums),
                                              files=len(result.files), size=_format_size(result.total_bytes)))
            self._set_report(result.warnings)
            if self.album_table.topLevelItemCount():
                self.album_table.setCurrentItem(self.album_table.topLevelItem(0))
            self._show_details()
            self.status_label.setText(self.summary_label.text())
        elif self._operation == "backup":
            self.result_folder = result.folder
            self.result_label.setText(str(result.folder))
            key = {"complete": "complete", "cancelled": "cancelled"}.get(result.status, "incomplete")
            self.status_label.setText(self._t("markiv." + key, verified=result.verified, converted=result.converted))
            self._set_report([*self.plan.warnings, *result.errors])
        elif self._operation == "verify":
            self.status_label.setText(self._t(
                "markiv.verification_failed" if result else "markiv.verified", count=len(result)
            ))
            self._set_report(result)
        if self._operation in ("scan", "verify") or (
            self._operation == "backup" and result.status == "complete"
        ):
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(1000)
        self._save_settings()

    def _failed(self, message):
        self.status_label.setText(self._t("markiv.failed"))
        self._set_report([*self._report_messages, message])

    def _cancelled(self):
        key = "markiv.scan_cancelled" if self._operation == "scan" else "markiv.cancelled"
        self.status_label.setText(self._t(key))

    def _finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.progress_bar.setRange(0, 1000)
        self._update_buttons()
        if self._closing:
            self.reject()

    def cancel_operation(self):
        if self.worker is not None:
            self._cancelling = True
            self.worker.cancel()
            self.status_label.setText(self._t("markiv.cancelling"))
            self._update_buttons()

    def _open_result(self):
        if self.result_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_folder)))

    def done(self, result):
        if self.is_busy:
            self._closing = True
            self.cancel_operation()
            self.status_label.setText(self._t("markiv.close_wait"))
            return
        self._closing = True
        self._save_settings()
        super().done(result)

    def closeEvent(self, event):
        if self.is_busy:
            event.ignore()
            self.reject()
        else:
            self._closing = True
            self._save_settings()
            super().closeEvent(event)
