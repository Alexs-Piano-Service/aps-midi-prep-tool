import gc
import os
import shutil

from PySide6.QtCore import Qt, QEvent, QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidgetItem,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QHeaderView,
    QSizePolicy,
    QProgressDialog,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QToolButton,
    QMenu,
)

from .midi_metadata import (
    update_midi_title,
    update_midi_title_to_destination,
    validate_legacy_title_input,
)
from .dos83_renamer import rename_midi_files_dos83
from .ui_utils import is_dark_theme, pixmap_from_base64, embedded_logo_dt, embedded_logo_lt
from .drop_table_widget import DropTableWidget
from .midi_scan_worker import MidiProcessingWorker


class MidiTitleWindow(QMainWindow):
    TITLE_COMPAT_LIMIT = 32
    SETTINGS_ORG = "AlexPianoServiceLLC"
    SETTINGS_APP = "APSMIDImanager"
    SETTING_SHOW_COMPAT_WARNING = "show_compat_warning"
    SETTING_STORE_BACKUPS = "store_backups"
    def __init__(self):
        super().__init__()
        self.setWindowTitle("APS MIDI Manager")
        self.resize(860, 800)
        self.pendingEdits = {}         # keys: full file paths, values: new titles
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)

        # Main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self.setCentralWidget(main_widget)

        # Top: Choose MIDI Folder button
        self.choose_button = QPushButton("Choose MIDI Folder")
        self.choose_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.choose_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.choose_button.clicked.connect(self.browse_directory)
        main_layout.addWidget(self.choose_button)

        # Middle: Table for displaying MIDI files (using our DropTableWidget subclass)
        # Column order:
        # 0: Delete ("X"), 1: FullPath (hidden), 2: 📋, 3: Filename, 4: Title, 5: Compat warning (>32)
        self.table = DropTableWidget(0, 6)
        self.table.setStyleSheet("QTableWidget::item:selected { background-color: #FFB347; }")
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "32+"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(2, 50)
        self.table.setColumnWidth(5, 65)
        self.table.setColumnHidden(1, True)  # Hide the full path column
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self.handle_cell_clicked)
        self.table.cellDoubleClicked.connect(self.handle_cell_double_clicked)
        main_layout.addWidget(self.table, stretch=1)

        # Bottom: Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(42)
        main_layout.addWidget(self.status_label)

        # Horizontal layout for options and save controls
        hlayout = QHBoxLayout()
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(5)
        hlayout.setAlignment(Qt.AlignLeft)

        show_compat_warning = self.settings.value(self.SETTING_SHOW_COMPAT_WARNING, True, type=bool)
        self.compat_warning_checkbox = QCheckBox("Show >32-char warning")
        self.compat_warning_checkbox.setChecked(show_compat_warning)
        self.compat_warning_checkbox.toggled.connect(self.toggle_compat_warnings)
        hlayout.addWidget(self.compat_warning_checkbox)

        store_backups = self.settings.value(self.SETTING_STORE_BACKUPS, False, type=bool)
        self.backup_checkbox = QCheckBox("Store backups on save")
        self.backup_checkbox.setChecked(store_backups)
        self.backup_checkbox.toggled.connect(self.toggle_store_backups)
        hlayout.addWidget(self.backup_checkbox)

        hlayout.addStretch()

        # Clear button (styled to match Save button)
        self.clearButton = QToolButton()
        self.clearButton.setText("Clear List")
        self.clearButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.clearButton.setFixedWidth(200)
        self.clearButton.clicked.connect(self.clear_list)
        hlayout.addWidget(self.clearButton)

        # Save button (using QToolButton with a menu)
        self.saveButton = QToolButton()
        self.saveButton.setText("Save")
        self.saveButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        # Instead of making it fixed to a very small width, we set its fixed width to 200.
        self.saveButton.setFixedWidth(200)
        self.saveButton.setPopupMode(QToolButton.MenuButtonPopup)
        menu = QMenu(self.saveButton)
        action_save = menu.addAction("Save")
        action_save.triggered.connect(self.save_pending_changes)
        action_save_as = menu.addAction("Save As...")
        action_save_as.triggered.connect(self.save_as_changes)
        menu.addSeparator()
        action_rename_all = menu.addAction("Rename All to DOS 8.3")
        action_rename_all.triggered.connect(self.rename_all_for_disk)
        self.saveButton.setMenu(menu)
        self.saveButton.clicked.connect(self.save_pending_changes)
        hlayout.addWidget(self.saveButton)
        
        main_layout.addLayout(hlayout)

        # Logo Area
        logo_container = QWidget()
        logo_layout = QVBoxLayout(logo_container)
        logo_layout.setAlignment(Qt.AlignCenter)
        self.logo_label = QLabel("Your Logo Here")
        self.logo_label.setAlignment(Qt.AlignCenter)
        # Replace embedded_logo_dt and embedded_logo_lt with your actual base64 strings.
        if is_dark_theme():
            pixmap = pixmap_from_base64(embedded_logo_dt)
        else:
            pixmap = pixmap_from_base64(embedded_logo_lt)
        if not pixmap.isNull():
            try:
                pixmap = pixmap.scaled(200, 62, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.logo_label.setPixmap(pixmap)
            except Exception as e:
                self.logo_label.setText("Error loading logo image.")
        else:
            self.logo_label.setText("Logo image not found.")
        logo_layout.addWidget(self.logo_label)
        prog_title = QLabel("APS MIDI Manager")
        prog_title.setFont(QFont("Helvetica", 12))
        prog_title.setAlignment(Qt.AlignCenter)
        website = QLabel("https://www.alexanderpeppe.com/")
        website.setFont(QFont("Helvetica", 12))
        website.setAlignment(Qt.AlignCenter)
        logo_layout.addWidget(prog_title)
        logo_layout.addWidget(website)
        main_layout.addWidget(logo_container)

        self.table.setColumnHidden(5, not self.compat_warning_checkbox.isChecked())

        # Set mouse tracking and install an event filter on the table viewport.
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.MouseMove:
            pos = event.position().toPoint()
            index = self.table.indexAt(pos)
            # When hovering over the Title cell, show a pointing hand.
            if index.isValid() and index.column() == 4:
                self.table.viewport().setCursor(Qt.PointingHandCursor)
            else:
                self.table.viewport().setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)

    def toggle_compat_warnings(self, state):
        self.table.setColumnHidden(5, not state)
        self.settings.setValue(self.SETTING_SHOW_COMPAT_WARNING, bool(state))
        if state:
            self.refresh_compat_indicators()

    def toggle_store_backups(self, state):
        self.settings.setValue(self.SETTING_STORE_BACKUPS, bool(state))

    def _get_backup_path(self, file_path):
        stem, ext = os.path.splitext(file_path)
        return f"{stem}_backup{ext}"

    def _create_backup_if_enabled(self, file_path):
        if not self.backup_checkbox.isChecked():
            return None
        backup_path = self._get_backup_path(file_path)
        try:
            shutil.copy2(file_path, backup_path)
            return None
        except Exception as e:
            return f"Error creating backup for {os.path.basename(file_path)}: {str(e)}"

    def _is_title_too_long(self, title):
        return len(title) > self.TITLE_COMPAT_LIMIT

    def _update_compat_indicator(self, row, title):
        indicator = QTableWidgetItem("LONG" if self._is_title_too_long(title) else "")
        indicator.setTextAlignment(Qt.AlignCenter)
        indicator.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if self._is_title_too_long(title):
            indicator.setToolTip(
                f"Title is longer than {self.TITLE_COMPAT_LIMIT} characters; "
                "older systems may truncate or reject it."
            )
        self.table.setItem(row, 5, indicator)

    def refresh_compat_indicators(self):
        for row in range(self.table.rowCount()):
            title_item = self.table.item(row, 4)
            title = title_item.text() if title_item else ""
            self._update_compat_indicator(row, title)

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose MIDI Folder")
        if directory:
            self.choose_button.setEnabled(False)
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            self.pendingEdits.clear()
            self.progressDialog = QProgressDialog("Processing MIDI files...", "Cancel", 0, 100, self)
            self.progressDialog.setWindowModality(Qt.WindowModal)
            self.progressDialog.setMinimumDuration(0)
            self.worker = MidiProcessingWorker(directory)
            self.worker.progressChanged.connect(self.progressDialog.setValue)
            self.worker.fileProcessed.connect(self.add_table_row)
            self.worker.finished.connect(lambda: self.on_worker_finished(directory))
            self.worker.start()

    def on_worker_finished(self, directory):
        self.progressDialog.close()
        self.table.setSortingEnabled(True)
        self.table.sortItems(3, order=Qt.AscendingOrder)  # sort by filename (col 3)
        self.refresh_compat_indicators()
        self.status_label.setText(f"Selected Folder: \"{directory}\"")
        self.choose_button.setEnabled(True)
        self.worker = None
        gc.collect()

    def clear_list(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return
        if self.table.rowCount() == 0:
            self.status_label.setText("List is already empty.")
            return

        reply = QMessageBox.question(
            self,
            "Clear List",
            "Remove all files from the current list?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.table.setRowCount(0)
        self.pendingEdits.clear()
        self.status_label.setText("List cleared.")

    def _apply_path_remap(self, old_to_new):
        if not old_to_new:
            return
        self.pendingEdits = {
            old_to_new.get(path, path): title
            for path, title in self.pendingEdits.items()
        }

    def _update_table_paths(self, old_to_new):
        if not old_to_new:
            return

        sorting_enabled = self.table.isSortingEnabled()
        if sorting_enabled:
            self.table.setSortingEnabled(False)

        try:
            for row in range(self.table.rowCount()):
                full_path_item = self.table.item(row, 1)
                if not full_path_item:
                    continue
                old_path = full_path_item.text()
                new_path = old_to_new.get(old_path)
                if not new_path:
                    continue

                full_path_item.setText(new_path)
                filename_item = self.table.item(row, 3)
                if filename_item:
                    filename_item.setText(os.path.basename(new_path))
                else:
                    filename_item = QTableWidgetItem(os.path.basename(new_path))
                    filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    self.table.setItem(row, 3, filename_item)
        finally:
            if sorting_enabled:
                self.table.setSortingEnabled(True)
                self.table.sortItems(3, order=Qt.AscendingOrder)

    def rename_all_for_disk(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return

        row_count = self.table.rowCount()
        if row_count == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return

        all_paths = []
        for row in range(row_count):
            full_path_item = self.table.item(row, 1)
            if not full_path_item:
                continue
            all_paths.append(full_path_item.text())

        if not all_paths:
            QMessageBox.information(self, "No Valid Files", "No valid files are currently listed.")
            return

        message = (
            f"Rename all {len(all_paths)} listed file(s) to DOS 8.3 format?\n"
            "This applies 00/01/... prefixes and a .MID extension."
        )
        if self.backup_checkbox.isChecked():
            message += "\n\nBackups will be created with each original name plus '_backup'."
        reply = QMessageBox.question(
            self,
            "Rename All Files",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            result = rename_midi_files_dos83(
                all_paths,
                create_backups=self.backup_checkbox.isChecked(),
                backup_path_builder=self._get_backup_path,
            )
        except Exception as e:
            QMessageBox.critical(self, "Rename Failed", str(e))
            return

        old_to_new = {source: target for source, target in result.renamed}
        self._apply_path_remap(old_to_new)
        self._update_table_paths(old_to_new)

        renamed_count = len(result.renamed)
        unchanged_count = len(result.unchanged)
        backup_count = len(result.backups_created)

        status_parts = [f"Renamed {renamed_count} file(s) to DOS 8.3."]
        if unchanged_count:
            status_parts.append(f"{unchanged_count} already matched and were left unchanged.")
        if backup_count:
            status_parts.append(f"Created {backup_count} backup file(s).")
        self.status_label.setText("\n".join(status_parts))

    def add_table_row(self, full_path, filename, title):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Column 0: Delete cell with "X"
        delete_item = QTableWidgetItem("X")
        delete_item.setTextAlignment(Qt.AlignCenter)
        delete_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self.table.setItem(row, 0, delete_item)

        # Column 1: FullPath (hidden)
        fullpath_item = QTableWidgetItem(full_path)
        fullpath_item.setFlags(fullpath_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 1, fullpath_item)

        # Column 2: Clipboard emoji
        copy_item = QTableWidgetItem("📋")
        copy_item.setTextAlignment(Qt.AlignCenter)
        copy_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self.table.setItem(row, 2, copy_item)

        # Column 3: Filename
        filename_item = QTableWidgetItem(filename)
        filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self.table.setItem(row, 3, filename_item)

        # Column 4: Title (fallback to filename only when no title is present)
        display_title = title if title != "" else filename
        title_item = QTableWidgetItem(display_title)
        title_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self.table.setItem(row, 4, title_item)

        # Column 5: Compatibility indicator for titles > 32 characters
        self._update_compat_indicator(row, display_title)

    def handle_cell_clicked(self, row, column):
        # Column 0: remove from list
        if column == 0:
            full_path_item = self.table.item(row, 1)
            if full_path_item:
                self.pendingEdits.pop(full_path_item.text(), None)
            self.table.removeRow(row)
            self.status_label.setText("File removed from the list.")
            return

        # Column 2: Clipboard copy (copies filename from col 3)
        elif column == 2:
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename = filename_item.text()
                QApplication.clipboard().setText(filename)
                self.status_label.setText(f"'{filename}' copied to clipboard.")
        # Column 4: Title edit via dialog.
        elif column == 4:
            self.edit_via_dialog(row)

    def handle_cell_double_clicked(self, row, column):
        # Double-clicking Filename (col 3) copies it.
        if column == 3:
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename = filename_item.text()
                QApplication.clipboard().setText(filename)
                self.status_label.setText(f"'{filename}' copied to clipboard.")
        # For Title (col 4): edit via dialog.
        elif column == 4:
            self.edit_via_dialog(row)

    def _prompt_for_title(self, current_title):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Title")
        dialog.setModal(True)
        dialog_layout = QVBoxLayout(dialog)

        prompt = QLabel("Enter new title:")
        dialog_layout.addWidget(prompt)

        editor = QLineEdit(current_title)
        dialog_layout.addWidget(editor)

        warning_label = QLabel("")
        warning_label.setStyleSheet("color: #C62828;")
        warning_label.setVisible(False)
        dialog_layout.addWidget(warning_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        dialog_layout.addWidget(buttons)

        current_normalized = current_title.strip()

        def update_state(text):
            stripped = text.strip()
            validation_error = validate_legacy_title_input(stripped)
            unchanged = stripped == current_normalized
            has_text = bool(stripped)
            is_valid = validation_error is None or unchanged
            ok_button.setEnabled(has_text and is_valid)

            if has_text and validation_error and not unchanged:
                warning_label.setVisible(True)
                warning_label.setText(validation_error)
                return

            show_warning = self.compat_warning_checkbox.isChecked() and self._is_title_too_long(stripped)
            warning_label.setVisible(show_warning)
            if show_warning:
                warning_label.setText(
                    f"Compatibility warning: title is over {self.TITLE_COMPAT_LIMIT} characters."
                )
            else:
                warning_label.setText("")

        editor.textChanged.connect(update_state)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        update_state(current_title)
        editor.selectAll()
        editor.setFocus()

        if dialog.exec() == QDialog.Accepted:
            return editor.text().strip(), True
        return "", False

    def edit_via_dialog(self, row):
        title_item = self.table.item(row, 4)
        current_title = title_item.text() if title_item else ""
        if current_title == "No title found.":
            current_title = ""
        new_title, ok = self._prompt_for_title(current_title)
        if ok and new_title.strip():
            new_title = new_title.strip()
            if new_title == current_title:
                return

            validation_error = validate_legacy_title_input(new_title)
            if validation_error:
                QMessageBox.warning(self, "Invalid Title", validation_error)
                return

            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                return
            full_path = full_path_item.text()
            self.pendingEdits[full_path] = new_title
            new_title_item = QTableWidgetItem(new_title)
            new_title_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, 4, new_title_item)
            self._update_compat_indicator(row, new_title)
            filename = self.table.item(row, 3).text() if self.table.item(row, 3) else "this file"
            warning = ""
            if self.compat_warning_checkbox.isChecked() and self._is_title_too_long(new_title):
                warning = f"\nCompatibility warning: over {self.TITLE_COMPAT_LIMIT} characters."
            self.status_label.setText(
                f"Pending change:\nTitle for '{filename}' will be updated to '{new_title}' on save.{warning}"
            )
        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)

    def save_pending_changes(self):
        if not self.pendingEdits:
            QMessageBox.information(self, "No Changes", "There are no pending changes to save.")
            return
        
        errors = []
        # Update all pending title edits.
        if self.pendingEdits:
            progressDialog = QProgressDialog("Saving title changes...", "Cancel", 0, len(self.pendingEdits), self)
            progressDialog.setWindowModality(Qt.WindowModal)
            progressDialog.setMinimumDuration(0)
            current = 0
            for full_path, new_title in list(self.pendingEdits.items()):
                validation_error = validate_legacy_title_input(new_title)
                if validation_error:
                    errors.append(f"Invalid title for {os.path.basename(full_path)}: {validation_error}")
                    current += 1
                    progressDialog.setValue(current)
                    QApplication.processEvents()
                    if progressDialog.wasCanceled():
                        break
                    continue

                backup_error = self._create_backup_if_enabled(full_path)
                if backup_error:
                    errors.append(backup_error)
                    current += 1
                    progressDialog.setValue(current)
                    QApplication.processEvents()
                    if progressDialog.wasCanceled():
                        break
                    continue

                error_msg = update_midi_title(full_path, new_title)
                if error_msg:
                    errors.append(error_msg)
                current += 1
                progressDialog.setValue(current)
                QApplication.processEvents()
                if progressDialog.wasCanceled():
                    break
            progressDialog.close()
            self.pendingEdits.clear()
        
        if errors:
            QMessageBox.critical(self, "Errors Occurred", "\n".join(errors))
        else:
            QMessageBox.information(self, "Save Complete", "All pending changes have been saved.")

    def save_as_changes(self):
        dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if not dest_dir:
            return
        progressDialog = QProgressDialog("Saving files to new folder...", "Cancel", 0, self.table.rowCount(), self)
        progressDialog.setWindowModality(Qt.WindowModal)
        progressDialog.setMinimumDuration(0)
        row_count = self.table.rowCount()
        errors = []
        for i in range(row_count):
            full_path = self.table.item(i, 1).text()  # original file path (col 1)
            title = self.table.item(i, 4).text()        # Title (col 4)
            error_msg = update_midi_title_to_destination(full_path, title, dest_dir)
            if error_msg:
                errors.append(error_msg)
            progressDialog.setValue(i + 1)
            QApplication.processEvents()
            if progressDialog.wasCanceled():
                break
        progressDialog.close()
        if errors:
            QMessageBox.critical(self, "Errors Occurred", "\n".join(errors))
        else:
            QMessageBox.information(self, "Save As Complete", "Files have been saved to the new folder.")
