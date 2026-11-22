"""Admin Mode batch recovery of Disklavier control data and accompaniment."""

import os
from pathlib import Path
import threading

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .audio_decode_batch import run_audio_decode_batch
from .helpers.output_names import random_output_suffixes_enabled
from .icon_utils import apply_window_icon
from .message_catalog import normalize_language_code, tr, translate_text


SETTING_OUTPUT = "audio_decode_batch_output"
SETTING_INPUT = "audio_decode_batch_input"


class AudioDecodeBatchWorker(QThread):
    progressChanged = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, sources, output_parent, *, random_suffixes=True, parent=None):
        super().__init__(parent)
        self.sources = tuple(Path(path) for path in sources)
        self.output_parent = Path(output_parent)
        self.random_suffixes = random_suffixes
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            result = run_audio_decode_batch(
                self.sources, self.output_parent, random_suffixes=self.random_suffixes,
                cancel=self._cancel, progress=self.progressChanged.emit,
            )
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class AudioDecodeBatchDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        apply_window_icon(self)
        self.settings = settings
        self.language_code = normalize_language_code(
            parent._language_code() if parent is not None and hasattr(parent, "_language_code")
            else settings.value("language", "en")
        )
        self.worker = None
        self.batch_result = None
        self.result_folder = None
        self._closing = False
        self._close_result = QDialog.Rejected
        self._cancelling = False
        self.setWindowTitle(self._t("abd.title"))
        self.resize(880, 610)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        inputs_page = QWidget()
        inputs_layout = QVBoxLayout(inputs_page)
        self.input_list = QTreeWidget()
        self.input_list.setObjectName("audioDecodeBatchInputs")
        self.input_list.setHeaderHidden(True)
        self.input_list.setRootIsDecorated(False)
        self.input_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.input_list.header().setSectionResizeMode(QHeaderView.Stretch)
        inputs_layout.addWidget(self.input_list, 1)
        input_buttons = QHBoxLayout()
        self.files_button = QPushButton(self._t("abd.add_files"))
        self.folder_button = QPushButton(self._t("abd.add_folder"))
        self.remove_button = QPushButton(self._t("abd.remove"))
        input_buttons.addWidget(self.files_button)
        input_buttons.addStretch(1)
        input_buttons.addWidget(self.remove_button)
        inputs_layout.addLayout(input_buttons)
        folder_buttons = QHBoxLayout()
        folder_buttons.addWidget(self.folder_button)
        folder_buttons.addStretch(1)
        inputs_layout.addLayout(folder_buttons)
        self.tabs.addTab(inputs_page, self._t("abd.inputs"))

        results_page = QWidget()
        results_layout = QVBoxLayout(results_page)
        self.results_table = QTreeWidget()
        self.results_table.setObjectName("audioDecodeBatchResults")
        self.results_table.setRootIsDecorated(False)
        self.results_table.setHeaderLabels([
            self._t("abd.source"), self.lt("Status"), self.lt("Details"),
        ])
        self.results_table.header().setStretchLastSection(False)
        self.results_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results_table.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.results_table.header().setSectionResizeMode(2, QHeaderView.Stretch)
        results_layout.addWidget(self.results_table, 1)
        self.details = QPlainTextEdit()
        self.details.setObjectName("audioDecodeBatchDetails")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(self.fontMetrics().lineSpacing() * 5 + 20)
        results_layout.addWidget(self.details)
        self.tabs.addTab(results_page, self._t("abd.results"))
        layout.addWidget(self.tabs, 1)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setHorizontalSpacing(max(12, self.fontMetrics().horizontalAdvance("  ")))
        self.destination_edit = QLineEdit(str(settings.value(SETTING_OUTPUT, "") or ""))
        self.destination_edit.setObjectName("audioDecodeBatchOutput")
        self.destination_edit.setToolTip(self._t("abd.output_note"))
        self.browse_button = QPushButton(self.lt("Browse..."))
        output_row = QHBoxLayout()
        output_row.addWidget(self.destination_edit, 1)
        output_row.addWidget(self.browse_button)
        form.addRow(self._t("abd.output"), output_row)
        layout.addLayout(form)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.current_source = QLineEdit()
        self.current_source.setObjectName("audioDecodeBatchCurrentSource")
        self.current_source.setReadOnly(True)
        self.current_source.setFrame(False)
        self.current_source.hide()
        layout.addWidget(self.current_source)

        buttons = QHBoxLayout()
        self.open_button = QPushButton(self._t("abd.open_output"))
        self.start_button = QPushButton(self._t("abd.start"))
        self.cancel_button = QPushButton(self.lt("Cancel"))
        self.close_button = QPushButton(self.lt("Close"))
        open_row = QHBoxLayout()
        open_row.addWidget(self.open_button)
        open_row.addStretch(1)
        layout.addLayout(open_row)
        buttons.addStretch(1)
        for button in (self.start_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.files_button.clicked.connect(self._browse_files)
        self.folder_button.clicked.connect(self._browse_folder)
        self.remove_button.clicked.connect(self._remove_selected)
        self.input_list.itemSelectionChanged.connect(self._update_buttons)
        self.results_table.currentItemChanged.connect(self._show_details)
        self.destination_edit.textChanged.connect(self._update_buttons)
        self.browse_button.clicked.connect(self._browse_destination)
        self.open_button.clicked.connect(self._open_result)
        self.start_button.clicked.connect(self.start_decode)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.close_button.clicked.connect(self.reject)
        self._update_buttons()

    def _t(self, key, **kwargs):
        return tr(key, self.language_code, **kwargs)

    def lt(self, text):
        return translate_text(text, self.language_code)

    @property
    def is_busy(self):
        # A result can arrive before QThread has exited. Keep its owner alive
        # until the finished signal has been handled.
        return self.worker is not None

    @property
    def sources(self):
        return tuple(Path(self.input_list.topLevelItem(index).data(0, Qt.UserRole))
                     for index in range(self.input_list.topLevelItemCount()))

    def add_sources(self, paths):
        if self.is_busy or self._closing:
            return
        # Recursive discovery belongs to the worker; adding a root never walks
        # a potentially large or slow device on the UI thread.
        existing = {os.path.normcase(str(path)) for path in self.sources}
        for path in paths:
            path = Path(path).expanduser().absolute()
            key = os.path.normcase(str(path))
            if key in existing:
                continue
            existing.add(key)
            item = QTreeWidgetItem([str(path)])
            item.setData(0, Qt.UserRole, str(path))
            item.setToolTip(0, str(path))
            self.input_list.addTopLevelItem(item)
        self.tabs.setCurrentIndex(0)
        self._update_buttons()

    def _browse_files(self):
        if self.is_busy:
            return
        paths, _filter = QFileDialog.getOpenFileNames(
            self, self._t("abd.choose_files"), str(self.settings.value(SETTING_INPUT, "") or ""),
            self._t("abd.audio_filter") + ";;" + self._t("abd.all_files"),
        )
        if paths:
            self.settings.setValue(SETTING_INPUT, str(Path(paths[0]).parent))
            self.add_sources(paths)

    def _browse_folder(self):
        if self.is_busy:
            return
        path = QFileDialog.getExistingDirectory(
            self, self._t("abd.choose_folder"), str(self.settings.value(SETTING_INPUT, "") or ""),
        )
        if path:
            self.settings.setValue(SETTING_INPUT, path)
            self.add_sources([path])

    def _browse_destination(self):
        if self.is_busy:
            return
        path = QFileDialog.getExistingDirectory(
            self, self._t("abd.choose_output"), self.destination_edit.text(),
        )
        if path:
            self.destination_edit.setText(path)

    def _remove_selected(self):
        if self.is_busy:
            return
        for item in self.input_list.selectedItems():
            self.input_list.takeTopLevelItem(self.input_list.indexOfTopLevelItem(item))
        self._update_buttons()

    def _update_buttons(self, *_args):
        idle = not self.is_busy and not self._closing
        for widget in (self.files_button, self.folder_button, self.browse_button,
                       self.destination_edit, self.input_list):
            widget.setEnabled(idle)
        self.remove_button.setEnabled(idle and bool(self.input_list.selectedItems()))
        self.start_button.setEnabled(idle and bool(self.sources) and bool(self.destination_edit.text().strip()))
        self.cancel_button.setEnabled(self.is_busy and not self._cancelling)
        self.open_button.setEnabled(not self.is_busy and self.result_folder is not None)

    def start_decode(self):
        if self.is_busy or self._closing or not self.sources or not self.destination_edit.text().strip():
            return
        self.settings.setValue(SETTING_OUTPUT, self.destination_edit.text().strip())
        self.batch_result = None
        self.result_folder = None
        self.results_table.clear()
        self.details.clear()
        self._cancelling = False
        self.current_source.clear()
        self.current_source.hide()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(self._t("abd.scanning"))
        self.worker = AudioDecodeBatchWorker(
            self.sources, Path(self.destination_edit.text().strip()).expanduser(),
            random_suffixes=random_output_suffixes_enabled(self.settings), parent=self,
        )
        self.worker.progressChanged.connect(self._progress)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self._update_buttons()
        self.worker.start()

    def _progress(self, progress):
        if self._cancelling:
            return
        phase = progress.get("phase", "scanning")
        key = {"decoding": "abd.decoding", "exporting": "abd.exporting"}.get(phase, "abd.scanning")
        status = self._t(key)
        total = progress.get("total", 0)
        if total:
            status += " · " + self._t("abd.file_progress", completed=progress.get("completed", 0), total=total)
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(max(0, min(1000, int(progress.get("fraction", 0) * 1000))))
        self.status_label.setText(status)
        source = str(progress.get("source", "") or "")
        self.current_source.setText(source)
        self.current_source.setToolTip(source)
        self.current_source.setVisible(bool(source))

    def _succeeded(self, result):
        self.batch_result = result
        self.result_folder = result.output_directory
        self.results_table.setUpdatesEnabled(False)
        try:
            for record in result.records:
                output_paths = [str(path) for path in (record.midi_path, record.mp3_path) if path is not None]
                details = record.error or "\n".join(output_paths)
                item = QTreeWidgetItem([
                    str(record.source), self._t("abd." + record.status),
                    details.replace("\n", " · "),
                ])
                full_details = "\n".join([str(record.source), self._t("abd." + record.status), details])
                item.setData(0, Qt.UserRole, full_details)
                for column in range(3):
                    item.setToolTip(column, full_details)
                self.results_table.addTopLevelItem(item)
        finally:
            self.results_table.setUpdatesEnabled(True)
        if self.results_table.topLevelItemCount():
            self.results_table.setCurrentItem(self.results_table.topLevelItem(0))
        self.tabs.setCurrentIndex(1)
        key = "abd.cancelled_summary" if result.cancelled else "abd.summary"
        self.status_label.setText(self._t(key, converted=result.converted, skipped=result.skipped, failed=result.failed))
        self.progress_bar.setRange(0, 1000)
        if not result.cancelled:
            self.progress_bar.setValue(1000)
        self.current_source.setText(str(result.output_directory or ""))
        self.current_source.setToolTip(str(result.output_directory or ""))
        self.current_source.setVisible(result.output_directory is not None)

    def _show_details(self, current, _previous=None):
        self.details.setPlainText(str(current.data(0, Qt.UserRole) or "") if current else "")

    def _failed(self, message):
        self.status_label.setText(self._t("abd.fatal"))
        self.details.setPlainText(message)
        self.tabs.setCurrentIndex(1)

    def _finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.progress_bar.setRange(0, 1000)
        self._update_buttons()
        if self._closing:
            super().done(self._close_result)

    def cancel_operation(self):
        if self.worker is not None:
            self._cancelling = True
            self.worker.cancel()
            self.status_label.setText(self._t("abd.close_wait" if self._closing else "abd.cancelling"))
            self._update_buttons()

    def _open_result(self):
        if not self.is_busy and self.result_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_folder)))

    def done(self, result):
        self._closing = True
        self._close_result = result
        self.settings.setValue(SETTING_OUTPUT, self.destination_edit.text().strip())
        if self.is_busy:
            self.cancel_operation()
            return
        super().done(result)

    def closeEvent(self, event):
        if self.is_busy:
            event.ignore()
            self.reject()
        else:
            self._closing = True
            self.settings.setValue(SETTING_OUTPUT, self.destination_edit.text().strip())
            super().closeEvent(event)
