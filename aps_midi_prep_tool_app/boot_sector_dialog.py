"""Standalone file/folder boot repair with cancellable conversion work."""

import os

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QFileDialog, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from .boot_sector_repair import ImageRepairBatch, repair_boot_sector_batch
from .floppy_image import FloppyOperationCancelled


class BootSectorRepairWorker(QThread):
    progressChanged = Signal(int, int, str)
    itemFinished = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, source_path, options, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.options = options

    def run(self):
        try:
            result = repair_boot_sector_batch(
                self.source_path, **self.options,
                progress_callback=self.progressChanged.emit,
                result_callback=self.itemFinished.emit,
                cancel_callback=self.isInterruptionRequested,
            )
            self.completed.emit(result)
        except FloppyOperationCancelled:
            self.completed.emit(ImageRepairBatch((), cancelled=True))
        except Exception as exc:
            self.failed.emit(str(exc))


class BootSectorRepairDialog(QDialog):
    def __init__(self, parent, initial_path="", *, before_repair=None, after_repair=None):
        super().__init__(parent)
        self._lt = parent._lt
        self._browse_path = initial_path
        self.worker = None
        self.results = []
        self.before_repair = before_repair
        self.after_repair = after_repair
        self.setWindowTitle(self._lt("Repair Yamaha Boot Sector..."))
        self.resize(690, 520)
        layout = QVBoxLayout(self)
        description = QLabel(self._lt(
            "Repair images in place and make files visible by clearing hidden and system flags. "
            "File contents are preserved. "
            "Changing format also creates a converted file beside the repaired original."
        ))
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.scope_combo = QComboBox(self)
        self.scope_combo.setObjectName("bootRepairScope")
        self.scope_combo.addItem(self._lt("Image file"), False)
        self.scope_combo.addItem(self._lt("Entire folder"), True)
        form.addRow(self._lt("Apply to:"), self.scope_combo)
        self.path_edit = QLineEdit(initial_path if os.path.isfile(initial_path) else "", self)
        self.path_edit.setObjectName("bootRepairPath")
        self.path_edit.setPlaceholderText(self._lt("Choose a floppy image"))
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        self.browse_button = QPushButton(self._lt("Browse..."), self)
        self.browse_button.clicked.connect(self._browse)
        path_row.addWidget(self.browse_button)
        form.addRow(path_row)
        self.target_combo = QComboBox(self)
        self.target_combo.setObjectName("bootRepairTarget")
        for extension, label in (
            ("", self._lt("Keep current format")),
            ("hfe", self._lt("HFE (Nalbantov) image")),
            ("img", self._lt("IMG (Gotek) raw sector image")),
            ("ima", self._lt("IMA raw sector image")),
            ("bin", self._lt("BIN raw sector image")),
            ("vfd", "VFD"),
        ):
            self.target_combo.addItem(label, extension)
        form.addRow(self._lt("Target format:"), self.target_combo)
        layout.addLayout(form)
        self.backup_check = QCheckBox(self._lt("Create a backup before repairing"), self)
        self.backup_check.setObjectName("bootRepairBackup")
        self.backup_check.setChecked(False)
        layout.addWidget(self.backup_check)
        self.recursive_check = QCheckBox(self._lt("Include subfolders"), self)
        self.recursive_check.setObjectName("bootRepairRecursive")
        self.recursive_check.setEnabled(False)
        layout.addWidget(self.recursive_check)
        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.report = QPlainTextEdit(self)
        self.report.setReadOnly(True)
        self.report.setObjectName("bootRepairReport")
        layout.addWidget(self.report, 1)
        buttons = QDialogButtonBox(self)
        self.run_button = buttons.addButton(self._lt("Repair"), QDialogButtonBox.ActionRole)
        self.run_button.setObjectName("bootRepairRun")
        self.run_button.clicked.connect(self._start)
        self.close_button = buttons.addButton(self._lt("Close"), QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _scope_changed(self):
        directory = bool(self.scope_combo.currentData())
        self.recursive_check.setEnabled(directory)
        if directory and os.path.isfile(self.path_edit.text()):
            self.path_edit.setText(os.path.dirname(self.path_edit.text()))

    def _browse(self):
        current = self.path_edit.text() or self._browse_path or os.path.expanduser("~")
        if self.scope_combo.currentData():
            path = QFileDialog.getExistingDirectory(self, self._lt("Choose a folder containing image files."), current)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, self._lt("Choose a floppy image"), current,
                self._lt("Common floppy images") + " (*.img *.ima *.bin *.vfd *.hfe *.IMG *.IMA *.BIN *.VFD *.HFE)",
            )
        if path:
            self.path_edit.setText(path)

    def _start(self):
        if self.worker is not None:
            return
        source_path = self.path_edit.text().strip()
        if not source_path:
            self.status_label.setText(self._lt("Choose a regular IMG, IMA, BIN, VFD, or HFE image file."))
            return
        options = {
            "directory": bool(self.scope_combo.currentData()),
            "recursive": self.recursive_check.isChecked(),
            "target_format": self.target_combo.currentData(),
            "backup": self.backup_check.isChecked(),
        }
        if self.before_repair is not None and not self.before_repair(source_path, options):
            return
        self.report.clear()
        self.results.clear()
        self.status_label.clear()
        self.progress.setValue(0)
        self._set_running(True)
        worker = BootSectorRepairWorker(source_path, options, self)
        self.worker = worker
        worker.progressChanged.connect(self._progress)
        worker.itemFinished.connect(self._item_finished)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def _set_running(self, running):
        for widget in (self.scope_combo, self.path_edit, self.browse_button, self.target_combo, self.backup_check, self.run_button):
            widget.setEnabled(not running)
        self.recursive_check.setEnabled(not running and bool(self.scope_combo.currentData()))
        self.close_button.setText(self._lt("Cancel") if running else self._lt("Close"))

    @Slot(int, int, str)
    def _progress(self, index, total, path):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(index)
        self.status_label.setText(path)

    @Slot(object)
    def _item_finished(self, result):
        self.results.append(result)
        if result.error:
            status = self._lt("Failed")
        elif result.repaired and result.converted:
            status = self._lt("Repaired and converted")
        elif result.repaired:
            status = self._lt("Repaired")
        elif result.converted:
            status = self._lt("Converted")
        else:
            status = self._lt("Unchanged")
        self.report.appendPlainText(f"{status}: {result.source_path}")
        if result.error:
            self.report.appendPlainText(self._lt(result.error))
        elif result.converted:
            self.report.appendPlainText(f"→ {result.output_path}")
        if result.backup_path:
            self.report.appendPlainText(self._lt("Backup: {path}", path=result.backup_path))

    @Slot(object)
    def _completed(self, batch):
        changed = sum(bool(item.repaired or item.converted) for item in batch.results)
        failed = sum(bool(item.error) for item in batch.results)
        if not batch.results and not batch.cancelled and not batch.scan_errors:
            text = self._lt("No supported image files found.")
        else:
            text = self._lt(
                "Processed {count} image(s): {changed} changed, {unchanged} unchanged, {failed} failed.",
                count=len(batch.results), changed=changed, unchanged=len(batch.results) - changed - failed, failed=failed,
            )
        if batch.cancelled:
            text = self._lt("Operation cancelled.") + "\n" + text
        if batch.scan_errors:
            text += "\n" + self._lt(
                "Folder scan incomplete: {count} folder(s) could not be read.", count=len(batch.scan_errors),
            )
            for error in batch.scan_errors:
                self.report.appendPlainText(self._lt("Could not scan folder: {path}", path=error.path))
                self.report.appendPlainText(error.error)
        self.status_label.setText(text)

    @Slot(str)
    def _failed(self, message):
        self.status_label.setText(self._lt(message))

    @Slot()
    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self._set_running(False)
        if self.after_repair is not None:
            self.after_repair()

    def reject(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status_label.setText(self._lt("Cancelling..."))
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
