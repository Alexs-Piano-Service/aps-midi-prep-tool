"""Review an actual packed disk set, then rebuild after collection edits."""

import os
import re
from string import Formatter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .icon_utils import apply_window_icon
from .message_catalog import translate_text
from .conversion_review import localize_music_error


_EMULATOR_DIAGNOSTICS = (
    "Could not prepare '{name}' as {format}: {error}",
    "Prepared song could not be musically verified: {name}: {error}",
    "Source contains [BAD SECTOR] recovery filler. MIDI bytes were preserved unchanged; missing data was not repaired and playback may fail.",
    "Could not update the embedded title: {error} MIDI bytes were preserved unchanged; the title is stored in PSONG.MNG. Playback may fail.",
    "Musical parsing unavailable; source bytes were preserved. Playback remains unverified: {error}",
    "The PSONG.MNG title is limited to 32 bytes.",
    "The source contains [BAD SECTOR] recovery filler. Its title cannot be updated without a PSONG.MNG catalog.",
    "The preserved MIDI contains unreadable data and cannot be prepared as MIDI Type 0.",
    "Preparation did not produce MIDI Type 0.",
    "Preparation did not produce a valid {format} file for '{name}'.",
)


def localize_emulator_warning(warning, language_code=None):
    """Translate known preparation diagnostics, preserving song names and path prefixes."""
    text = str(warning or "")
    # The builder joins these separate diagnostics after the first warning.
    # Split only at its canonical boundaries, never at arbitrary punctuation in a path.
    for separator in (
        " Musical parsing unavailable; source bytes were preserved. Playback remains unverified: ",
        " The PSONG.MNG title is limited to 32 bytes.",
    ):
        before, found, after = text.partition(separator)
        if found:
            return (localize_emulator_warning(before, language_code) + " "
                    + localize_emulator_warning(found.lstrip() + after, language_code))
    for template in _EMULATOR_DIAGNOSTICS:
        fields = []
        pattern = ""
        for literal, field, _format, _conversion in Formatter().parse(template):
            pattern += re.escape(literal)
            if field:
                fields.append(field)
                pattern += "(.+?)"
        match = re.fullmatch(r"(.*?: )?" + pattern, text, re.DOTALL)
        if match:
            values = dict(zip(fields, match.groups()[1:]))
            if "error" in values:
                values["error"] = localize_emulator_warning(values["error"], language_code)
            return (match[1] or "") + translate_text(template, language_code, **values)
    return localize_music_error(text, language_code)


