import gc
import os
import shutil

from PySide6.QtCore import Qt, QEvent, QSettings
from PySide6.QtGui import QColor, QFont, QPalette
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
    QGroupBox,
    QToolButton,
    QMenu,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
)

from .midi_metadata import (
    update_midi_title,
    update_midi_title_to_destination,
    validate_legacy_title_input,
    extract_midi_type_label_from_midi,
)
from .dos83_renamer import rename_midi_files_dos83
from .midi_type0_converter import convert_midi_files_to_type0
from .ui_utils import is_dark_theme, pixmap_from_base64, embedded_logo_dt, embedded_logo_lt
from .drop_table_widget import DropTableWidget
from .midi_scan_worker import MidiProcessingWorker
from .icon_utils import apply_window_icon
from .app_info import (
    APP_TITLE_WITH_VERSION,
    APP_WEBSITE,
    SETTINGS_APP as APP_SETTINGS_APP,
    SETTINGS_ORG as APP_SETTINGS_ORG,
)


class TitleOverflowDelegate(QStyledItemDelegate):
    def __init__(self, limit, parent=None):
        super().__init__(parent)
        self.limit = limit
        self.warning_color = QColor("#F5B041")
        self.highlight_enabled = True

    def set_highlight_enabled(self, enabled):
        self.highlight_enabled = bool(enabled)

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole) or ""
        if (
            not self.highlight_enabled
            or index.column() != 4
            or len(text) <= self.limit
            or option.state & QStyle.State_Selected
        ):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        full_text = opt.text
        normal_text = full_text[:self.limit]
        overflow_text = full_text[self.limit:]

        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget).adjusted(4, 0, -2, 0)
        if text_rect.width() <= 0:
            return

        painter.save()
        painter.setClipRect(text_rect)
        fm = opt.fontMetrics
        baseline = text_rect.top() + (text_rect.height() + fm.ascent() - fm.descent()) // 2
        x = text_rect.left()

        painter.setPen(opt.palette.color(QPalette.Text))
        painter.drawText(x, baseline, normal_text)
        x += fm.horizontalAdvance(normal_text)

        painter.setPen(self.warning_color)
        painter.drawText(x, baseline, overflow_text)
        painter.restore()


