from PySide6.QtCore import QSize, Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .icon_utils import apply_window_icon
from .message_catalog import DEFAULT_LANGUAGE, normalize_language_code, translate_text
from .ui_utils import center_dialog_on_parent, is_dark_theme
from .formatting import (
    FAT32_LAYOUT_LABELS,
    FAT32_LAYOUT_MBR,
    FAT32_LAYOUT_SUPERFLOPPY,
    UsbDriveInfo,
    UsbFormatCancelled,
    display_bytes,
    list_removable_usb_drives,
)
from .formatting.usb_format_launcher import run_usb_format_for_gui


class UsbFormatWorker(QThread):
    progressChanged = Signal(int, int, str)
    formatFinished = Signal(object)
    formatFailed = Signal(str)
    operationCancelled = Signal(str)

    def __init__(self, drive_info, layout_kind, volume_label, parent=None):
        super().__init__(parent)
        self.drive_info = drive_info
        self.layout_kind = layout_kind
        self.volume_label = volume_label
        self._cancel_was_requested = False

    def cancel(self):
        self._cancel_was_requested = True
        self.requestInterruption()

    def _cancel_requested(self):
        return self._cancel_was_requested or self.isInterruptionRequested()

    def _emit_progress(self, step, total, message):
        self.progressChanged.emit(int(step or 0), int(total or 0), str(message or ""))

    def run(self):
        try:
            result = run_usb_format_for_gui(
                self.drive_info,
                self.layout_kind,
                volume_label=self.volume_label,
                progress_callback=self._emit_progress,
                cancel_callback=self._cancel_requested,
            )
            self.formatFinished.emit(result)
        except UsbFormatCancelled as exc:
            self.operationCancelled.emit(str(exc) or "Operation cancelled.")
        except Exception as exc:
            if self._cancel_requested():
                self.operationCancelled.emit(str(exc) or "Operation cancelled.")
                return
            self.formatFailed.emit(str(exc))


class UsbUsagePieChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.slices = []
        self.setMinimumSize(170, 132)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(210, 142)

    def set_slices(self, slices):
        self.slices = [(str(label), max(0, int(value or 0)), QColor(color)) for label, value, color in slices]
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        pie_size = min(rect.height(), 112)
        pie_rect = rect.adjusted(0, 0, -(rect.width() - pie_size), -(rect.height() - pie_size))
        legend_left = pie_rect.right() + 14
        total = sum(value for _label, value, _color in self.slices)

        if total <= 0:
            color = QColor("#9AA1A9") if is_dark_theme() else QColor("#C4CBD2")
            painter.setBrush(color)
            painter.setPen(QPen(color.darker(120), 1))
            painter.drawEllipse(pie_rect)
            painter.setPen(QColor("#D8DEE5") if is_dark_theme() else QColor("#47515A"))
            painter.drawText(rect.adjusted(pie_size + 14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "No readable usage")
            return

        start_angle = 90 * 16
        for _label, value, color in self.slices:
            if value <= 0:
                continue
            span = int(round((value / total) * 360 * 16))
            painter.setBrush(color)
            painter.setPen(QPen(color.darker(125), 1))
            painter.drawPie(pie_rect, start_angle, -span)
            start_angle -= span

        painter.setPen(QColor("#D8DEE5") if is_dark_theme() else QColor("#2E343B"))
        font = painter.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        y = rect.top() + 4
        for label, value, color in self.slices:
            if value <= 0:
                continue
            marker = QRectLike(legend_left, y + 3, 9, 9)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRect(marker.x, marker.y, marker.w, marker.h)
            painter.setPen(QColor("#D8DEE5") if is_dark_theme() else QColor("#2E343B"))
            text = f"{label}: {display_bytes(value)}"
            painter.drawText(legend_left + 15, y, max(10, rect.right() - legend_left - 15), 18, Qt.AlignLeft, text)
            y += 20


class QRectLike:
    def __init__(self, x, y, w, h):
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)


class UsbFormatDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        language_method = getattr(parent, "_language_code", None)
        self.language_code = normalize_language_code(language_method() if callable(language_method) else DEFAULT_LANGUAGE)
        apply_window_icon(self)
        self.setWindowTitle(self._lt("Format USB Stick for Disklavier"))
        self.setModal(True)
        self.setMinimumWidth(720)
        self.devices = []
        self.worker = None
        self.was_formatted = False
        self.format_result = None
        self._is_running = False

        self._build_ui()
        self._connect_ui()
        self.refresh_devices()
        QTimer.singleShot(0, lambda: center_dialog_on_parent(self, parent))

    def _lt(self, text):
        return translate_text(text, self.language_code)

    def _translate_dialog_button_box(self):
        button_labels = {
            QDialogButtonBox.Ok: "OK",
            QDialogButtonBox.Cancel: "Cancel",
            QDialogButtonBox.Close: "Close",
        }
        for standard_button, label in button_labels.items():
            button = self.buttons.button(standard_button) if hasattr(self, "buttons") else None
            if button is not None:
                button.setText(self._lt(label))

    def _show_message(self, icon, title, message, buttons=QMessageBox.Ok, default_button=QMessageBox.NoButton):
        dialog = QMessageBox(self)
        apply_window_icon(dialog)
        dialog.setIcon(icon)
        dialog.setWindowTitle(self._lt(title))
        dialog.setText(self._lt(message))
        dialog.setStandardButtons(buttons)
        if default_button != QMessageBox.NoButton:
            dialog.setDefaultButton(default_button)
        for standard_button, label in (
            (QMessageBox.Ok, "OK"),
            (QMessageBox.Cancel, "Cancel"),
            (QMessageBox.Yes, "Yes"),
            (QMessageBox.No, "No"),
        ):
            button = dialog.button(standard_button)
            if button is not None:
                button.setText(self._lt(label))
        return dialog.exec()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        intro = QLabel(
            self._lt("Prepare a removable USB stick as FAT32 for Yamaha Disklavier and PianoForce workflows.")
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.warning_label = QLabel(
            self._lt(
                "Formatting erases the entire selected removable device and cannot be undone. "
                "Confirm the device, capacity, and current contents before continuing."
            )
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(
            "QLabel { padding: 8px 10px; border: 1px solid #C57A00; background: #FFF4D6; color: #3D2A00; }"
        )
        layout.addWidget(self.warning_label)

        device_group = QGroupBox(self._lt("Device"))
        device_layout = QGridLayout(device_group)
        device_layout.setColumnStretch(1, 1)
        self.device_combo = QComboBox(device_group)
        self.refresh_button = QPushButton(self._lt("Refresh"), device_group)
        device_layout.addWidget(QLabel(self._lt("USB stick:")), 0, 0)
        device_layout.addWidget(self.device_combo, 0, 1)
        device_layout.addWidget(self.refresh_button, 0, 2)
        self.device_detail_label = QLabel(device_group)
        self.device_detail_label.setWordWrap(True)
        device_layout.addWidget(self.device_detail_label, 1, 1, 1, 2)
        layout.addWidget(device_group)

        format_group = QGroupBox(self._lt("Format"))
        format_layout = QGridLayout(format_group)
        format_layout.setColumnStretch(1, 1)
        self.mode_group = QButtonGroup(format_group)
        self.superfloppy_radio = QRadioButton(self._lt("Superfloppy FAT32 (no partitions)"), format_group)
        self.superfloppy_radio.setChecked(True)
        self.mode_group.addButton(self.superfloppy_radio)
        self.mbr_radio = QRadioButton(self._lt("MBR with one FAT32 partition"), format_group)
        self.mode_group.addButton(self.mbr_radio)
        self.superfloppy_hint = QLabel(
            self._lt(
                "Best for devices that need FAT32 with no partition table: Nalbantov and Gotek-style USB floppy emulators, plus older Yamaha/Disklavier/keyboard readers."
            ),
            format_group,
        )
        self.mbr_hint = QLabel(
            self._lt(
                "Best for devices that expect a normal partitioned USB stick: most Yamaha Clavinova/CVP/CLP instruments, QRS/PNOmation and PianoForce players, and newer computers/keyboards."
            ),
            format_group,
        )
        self.superfloppy_hint.setWordWrap(True)
        self.mbr_hint.setWordWrap(True)
        format_layout.addWidget(self.superfloppy_radio, 0, 0, 1, 2)
        format_layout.addWidget(self.superfloppy_hint, 1, 1)
        format_layout.addWidget(self.mbr_radio, 2, 0, 1, 2)
        format_layout.addWidget(self.mbr_hint, 3, 1)
        self.label_edit = QLineEdit(format_group)
        self.label_edit.setMaxLength(11)
        self.label_edit.setText("DISKLAV")
        self.label_edit.setToolTip(self._lt("FAT32 volume label. FAT labels are limited to 11 characters."))
        format_layout.addWidget(QLabel(self._lt("Volume label:")), 4, 0)
        format_layout.addWidget(self.label_edit, 4, 1)
        layout.addWidget(format_group)

        preview_group = QGroupBox(self._lt("Current Contents"))
        preview_layout = QHBoxLayout(preview_group)
        self.usage_chart = UsbUsagePieChart(preview_group)
        preview_layout.addWidget(self.usage_chart)
        self.contents_tree = QTreeWidget(preview_group)
        self.contents_tree.setHeaderLabels([self._lt("Item"), self._lt("Size"), self._lt("Details")])
        self.contents_tree.setRootIsDecorated(True)
        self.contents_tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.contents_tree.setMinimumHeight(210)
        preview_layout.addWidget(self.contents_tree, stretch=1)
        layout.addWidget(preview_group, stretch=1)

        self.confirm_checkbox = QCheckBox(self._lt("I understand this will erase the selected USB stick."), self)
        layout.addWidget(self.confirm_checkbox)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.format_button = QPushButton(self._lt("Format USB Stick"), self.buttons)
        self.buttons.addButton(self.format_button, QDialogButtonBox.DestructiveRole)
        self._translate_dialog_button_box()
        layout.addWidget(self.buttons)

    def _connect_ui(self):
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.device_combo.currentIndexChanged.connect(self._refresh_selected_device)
        self.confirm_checkbox.toggled.connect(self._refresh_action_state)
        self.superfloppy_radio.toggled.connect(self._refresh_mode_text)
        self.mbr_radio.toggled.connect(self._refresh_mode_text)
        self.format_button.clicked.connect(self._start_format)
        self.buttons.rejected.connect(self._close_or_cancel)

    def refresh_devices(self):
        if self._is_running:
            return
        previous_path = ""
        current = self._selected_device()
        if current is not None:
            previous_path = current.device_path
        self.status_label.setText(self._lt("Scanning removable USB devices..."))
        QApplication.processEvents()
        self.devices = list_removable_usb_drives()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        selected_index = 0
        if self.devices:
            for index, device in enumerate(self.devices):
                self.device_combo.addItem(device.display_name, device)
                if device.device_path == previous_path:
                    selected_index = index
            self.device_combo.setCurrentIndex(selected_index)
            self.device_combo.setEnabled(True)
        else:
            self.device_combo.addItem(self._lt("No removable USB sticks detected"), None)
            self.device_combo.setEnabled(False)
        self.device_combo.blockSignals(False)
        self.status_label.setText("")
        self._refresh_selected_device()

    def _selected_device(self):
        data = self.device_combo.currentData()
        return data if isinstance(data, UsbDriveInfo) else None

    def _selected_layout_kind(self):
        return FAT32_LAYOUT_MBR if self.mbr_radio.isChecked() else FAT32_LAYOUT_SUPERFLOPPY

    def _refresh_mode_text(self):
        current_label = self.label_edit.text().strip().upper()
        if self.superfloppy_radio.isChecked() and current_label in {"", "PIANOFORCE"}:
            self.label_edit.setText("DISKLAV")
        elif self.mbr_radio.isChecked() and current_label in {"", "DISKLAV"}:
            self.label_edit.setText("PIANOFORCE")
        self._refresh_action_state()

    def _refresh_selected_device(self):
        device = self._selected_device()
        self.contents_tree.clear()
        if device is None:
            self.device_detail_label.setText(
                self._lt("Connect a USB stick, then refresh. Only storage devices reported by the operating system as removable are listed.")
            )
            self.usage_chart.set_slices([])
            self._add_tree_message(self._lt("No removable USB stick is selected."))
            self._refresh_action_state()
            return

        mount_text = ", ".join(device.mountpoints) if device.mountpoints else self._lt("not mounted")
        detail = f"{device.display_name}\n{self._lt('Current mount points:')} {mount_text}"
        if device.read_only:
            detail += f"\n{self._lt('This device is read-only and cannot be formatted.')}"
        self.device_detail_label.setText(detail)
        self._populate_contents_tree(device)
        self._populate_usage_chart(device)
        self._refresh_action_state()

    def _populate_usage_chart(self, device):
        volume_size = sum(max(0, volume.size_bytes) for volume in device.volumes)
        used = sum(max(0, volume.used_bytes) for volume in device.volumes)
        free = sum(max(0, volume.free_bytes) for volume in device.volumes)
        unallocated = max(0, int(device.size_bytes or 0) - volume_size)
        unknown = 0
        if volume_size and used <= 0 and free <= 0:
            unknown = volume_size
        elif volume_size > used + free:
            unknown = volume_size - used - free
        if not device.volumes:
            unknown = int(device.size_bytes or 0)

        self.usage_chart.set_slices(
            [
                (self._lt("Used"), used, "#D45B5B"),
                (self._lt("Free"), free, "#4C9F70"),
                (self._lt("Unallocated"), unallocated, "#6D8CC7"),
                (self._lt("Unknown"), unknown, "#9AA1A9"),
            ]
        )

    def _populate_contents_tree(self, device):
        if not device.volumes:
            self._add_tree_message(self._lt("No readable volumes or partitions were detected."))
            return
        for volume in device.volumes:
            details = []
            if volume.file_system:
                details.append(volume.file_system)
            if volume.label:
                details.append(f"{self._lt('Label:')} {volume.label}")
            if volume.mountpoints:
                details.append(self._lt("Mounted:") + " " + ", ".join(volume.mountpoints))
            volume_item = QTreeWidgetItem(
                [
                    volume.display_name,
                    display_bytes(volume.size_bytes),
                    " - ".join(details) if details else self._lt("No mounted file-system details"),
                ]
            )
            self.contents_tree.addTopLevelItem(volume_item)
            if volume.contents:
                for entry in volume.contents:
                    child = QTreeWidgetItem(
                        [
                            entry.name,
                            "" if entry.kind == "folder" else display_bytes(entry.size_bytes),
                            entry.kind,
                        ]
                    )
                    volume_item.addChild(child)
            else:
                volume_item.addChild(QTreeWidgetItem([self._lt("No top-level files could be shown"), "", self._lt("Unmounted or unreadable")]))
            volume_item.setExpanded(True)
        for column in range(self.contents_tree.columnCount()):
            self.contents_tree.resizeColumnToContents(column)

    def _add_tree_message(self, message):
        self.contents_tree.addTopLevelItem(QTreeWidgetItem([message, "", ""]))

    def _refresh_action_state(self):
        device = self._selected_device()
        enabled = (
            not self._is_running
            and device is not None
            and not device.read_only
            and self.confirm_checkbox.isChecked()
        )
        self.format_button.setEnabled(enabled)
        self.refresh_button.setEnabled(not self._is_running)
        self.device_combo.setEnabled(not self._is_running and bool(self.devices))
        self.superfloppy_radio.setEnabled(not self._is_running)
        self.mbr_radio.setEnabled(not self._is_running)
        self.label_edit.setEnabled(not self._is_running)
        self.confirm_checkbox.setEnabled(not self._is_running)

    def _start_format(self):
        device = self._selected_device()
        if device is None:
            return
        layout_kind = self._selected_layout_kind()
        layout_label = FAT32_LAYOUT_LABELS[layout_kind]
        target_family = self.superfloppy_hint.text() if layout_kind == FAT32_LAYOUT_SUPERFLOPPY else self.mbr_hint.text()
        message = (
            f"{self._lt('Erase and format this USB stick?')}\n\n"
            f"{self._lt('Device:')} {device.display_name}\n"
            f"{self._lt('Format:')} {layout_label}\n"
            f"{self._lt('Use:')} {target_family}\n\n"
            f"{self._lt('This cannot be undone. The existing partition table, files, and disk contents will be removed.')}"
        )
        confirmed = self._show_message(
            QMessageBox.Warning,
            "Confirm USB Format",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes
        if not confirmed:
            return

        self._is_running = True
        self.was_formatted = False
        self.format_result = None
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText(self._lt("Starting USB format..."))
        self.format_button.setEnabled(False)
        close_button = self.buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText(self._lt("Cancel"))
        self._refresh_action_state()

        self.worker = UsbFormatWorker(device, layout_kind, self.label_edit.text().strip(), self)
        self.worker.progressChanged.connect(self._on_progress_changed)
        self.worker.formatFinished.connect(self._on_format_finished)
        self.worker.formatFailed.connect(self._on_format_failed)
        self.worker.operationCancelled.connect(self._on_format_cancelled)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_progress_changed(self, step, total, message):
        if total and total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(max(0, min(int(step), int(total))))
        else:
            self.progress_bar.setRange(0, 0)
        self.status_label.setText(message)

    def _on_format_finished(self, result):
        self.was_formatted = True
        self.format_result = dict(result or {})
        self.status_label.setText(
            f"Formatted {self.format_result.get('device', 'the selected USB stick')} as "
            f"{self.format_result.get('layout', 'FAT32')}."
        )
        self.confirm_checkbox.setChecked(False)

    def _on_format_failed(self, message):
        self.status_label.setText(self._lt("USB format failed."))
        self._show_message(
            QMessageBox.Critical,
            "USB Format Failed",
            str(message or "The USB stick was not formatted."),
        )

    def _on_format_cancelled(self, _message):
        self.status_label.setText(self._lt("USB formatting cancelled."))
        self._show_message(
            QMessageBox.Warning,
            "USB Format Cancelled",
            "Formatting was cancelled. The USB stick may be partially formatted; format it again before using it.",
        )

    def _on_worker_finished(self):
        self._is_running = False
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        close_button = self.buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText(self._lt("Close"))
            close_button.setEnabled(True)
        self._refresh_action_state()
        if self.was_formatted:
            success_text = self.status_label.text()
            self.refresh_devices()
            self.status_label.setText(success_text)

    def _close_or_cancel(self):
        if self._is_running and self.worker is not None:
            self.status_label.setText(self._lt("Cancelling USB format..."))
            self.worker.cancel()
            self.buttons.button(QDialogButtonBox.Close).setEnabled(False)
            return
        self.reject()

    def closeEvent(self, event):
        if self._is_running and self.worker is not None:
            self._close_or_cancel()
            event.ignore()
            return
        super().closeEvent(event)