class EmulatorPreviewDialog(QDialog):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        apply_window_icon(self)
        self.language_code = parent._language_code() if parent is not None and hasattr(parent, "_language_code") else "en"
        self.preview = preview
        self.decision = None
        self.dirty = False
        self.setWindowTitle(self.t("Review emulator disk set"))
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        count = sum(len(disk.songs) for disk in preview.disks)
        summary = QLabel(self.t("{disks} disk(s), {songs} song(s). Output: {path}", disks=len(preview.disks), songs=count, path=preview.output_directory))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        note = QLabel(self.t("Review the packed output below. Exclude albums, edit titles, or change album order, then choose Update Preview before building."))
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        album_page = QWidget()
        album_layout = QVBoxLayout(album_page)
        self.albums_table = QTableWidget(len(preview.albums), 5)
        self.albums_table.setObjectName("emulatorPreviewAlbums")
        self.albums_table.setHorizontalHeaderLabels([self.t(text) for text in ("Include", "Album title", "Songs", "Source folder", "Title source")])
        self.albums_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.albums_table.setSelectionMode(QAbstractItemView.SingleSelection)
        for row, album in enumerate(preview.albums):
            included = QTableWidgetItem()
            included.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            included.setCheckState(Qt.Checked if album.included else Qt.Unchecked)
            included.setData(Qt.UserRole, album.source_directory)
            self.albums_table.setItem(row, 0, included)
            title = QTableWidgetItem(album.title)
            title.setData(Qt.UserRole, album.title)
            self.albums_table.setItem(row, 1, title)
            for column, value in enumerate((str(album.song_count), os.path.relpath(album.source_directory, preview.source_directory), self.t(album.title_source)), start=2):
                self.albums_table.setItem(row, column, self._readonly(value))
        self.albums_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.albums_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        album_layout.addWidget(self.albums_table)
        reorder = QHBoxLayout()
        self.up_button = QPushButton(self.t("Move album up"))
        self.down_button = QPushButton(self.t("Move album down"))
        self.up_button.clicked.connect(lambda: self._move_album(-1))
        self.down_button.clicked.connect(lambda: self._move_album(1))
        reorder.addWidget(self.up_button)
        reorder.addWidget(self.down_button)
        reorder.addStretch()
        album_layout.addLayout(reorder)
        tabs.addTab(album_page, self.t("Albums"))

        self.disks_table = QTableWidget(len(preview.disks), 4)
        self.disks_table.setObjectName("emulatorPreviewDisks")
        self.disks_table.setHorizontalHeaderLabels([self.t(text) for text in ("Output image", "Songs", "Free space", "Albums")])
        self.songs_table = QTableWidget(count, 6)
        self.songs_table.setObjectName("emulatorPreviewSongs")
        self.songs_table.setHorizontalHeaderLabels([self.t(text) for text in ("Disk", "Source song", "Image filename", "Song title", "Title source", "Musical changes")])
        self.song_reports = []
        song_row = 0
        for row, disk in enumerate(preview.disks):
            albums = list(dict.fromkeys(song.album_title or os.path.basename(os.path.dirname(song.source_path)) for song in disk.songs))
            for column, value in enumerate((os.path.basename(disk.output_path), str(len(disk.songs)), self.t("{count} bytes", count=f"{disk.free_bytes:,}"), "; ".join(albums))):
                self.disks_table.setItem(row, column, self._readonly(value))
            for song in disk.songs:
                report = song.conversion_report
                changes = []
                if report is not None:
                    if report.notes_changed:
                        changes.append("Notes / timing")
                    if report.pedals_changed:
                        changes.append("Pedals")
                    if report.channel_events_changed:
                        changes.append("Channel messages")
                    if report.removed_metadata:
                        changes.append("Metadata")
                summary = ", ".join(self.t(change) for change in changes) or self.t("Musical events preserved" if report else "Unverified")
                report_detail = report.to_text(self.language_code) if report else ""
                warning_detail = localize_emulator_warning(song.warning, self.language_code)
                self.song_reports.append("\n".join(part for part in (report_detail, warning_detail) if part))
                values = (os.path.basename(disk.output_path), os.path.relpath(song.source_path, preview.source_directory), song.image_path, song.title, self.t(song.title_source), summary)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value) if column == 3 else self._readonly(value)
                    if column == 3:
                        item.setData(Qt.UserRole, song.source_path)
                        item.setData(Qt.UserRole + 1, song.title)
                    self.songs_table.setItem(song_row, column, item)
                song_row += 1
        for table in (self.disks_table, self.songs_table):
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().hide()
        tabs.addTab(self.disks_table, self.t("Packed disks"))
        songs_page = QWidget()
        songs_layout = QVBoxLayout(songs_page)
        songs_layout.addWidget(self.songs_table, 2)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setPlaceholderText(self.t("Select a song to inspect its before-and-after musical report."))
        songs_layout.addWidget(self.report_text, 1)
        self.songs_table.currentCellChanged.connect(
            lambda row, *_args: self.report_text.setPlainText(self.song_reports[row] if 0 <= row < len(self.song_reports) else "")
        )
        tabs.addTab(songs_page, self.t("Songs and title sources"))
        warnings = QLabel("\n".join(localize_emulator_warning(warning, self.language_code)
                                  for warning in preview.warnings) if preview.warnings else self.t("No preparation warnings."))
        warnings.setTextFormat(Qt.PlainText)
        warnings.setWordWrap(True)
        warnings.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(warnings)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.t("Cancel"))
        self.update_button = self.buttons.addButton(self.t("Update Preview"), QDialogButtonBox.ActionRole)
        self.build_button = self.buttons.addButton(self.t("Build Reviewed Output"), QDialogButtonBox.AcceptRole)
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self._revise)
        self.build_button.clicked.connect(self._build)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.albums_table.itemChanged.connect(self._mark_dirty)
        self.songs_table.itemChanged.connect(self._mark_dirty)

    def t(self, source_text, **kwargs):
        return translate_text(source_text, self.language_code, **kwargs)

    @staticmethod
    def _readonly(value):
        item = QTableWidgetItem(str(value))
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return item

    def _mark_dirty(self, _item=None):
        self.dirty = True
        self.build_button.setEnabled(False)
        self.update_button.setEnabled(True)

    def _move_album(self, direction):
        current = self.albums_table.currentRow()
        target = current + direction
        if current < 0 or target < 0 or target >= self.albums_table.rowCount():
            return
        self.albums_table.blockSignals(True)
        for column in range(self.albums_table.columnCount()):
            first = self.albums_table.takeItem(current, column)
            second = self.albums_table.takeItem(target, column)
            self.albums_table.setItem(target, column, first)
            self.albums_table.setItem(current, column, second)
        self.albums_table.blockSignals(False)
        self.albums_table.setCurrentCell(target, 1)
        self._mark_dirty()

    def _revise(self):
        selected = []
        album_titles = dict(self.preview.album_titles)
        titles = dict(self.preview.title_overrides)
        for row in range(self.albums_table.rowCount()):
            include = self.albums_table.item(row, 0)
            folder = include.data(Qt.UserRole)
            if include.checkState() == Qt.Checked:
                selected.append(folder)
            title = self.albums_table.item(row, 1)
            if title.text().strip() != title.data(Qt.UserRole):
                album_titles[folder] = title.text().strip()
        for row in range(self.songs_table.rowCount()):
            title = self.songs_table.item(row, 3)
            if title.text().strip() != title.data(Qt.UserRole + 1):
                titles[title.data(Qt.UserRole)] = title.text().strip()
        if not selected or any(not value or "\x00" in value for value in (*album_titles.values(), *titles.values())):
            QMessageBox.warning(self, self.t("Review emulator disk set"), self.t("Include at least one album and use nonempty titles without NUL characters."))
            return
        self.decision = {"action": "revise", "included_folders": selected, "album_titles": album_titles, "title_overrides": titles}
        self.accept()

    def _build(self):
        if self.dirty:
            return
        self.decision = {"action": "build"}
        self.accept()
