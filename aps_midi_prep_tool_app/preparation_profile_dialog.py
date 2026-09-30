"""Reviewable destination selection; settings are written only after Apply."""

from html import escape

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHeaderView, QLabel, QStyledItemDelegate, QStyleOptionViewItem,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from .icon_utils import apply_window_icon
from .message_catalog import DEFAULT_LANGUAGE, normalize_language_code, translate_text
from .preparation_profiles import (
    COMPATIBILITY_SOURCE, MEDIA_BY_KEY, PIANO_PROFILES, PIANO_PROFILE_CATEGORIES,
    SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT, SETTING_PROFILE,
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
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        scale = max(1.0, self.fontMetrics().height() / 16)
        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("preparationProfileScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("preparationProfileCombo")
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(16)
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
        form.addRow(self.t("Preparing for:"), self.profile_combo)
        self.medium_combo = QComboBox()
        self.medium_combo.setObjectName("preparationMediumCombo")
        self.medium_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.medium_combo.setMinimumContentsLength(16)
        form.addRow(self.t("Delivery:"), self.medium_combo)
        content_layout.addLayout(form)
        self.outcome_label = QLabel()
        self.outcome_label.setObjectName("preparationOutcomeLabel")
        self.outcome_label.setWordWrap(True)
        self.outcome_label.setTextFormat(Qt.TextFormat.PlainText)
        self.outcome_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_layout.addWidget(self.outcome_label)
        self.attention_label = QLabel()
        self.attention_label.setObjectName("preparationAttentionLabel")
        self.attention_label.setWordWrap(True)
        self.attention_label.setTextFormat(Qt.TextFormat.PlainText)
        self.attention_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_layout.addWidget(self.attention_label)
        self.details_button = QToolButton()
        self.details_button.setObjectName("preparationDetailsButton")
        self.details_button.setText(self.t("Details"))
        self.details_button.setCheckable(True)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.setArrowType(Qt.ArrowType.RightArrow)
        content_layout.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignLeading)
        self.details_widget = QWidget()
        self.details_widget.setObjectName("preparationDetails")
        details_layout = QVBoxLayout(self.details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.changes_table = QTableWidget(0, 3)
        self.changes_table.setObjectName("preparationChangesTable")
        self.changes_table.setHorizontalHeaderLabels([self.t(text) for text in ("Setting", "Saved settings", "On apply")])
        self.changes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.changes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Stretch updates the column widths together. Reflow wrapped rows after
        # the header finishes, when its section sizes and viewport agree.
        self.changes_table.horizontalHeader().sectionResized.connect(
            self.changes_table.resizeRowsToContents, Qt.ConnectionType.QueuedConnection,
        )
        self.changes_table.verticalHeader().hide()
        self.changes_table.setMinimumHeight(round(180 * scale))
        details_layout.addWidget(self.changes_table, 1)
        self.manual_label = QLabel(self.t("Keep current settings"))
        self.manual_label.setWordWrap(True)
        self.manual_label.setTextFormat(Qt.TextFormat.PlainText)
        details_layout.addWidget(self.manual_label)
        self.preparation_note_label = QLabel()
        self.preparation_note_label.setObjectName("preparationNoteLabel")
        self.preparation_note_label.setWordWrap(True)
        self.preparation_note_label.setTextFormat(Qt.TextFormat.PlainText)
        self.preparation_note_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        details_layout.addWidget(self.preparation_note_label)
        self.source_label = QLabel()
        self.source_label.setObjectName("preparationSourcesLabel")
        self.source_label.setWordWrap(True)
        self.source_label.setOpenExternalLinks(True)
        source_font = self.source_label.font()
        source_font.setPointSizeF(max(8, source_font.pointSizeF() - 1))
        self.source_label.setFont(source_font)
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        details_layout.addWidget(self.source_label)
        content_layout.addWidget(self.details_widget)
        content_layout.addStretch(1)
        self.details_widget.hide()
        self.details_button.toggled.connect(self._toggle_details)
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Apply).setText(self.t("Apply"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.t("Cancel"))
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.profile_combo.currentIndexChanged.connect(self._refresh_media)
        self.medium_combo.currentIndexChanged.connect(self._refresh_preview)
        self._refresh_media(preferred_key=medium_key)
        self._resize_to_visible_content()

    def t(self, source_text, **kwargs):
        return translate_text(source_text, self.language_code, **kwargs)

    def selection(self):
        profile = get_preparation_profile(self.profile_combo.currentData())
        return profile, get_preparation_medium(profile, self.medium_combo.currentData())

    def _toggle_details(self, expanded):
        fit_contents = self.size() == self._content_size
        self.details_widget.setVisible(expanded)
        self.details_button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        if expanded:
            self.changes_table.resizeRowsToContents()
        if fit_contents:
            self._resize_to_visible_content()

    def _resize_to_visible_content(self):
        """Fit the initial summary and explicit Details toggles until resized."""
        self.ensurePolished()
        content_layout = self.scroll_area.widget().layout()
        content_layout.activate()
        layout = self.layout()
        margins = layout.contentsMargins()
        horizontal_margins = margins.left() + margins.right()
        available = self.screen().availableGeometry()
        width = min(
            max(560, content_layout.sizeHint().width() + horizontal_margins),
            max(1, available.width() - 40),
        )
        content_height = content_layout.heightForWidth(width - horizontal_margins)
        if content_height < 0:
            content_height = content_layout.sizeHint().height()
        height = (content_height + margins.top() + margins.bottom()
                  + layout.spacing() + self.buttons.sizeHint().height())
        max_height = max(1, available.height() - 60)
        if self.isVisible():
            # Expanding Details keeps the current position, so leave room for
            # the footer below it; overflowing details remain scrollable.
            max_height = min(max_height, max(1, available.bottom() - self.frameGeometry().top() - 40))
        self.resize(width, min(height, max_height))
        self._content_size = self.size()

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
        self._refresh_outcome(profile, medium, changes)
        if profile.key == "custom":
            self.changes_table.setRowCount(0)
            self.changes_table.hide()
            self.manual_label.setText(self.t(profile.caution))
            self.manual_label.show()
            self.preparation_note_label.clear()
            self.preparation_note_label.hide()
            self.source_label.clear()
            self.source_label.hide()
            return
        self.manual_label.setText(self.t("Keep current settings"))
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
        if medium.image_prefix is not None:
            rows.append(("Image prefix", self._current_value("emulator_image_prefix"), medium.image_prefix))
        if medium.starting_number is not None:
            rows.append(("Starting disk number", self._current_value("emulator_image_starting_number"), str(medium.starting_number)))
        if profile.song_format:
            source_kind = "midi" if profile.song_format == "eseq" else "eseq"
            count = self.song_counts.get(source_kind, 0)
            if count:
                rows.append(("Convert songs", "—", f"{count} · " + self.t(
                    "{source} → {target} (staged)",
                    source="MIDI" if source_kind == "midi" else "E-SEQ",
                    target="MIDI" if profile.song_format == "midi" else "E-SEQ",
                )))
            if profile.song_format == "midi" and profile.midi_types == (0,) and self.song_counts.get("midi_non_type0", 0):
                rows.append(("Convert songs", "—", f"{self.song_counts['midi_non_type0']} · " + self.t(
                    "{source} → {target} (staged)", source="MIDI", target="SMF0",
                )))
            if profile.song_format == "eseq" and self.song_counts.get("clavinova", 0):
                rows.append((
                    "Convert songs", "—",
                    f"{self.song_counts['clavinova']} · " + self.t("{source} → {target} (staged)", source="Clavinova MDA", target="Disklavier E-SEQ"),
                ))
        if not profile.song_format:
            rows.append(("Filenames", self._current_filenames(), "Descriptive filenames"))
        rows.append((
            "Format for Disklavier screen", self._current_value("format_disklavier_screen"),
            display_setting(changes["format_disklavier_screen"]),
        ))
        current_profile = get_preparation_profile(self.settings.value(SETTING_PROFILE, "unsure"))
        if profile.trim_title_spaces or current_profile.trim_title_spaces:
            rows.append((
                "Trim Title Spaces", display_setting(current_profile.trim_title_spaces),
                display_setting(profile.trim_title_spaces),
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

    def _refresh_outcome(self, profile, medium, changes):
        count = self.song_counts.get("total", self.song_counts.get("midi", 0) + self.song_counts.get("eseq", 0))
        songs = self.t("1 song") if count == 1 else self.t("{count} songs", count=count)
        if not count:
            songs = self.t("No songs loaded")
        if profile.key == "custom":
            self.outcome_label.setText(" · ".join((songs, self.t("Current work and settings kept"))))
            self.attention_label.clear()
            self.attention_label.hide()
            return
        if medium.key == "original":
            delivery = self.t("Floppy disks")
        elif medium.key in {"nalbantov", "flashfloppy_img", "flashfloppy_hfe", "emulator_custom"}:
            delivery = self.t("Disk images")
        else:
            delivery = self.t("MIDI files") if profile.song_format == "midi" else self.t("Prepared files")
        size = {"ibm.720": "720 KB", "ibm.1440": "1.44 MB"}.get(changes.get(SETTING_DISK_FORMAT))
        if size:
            if medium.key == "original":
                delivery = self.t("{size} floppy disks", size=size)
            else:
                delivery = self.t("{size} disk images", size=size)
        self.outcome_label.setText(" · ".join((songs, delivery, self.t("Originals unchanged"))))
        attention_count = self.song_counts.get("attention", 0)
        if (self.song_counts.get("attention_profile", profile.key) != profile.key
                or self.song_counts.get("attention_medium", medium.key) != medium.key):
            attention_count = 0
        attention = self.t("1 song needs attention") if attention_count == 1 else self.t(
            "{count} songs need attention", count=attention_count,
        )
        self.attention_label.setText(attention if attention_count else "")
        self.attention_label.setVisible(bool(attention_count))

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