class MidiTitleWindow(QMainWindow):
    TITLE_COMPAT_LIMIT = 32
    SETTINGS_ORG = APP_SETTINGS_ORG
    SETTINGS_APP = APP_SETTINGS_APP
    SETTING_SHOW_COMPAT_WARNING = "show_compat_warning"
    SETTING_STORE_BACKUPS = "store_backups"
    SETTING_SKIP_TYPE0_WARNING = "skip_type0_warning"
    SETTING_SHOW_MIDI_TYPE_COLUMN = "show_midi_type_column"
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE_WITH_VERSION)
        apply_window_icon(self)
        self.resize(860, 800)
        self.pendingEdits = {}         # keys: full file paths, values: new titles
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._did_apply_initial_column_sizing = False
        self._is_adjusting_columns = False

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
        self.choose_button.setToolTip(
            "Select a folder to scan for .mid and .midi files."
        )
        self.choose_button.clicked.connect(self.browse_directory)
        main_layout.addWidget(self.choose_button)

        # Middle: Table for displaying MIDI files (using our DropTableWidget subclass)
        # Column order:
        # 0: Delete ("X"), 1: FullPath (hidden), 2: 📋, 3: Filename, 4: Title, 5: Compat warning (>32), 6: MIDI type
        self.table = DropTableWidget(0, 7)
        self.table.setStyleSheet("QTableWidget::item:selected { background-color: #FFB347; }")
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "32+", "Type"])
        self.table.setToolTip(
            "Drop MIDI files here, click a Title cell to edit, or click the clipboard icon to copy a filename."
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(40)
        header.sectionResized.connect(self._handle_section_resized)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(2, 50)
        self.table.setColumnWidth(3, 260)
        self.table.setColumnWidth(4, 260)
        self.table.setColumnWidth(5, 65)
        self.table.setColumnWidth(6, 70)
        self.table.setColumnHidden(1, True)  # Hide the full path column
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self.handle_cell_clicked)
        self.table.cellDoubleClicked.connect(self.handle_cell_double_clicked)
        self.title_delegate = TitleOverflowDelegate(self.TITLE_COMPAT_LIMIT, self.table)
        self.table.setItemDelegateForColumn(4, self.title_delegate)
        header_tooltips = {
            0: "Remove this row from the list (does not delete the file on disk).",
            1: "Internal full file path (hidden).",
            2: "Copy filename to clipboard.",
            3: "Filename on disk.",
            4: "MIDI title metadata. Click to edit.",
            5: f"Shows if title exceeds {self.TITLE_COMPAT_LIMIT} characters.",
            6: "Detected MIDI type from the file header.",
        }
        for column, tooltip in header_tooltips.items():
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip(tooltip)
        main_layout.addWidget(self.table, stretch=1)

        # Bottom: Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(42)
        self.status_label.setToolTip("Operation status, warnings, and progress messages.")
        main_layout.addWidget(self.status_label)

        # Controls area: grouped into equally spaced sections for clarity.
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        options_group = QGroupBox("Options")
        options_group.setToolTip("Display and compatibility preferences for the file list.")
        options_layout = QVBoxLayout(options_group)
        options_layout.setContentsMargins(10, 14, 10, 10)
        options_layout.setSpacing(6)

        show_compat_warning = self.settings.value(self.SETTING_SHOW_COMPAT_WARNING, True, type=bool)
        self.compat_warning_checkbox = QCheckBox("Show >32-char warning")
        self.compat_warning_checkbox.setChecked(show_compat_warning)
        self.compat_warning_checkbox.setToolTip(
            "Highlight title characters beyond the 32-character legacy compatibility limit."
        )
        self.compat_warning_checkbox.toggled.connect(self.toggle_compat_warnings)
        self.title_delegate.set_highlight_enabled(show_compat_warning)
        options_layout.addWidget(self.compat_warning_checkbox, alignment=Qt.AlignLeft)

        show_midi_type_column = self.settings.value(self.SETTING_SHOW_MIDI_TYPE_COLUMN, False, type=bool)
        self.midi_type_column_checkbox = QCheckBox("Show MIDI type column")
        self.midi_type_column_checkbox.setChecked(show_midi_type_column)
        self.midi_type_column_checkbox.setToolTip(
            "Show an extra table column with each file's detected MIDI type."
        )
        self.midi_type_column_checkbox.toggled.connect(self.toggle_midi_type_column)
        options_layout.addWidget(self.midi_type_column_checkbox, alignment=Qt.AlignLeft)

        store_backups = self.settings.value(self.SETTING_STORE_BACKUPS, False, type=bool)
        self.backup_checkbox = QCheckBox("Store backups on save")
        self.backup_checkbox.setChecked(store_backups)
        self.backup_checkbox.setToolTip(
            "Create a <filename>_backup.mid copy before save and utility operations."
        )
        self.backup_checkbox.toggled.connect(self.toggle_store_backups)
        options_layout.addWidget(self.backup_checkbox, alignment=Qt.AlignLeft)
        options_layout.addStretch()

        utilities_group = QGroupBox("Utilities")
        utilities_group.setToolTip("Batch tools that run across every listed file immediately.")
        utilities_layout = QVBoxLayout(utilities_group)
        utilities_layout.setContentsMargins(10, 14, 10, 10)
        utilities_layout.setSpacing(6)

        utilities_hint = QLabel("Run these on all listed files:")
        utilities_hint.setWordWrap(True)
        utilities_hint.setAlignment(Qt.AlignCenter)
        utilities_layout.addWidget(utilities_hint)

        self.renameAllButton = QPushButton("Rename All to DOS 8.3")
        self.renameAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.renameAllButton.setMinimumHeight(36)
        self.renameAllButton.setToolTip(
            "Utility: rename every listed file to DOS 8.3 format (00.MID, 01.MID, ...)."
        )
        self.renameAllButton.clicked.connect(self.rename_all_for_disk)
        utilities_layout.addWidget(self.renameAllButton)

        self.convertType0Button = QPushButton("Convert All to MIDI Type 0")
        self.convertType0Button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertType0Button.setMinimumHeight(36)
        self.convertType0Button.setToolTip(
            "Utility: convert every listed file to MIDI Type 0 (single-track)."
        )
        self.convertType0Button.clicked.connect(self.convert_all_to_type0)
        utilities_layout.addWidget(self.convertType0Button)
        utilities_layout.addStretch()

        actions_group = QGroupBox("Actions")
        actions_group.setToolTip("List management and save actions.")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(10, 14, 10, 10)
        actions_layout.setSpacing(6)

        # Clear button (styled to match Save button)
        self.clearButton = QToolButton()
        self.clearButton.setText("Clear List")
        self.clearButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.clearButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clearButton.setMinimumHeight(36)
        self.clearButton.setToolTip("Remove all files from the current list.")
        self.clearButton.clicked.connect(self.clear_list)

        # Save button (using QToolButton with a menu)
        self.saveButton = QToolButton()
        self.saveButton.setText("Save")
        self.saveButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveButton.setMinimumHeight(36)
        self.saveButton.setToolTip(
            "Save pending title edits to current files. Use the arrow for Save As..."
        )
        self.saveButton.setPopupMode(QToolButton.MenuButtonPopup)
        menu = QMenu(self.saveButton)
        action_save = menu.addAction("Save")
        action_save.setToolTip("Write pending title edits to the currently listed files.")
        action_save.triggered.connect(self.save_pending_changes)
        action_save_as = menu.addAction("Save As...")
        action_save_as.setToolTip("Save copies with current titles to a selected destination folder.")
        action_save_as.triggered.connect(self.save_as_changes)
        self.saveButton.setMenu(menu)
        self.saveButton.clicked.connect(self.save_pending_changes)

        actions_layout.addWidget(self.clearButton)
        actions_layout.addWidget(self.saveButton)
        actions_layout.addStretch()

        for section in (options_group, utilities_group, actions_group):
            section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        controls_layout.addWidget(options_group, stretch=1)
        controls_layout.addWidget(utilities_group, stretch=1)
        controls_layout.addWidget(actions_group, stretch=1)
        main_layout.addLayout(controls_layout)

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
        prog_title = QLabel(APP_TITLE_WITH_VERSION)
        prog_title.setFont(QFont("Helvetica", 12))
        prog_title.setAlignment(Qt.AlignCenter)
        website = QLabel(APP_WEBSITE)
        website.setFont(QFont("Helvetica", 12))
        website.setAlignment(Qt.AlignCenter)
        website.setToolTip("Project website.")
        logo_layout.addWidget(prog_title)
        logo_layout.addWidget(website)
        main_layout.addWidget(logo_container)

        self.table.setColumnHidden(5, not self.compat_warning_checkbox.isChecked())
        self.table.setColumnHidden(6, not self.midi_type_column_checkbox.isChecked())

        # Set mouse tracking and install an event filter on the table viewport.
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport():
            if event.type() == QEvent.Resize:
                self._resize_table_columns_to_fill()
            elif event.type() == QEvent.MouseMove:
                pos = event.position().toPoint()
                index = self.table.indexAt(pos)
                # When hovering over the Title cell, show a pointing hand.
                if index.isValid() and index.column() == 4:
                    self.table.viewport().setCursor(Qt.PointingHandCursor)
                else:
                    self.table.viewport().setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_apply_initial_column_sizing:
            self._resize_table_columns_to_fill()
            self._did_apply_initial_column_sizing = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_table_columns_to_fill()

    def _handle_section_resized(self, logical_index, old_size, new_size):
        if self._is_adjusting_columns:
            return
        if logical_index != 1:
            self._resize_table_columns_to_fill(preferred_column=logical_index)

    def _resize_table_columns_to_fill(self, preferred_column=None):
        if self._is_adjusting_columns:
            return

        available_width = self.table.viewport().width()
        if available_width <= 0:
            return

        fixed_columns = [0, 2]
        if not self.table.isColumnHidden(5):
            fixed_columns.append(5)
        if not self.table.isColumnHidden(6):
            fixed_columns.append(6)
        fixed_total = sum(self.table.columnWidth(column) for column in fixed_columns)

        min_section = self.table.horizontalHeader().minimumSectionSize()
        remaining = max((min_section * 2), available_width - fixed_total)

        filename_width = max(min_section, self.table.columnWidth(3))
        title_width = max(min_section, self.table.columnWidth(4))

        if preferred_column == 3:
            filename_width = min(filename_width, remaining - min_section)
            title_width = remaining - filename_width
        elif preferred_column == 4:
            title_width = min(title_width, remaining - min_section)
            filename_width = remaining - title_width
        else:
            combined_width = max(1, filename_width + title_width)
            filename_width = int(round(remaining * filename_width / combined_width))
            filename_width = max(min_section, min(filename_width, remaining - min_section))
            title_width = remaining - filename_width

        self._is_adjusting_columns = True
        try:
            self.table.setColumnWidth(3, filename_width)
            self.table.setColumnWidth(4, title_width)
        finally:
            self._is_adjusting_columns = False

    def toggle_compat_warnings(self, state):
        self.table.setColumnHidden(5, not state)
        self.settings.setValue(self.SETTING_SHOW_COMPAT_WARNING, bool(state))
        self.title_delegate.set_highlight_enabled(state)
        self.table.viewport().update()
        self._resize_table_columns_to_fill()
        if state:
            self.refresh_compat_indicators()

    def toggle_midi_type_column(self, state):
        self.table.setColumnHidden(6, not state)
        self.settings.setValue(self.SETTING_SHOW_MIDI_TYPE_COLUMN, bool(state))
        self._resize_table_columns_to_fill()
        if state:
            self.refresh_midi_type_indicators()

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
        else:
            indicator.setToolTip(
                f"Title length is within the {self.TITLE_COMPAT_LIMIT}-character compatibility limit."
            )
        self.table.setItem(row, 5, indicator)

    def refresh_compat_indicators(self):
        for row in range(self.table.rowCount()):
            title_item = self.table.item(row, 4)
            title = title_item.text() if title_item else ""
            self._update_compat_indicator(row, title)

    def _update_midi_type_indicator(self, row, midi_type):
        indicator = QTableWidgetItem(midi_type if midi_type else "Unknown")
        indicator.setTextAlignment(Qt.AlignCenter)
        indicator.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        indicator.setToolTip(
            "Detected MIDI file type from header bytes."
            if midi_type
            else "MIDI type could not be determined for this file."
        )
        self.table.setItem(row, 6, indicator)

    def refresh_midi_type_indicators(self):
        for row in range(self.table.rowCount()):
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                self._update_midi_type_indicator(row, "Unknown")
                continue
            midi_type = extract_midi_type_label_from_midi(full_path_item.text())
            self._update_midi_type_indicator(row, midi_type)

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

    def _confirm_type0_conversion(self, file_count):
        skip_warning = self.settings.value(self.SETTING_SKIP_TYPE0_WARNING, False, type=bool)
        if skip_warning:
            return True

        warning_box = QMessageBox(self)
        apply_window_icon(warning_box)
        warning_box.setIcon(QMessageBox.Warning)
        warning_box.setWindowTitle("Convert All to MIDI Type 0")
        warning_box.setText(
            f"This will convert all {file_count} listed file(s) to MIDI Type 0 (single track).\n\n"
            "This conversion is not compatible with Yamaha XG files."
        )

        backup_hint = (
            "Backup recommendation: backups are currently enabled."
            if self.backup_checkbox.isChecked()
            else (
                "Backup recommendation: enable \"Store backups on save\" before running this utility."
            )
        )
        warning_box.setInformativeText(
            f"{backup_hint}\n\nDo you want to continue?"
        )
        dont_show_checkbox = QCheckBox("Do not show this warning again")
        warning_box.setCheckBox(dont_show_checkbox)
        warning_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        warning_box.setDefaultButton(QMessageBox.No)
        result = warning_box.exec()
        confirmed = result == QMessageBox.Yes
        if confirmed and dont_show_checkbox.isChecked():
            self.settings.setValue(self.SETTING_SKIP_TYPE0_WARNING, True)
        return confirmed

    def convert_all_to_type0(self):
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

        if not self._confirm_type0_conversion(len(all_paths)):
            return

        result = convert_midi_files_to_type0(
            all_paths,
            create_backups=self.backup_checkbox.isChecked(),
            backup_path_builder=self._get_backup_path,
        )

        converted_count = len(result.converted)
        unchanged_count = len(result.unchanged)
        backup_count = len(result.backups_created)
        failed_count = len(result.failed)

        status_parts = [f"Converted {converted_count} file(s) to MIDI Type 0."]
        if unchanged_count:
            status_parts.append(f"{unchanged_count} already Type 0 and were left unchanged.")
        if backup_count:
            status_parts.append(f"Created {backup_count} backup file(s).")
        if failed_count:
            status_parts.append(f"{failed_count} file(s) failed conversion.")
        self.status_label.setText("\n".join(status_parts))
        self.refresh_midi_type_indicators()

        if failed_count:
            max_rows = 10
            details = "\n".join(
                f"{os.path.basename(path)}: {error}"
                for path, error in result.failed[:max_rows]
            )
            if failed_count > max_rows:
                details += f"\n...and {failed_count - max_rows} more."
            QMessageBox.warning(self, "Type 0 Conversion Issues", details)

    def add_table_row(self, full_path, filename, title, midi_type=""):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Column 0: Delete cell with "X"
        delete_item = QTableWidgetItem("X")
        delete_item.setTextAlignment(Qt.AlignCenter)
        delete_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        delete_item.setToolTip("Remove this file from the list.")
        self.table.setItem(row, 0, delete_item)

        # Column 1: FullPath (hidden)
        fullpath_item = QTableWidgetItem(full_path)
        fullpath_item.setFlags(fullpath_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 1, fullpath_item)

        # Column 2: Clipboard emoji
        copy_item = QTableWidgetItem("📋")
        copy_item.setTextAlignment(Qt.AlignCenter)
        copy_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        copy_item.setToolTip("Copy filename to clipboard.")
        self.table.setItem(row, 2, copy_item)

        # Column 3: Filename
        filename_item = QTableWidgetItem(filename)
        filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        filename_item.setToolTip("Double-click to copy filename.")
        self.table.setItem(row, 3, filename_item)

        # Column 4: Title (fallback to filename only when no title is present)
        display_title = title if title != "" else filename
        title_item = QTableWidgetItem(display_title)
        title_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        title_item.setToolTip("Click to edit this MIDI title.")
        self.table.setItem(row, 4, title_item)

        # Column 5: Compatibility indicator for titles > 32 characters
        self._update_compat_indicator(row, display_title)

        # Column 6: MIDI type from file header bytes
        self._update_midi_type_indicator(row, midi_type)

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
        apply_window_icon(dialog)
        dialog.setWindowTitle("Edit Title")
        dialog.setModal(True)
        dialog.setMinimumWidth(760)
        dialog_layout = QVBoxLayout(dialog)

        prompt = QLabel("Enter new title:")
        dialog_layout.addWidget(prompt)

        editor = QLineEdit(current_title)
        editor.setMinimumWidth(720)
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
