"""Native Qt structured and raw views of Yamaha disk management catalogs."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .catalog_inspection import catalog_hex, catalog_text, read_catalog, song_fields
from .message_catalog import translate_text


class CatalogInspectionWidget(QWidget):
    applyRequested = Signal()

    def __init__(self, parent=None, *, language="en"):
        super().__init__(parent)
        self.language = language
        self.t = lambda text: translate_text(text, self.language)
        self.inspection = None
        self._raw_populated = False
        self._apply_enabled = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        self.heading = QLabel(self.t("Select a file to inspect."))
        font = self.heading.font()
        font.setPointSizeF(font.pointSizeF() + 3)
        font.setBold(True)
        self.heading.setFont(font)
        self.heading.setTextFormat(Qt.PlainText)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.description = QLabel(self.t("Open a PDISK.MNG or PSONG.MNG catalog to view its contents."))
        self.description.setTextFormat(Qt.PlainText)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        overview = QWidget()
        details = QVBoxLayout(overview)
        details.setContentsMargins(8, 8, 8, 8)
        self.fields = self._field_tree()
        details.addWidget(self.fields)
        self.songs = QTableWidget(0, 4)
        self.songs.setHorizontalHeaderLabels([
            self.t("Catalog order"), self.t("Title"), self.t("Filename"),
            self.t("Playback descriptor"),
        ])
        self.songs.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.songs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.songs.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.songs.setAlternatingRowColors(True)
        self.songs.setWordWrap(False)
        self.songs.verticalHeader().hide()
        self.songs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in (1, 2, 3):
            self.songs.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        self.songs.setMinimumHeight(120)
        self.songs.itemSelectionChanged.connect(self._select_song)
        details.addWidget(self.songs, 2)
        self.song_group = QGroupBox(self.t("Selected song"))
        song_layout = QVBoxLayout(self.song_group)
        self.song_details = self._field_tree()
        song_layout.addWidget(self.song_details)
        details.addWidget(self.song_group, 1)
        self.offset_note = QLabel(self.t("Offsets use the CRLF catalog layout."))
        self.offset_note.setWordWrap(True)
        details.addWidget(self.offset_note)
        details.addStretch(0)
        self.tabs.addTab(overview, self.t("Overview"))
        self.raw_text = self._raw_box()
        self.hex_text = self._raw_box()
        self.tabs.addTab(self.raw_text, self.t("Raw text"))
        self.tabs.addTab(self.hex_text, self.t("Hexadecimal"))
        self.tabs.currentChanged.connect(self._show_raw)
        self.tabs.hide()
        self.apply_row = QWidget(self)
        apply_layout = QHBoxLayout(self.apply_row)
        apply_layout.setContentsMargins(0, 0, 0, 0)
        self.apply_status = QLabel(self.apply_row)
        self.apply_status.setTextFormat(Qt.PlainText)
        self.apply_status.setWordWrap(True)
        self.apply_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        apply_layout.addWidget(self.apply_status, 1)
        self.apply_button = QPushButton(self.t("Apply album title"), self.apply_row)
        self.apply_button.clicked.connect(self.applyRequested)
        apply_layout.addWidget(self.apply_button, 0, Qt.AlignBottom)
        layout.addWidget(self.apply_row)
        self._refresh_apply_button()

    def set_apply_enabled(self, enabled):
        self._apply_enabled = bool(enabled)
        self._refresh_apply_button()

    def set_apply_status(self, message):
        self.apply_status.setText(str(message or ""))

    def _refresh_apply_button(self):
        inspected = self.inspection
        self.apply_row.setVisible(inspected is not None)
        is_disk = inspected is not None and inspected.kind == "PDISK.MNG"
        self.apply_button.setText(self.t("Apply album title" if is_disk else "Apply song titles"))
        has_metadata = inspected is not None and bool(inspected.title if is_disk else inspected.songs)
        self.apply_button.setEnabled(self._apply_enabled and has_metadata)

    def _raw_box(self):
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setLineWrapMode(QPlainTextEdit.NoWrap)
        box.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        return box

    def _field_tree(self):
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels([self.t("Field"), self.t("Value"), self.t("Offset")])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        return tree

    def _set_fields(self, tree, fields):
        tree.clear()
        for field in fields:
            value = field.value or "—"
            offset = f"0x{field.offset:04X} · {field.size} B"
            item = QTreeWidgetItem([self.t(field.label), value, offset])
            item.setToolTip(1, value)
            tree.addTopLevelItem(item)
        row_height = tree.sizeHintForRow(0) if fields else tree.fontMetrics().height() + 4
        height = tree.header().sizeHint().height() + len(fields) * row_height + 6
        if tree is self.fields:
            tree.setFixedHeight(height)
        else:
            tree.setMinimumHeight(height)

    def clear(self):
        self.inspection = None
        self._raw_populated = False
        self.heading.setText(self.t("Select a file to inspect."))
        self.description.setText(self.t("Open a PDISK.MNG or PSONG.MNG catalog to view its contents."))
        self.description.setToolTip("")
        self.fields.clear()
        self.songs.setRowCount(0)
        self.song_details.clear()
        self.raw_text.clear()
        self.hex_text.clear()
        self.tabs.hide()
        self.set_apply_status("")
        self._refresh_apply_button()

    def load_path(self, path, *, display_name=""):
        self.inspection = None
        self.set_apply_status("")
        self._refresh_apply_button()
        self._raw_populated = False
        self.raw_text.clear()
        self.hex_text.clear()
        self.songs.setRowCount(0)
        self.song_details.clear()
        self.heading.setText(display_name or Path(path).name)
        self.description.setToolTip(str(path))
        try:
            inspected = read_catalog(path)
        except (OSError, ValueError) as exc:
            self.description.setText(self.t("Could not inspect {filename}.").format(
                filename=display_name or Path(path).name) + "\n" + self.t(str(exc)))
            self.tabs.hide()
            return
        self.inspection = inspected
        if inspected.title:
            self.heading.setText(inspected.title)
        self.description.setText(self.t("{name} · {size} bytes · Read-only").format(
            name=inspected.kind, size=len(inspected.raw)))
        self._set_fields(self.fields, inspected.fields)
        self.songs.setVisible(inspected.kind == "PSONG.MNG")
        self.song_group.setVisible(bool(inspected.songs))
        self.offset_note.setVisible(inspected.normalized_line_endings)
        self.songs.setRowCount(len(inspected.songs))
        for row, song in enumerate(inspected.songs):
            values = (str(song.track_number), song.title, song.filename,
                      catalog_text(song.raw_record[0x50:0x5e]))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.songs.setItem(row, column, item)
        if inspected.songs:
            self.songs.selectRow(0)
        self.tabs.setCurrentIndex(0)
        self.tabs.show()
        self.set_apply_status(self.t("Use Save to write applied changes, or Undo to revert."))
        self._refresh_apply_button()

    def _select_song(self):
        if self.inspection is None:
            return
        row = self.songs.currentRow()
        if 0 <= row < len(self.inspection.songs):
            self._set_fields(self.song_details, song_fields(self.inspection.songs[row]))

    def _show_raw(self, index):
        if index < 1 or self.inspection is None or self._raw_populated:
            return
        # Text is intentionally a readable view; Hexadecimal preserves every
        # original byte and offset, including LF-only copies and padding.
        data = self.inspection.raw
        self.raw_text.setPlainText(data.decode("cp1252", errors="replace"))
        self.hex_text.setPlainText(catalog_hex(data))
        self._raw_populated = True
