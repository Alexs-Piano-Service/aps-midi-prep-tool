"""Reviewable destination selection; settings are written only after Apply."""

from html import escape

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHeaderView, QLabel, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .icon_utils import apply_window_icon
from .message_catalog import DEFAULT_LANGUAGE, normalize_language_code, translate_text
from .preparation_profiles import (
    COMPATIBILITY_SOURCE, MEDIA_BY_KEY, PIANO_PROFILES, PIANO_PROFILE_CATEGORIES,
    SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT,
    display_setting, get_preparation_medium, get_preparation_profile, proposed_settings,
)


class _ProfilePopupDelegate(QStyledItemDelegate):
    """Indent popup choices without changing searchable or selected text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._inset_size = QSize(12, 1)
        spacer = QPixmap(self._inset_size)
        spacer.fill(Qt.GlobalColor.transparent)
        self._spacer = QIcon(spacer)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(Qt.ItemDataRole.UserRole) is not None:
            # Let the native style reserve the leading space. It also handles
            # RTL, elision, size hints, and selection across the complete row.
            option.icon = self._spacer
            option.decorationSize = self._inset_size
            option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration


class PreparationProfileDialog(QDialog):
    def __init__(self, settings, profile_key="unsure", medium_key="", parent=None, *, song_counts=None):
        super().__init__(parent)
        apply_window_icon(self)
        self.settings = settings
        self.language_code = normalize_language_code(
            parent._language_code() if parent is not None and hasattr(parent, "_language_code")
            else settings.value("language", DEFAULT_LANGUAGE)
        )
        self.song_counts = dict(song_counts or {})
        self.setWindowTitle(self.t("Preparing for..."))
        self.resize(660, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("preparationProfileCombo")
        self.profile_combo.view().setItemDelegate(_ProfilePopupDelegate(self.profile_combo.view()))
        for category, label in PIANO_PROFILE_CATEGORIES:
            profiles = [profile for profile in PIANO_PROFILES if profile.category == category]
            if not profiles:
                continue
            self.profile_combo.addItem(self.t(label))
            heading = self.profile_combo.model().item(self.profile_combo.count() - 1)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            for profile in profiles:
                self.profile_combo.addItem(self.t(profile.label), profile.key)
        selected_index = self.profile_combo.findData(str(profile_key or ""))
        if selected_index < 0:
            selected_index = self.profile_combo.findData("unsure")
        self.profile_combo.setCurrentIndex(selected_index)
        form.addRow(self.t("Piano / controller:"), self.profile_combo)
        self.medium_combo = QComboBox()
        self.medium_combo.setObjectName("preparationMediumCombo")
        form.addRow(self.t("Drive / delivery:"), self.medium_combo)
        layout.addLayout(form)
        self.changes_table = QTableWidget(0, 3)
        self.changes_table.setObjectName("preparationChangesTable")
        self.changes_table.setHorizontalHeaderLabels([self.t(text) for text in ("Setting", "Current", "Proposed")])
        self.changes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.changes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.changes_table.verticalHeader().hide()
        layout.addWidget(self.changes_table, 1)
        self.manual_label = QLabel(self.t("Keep current settings"))
        layout.addWidget(self.manual_label)
        self.preparation_note_label = QLabel()
        self.preparation_note_label.setObjectName("preparationNoteLabel")
        self.preparation_note_label.setWordWrap(True)
        self.preparation_note_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.preparation_note_label)
        self.source_label = QLabel()
        self.source_label.setObjectName("preparationSourcesLabel")
        self.source_label.setWordWrap(True)
        self.source_label.setOpenExternalLinks(True)
        source_font = self.source_label.font()
        source_font.setPointSizeF(max(8, source_font.pointSizeF() - 1))
        self.source_label.setFont(source_font)
        layout.addWidget(self.source_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Apply).setText(self.t("Apply and Prepare"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.t("Cancel"))
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.profile_combo.currentIndexChanged.connect(self._refresh_media)
        self.medium_combo.currentIndexChanged.connect(self._refresh_preview)
        self._refresh_media(preferred_key=medium_key)

    def t(self, source_text, **kwargs):
        return translate_text(source_text, self.language_code, **kwargs)

    def selection(self):
        profile = get_preparation_profile(self.profile_combo.currentData())
        return profile, get_preparation_medium(profile, self.medium_combo.currentData())

    def _refresh_media(self, _index=None, *, preferred_key=""):
        profile = get_preparation_profile(self.profile_combo.currentData())
        medium = get_preparation_medium(profile, preferred_key or self.medium_combo.currentData())
        self.medium_combo.blockSignals(True)
        self.medium_combo.clear()
        for key in profile.media:
            self.medium_combo.addItem(self.t(MEDIA_BY_KEY[key].label), key)
        self.medium_combo.setCurrentIndex(max(0, self.medium_combo.findData(medium.key)))
        self.medium_combo.blockSignals(False)
        self._refresh_preview()

    def _refresh_preview(self, _index=None):
        profile, medium = self.selection()
        changes = proposed_settings(profile, medium)
        rows = []
        if profile.song_format:
            rows.append(("Song format", self._current_value("emulator_image_content"), display_setting(profile.song_format)))
            if profile.song_format == "midi" and profile.midi_types == (0,):
                rows.append(("MIDI type", "Current default", "MIDI Type 0"))
            filenames = "DOS 8.3" if changes["use_dos83_filenames"] else "Descriptive filenames"
            invalid_count = sum(self.song_counts.get("dos83_" + kind, 0) for kind in ("midi", "eseq"))
            if changes["use_dos83_filenames"] and invalid_count:
                filenames = self.t("DOS 8.3 · {count} to rename", count=invalid_count)
            rows.append(("Filenames", self._current_filenames(), filenames))
            if SETTING_DISK_FORMAT in changes:
                rows.append(("Disk size", self._current_value("emulator_image_disk_format", SETTING_DISK_FORMAT), display_setting(changes[SETTING_DISK_FORMAT])))
        if SETTING_IMAGE_FORMAT in changes:
            rows.append(("Image type", self._current_value("emulator_image_output_format", SETTING_IMAGE_FORMAT), display_setting(changes[SETTING_IMAGE_FORMAT])))
        if profile.song_format:
            source_kind = "midi" if profile.song_format == "eseq" else "eseq"
            count = self.song_counts.get(source_kind, 0)
            if count:
                rows.append(("Convert songs", str(count), self.t(
                    "{source} → {target} (staged)",
                    source="MIDI" if source_kind == "midi" else "E-SEQ",
                    target="MIDI" if profile.song_format == "midi" else "E-SEQ",
                )))
            if profile.song_format == "midi" and profile.midi_types == (0,) and self.song_counts.get("midi_non_type0", 0):
                rows.append(("Convert songs", str(self.song_counts["midi_non_type0"]), self.t(
                    "{source} → {target} (staged)", source="MIDI", target="SMF0",
                )))
            if profile.song_format == "eseq" and self.song_counts.get("clavinova", 0):
                rows.append((
                    "Convert songs", str(self.song_counts["clavinova"]),
                    self.t("{source} → {target} (staged)", source="Clavinova MDA", target="Disklavier E-SEQ"),
                ))
        if not profile.song_format:
            rows.append(("Filenames", self._current_filenames(), "Descriptive filenames"))
        rows.append((
            "Format for Disklavier screen", self._current_value("format_disklavier_screen"),
            display_setting(changes["format_disklavier_screen"]),
        ))
        self.changes_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                item = QTableWidgetItem(self.t(text))
                item.setToolTip(item.text())
                self.changes_table.setItem(row, column, item)
        self.changes_table.resizeRowsToContents()
        self.changes_table.setVisible(bool(rows))
        self.manual_label.setVisible(not rows)
        note = self.t(profile.preparation_note) if profile.preparation_note else ""
        self.preparation_note_label.setText(note)
        self.preparation_note_label.setVisible(bool(note))
        source_url, source_label = profile.source_url, profile.source_label
        if not source_url and profile.category == "general":
            source_url, source_label = COMPATIBILITY_SOURCE, "APS Disklavier Compatibility Table"
        sources = [self._source_link(source_url, source_label)] if source_url else []
        if medium.source_url:
            sources.append(self._source_link(medium.source_url, "Emulator documentation"))
        self.source_label.setText(" · ".join(sources))
        self.source_label.setVisible(bool(sources))

    def _source_link(self, url, label):
        return f'<a href="{escape(url, quote=True)}">{escape(self.t(label), quote=True)}</a>'

    def _current_value(self, *keys):
        values = {str(self.settings.value(key, "")) for key in keys} - {""}
        if len(values) == 1:
            value = next(iter(values))
            if keys == ("format_disklavier_screen",):
                return display_setting(value.lower() == "true")
            return display_setting(value)
        return "Mixed" if values else "Current default"

    def _current_filenames(self):
        values = set()
        for key in ("use_dos83_filenames", "long_midi_filenames", "eseq_to_midi_long_filenames", "read_floppy_long_filenames"):
            if self.settings.value(key, "") != "":
                value = self.settings.value(key, False, type=bool)
                values.add(value if key == "use_dos83_filenames" else not value)
        if len(values) == 1:
            return "DOS 8.3" if next(iter(values)) else "Descriptive filenames"
        return "Mixed" if values else "Current default"
