import gc
import os
import re
import shutil
import tempfile
import uuid

from PySide6.QtCore import Qt, QEvent, QSettings
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QGridLayout,
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
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QInputDialog,
    QComboBox,
    QSpinBox,
    QStackedWidget,
    QLayout,
)

from .midi_metadata import (
    extract_eseq_title_from_file,
    update_eseq_title_to_path,
    update_midi_title,
    update_midi_title_to_path,
    update_midi_title_to_destination,
    validate_legacy_title_input,
    extract_first_title_from_midi,
    extract_midi_type_label_from_midi,
    has_eseq_title_metadata,
    is_midi_file,
)
from .eseq_converter import (
    EseqConversionError,
    convert_eseq_file_to_midi_path,
    convert_midi_file_to_eseq_path,
    is_eseq_file,
)
from .dos83_renamer import rename_midi_files_dos83
from .midi_type0_converter import convert_midi_files_to_type0
from .ui_utils import (
    center_dialog_on_parent,
    embedded_logo_dt,
    embedded_logo_lt,
    is_dark_theme,
    pixmap_from_base64,
)
from .drop_table_widget import DropTableWidget
from .midi_scan_worker import MidiProcessingWorker
from .disk_session_worker import DiskSessionLoadWorker
from .icon_utils import apply_window_icon
from .onboarding_dialog import show_first_time_dialog
from .floppy_image import (
    DISK_FORMATS,
    PREFERRED_OUTPUT_EXTENSIONS,
    FloppyImageError,
    FloppyImageSession,
    GreaseweazleFloppySource,
    allocated_size,
    create_floppy_images_from_files,
    display_bytes,
    image_extension,
    list_greaseweazle_devices,
    list_floppy_drives,
    output_filters,
)
from .eseq_pianodir import (
    PIANODIR_FILENAME,
    PIANODIR_DISK_METADATA_SIZE,
    PIANODIR_MAX_TRACKS,
    PIANODIR_ROW_PATH,
    PIANODIR_TARGET_FILE_SIZE,
    PianodirMetadata,
    PianodirTrackEntry,
    build_eseq_order_key_from_path,
    build_pianodir_bytes,
    eseq_type_display_label,
    normalize_pianodir_catalog_number,
    normalize_eseq_order_key,
    read_eseq_order_key_from_file,
    read_eseq_arrangement_type_label_from_file,
    read_eseq_write_protect_from_file,
    is_eseq_filename,
    is_pianodir_path,
    pianodir_is_populated,
    read_pianodir_metadata_from_file,
    update_eseq_order_key,
    update_eseq_order_key_to_path,
)
from .app_info import (
    APP_NAME,
    APP_TITLE_WITH_VERSION,
    APP_WEBSITE,
    SETTINGS_APP as APP_SETTINGS_APP,
    SETTINGS_ORG as APP_SETTINGS_ORG,
)


class TitleOverflowDelegate(QStyledItemDelegate):
    RAW_TITLE_ROLE = Qt.UserRole + 1

    def __init__(self, limit, parent=None):
        super().__init__(parent)
        self.limit = limit
        self.warning_color = QColor("#F5B041")
        self.highlight_enabled = True

    def set_highlight_enabled(self, enabled):
        self.highlight_enabled = bool(enabled)

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole) or ""
        raw_text = index.data(self.RAW_TITLE_ROLE)
        measured_text = str(raw_text) if raw_text is not None else text
        if (
            not self.highlight_enabled
            or index.column() != 4
            or len(measured_text) <= self.limit
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


class VerticalUsageBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction = 0.0
        self.setFixedWidth(14)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_fraction(self, fraction):
        fraction = max(0.0, min(float(fraction or 0.0), 1.0))
        if abs(self._fraction - fraction) < 0.001:
            return
        self._fraction = fraction
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(2, 2, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        if is_dark_theme():
            border = QColor("#64707A")
            background = QColor("#12171B")
            fill = QColor("#3E8CC7")
        else:
            border = QColor("#7E8992")
            background = QColor("#F4F6F8")
            fill = QColor("#2E7DB2")

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRect(rect)

        inner = rect.adjusted(2, 2, -2, -2)
        fill_height = int(round(inner.height() * self._fraction))
        if fill_height > 0:
            fill_rect = inner.adjusted(0, inner.height() - fill_height, 0, 0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRect(fill_rect)


class SegmentedEseqCountBar(QWidget):
    def __init__(self, segment_limit, parent=None):
        super().__init__(parent)
        self.segment_limit = int(segment_limit)
        self._count = 0
        self.setFixedWidth(14)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_count(self, count):
        count = max(0, min(int(count or 0), self.segment_limit))
        if self._count == count:
            return
        self._count = count
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(2, 2, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0 or self.segment_limit <= 0:
            return

        if is_dark_theme():
            border = QColor("#64707A")
            empty = QColor("#151A1E")
            filled = QColor("#3B8B5A")
        else:
            border = QColor("#7E8992")
            empty = QColor("#F4F6F8")
            filled = QColor("#3E9A62")

        painter.setPen(QPen(border, 1))
        painter.setBrush(empty)
        painter.drawRect(rect)

        inner = rect.adjusted(2, 2, -2, -2)
        gap = 1
        total_gap = gap * (self.segment_limit - 1)
        raw_segment_height = (inner.height() - total_gap) / self.segment_limit
        if raw_segment_height < 1:
            gap = 0
            raw_segment_height = inner.height() / self.segment_limit

        painter.setPen(Qt.NoPen)
        for index in range(self.segment_limit):
            segment_from_bottom = index
            y_bottom = inner.bottom() - int(round(segment_from_bottom * (raw_segment_height + gap)))
            y_top = y_bottom - max(1, int(round(raw_segment_height))) + 1
            color = filled if index < self._count else empty
            painter.setBrush(color)
            painter.drawRect(inner.left(), y_top, inner.width(), max(1, y_bottom - y_top + 1))


class WriteProtectToggle(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._target_label = "original"
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(30, 50)
        self.setAccessibleName("Allow saving to original media")
        self.setFocusPolicy(Qt.StrongFocus)
        self.toggled.connect(self._refresh_tooltip)
        self._refresh_tooltip()

    def set_target_label(self, target_label):
        self._target_label = str(target_label or "original")
        self._refresh_tooltip()

    def _refresh_tooltip(self):
        if self.isChecked():
            self.setToolTip(
                f"Write enabled for this {self._target_label}. Save will modify the original."
            )
        else:
            self.setToolTip(
                f"Write protected for this {self._target_label}. Use Save As or Save As Image instead."
            )

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(3, 3, -3, -3)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        write_enabled = self.isChecked()
        if is_dark_theme():
            border = QColor("#7B8792")
            fill = QColor("#A63E3E") if write_enabled else QColor("#286B48")
            thumb = QColor("#DDE4EA")
            thumb_edge = QColor("#283038")
        else:
            border = QColor("#5F6870")
            fill = QColor("#C94842") if write_enabled else QColor("#2F8A58")
            thumb = QColor("#FFFFFF")
            thumb_edge = QColor("#55606A")

        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 2, 2)

        mid = rect.center().y()
        thumb_rect = rect.adjusted(5, 5, -5, -5)
        if write_enabled:
            thumb_rect.setTop(mid + 2)
        else:
            thumb_rect.setBottom(mid - 2)
        painter.setPen(QPen(thumb_edge, 1))
        painter.setBrush(thumb)
        painter.drawRect(thumb_rect)


class MidiTitleWindow(QMainWindow):
    TITLE_COMPAT_LIMIT = 32
    ESEQ_FILE_LIMIT = PIANODIR_MAX_TRACKS
    TITLE_RAW_ROLE = TitleOverflowDelegate.RAW_TITLE_ROLE
    CENTERED_TITLE_DISK_THRESHOLD = 3
    SETTINGS_ORG = APP_SETTINGS_ORG
    SETTINGS_APP = APP_SETTINGS_APP
    SETTING_SHOW_COMPAT_WARNING = "show_compat_warning"
    SETTING_STORE_BACKUPS = "store_backups"
    SETTING_SKIP_TYPE0_WARNING = "skip_type0_warning"
    SETTING_SKIP_IMAGE_REMOVE_WARNING = "skip_image_remove_warning"
    SETTING_SKIP_IMAGE_DELETE_ON_SAVE_WARNING = "skip_image_delete_on_save_warning"
    SETTING_SKIP_FLOPPY_WRITE_WARNING = "skip_floppy_write_warning"
    SETTING_ALLOW_FLOPPY_SAVE = "allow_floppy_save"
    SETTING_CONFIRM_IMAGE_SAVE = "confirm_image_save"
    SETTING_FORMAT_DISKLAVIER_SCREEN = "format_disklavier_screen"
    SETTING_ESEQ_EXPORT_ALBUM_SUBFOLDER = "eseq_export_album_subfolder"
    SETTING_ESEQ_TO_MIDI_SWITCH_MODE = "eseq_to_midi_switch_mode"
    IMAGE_FILENAME_INVALID_CHARS = set('\\/:*?"<>|+,;=[]')
    EXPORT_FOLDER_INVALID_CHARS = set('\\/:*?"<>|')
    TYPE_COLUMN_MIN_WIDTH = 70
    TYPE_COLUMN_MAX_WIDTH = 420
    TYPE_COLUMN_ESEQ_DETAIL_MIN_WIDTH = 240
    FILENAME_COLUMN_CHARS = 9
    FILENAME_COLUMN_PADDING = 22
    TITLE_COLUMN_MIN_CHARS = 32
    TITLE_COLUMN_PADDING = 30
    USER_RESIZABLE_EDGE_COLUMNS = {3, 4, 5, 6}

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        apply_window_icon(self)
        self.resize(860, 800)
        self.pendingEdits = {}         # keys: full file paths, values: new titles
        self.image_session = None
        self.pendingImageRenames = {}  # keys: image paths, values: target image paths
        self.pendingImageTitleEdits = {}  # keys: image paths, values: new MIDI titles
        self.pendingImageDeletes = set()
        self.pendingImageAdditions = {}  # keys: target image paths, values: host file paths
        self.pendingImageReplacements = {}  # keys: image paths, values: replacement host file paths
        self.imageEntriesByPath = {}
        self.imageFileInfo = {}
        self.imageEseqMode = False
        self.imageTitlesLikelyCentered = False
        self.imageHasPianodir = False
        self.imagePianodirPopulated = False
        self.loadedImagePianodirMetadata = PianodirMetadata()
        self.pendingExportPianodirMetadata = PianodirMetadata()
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = False
        self.midiScratchDir = None
        self.listedFileInfo = {}
        self.pendingRegularConversions = {}
        self.regularModeContextPath = ""
        self.regularEseqMode = False
        self.regularTitlesLikelyCentered = False
        self.regularHasPianodir = False
        self.regularPianodirPopulated = False
        self.regularPianodirSourcePath = ""
        self.loadedRegularPianodirMetadata = PianodirMetadata()
        self.loadedRegularEseqPaths = tuple()
        self.diskLoadWorker = None
        self.diskLoadProgressDialog = None
        self.diskLoadFailureTitle = "Disk Load Failed"
        self.diskLoadShouldOfferCapture = False
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._did_apply_initial_column_sizing = False
        self._is_adjusting_columns = False
        self._manual_column_widths = {}
        self.title_monospace_font = QFont("Courier New")
        self.title_monospace_font.setStyleHint(QFont.Monospace)

        # Main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self.setCentralWidget(main_widget)

        # Top: source buttons
        source_layout = QHBoxLayout()
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(10)

        self.choose_button = QPushButton("Open MIDI Folder")
        self.choose_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.choose_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.choose_button.setToolTip(
            "Select a folder to scan for .mid and .midi files."
        )
        self.choose_button.clicked.connect(self.browse_directory)
        source_layout.addWidget(self.choose_button, stretch=1)

        self.open_image_button = QPushButton("Open Image")
        self.open_image_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.open_image_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.open_image_button.setToolTip(
            "Open a floppy image file for editing in Image Mode."
        )
        self.open_image_button.clicked.connect(self.open_image_dialog)
        source_layout.addWidget(self.open_image_button, stretch=1)

        self.read_floppy_button = QPushButton("Read Floppy")
        self.read_floppy_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.read_floppy_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.read_floppy_button.setToolTip(
            "Read a floppy from a USB floppy drive or from a Greaseweazle-connected drive."
        )
        self.read_floppy_button.clicked.connect(self.load_floppy_drive)
        source_layout.addWidget(self.read_floppy_button, stretch=1)

        main_layout.addLayout(source_layout)

        # Middle: Table for displaying MIDI files (using our DropTableWidget subclass)
        # Column order:
        # 0: Delete ("X"), 1: FullPath (hidden), 2: 📋, 3: Filename, 4: Title, 5: Compat warning (>32), 6: MIDI type
        self.table = DropTableWidget(0, 7)
        self.table.setStyleSheet("QTableWidget::item:selected { background-color: #FFB347; }")
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "Long", "Type"])
        self.table.setToolTip(
            "Drop MIDI files here, click a Title cell to edit, or click the clipboard icon to copy a filename."
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(40)
        header.sectionResized.connect(self._handle_section_resized)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(2, 50)
        self.table.setColumnWidth(3, self._default_filename_column_width())
        self.table.setColumnWidth(4, 260)
        self.table.setColumnWidth(5, 65)
        self.table.setColumnWidth(6, self.TYPE_COLUMN_MIN_WIDTH)
        self.table.setColumnHidden(1, True)  # Hide the full path column
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self.handle_cell_clicked)
        self.table.cellDoubleClicked.connect(self.handle_cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._refresh_eseq_reorder_buttons)
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

        file_list_layout = QHBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(6)
        file_list_layout.addWidget(self.table, stretch=1)

        self.diskUsageBarsWidget = QWidget()
        usage_bars_layout = QHBoxLayout(self.diskUsageBarsWidget)
        usage_bars_layout.setContentsMargins(0, 0, 0, 0)
        usage_bars_layout.setSpacing(3)
        self.diskUsageBar = VerticalUsageBar(self.diskUsageBarsWidget)
        self.eseqCountBar = SegmentedEseqCountBar(self.ESEQ_FILE_LIMIT, self.diskUsageBarsWidget)
        self.diskUsageBar.setToolTip("Floppy image space used.")
        self.eseqCountBar.setToolTip("Yamaha E-SEQ file slots used.")
        usage_bars_layout.addWidget(self.diskUsageBar)
        usage_bars_layout.addWidget(self.eseqCountBar)
        self.diskUsageBarsWidget.setVisible(False)
        file_list_layout.addWidget(self.diskUsageBarsWidget)
        main_layout.addLayout(file_list_layout, stretch=1)

        self.eseqReorderWidget = QWidget()
        reorder_layout = QHBoxLayout(self.eseqReorderWidget)
        reorder_layout.setContentsMargins(0, 0, 0, 0)
        reorder_layout.setSpacing(8)
        reorder_layout.addStretch()

        self.moveEseqUpButton = QToolButton()
        self.moveEseqUpButton.setArrowType(Qt.UpArrow)
        self.moveEseqUpButton.setToolTip("Move the selected Yamaha E-SEQ file earlier in the PIANODIR order.")
        self.moveEseqUpButton.setFixedSize(34, 28)
        self.moveEseqUpButton.clicked.connect(lambda: self.move_selected_eseq_row(-1))
        reorder_layout.addWidget(self.moveEseqUpButton)

        self.moveEseqDownButton = QToolButton()
        self.moveEseqDownButton.setArrowType(Qt.DownArrow)
        self.moveEseqDownButton.setToolTip("Move the selected Yamaha E-SEQ file later in the PIANODIR order.")
        self.moveEseqDownButton.setFixedSize(34, 28)
        self.moveEseqDownButton.clicked.connect(lambda: self.move_selected_eseq_row(1))
        reorder_layout.addWidget(self.moveEseqDownButton)
        reorder_layout.addStretch()
        self.eseqReorderWidget.setVisible(False)
        main_layout.addWidget(self.eseqReorderWidget)

        # Status label
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
        self.compat_warning_checkbox = QCheckBox("Long title warning")
        self.compat_warning_checkbox.setChecked(show_compat_warning)
        self.compat_warning_checkbox.setToolTip(
            "Highlight title characters beyond the 32-character legacy compatibility limit."
        )
        self.compat_warning_checkbox.toggled.connect(self.toggle_compat_warnings)
        self.title_delegate.set_highlight_enabled(show_compat_warning)
        options_layout.addWidget(self.compat_warning_checkbox, alignment=Qt.AlignLeft)

        format_disklavier_screen = self.settings.value(
            self.SETTING_FORMAT_DISKLAVIER_SCREEN, False, type=bool
        )
        self.format_disklavier_checkbox = QCheckBox("Format for Disklavier screen")
        self.format_disklavier_checkbox.setChecked(format_disklavier_screen)
        self.format_disklavier_checkbox.setToolTip(
            "When editing titles, use the Disklavier's two 16-character screen rows."
        )
        self.format_disklavier_checkbox.toggled.connect(self.toggle_format_disklavier_screen)
        options_layout.addWidget(self.format_disklavier_checkbox, alignment=Qt.AlignLeft)

        store_backups = self.settings.value(self.SETTING_STORE_BACKUPS, False, type=bool)
        self.backup_checkbox = QCheckBox("Back up before saving")
        self.backup_checkbox.setChecked(store_backups)
        self.backup_checkbox.setToolTip(
            "Before overwriting, back up images beside the image and individual files into a backup folder."
        )
        self.backup_checkbox.toggled.connect(self.toggle_store_backups)
        options_layout.addWidget(self.backup_checkbox, alignment=Qt.AlignLeft)

        options_layout.addStretch()

        self.modeBannerLabel = QLabel("MIDI MODE")
        self.modeBannerLabel.setAlignment(Qt.AlignCenter)
        mode_font = QFont("Helvetica", 14, QFont.Bold)
        self.modeBannerLabel.setFont(mode_font)
        self.modeBannerLabel.setWordWrap(True)
        self.modeBannerLabel.setToolTip("Shows the current editing mode and active source.")

        utilities_group = QGroupBox("Utilities")
        utilities_group.setToolTip("Batch tools that run across every listed file immediately.")
        utilities_layout = QVBoxLayout(utilities_group)
        utilities_layout.setContentsMargins(10, 14, 10, 10)
        utilities_layout.setSpacing(6)

        utilities_hint = QLabel("Apply to all listed files:")
        utilities_hint.setWordWrap(True)
        utilities_hint.setAlignment(Qt.AlignCenter)
        utilities_layout.addWidget(utilities_hint)

        self.renameAllButton = QPushButton("Rename 8.3")
        self.renameAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.renameAllButton.setMinimumHeight(36)
        self.renameAllButton.setToolTip(
            "Utility: rename every listed file to DOS 8.3 format (00.MID, 01.MID, ...)."
        )
        self.renameAllButton.clicked.connect(self.rename_all_for_disk)

        self.convertType0Button = QPushButton("SMF1 -> SMF0")
        self.convertType0Button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertType0Button.setMinimumHeight(36)
        self.convertType0Button.setToolTip(
            "Utility: convert every listed file to MIDI Type 0 (single-track)."
        )
        self.convertType0Button.clicked.connect(self.convert_all_to_type0)

        self.convertEseqToMidiButton = QPushButton("E-SEQ -> MIDI")
        self.convertEseqToMidiButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertEseqToMidiButton.setMinimumHeight(36)
        self.convertEseqToMidiButton.setToolTip(
            "Image/Floppy Mode utility: queue conversion of listed E-SEQ files to SMF MIDI."
        )
        self.convertEseqToMidiButton.clicked.connect(self.convert_all_eseq_to_midi)

        self.convertMidiToEseqButton = QPushButton("MIDI -> E-SEQ")
        self.convertMidiToEseqButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertMidiToEseqButton.setMinimumHeight(36)
        self.convertMidiToEseqButton.setToolTip(
            "Image/Floppy Mode utility: queue conversion of listed MIDI files to Yamaha E-SEQ."
        )
        self.convertMidiToEseqButton.clicked.connect(self.convert_all_midi_to_eseq)
        self._apply_compact_button_labels()

        utilities_buttons_layout = QGridLayout()
        utilities_buttons_layout.setContentsMargins(0, 0, 0, 0)
        utilities_buttons_layout.setHorizontalSpacing(6)
        utilities_buttons_layout.setVerticalSpacing(6)
        utilities_buttons_layout.addWidget(self.renameAllButton, 0, 0)
        utilities_buttons_layout.addWidget(self.convertType0Button, 0, 1)
        utilities_buttons_layout.addWidget(self.convertEseqToMidiButton, 1, 0)
        utilities_buttons_layout.addWidget(self.convertMidiToEseqButton, 1, 1)
        utilities_buttons_layout.setColumnStretch(0, 1)
        utilities_buttons_layout.setColumnStretch(1, 1)
        utilities_layout.addLayout(utilities_buttons_layout)
        utilities_layout.addStretch()

        actions_group = QGroupBox("File Actions")
        actions_group.setToolTip("Save files, create images, or clear the current list.")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(10, 14, 10, 10)
        actions_layout.setSpacing(6)

        # Clear button (styled to match Save button)
        self.clearButton = QToolButton()
        self.clearButton.setText("Clear")
        self.clearButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.clearButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clearButton.setMinimumHeight(36)
        self.clearButton.setToolTip("Remove all files from the current list.")
        self.clearButton.clicked.connect(self.clear_list)

        self.saveButton = QToolButton()
        self.saveButton.setText("Save")
        self.saveButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveButton.setMinimumHeight(36)
        self.saveButton.clicked.connect(self.save_pending_changes)

        self.saveAsButton = QToolButton()
        self.saveAsButton.setText("Save As")
        self.saveAsButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveAsButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveAsButton.setMinimumHeight(36)
        self.saveAsButton.setToolTip("Save copies with current titles to a selected destination folder.")
        self.saveAsButton.clicked.connect(self.save_as_changes)

        self.saveAsImageButton = QToolButton()
        self.saveAsImageButton.setText("Save As Image")
        self.saveAsImageButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveAsImageButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveAsImageButton.setMinimumHeight(36)
        self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self.saveAsImageButton.clicked.connect(self.save_as_image)
        self._apply_compact_button_labels()

        actions_buttons_layout = QGridLayout()
        actions_buttons_layout.setContentsMargins(0, 0, 0, 0)
        actions_buttons_layout.setHorizontalSpacing(6)
        actions_buttons_layout.setVerticalSpacing(6)
        actions_buttons_layout.addWidget(self.clearButton, 0, 0)
        actions_buttons_layout.addWidget(self.saveAsButton, 1, 0)
        actions_buttons_layout.addWidget(self.saveAsImageButton, 1, 1)
        actions_buttons_layout.setColumnStretch(0, 1)
        actions_buttons_layout.setColumnStretch(1, 1)
        actions_layout.addLayout(actions_buttons_layout)

        save_with_toggle_widget = QWidget(actions_group)
        save_with_toggle_layout = QHBoxLayout(save_with_toggle_widget)
        save_with_toggle_layout.setContentsMargins(0, 0, 0, 0)
        save_with_toggle_layout.setSpacing(6)
        save_with_toggle_layout.addWidget(self.saveButton, stretch=1)
        self.writeProtectToggle = WriteProtectToggle(actions_group)
        self.writeProtectToggle.toggled.connect(self.toggle_original_write)
        self.writeProtectToggle.setVisible(False)
        save_with_toggle_layout.addWidget(self.writeProtectToggle, alignment=Qt.AlignCenter)
        actions_buttons_layout.addWidget(save_with_toggle_widget, 0, 1)

        actions_layout.addStretch()

        for section in (options_group, utilities_group, actions_group):
            section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        controls_layout.addWidget(options_group, stretch=1)
        controls_layout.addWidget(utilities_group, stretch=1)
        controls_layout.addWidget(actions_group, stretch=1)
        main_layout.addLayout(controls_layout)

        self.imagePianodirMetadataWidget = QWidget()
        self.imagePianodirMetadataWidget.setToolTip(
            "Album title and catalog number stored in PIANODIR.FIL for Yamaha E-SEQ images and floppies."
        )
        pianodir_meta_layout = QHBoxLayout(self.imagePianodirMetadataWidget)
        pianodir_meta_layout.setContentsMargins(0, 0, 0, 0)
        pianodir_meta_layout.setSpacing(8)

        album_title_label = QLabel("Album Title")
        album_title_label.setToolTip(
            "Album title stored in PIANODIR.FIL for Yamaha E-SEQ images and floppies."
        )
        pianodir_meta_layout.addWidget(album_title_label)

        self.imagePianodirTitleEdit = QLineEdit()
        self.imagePianodirTitleEdit.setPlaceholderText("Album title")
        self.imagePianodirTitleEdit.setMaxLength(PIANODIR_DISK_METADATA_SIZE)
        self.imagePianodirTitleEdit.setToolTip(
            "Album title stored in PIANODIR.FIL for Yamaha E-SEQ images and floppies."
        )
        pianodir_meta_layout.addWidget(self.imagePianodirTitleEdit, stretch=3)

        catalog_label = QLabel("Catalog Number")
        catalog_label.setToolTip(
            "Catalog number stored in PIANODIR.FIL for Yamaha E-SEQ images and floppies."
        )
        pianodir_meta_layout.addWidget(catalog_label)

        self.imagePianodirCatalogEdit = QLineEdit()
        self.imagePianodirCatalogEdit.setPlaceholderText("Catalog number")
        self.imagePianodirCatalogEdit.setMaxLength(PIANODIR_DISK_METADATA_SIZE)
        self.imagePianodirCatalogEdit.setToolTip(
            "Catalog number stored in PIANODIR.FIL for Yamaha E-SEQ images and floppies."
        )
        self.imagePianodirCatalogEdit.editingFinished.connect(self._normalize_pianodir_catalog_field)
        pianodir_meta_layout.addWidget(self.imagePianodirCatalogEdit, stretch=1)

        use_album_subfolder = self.settings.value(
            self.SETTING_ESEQ_EXPORT_ALBUM_SUBFOLDER, False, type=bool
        )
        self.album_subfolder_checkbox = QCheckBox("Create Album Subfolder")
        self.album_subfolder_checkbox.setChecked(use_album_subfolder)
        self.album_subfolder_checkbox.setToolTip(
            "When exporting E-SEQ files or converting them to MIDI, place the files in a folder named from the catalog number and album title."
        )
        self.album_subfolder_checkbox.toggled.connect(self.toggle_album_subfolder)
        self.album_subfolder_checkbox.setVisible(False)
        pianodir_meta_layout.addWidget(self.album_subfolder_checkbox)

        self.imagePianodirMetadataWidget.setVisible(False)
        main_layout.addWidget(self.imagePianodirMetadataWidget)
        main_layout.addWidget(self.modeBannerLabel)

        self.fileMenu = self.menuBar().addMenu("&File")
        self.fileSaveAction = QAction("Save", self)
        self.fileSaveAction.triggered.connect(self.save_pending_changes)
        self.fileMenu.addAction(self.fileSaveAction)

        self.fileSaveAsAction = QAction("Save As...", self)
        self.fileSaveAsAction.triggered.connect(self.save_as_changes)
        self.fileMenu.addAction(self.fileSaveAsAction)

        self.fileSaveAsImageAction = QAction("Save As Image...", self)
        self.fileSaveAsImageAction.triggered.connect(self.save_as_image)
        self.fileMenu.addAction(self.fileSaveAsImageAction)

        self.utilitiesMenu = self.menuBar().addMenu("&Utilities")
        self.utilitiesRenameAction = QAction("Rename All to DOS 8.3", self)
        self.utilitiesRenameAction.triggered.connect(self.rename_all_for_disk)
        self.utilitiesMenu.addAction(self.utilitiesRenameAction)

        self.utilitiesSmfAction = QAction("Convert All SMF1 to SMF0", self)
        self.utilitiesSmfAction.triggered.connect(self.convert_all_to_type0)
        self.utilitiesMenu.addAction(self.utilitiesSmfAction)

        self.utilitiesEseqToMidiAction = QAction("Convert All E-SEQ to MIDI", self)
        self.utilitiesEseqToMidiAction.triggered.connect(self.convert_all_eseq_to_midi)
        self.utilitiesMenu.addAction(self.utilitiesEseqToMidiAction)

        self.utilitiesMidiToEseqAction = QAction("Convert All MIDI to E-SEQ", self)
        self.utilitiesMidiToEseqAction.triggered.connect(self.convert_all_midi_to_eseq)
        self.utilitiesMenu.addAction(self.utilitiesMidiToEseqAction)

        help_menu = self.menuBar().addMenu("&Help")
        welcome_action = QAction("Show Welcome Screen", self)
        welcome_action.triggered.connect(self.show_welcome_dialog)
        help_menu.addAction(welcome_action)

        about_action = QAction("About APS MIDI Prep Tool", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
        self._update_compat_warning_ui()
        self.table.setColumnHidden(6, False)

        # Set mouse tracking and install an event filter on the table viewport.
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)
        self._update_floppy_save_option_ui()
        self._update_menu_actions()

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

    def _center_child_dialog(self, dialog):
        center_dialog_on_parent(dialog, self)

    def _exec_child_dialog(self, dialog):
        dialog.setWindowModality(Qt.WindowModal)
        self._center_child_dialog(dialog)
        return dialog.exec()

    def _prepare_progress_dialog(self, dialog):
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        self._center_child_dialog(dialog)
        return dialog

    def _apply_stage_progress(self, dialog, step, total, message):
        if dialog is None:
            return
        if total and total > 0:
            if dialog.maximum() != total:
                dialog.setRange(0, total)
            dialog.setValue(max(0, min(step, total)))
        else:
            dialog.setRange(0, 0)
        dialog.setLabelText(message)
        QApplication.processEvents()

    def _set_disk_load_busy(self, busy):
        is_busy = bool(busy)
        self.choose_button.setEnabled(not is_busy)
        self.open_image_button.setEnabled(not is_busy)
        self.read_floppy_button.setEnabled(not is_busy)

    def _start_disk_load_worker(
        self,
        *,
        load_kind,
        source,
        progress_title,
        progress_total,
        initial_message,
        final_message,
        failure_title,
        offer_greaseweazle_capture=False,
    ):
        if self.diskLoadWorker is not None:
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return False

        progress_dialog = QProgressDialog(progress_title, None, 0, progress_total, self)
        progress_dialog.setWindowTitle(progress_title)
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        progress_dialog.setCancelButton(None)
        self._apply_stage_progress(progress_dialog, 0, progress_total, initial_message)

        worker = DiskSessionLoadWorker(
            load_kind,
            source,
            final_total=progress_total,
            final_message=final_message,
            parent=self,
        )
        worker.progressChanged.connect(
            lambda step, total, message, dialog=progress_dialog: self._apply_stage_progress(
                dialog, step, total, message
            )
        )
        worker.sessionLoaded.connect(self._on_disk_load_success)
        worker.loadFailed.connect(self._on_disk_load_failure)
        worker.finished.connect(self._on_disk_load_finished)

        self.diskLoadWorker = worker
        self.diskLoadProgressDialog = progress_dialog
        self.diskLoadFailureTitle = failure_title
        self.diskLoadShouldOfferCapture = bool(offer_greaseweazle_capture)
        self._set_disk_load_busy(True)
        worker.start()
        return True

    def _on_disk_load_success(self, session, listing):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None

        try:
            self._activate_disk_session(session, listing)
        except Exception as exc:
            try:
                session.cleanup()
            except Exception:
                pass
            QMessageBox.critical(self, self.diskLoadFailureTitle, str(exc))
            return

        if self.diskLoadShouldOfferCapture:
            self._offer_save_greaseweazle_capture()

    def _on_disk_load_failure(self, message):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None
        QMessageBox.critical(self, self.diskLoadFailureTitle, message)

    def _on_disk_load_finished(self):
        self._set_disk_load_busy(False)
        self.diskLoadShouldOfferCapture = False
        if self.diskLoadWorker is not None:
            self.diskLoadWorker.deleteLater()
            self.diskLoadWorker = None

    def _confirm_with_optional_skip(self, *, setting_key, title, message, icon=QMessageBox.Warning):
        if self.settings.value(setting_key, False, type=bool):
            return True

        dialog = QMessageBox(self)
        apply_window_icon(dialog)
        dialog.setIcon(icon)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dialog.setDefaultButton(QMessageBox.No)
        skip_checkbox = QCheckBox("Do not remind me again for this action")
        dialog.setCheckBox(skip_checkbox)

        confirmed = self._exec_child_dialog(dialog) == QMessageBox.Yes
        if confirmed and skip_checkbox.isChecked():
            self.settings.setValue(setting_key, True)
        return confirmed

    def closeEvent(self, event):
        if self.is_image_mode() and not self._confirm_discard_image_changes():
            event.ignore()
            return
        self._reset_image_state()
        self._cleanup_midi_scratch_dir()
        super().closeEvent(event)

    def _handle_section_resized(self, logical_index, old_size, new_size):
        if self._is_adjusting_columns:
            return
        if logical_index in self.USER_RESIZABLE_EDGE_COLUMNS:
            self._manual_column_widths[logical_index] = new_size
            return
        if logical_index != 1:
            self._resize_table_columns_to_fill(preferred_column=logical_index)

    def _default_filename_column_width(self):
        metrics = QFontMetrics(self.table.font())
        sample = "M" * self.FILENAME_COLUMN_CHARS
        return max(
            self.table.horizontalHeader().minimumSectionSize(),
            metrics.horizontalAdvance(sample) + self.FILENAME_COLUMN_PADDING,
        )

    def _minimum_title_column_width(self):
        metrics = QFontMetrics(self.title_monospace_font)
        sample = "M" * self.TITLE_COLUMN_MIN_CHARS
        return max(
            self.table.horizontalHeader().minimumSectionSize(),
            metrics.horizontalAdvance(sample) + self.TITLE_COLUMN_PADDING,
        )

    def _preferred_type_column_width(self):
        if self.table.isColumnHidden(6):
            return self.TYPE_COLUMN_MIN_WIDTH

        header_item = self.table.horizontalHeaderItem(6)
        header_text = header_item.text() if header_item is not None else "Type"
        header_metrics = QFontMetrics(self.table.horizontalHeader().font())
        preferred = header_metrics.horizontalAdvance(header_text)
        has_eseq_detail = False

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 6)
            if item is None:
                continue
            text = item.text()
            if not text:
                continue
            metrics = QFontMetrics(item.font() if item.font() is not None else self.table.font())
            preferred = max(preferred, metrics.horizontalAdvance(text))
            if "(" in text and ")" in text:
                has_eseq_detail = True

        preferred += 30
        if has_eseq_detail:
            preferred = max(preferred, self.TYPE_COLUMN_ESEQ_DETAIL_MIN_WIDTH)
        return max(self.TYPE_COLUMN_MIN_WIDTH, min(preferred, self.TYPE_COLUMN_MAX_WIDTH))

    def _resize_table_columns_to_fill(self, preferred_column=None):
        if self._is_adjusting_columns:
            return

        available_width = self.table.viewport().width()
        if available_width <= 0:
            return

        min_section = self.table.horizontalHeader().minimumSectionSize()
        type_width = None
        fixed_columns = [0, 2]
        if not self.table.isColumnHidden(5):
            fixed_columns.append(5)
        if not self.table.isColumnHidden(6):
            type_width = self._preferred_type_column_width()
            if 6 in self._manual_column_widths:
                type_width = max(type_width, min_section, self._manual_column_widths[6])
            if preferred_column == 6:
                type_width = max(type_width, self.table.columnWidth(6))
        fixed_total = sum(self.table.columnWidth(column) for column in fixed_columns)
        if type_width is not None:
            fixed_total += type_width

        title_min_width = self._minimum_title_column_width()
        remaining = max((min_section + title_min_width), available_width - fixed_total)

        filename_width = self._manual_column_widths.get(3, self._default_filename_column_width())
        filename_width = max(min_section, min(filename_width, remaining - title_min_width))
        title_width = remaining - filename_width

        self._is_adjusting_columns = True
        try:
            if type_width is not None:
                self.table.setColumnWidth(6, type_width)
            self.table.setColumnWidth(3, filename_width)
            self.table.setColumnWidth(4, title_width)
        finally:
            self._is_adjusting_columns = False

    def toggle_compat_warnings(self, state):
        self.settings.setValue(self.SETTING_SHOW_COMPAT_WARNING, bool(state))
        self._update_compat_warning_ui()
        self._resize_table_columns_to_fill()
        if self._compat_warning_is_active():
            self.refresh_compat_indicators()

    def toggle_format_disklavier_screen(self, state):
        self.settings.setValue(self.SETTING_FORMAT_DISKLAVIER_SCREEN, bool(state))

    def _enable_disklavier_screen_format_option(self):
        checkbox = getattr(self, "format_disklavier_checkbox", None)
        if checkbox is None:
            return
        if checkbox.isChecked():
            self.settings.setValue(self.SETTING_FORMAT_DISKLAVIER_SCREEN, True)
            return
        checkbox.setChecked(True)

    def toggle_store_backups(self, state):
        self.settings.setValue(self.SETTING_STORE_BACKUPS, bool(state))

    def _original_write_setting_key(self):
        if self.is_floppy_mode():
            return self.SETTING_ALLOW_FLOPPY_SAVE
        if self.is_image_mode():
            return self.SETTING_CONFIRM_IMAGE_SAVE
        return None

    def _original_write_is_allowed(self):
        setting_key = self._original_write_setting_key()
        if setting_key is None:
            return True
        return self.settings.value(setting_key, False, type=bool)

    def toggle_original_write(self, state):
        setting_key = self._original_write_setting_key()
        if setting_key is None:
            return
        self.settings.setValue(setting_key, bool(state))
        self._update_floppy_save_option_ui()

    def toggle_album_subfolder(self, state):
        self.settings.setValue(self.SETTING_ESEQ_EXPORT_ALBUM_SUBFOLDER, bool(state))

    def is_image_mode(self):
        return self.image_session is not None

    def is_floppy_mode(self):
        return self.image_session is not None and self.image_session.source_kind.startswith("floppy")

    def _is_compat_warning_locked(self):
        return self.is_local_eseq_mode() or (self.is_image_mode() and self.imageEseqMode)

    def _compat_warning_is_active(self):
        return self.compat_warning_checkbox.isChecked() and not self._is_compat_warning_locked()

    def _update_compat_warning_ui(self):
        locked = self._is_compat_warning_locked()
        self.compat_warning_checkbox.setEnabled(not locked)
        if locked:
            self.compat_warning_checkbox.setToolTip(
                "Disabled while editing E-SEQ files because the 32-character limit is already enforced."
            )
            self.table.setColumnHidden(5, True)
            self.title_delegate.set_highlight_enabled(False)
        else:
            self.compat_warning_checkbox.setToolTip(
                "Highlight title characters beyond the 32-character legacy compatibility limit."
            )
            self.table.setColumnHidden(5, not self.compat_warning_checkbox.isChecked())
            self.title_delegate.set_highlight_enabled(self.compat_warning_checkbox.isChecked())
        self.table.viewport().update()

    def _update_floppy_save_option_ui(self):
        is_floppy = self.is_floppy_mode()
        is_image = self.is_image_mode() and not is_floppy
        show_original_write_toggle = is_floppy or is_image
        if hasattr(self, "writeProtectToggle"):
            self.writeProtectToggle.setVisible(show_original_write_toggle)
            self.writeProtectToggle.setEnabled(show_original_write_toggle)
            if show_original_write_toggle:
                target_label = "floppy" if is_floppy else "image"
                self.writeProtectToggle.set_target_label(target_label)
                self.writeProtectToggle.blockSignals(True)
                self.writeProtectToggle.setChecked(self._original_write_is_allowed())
                self.writeProtectToggle.blockSignals(False)
                self.writeProtectToggle._refresh_tooltip()
        if not hasattr(self, "saveButton"):
            return

        if is_floppy and not self._original_write_is_allowed():
            self.saveButton.setEnabled(False)
            self.saveButton.setToolTip(
                "Original floppy write is protected. Turn on Overwrite Original, or use Save As or Save As Image."
            )
            self.saveAsButton.setToolTip(
                "Save the current floppy session's listed files to a destination folder and leave Floppy Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current floppy session as a separate image file.")
        elif is_floppy:
            self.saveButton.setEnabled(True)
            self.saveButton.setToolTip("Write pending changes back to the floppy currently loaded in Floppy Mode.")
            self.saveAsButton.setToolTip(
                "Save the current floppy session's listed files to a destination folder and leave Floppy Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current floppy session as a separate image file.")
        elif is_image and not self._original_write_is_allowed():
            self.saveButton.setEnabled(False)
            self.saveButton.setToolTip(
                "Original image write is protected. Turn on Overwrite Original, or use Save As or Save As Image."
            )
            self.saveAsButton.setToolTip(
                "Save the current image session's listed files to a destination folder and leave Image Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current image session as a separate image file.")
        elif is_image:
            self.saveButton.setEnabled(True)
            self.saveButton.setToolTip("Write pending image changes back to the currently loaded image.")
            self.saveAsButton.setToolTip(
                "Save the current image session's listed files to a destination folder and leave Image Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current image session as a separate image file.")
        else:
            self.saveButton.setEnabled(True)
            self.saveButton.setToolTip("Write pending title edits to the currently listed files.")
            self.saveAsButton.setToolTip("Save copies with current titles to a selected destination folder.")
            self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self._update_menu_actions()

    def _has_pending_image_changes(self):
        return bool(
            self.pendingImageRenames
            or self.pendingImageTitleEdits
            or self.pendingImageDeletes
            or self.pendingImageAdditions
            or self.pendingImageReplacements
            or self._eseq_order_changed()
            or self._image_pianodir_metadata_changed()
            or self.pendingGeneratePianodir
            or self.pendingDeletePianodir
            or (self.image_session and self.image_session.repair_changed)
        )

    def _confirm_discard_image_changes(self):
        if not self.is_image_mode() or not self._has_pending_image_changes():
            return True
        reply = QMessageBox.question(
            self,
            "Discard Image Changes",
            "Leave Image Mode and discard pending image changes?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _reset_image_state(self, cleanup=True):
        if cleanup and self.image_session is not None:
            self.image_session.cleanup()
        self.image_session = None
        self.pendingImageRenames.clear()
        self.pendingImageTitleEdits.clear()
        self.pendingImageDeletes.clear()
        self.pendingImageAdditions.clear()
        self.pendingImageReplacements.clear()
        self.imageEntriesByPath.clear()
        self.imageFileInfo.clear()
        self.imageEseqMode = False
        self.imageTitlesLikelyCentered = False
        self.imageHasPianodir = False
        self.imagePianodirPopulated = False
        self.loadedImagePianodirMetadata = PianodirMetadata()
        self.pendingExportPianodirMetadata = PianodirMetadata()
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = False
        self.imagePianodirTitleEdit.clear()
        self.imagePianodirCatalogEdit.clear()
        self._update_image_pianodir_metadata_ui()
        self._refresh_disk_usage_bars()

    def _cleanup_midi_scratch_dir(self):
        scratch_dir = self.midiScratchDir
        self.midiScratchDir = None
        if not scratch_dir:
            return
        shutil.rmtree(scratch_dir, ignore_errors=True)

    def _ensure_midi_scratch_dir(self):
        if self.midiScratchDir and os.path.isdir(self.midiScratchDir):
            return self.midiScratchDir
        self.midiScratchDir = tempfile.mkdtemp(prefix="aps_midi_prep_")
        return self.midiScratchDir

    def _set_mode_banner(self, headline, detail=""):
        text = headline.strip().upper()
        if detail:
            text += f"\n{detail}"
        self.modeBannerLabel.setText(text)

    def _apply_compact_button_labels(self):
        if hasattr(self, "renameAllButton"):
            self.renameAllButton.setText("Rename 8.3")
        if hasattr(self, "convertType0Button"):
            self.convertType0Button.setText("SMF1 -> SMF0")
        if hasattr(self, "convertEseqToMidiButton"):
            self.convertEseqToMidiButton.setText("E-SEQ -> MIDI")
        if hasattr(self, "convertMidiToEseqButton"):
            self.convertMidiToEseqButton.setText("MIDI -> E-SEQ")
        if hasattr(self, "clearButton"):
            self.clearButton.setText("Clear")
        if hasattr(self, "saveButton"):
            self.saveButton.setText("Save")
        if hasattr(self, "saveAsButton"):
            self.saveAsButton.setText("Save As")
        if hasattr(self, "saveAsImageButton"):
            self.saveAsImageButton.setText("Save As Image")

    def _update_menu_actions(self):
        if not hasattr(self, "fileSaveAction"):
            return

        self.fileSaveAction.setText("Save")
        self.fileSaveAction.setEnabled(self.saveButton.isEnabled())
        self.fileSaveAction.setToolTip(self.saveButton.toolTip())
        self.fileSaveAction.setStatusTip(self.saveButton.toolTip())

        self.fileSaveAsAction.setText("Save As...")
        self.fileSaveAsAction.setEnabled(self.saveAsButton.isEnabled())
        self.fileSaveAsAction.setToolTip(self.saveAsButton.toolTip())
        self.fileSaveAsAction.setStatusTip(self.saveAsButton.toolTip())

        image_action_text = "Save As Image..."
        if self.is_image_mode():
            image_action_text = "Save As Image..."
        self.fileSaveAsImageAction.setText(image_action_text)
        self.fileSaveAsImageAction.setEnabled(self.saveAsImageButton.isEnabled())
        self.fileSaveAsImageAction.setToolTip(self.saveAsImageButton.toolTip())
        self.fileSaveAsImageAction.setStatusTip(self.saveAsImageButton.toolTip())

        self.utilitiesRenameAction.setEnabled(self.renameAllButton.isEnabled())
        self.utilitiesRenameAction.setToolTip(self.renameAllButton.toolTip())
        self.utilitiesRenameAction.setStatusTip(self.renameAllButton.toolTip())

        self.utilitiesSmfAction.setEnabled(self.convertType0Button.isEnabled())
        self.utilitiesSmfAction.setToolTip(self.convertType0Button.toolTip())
        self.utilitiesSmfAction.setStatusTip(self.convertType0Button.toolTip())

        self.utilitiesEseqToMidiAction.setEnabled(self.convertEseqToMidiButton.isEnabled())
        self.utilitiesEseqToMidiAction.setToolTip(self.convertEseqToMidiButton.toolTip())
        self.utilitiesEseqToMidiAction.setStatusTip(self.convertEseqToMidiButton.toolTip())

        self.utilitiesMidiToEseqAction.setEnabled(self.convertMidiToEseqButton.isEnabled())
        self.utilitiesMidiToEseqAction.setToolTip(self.convertMidiToEseqButton.toolTip())
        self.utilitiesMidiToEseqAction.setStatusTip(self.convertMidiToEseqButton.toolTip())

    def _set_loaded_image_pianodir_metadata(self, metadata=None):
        metadata = metadata or PianodirMetadata()
        self.loadedImagePianodirMetadata = metadata
        self.imagePianodirTitleEdit.setText(metadata.disk_title)
        self.imagePianodirCatalogEdit.setText(metadata.catalog_number)

    def _set_loaded_regular_pianodir_metadata(self, metadata=None):
        metadata = metadata or PianodirMetadata()
        self.loadedRegularPianodirMetadata = metadata
        self.imagePianodirTitleEdit.setText(metadata.disk_title)
        self.imagePianodirCatalogEdit.setText(metadata.catalog_number)

    def _current_image_pianodir_metadata(self):
        return PianodirMetadata(
            catalog_number=normalize_pianodir_catalog_number(self.imagePianodirCatalogEdit.text()),
            disk_title=self.imagePianodirTitleEdit.text().strip(),
        )

    def _current_regular_pianodir_metadata(self):
        return PianodirMetadata(
            catalog_number=normalize_pianodir_catalog_number(self.imagePianodirCatalogEdit.text()),
            disk_title=self.imagePianodirTitleEdit.text().strip(),
        )

    def _normalize_pianodir_catalog_field(self):
        if not hasattr(self, "imagePianodirCatalogEdit"):
            return
        normalized = normalize_pianodir_catalog_number(self.imagePianodirCatalogEdit.text())
        if normalized != self.imagePianodirCatalogEdit.text():
            self.imagePianodirCatalogEdit.setText(normalized)

    def _current_visible_pianodir_metadata(self):
        if self._pianodir_metadata_fields_should_show() and self.is_image_mode():
            return self._current_image_pianodir_metadata()
        if self._pianodir_metadata_fields_should_show() and self.is_local_eseq_mode():
            return self._current_regular_pianodir_metadata()
        if self._metadata_has_text(self.pendingExportPianodirMetadata):
            return self.pendingExportPianodirMetadata
        return PianodirMetadata()

    def _metadata_has_text(self, metadata):
        return bool(
            metadata
            and (
                str(metadata.catalog_number or "").strip()
                or str(metadata.disk_title or "").strip()
            )
        )

    def _image_pianodir_metadata_changed(self):
        return (
            self.is_image_mode()
            and self.imageHasPianodir
            and not self.pendingDeletePianodir
            and self._current_image_pianodir_metadata() != self.loadedImagePianodirMetadata
        )

    def _regular_pianodir_metadata_changed(self):
        return (
            self.is_local_eseq_mode()
            and self.regularHasPianodir
            and self._current_regular_pianodir_metadata() != self.loadedRegularPianodirMetadata
        )

    def _image_pianodir_metadata_for_save(self):
        if not self.is_image_mode() or not self.imageHasPianodir or self.pendingDeletePianodir:
            return None
        metadata = self._current_image_pianodir_metadata()
        if metadata == self.loadedImagePianodirMetadata:
            return self.loadedImagePianodirMetadata
        return metadata

    def _regular_pianodir_metadata_for_save(self):
        if not self.is_local_eseq_mode() or not (self.regularHasPianodir or self.pendingGeneratePianodir):
            return None
        metadata = self._current_regular_pianodir_metadata()
        if metadata == self.loadedRegularPianodirMetadata:
            return self.loadedRegularPianodirMetadata
        return metadata

    def _pianodir_metadata_fields_should_show(self):
        if self.is_image_mode():
            return (
                self.imageEseqMode
                and not self.pendingDeletePianodir
                and (self.imageHasPianodir or self.pendingGeneratePianodir)
            )
        return self.is_local_eseq_mode() and (self.regularHasPianodir or self.pendingGeneratePianodir)

    def _album_subfolder_option_should_show(self):
        return self._pianodir_metadata_fields_should_show() or (
            self._metadata_has_text(self.pendingExportPianodirMetadata)
            and (
                bool(self.pendingRegularConversions)
                or bool(self.pendingImageReplacements)
                or bool(self.pendingImageRenames)
            )
        )

    def _update_image_pianodir_metadata_ui(self):
        should_show = self._pianodir_metadata_fields_should_show()
        album_option_should_show = self._album_subfolder_option_should_show()
        self.imagePianodirMetadataWidget.setVisible(should_show or album_option_should_show)
        if hasattr(self, "album_subfolder_checkbox"):
            self.album_subfolder_checkbox.setVisible(album_option_should_show)
            self.album_subfolder_checkbox.setEnabled(album_option_should_show)

    def _set_regular_mode_context(self, *, preferred_path="", file_paths=None):
        context_path = ""
        if preferred_path:
            context_path = os.path.abspath(preferred_path)
        elif file_paths:
            abs_paths = [os.path.abspath(path) for path in file_paths if path]
            if abs_paths:
                try:
                    context_path = os.path.commonpath(abs_paths)
                except ValueError:
                    context_path = os.path.dirname(abs_paths[0])
                if not os.path.isdir(context_path):
                    context_path = os.path.dirname(abs_paths[0])
        self.regularModeContextPath = context_path

    def _abbreviated_context_path(self, path):
        clean_path = os.path.abspath(path) if path else ""
        if not clean_path:
            return "No folder selected"

        home = os.path.expanduser("~")
        if clean_path == home:
            clean_path = "~"
        elif clean_path.startswith(home + os.sep):
            clean_path = "~" + clean_path[len(home):]

        max_length = 44
        if len(clean_path) <= max_length:
            return clean_path

        normalized = clean_path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 2:
            shortened = f".../{parts[-2]}/{parts[-1]}"
            if len(shortened) <= max_length:
                return shortened

        return "..." + clean_path[-(max_length - 3):]

    def _regular_mode_context_label(self):
        return self._abbreviated_context_path(self.regularModeContextPath)

    def _sanitize_export_folder_name(self, folder_name):
        text = re.sub(r"\s+", " ", str(folder_name or "")).strip()
        cleaned = []
        for char in text:
            if ord(char) < 32:
                cleaned.append(" ")
            elif char in self.EXPORT_FOLDER_INVALID_CHARS:
                cleaned.append(" ")
            else:
                cleaned.append(char)
        text = re.sub(r"\s+", " ", "".join(cleaned)).strip(" .")
        if not text:
            text = "Yamaha E-SEQ Disk"
        reserved_names = {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        }
        if text.upper() in reserved_names:
            text = f"{text} Album"
        return text[:150].rstrip(" .") or "Yamaha E-SEQ Disk"

    def _album_subfolder_name(self):
        metadata = self._current_visible_pianodir_metadata()
        parts = [
            metadata.catalog_number.strip(),
            metadata.disk_title.strip(),
        ]
        raw_name = " ".join(part for part in parts if part)
        return self._sanitize_export_folder_name(raw_name or "Yamaha E-SEQ Disk")

    def _album_subfolder_option_applies(self):
        checkbox = getattr(self, "album_subfolder_checkbox", None)
        return bool(
            checkbox is not None
            and checkbox.isChecked()
            and self._album_subfolder_option_should_show()
        )

    def _destination_with_album_subfolder(self, dest_dir):
        if not self._album_subfolder_option_applies():
            return dest_dir
        return os.path.join(dest_dir, self._album_subfolder_name())

    def _disk_content_label(self):
        return "E-SEQ" if self.imageEseqMode else "MIDI"

    def _disk_mode_banner_headline(self):
        if self.image_session is None:
            return "Image Mode"
        return f"{self.image_session.mode_name} ({self._disk_content_label()})"

    def _clear_regular_list_state(self):
        self.table.setRowCount(0)
        self.pendingEdits.clear()
        self.listedFileInfo.clear()
        self.regularEseqMode = False
        self.regularTitlesLikelyCentered = False
        self.regularHasPianodir = False
        self.regularPianodirPopulated = False
        self.regularPianodirSourcePath = ""
        self.loadedRegularPianodirMetadata = PianodirMetadata()
        self.loadedRegularEseqPaths = tuple()
        self.pendingGeneratePianodir = False
        self.pendingExportPianodirMetadata = PianodirMetadata()
        if hasattr(self, "imagePianodirTitleEdit"):
            self.imagePianodirTitleEdit.clear()
        if hasattr(self, "imagePianodirCatalogEdit"):
            self.imagePianodirCatalogEdit.clear()
        self.pendingRegularConversions.clear()

    def _set_listed_file_info(self, full_path, *, title_mode="", midi_type="", is_midi=False, order_key=b""):
        self.listedFileInfo[full_path] = {
            "title_mode": title_mode or "",
            "midi_type": midi_type or "",
            "is_midi": bool(is_midi),
            "order_key": normalize_eseq_order_key(order_key),
        }

    def _listed_file_info(self, full_path):
        return self.listedFileInfo.get(full_path, {})

    def _listed_file_title_mode(self, full_path):
        return self._listed_file_info(full_path).get("title_mode", "")

    def _listed_file_order_key(self, full_path):
        return normalize_eseq_order_key(self._listed_file_info(full_path).get("order_key", b""))

    def is_local_eseq_mode(self):
        return self.image_session is None and self.regularEseqMode

    def _regular_file_rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            rows.append(row)
        return rows

    def _regular_file_count(self):
        return len(self.listedFileInfo)

    def _pending_regular_conversion(self, full_path):
        return self.pendingRegularConversions.get(full_path, {})

    def _regular_source_material_path(self, full_path):
        conversion = self._pending_regular_conversion(full_path)
        temp_path = conversion.get("temp_path")
        if temp_path:
            return temp_path
        return full_path

    def _regular_output_filename_for_path(self, full_path):
        conversion = self._pending_regular_conversion(full_path)
        target_filename = conversion.get("target_filename")
        if target_filename:
            return target_filename
        return os.path.basename(full_path)

    def _regular_row_output_filename(self, row):
        filename_item = self.table.item(row, 3)
        if filename_item is not None and filename_item.text().strip():
            return filename_item.text().strip()
        full_path_item = self.table.item(row, 1)
        if full_path_item is None:
            return "Untitled"
        return self._regular_output_filename_for_path(full_path_item.text())

    def _current_regular_eseq_paths(self):
        paths = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) == "eseq":
                paths.append(full_path)
        return tuple(sorted(paths, key=str.upper))

    def _regular_eseq_rows(self):
        rows = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            if self._listed_file_title_mode(full_path_item.text()) == "eseq":
                rows.append(row)
        return rows

    def _image_eseq_rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            image_path = path_item.text()
            if self._is_eseq_candidate(self._final_image_path(image_path), is_midi=self._image_path_is_midi(image_path)):
                rows.append(row)
        return rows

    def _current_eseq_rows(self):
        if self.is_image_mode():
            return self._image_eseq_rows()
        return self._regular_eseq_rows()

    def _supports_eseq_reordering(self):
        if self.is_local_eseq_mode():
            return True
        if not self.is_image_mode():
            return False
        return self.imageEseqMode or (self.imageHasPianodir and not self.pendingDeletePianodir)

    def _row_eseq_order_key(self, row):
        path_item = self.table.item(row, 1)
        if path_item is None:
            return b""
        path = path_item.text()
        if self.is_image_mode():
            order_key = self._image_path_order_key(path)
            fallback_path = self._final_image_path(path)
        else:
            order_key = self._listed_file_order_key(path)
            fallback_path = path
        return order_key or build_eseq_order_key_from_path(fallback_path, sort_last=True)

    def _current_eseq_order_keys(self):
        return [self._row_eseq_order_key(row) for row in self._current_eseq_rows()]

    def _eseq_order_changed(self):
        if not self._supports_eseq_reordering():
            return False
        current_keys = self._current_eseq_order_keys()
        return len(current_keys) >= 2 and current_keys != sorted(current_keys)

    def _regular_eseq_order_key_edits(self):
        rows = self._regular_eseq_rows()
        sorted_keys = sorted(self._row_eseq_order_key(row) for row in rows)
        edits = {}
        for row, assigned_key in zip(rows, sorted_keys):
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            full_path = path_item.text()
            current_key = self._listed_file_order_key(full_path)
            if current_key != assigned_key:
                edits[full_path] = assigned_key
        return edits

    def _image_eseq_order_key_edits(self):
        rows = self._image_eseq_rows()
        sorted_keys = sorted(self._row_eseq_order_key(row) for row in rows)
        edits = {}
        for row, assigned_key in zip(rows, sorted_keys):
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            image_path = path_item.text()
            current_key = self._image_path_order_key(image_path)
            if current_key != assigned_key:
                edits[image_path] = assigned_key
        return edits

    def _selected_table_row(self):
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selected_rows = selection_model.selectedRows()
            if selected_rows:
                return selected_rows[0].row()
        current_row = self.table.currentRow()
        if current_row >= 0:
            return current_row
        return -1

    def _neighbor_eseq_row(self, row, direction):
        rows = self._current_eseq_rows()
        if row not in rows:
            return -1
        index = rows.index(row) + direction
        if 0 <= index < len(rows):
            return rows[index]
        return -1

    def _refresh_eseq_reorder_buttons(self):
        if not hasattr(self, "moveEseqUpButton") or not hasattr(self, "eseqReorderWidget"):
            return
        should_show = self._supports_eseq_reordering()
        self.eseqReorderWidget.setVisible(should_show)
        if not should_show:
            self.moveEseqUpButton.setEnabled(False)
            self.moveEseqDownButton.setEnabled(False)
            return

        row = self._selected_table_row()
        self.moveEseqUpButton.setEnabled(self._neighbor_eseq_row(row, -1) >= 0)
        self.moveEseqDownButton.setEnabled(self._neighbor_eseq_row(row, 1) >= 0)

    def _move_table_row(self, source_row, target_row):
        if source_row == target_row or source_row < 0 or target_row < 0:
            return
        column_count = self.table.columnCount()
        saved_items = [self.table.takeItem(source_row, column) for column in range(column_count)]
        if source_row < target_row:
            for row in range(source_row, target_row):
                for column in range(column_count):
                    self.table.setItem(row, column, self.table.takeItem(row + 1, column))
        else:
            for row in range(source_row, target_row, -1):
                for column in range(column_count):
                    self.table.setItem(row, column, self.table.takeItem(row - 1, column))
        for column, item in enumerate(saved_items):
            self.table.setItem(target_row, column, item)

    def move_selected_eseq_row(self, direction):
        if direction not in {-1, 1} or not self._supports_eseq_reordering():
            return
        source_row = self._selected_table_row()
        target_row = self._neighbor_eseq_row(source_row, direction)
        if target_row < 0:
            return

        self.table.setSortingEnabled(False)
        self._move_table_row(source_row, target_row)
        moved_path_item = self.table.item(target_row, 1)
        moved_path = moved_path_item.text() if moved_path_item is not None else ""
        if self.is_local_eseq_mode():
            self._refresh_regular_pianodir_row()
        else:
            self._refresh_pianodir_row()
        if moved_path:
            for row in range(self.table.rowCount()):
                path_item = self.table.item(row, 1)
                if path_item is not None and path_item.text() == moved_path:
                    self.table.setCurrentCell(row, 4)
                    break
        self._refresh_eseq_reorder_buttons()
        direction_text = "earlier" if direction < 0 else "later"
        self.status_label.setText(f"Moved the selected E-SEQ file {direction_text} in the playback order.")

    def _image_song_file_count(self):
        count = 0
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is not None:
                count += 1
        return count

    def _current_eseq_file_count(self):
        if self.is_image_mode():
            return self._image_song_file_count()
        return self._regular_file_count()

    def _warn_eseq_file_limit(self, projected_count, *, action_text):
        QMessageBox.warning(
            self,
            "Too Many E-SEQ Files",
            (
                f"Yamaha E-SEQ supports at most {self.ESEQ_FILE_LIMIT} files per disk or set.\n\n"
                f"{action_text} would leave {projected_count} files, which exceeds that limit."
            ),
        )

    def _ensure_eseq_file_limit(self, projected_count, *, action_text):
        if projected_count <= self.ESEQ_FILE_LIMIT:
            return True
        self._warn_eseq_file_limit(projected_count, action_text=action_text)
        return False

    def _regular_pianodir_path(self, base_dir=None):
        target_dir = os.path.abspath(base_dir or self.regularModeContextPath or os.path.expanduser("~"))
        return os.path.join(target_dir, PIANODIR_FILENAME)

    def _existing_regular_pianodir_path(self):
        source_path = os.path.abspath(self.regularPianodirSourcePath) if self.regularPianodirSourcePath else ""
        if source_path and os.path.isfile(source_path):
            return source_path
        candidate = self._regular_pianodir_path()
        if os.path.isfile(candidate):
            return candidate
        return ""

    def _existing_image_pianodir_host_path(self):
        if self.image_session is None or not self.imageHasPianodir or self.pendingDeletePianodir:
            return ""
        for image_path in self.imageEntriesByPath:
            if is_pianodir_path(image_path):
                try:
                    return self.image_session.extract_file(image_path)
                except Exception:
                    return ""
        return ""

    def _image_pianodir_needs_refresh(self):
        if not self.imageEseqMode or not self.imageHasPianodir or self.pendingDeletePianodir:
            return False
        return bool(
            self.pendingImageRenames
            or self.pendingImageTitleEdits
            or self.pendingImageDeletes
            or self.pendingImageAdditions
            or self.pendingImageReplacements
            or self._eseq_order_changed()
            or self._image_pianodir_metadata_changed()
        )

    def _regular_pianodir_needs_refresh(self, *, for_export=False):
        if not self.regularEseqMode or not self.regularHasPianodir:
            return False
        if self._regular_pianodir_metadata_changed():
            return True
        if any(self._listed_file_title_mode(path) == "eseq" for path in self.pendingEdits):
            return True
        if self._eseq_order_changed():
            return True
        if for_export and self._current_regular_eseq_paths() != self.loadedRegularEseqPaths:
            return True
        return False

    def _refresh_regular_eseq_mode(self):
        has_eseq_rows = any(
            info.get("title_mode") == "eseq"
            for info in self.listedFileInfo.values()
        )
        self.regularEseqMode = self.regularHasPianodir or has_eseq_rows
        if not self.regularEseqMode:
            self.pendingGeneratePianodir = False

    def _populate_regular_pianodir_row(self, row):
        row_items = []
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(row, column, item)
            row_items.append(item)

        is_present = self.regularHasPianodir and self.regularPianodirPopulated
        is_missing = self.regularEseqMode and not is_present
        refresh_on_save = self._should_generate_pianodir()
        title_text = "Present - will refresh on save" if (is_present and refresh_on_save) else ("Present" if is_present else "")
        if is_missing and self.pendingGeneratePianodir:
            title_text = "Missing - will generate on save"
        elif is_missing:
            title_text = "Missing - click to generate"

        row_items[0].setText("")
        row_items[0].setToolTip("PIANODIR.FIL is managed automatically.")
        row_items[1].setText(PIANODIR_ROW_PATH)
        row_items[2].setText("")
        row_items[2].setToolTip("PIANODIR.FIL is managed automatically.")
        row_items[3].setText(PIANODIR_FILENAME)
        row_items[3].setToolTip("Directory file for Yamaha E-SEQ folders.")
        row_items[4].setText(title_text)
        if is_missing:
            row_items[4].setToolTip("Click to offer PIANODIR.FIL generation.")
        elif refresh_on_save:
            row_items[4].setToolTip("PIANODIR.FIL will be refreshed on save because related E-SEQ metadata has changed.")
        else:
            row_items[4].setToolTip("PIANODIR.FIL is present and will be left unchanged unless E-SEQ metadata changes.")
        row_items[5].setText("")
        row_items[5].setToolTip("Not applicable.")
        row_items[6].setText("DIR")
        row_items[6].setTextAlignment(Qt.AlignCenter)
        row_items[6].setToolTip("Special Yamaha E-SEQ directory file.")

        bg_color, fg_color = self._pianodir_row_colors(is_present)
        for item in row_items:
            item.setBackground(bg_color)
            item.setForeground(fg_color)

    def _refresh_regular_pianodir_row(self):
        self._refresh_regular_eseq_mode()
        row = self._find_pianodir_row()

        if not self.regularEseqMode:
            if row >= 0:
                self.table.removeRow(row)
            if not self.is_image_mode():
                self._apply_midi_mode_ui()
            return

        if row < 0:
            self.table.insertRow(0)
            row = 0
        elif row != 0:
            self.table.removeRow(row)
            self.table.insertRow(0)
            row = 0

        self._populate_regular_pianodir_row(row)
        if not self.is_image_mode():
            self._apply_local_eseq_mode_ui()

    def _probe_regular_file(self, file_path):
        title = ""
        title_mode = ""
        is_midi = is_midi_file(file_path)
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        midi_type = ext.upper() if ext else "File"
        order_key = b""

        if is_eseq_file(file_path) and has_eseq_title_metadata(file_path):
            title = extract_eseq_title_from_file(file_path)
            title_mode = "eseq"
            if title.startswith("Error:"):
                title = ""
            try:
                order_key = read_eseq_order_key_from_file(file_path)
            except Exception:
                order_key = build_eseq_order_key_from_path(file_path)
            eseq_kind = "ESQ" if ext == "esq" else "FIL"
            try:
                arrangement_type = read_eseq_arrangement_type_label_from_file(file_path)
            except Exception:
                arrangement_type = ""
            try:
                write_protected = read_eseq_write_protect_from_file(file_path)
            except Exception:
                write_protected = None
            midi_type = eseq_type_display_label(eseq_kind, arrangement_type, write_protected)

        if is_midi:
            if title_mode != "eseq":
                title = extract_first_title_from_midi(file_path)
                if title.startswith("Error:"):
                    title = ""
                title_mode = "midi"
            if title_mode != "eseq":
                midi_type = extract_midi_type_label_from_midi(file_path)

        return title, midi_type, title_mode, is_midi, order_key

    def _load_regular_files(self, file_paths, status_text):
        self.table.setSortingEnabled(False)
        self._clear_regular_list_state()
        self._set_regular_mode_context(file_paths=file_paths)
        regular_specs = []
        loaded_pianodir_metadata = PianodirMetadata()
        for full_path in sorted(file_paths, key=lambda path: (os.path.basename(path).upper(), path.upper())):
            if os.path.basename(full_path).upper() == PIANODIR_FILENAME:
                self.regularHasPianodir = True
                self.regularPianodirSourcePath = full_path
                try:
                    self.regularPianodirPopulated = self.regularPianodirPopulated or pianodir_is_populated(
                        os.path.getsize(full_path)
                    )
                except OSError:
                    pass
                try:
                    loaded_pianodir_metadata = read_pianodir_metadata_from_file(full_path)
                except Exception:
                    loaded_pianodir_metadata = PianodirMetadata()
                continue
            title, midi_type, title_mode, _, order_key = self._probe_regular_file(full_path)
            regular_specs.append(
                (
                    full_path,
                    os.path.basename(full_path),
                    title,
                    midi_type,
                    title_mode,
                    order_key,
                )
            )

        self.loadedRegularEseqPaths = tuple(
            sorted(
                (
                    full_path
                    for full_path, _filename, _title, _midi_type, title_mode, _order_key in regular_specs
                    if title_mode == "eseq"
                ),
                key=str.upper,
            )
        )

        self.regularEseqMode = self.regularHasPianodir or any(spec[4] == "eseq" for spec in regular_specs)
        if self.regularEseqMode:
            self._enable_disklavier_screen_format_option()
            regular_specs.sort(
                key=lambda spec: (
                    0 if spec[4] == "eseq" else 1,
                    spec[5] if spec[4] == "eseq" else b"",
                    spec[1].upper(),
                    spec[0].upper(),
                )
            )
        if self.regularEseqMode:
            self._apply_local_eseq_mode_ui()
        else:
            self._apply_midi_mode_ui()

        self._update_regular_centered_title_assumption(
            candidate_titles=[
                spec[2]
                for spec in regular_specs
                if spec[2] and spec[4] in {"midi", "eseq"}
            ]
        )
        for full_path, filename, title, midi_type, title_mode, order_key in regular_specs:
            self.add_table_row(
                full_path,
                filename,
                title,
                midi_type,
                title_mode=title_mode,
                order_key=order_key,
            )

        if self.regularEseqMode:
            self._set_loaded_regular_pianodir_metadata(loaded_pianodir_metadata)
            self._refresh_regular_pianodir_row()
        else:
            self._set_loaded_regular_pianodir_metadata(PianodirMetadata())
            self.table.setSortingEnabled(True)
            self.table.sortItems(3, order=Qt.AscendingOrder)
        self._refresh_regular_title_display_items()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()
        self.status_label.setText(status_text)

    def _refresh_regular_mode_action_state(self):
        if self.is_image_mode():
            self._refresh_eseq_reorder_buttons()
            return

        row_count = self._regular_file_count()
        midi_count = 0
        eseq_count = 0
        unknown_count = 0
        for full_path, info in self.listedFileInfo.items():
            title_mode = info.get("title_mode", "")
            if title_mode == "midi":
                midi_count += 1
            elif title_mode == "eseq":
                eseq_count += 1
            else:
                unknown_count += 1

        has_only_midi = row_count > 0 and midi_count == row_count
        has_only_eseq = row_count > 0 and eseq_count == row_count
        rename_needed = has_only_midi and self._regular_filenames_need_dos83_rename()
        type0_needed = has_only_midi and self._regular_midi_files_need_type0_conversion()
        if self.is_local_eseq_mode():
            self._set_rename_all_enabled(False, "Rename 8.3 is available for MIDI folders only.")
            self._set_type0_enabled(False, "SMF1 -> SMF0 is available for MIDI folders only.")
        else:
            self._set_rename_all_enabled(rename_needed)
            self._set_type0_enabled(type0_needed)
        self.convertMidiToEseqButton.setEnabled(has_only_midi)
        self.convertEseqToMidiButton.setEnabled(has_only_eseq)

        if row_count == 0:
            self._set_rename_all_enabled(False, "Add MIDI files before using Rename 8.3.")
            self._set_type0_enabled(False, "Add MIDI files before using SMF1 -> SMF0.")
            self.convertMidiToEseqButton.setEnabled(False)
            self.convertEseqToMidiButton.setEnabled(False)
        elif unknown_count:
            self._set_rename_all_enabled(False, "Rename 8.3 is available only when all listed files are MIDI files.")
            self._set_type0_enabled(False, "SMF1 -> SMF0 is available only when all listed files are MIDI files.")
        elif has_only_midi and not rename_needed:
            self._set_rename_all_enabled(False, "All listed filenames are already 8.3 length or shorter.")
            if not type0_needed:
                self._set_type0_enabled(False, "All listed MIDI files are already SMF0 / Type 0.")
        self._refresh_eseq_reorder_buttons()
        self._update_menu_actions()

    def _filename_needs_dos83_rename(self, filename):
        name = os.path.basename(filename or "")
        if not name:
            return False
        stem, ext = os.path.splitext(name)
        if not stem:
            return False
        if "." in stem:
            return True
        extension = ext[1:] if ext.startswith(".") else ext
        return len(stem) > 8 or len(extension) > 3

    def _regular_filenames_need_dos83_rename(self):
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            if self._listed_file_title_mode(full_path_item.text()) != "midi":
                continue
            if self._filename_needs_dos83_rename(os.path.basename(full_path_item.text())):
                return True
        return False

    def _row_midi_type_label(self, row, full_path=""):
        type_item = self.table.item(row, 6)
        if type_item is not None:
            label = type_item.text().strip()
            if label:
                return label
        if full_path:
            return extract_midi_type_label_from_midi(full_path)
        return ""

    def _regular_midi_files_need_type0_conversion(self):
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) != "midi":
                continue
            if self._row_midi_type_label(row, full_path) != "Type 0":
                return True
        return False

    def _set_rename_all_enabled(self, enabled, disabled_tooltip=""):
        self.renameAllButton.setEnabled(bool(enabled))
        if enabled:
            tooltip = "Rename every listed MIDI file to DOS 8.3 format (00.MID, 01.MID, ...)."
        else:
            tooltip = disabled_tooltip or "Rename 8.3 is not needed for the current list."
        self.renameAllButton.setToolTip(tooltip)

    def _set_type0_enabled(self, enabled, disabled_tooltip=""):
        self.convertType0Button.setEnabled(bool(enabled))
        if enabled:
            tooltip = "Convert every listed MIDI file to SMF0 / MIDI Type 0."
        else:
            tooltip = disabled_tooltip or "SMF1 -> SMF0 is not needed for the current list."
        self.convertType0Button.setToolTip(tooltip)

    def _image_mode_file_counts(self):
        midi_count = 0
        eseq_count = 0
        unknown_count = 0
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            is_midi = self._image_path_is_midi(source_path)
            if self._is_eseq_candidate(final_path, is_midi=is_midi):
                eseq_count += 1
            elif is_midi:
                midi_count += 1
            else:
                unknown_count += 1
        return midi_count, eseq_count, unknown_count

    def _refresh_image_mode_action_state(self):
        if not self.is_image_mode():
            return
        midi_count, eseq_count, unknown_count = self._image_mode_file_counts()
        row_count = midi_count + eseq_count + unknown_count
        has_only_midi = row_count > 0 and midi_count == row_count
        has_only_eseq = row_count > 0 and eseq_count == row_count
        self.convertMidiToEseqButton.setEnabled(has_only_midi)
        self.convertEseqToMidiButton.setEnabled(has_only_eseq)
        self._update_menu_actions()

    def _write_listed_file_to_path(self, source_path, new_title, dest_path, *, order_key=None):
        source_material_path = self._regular_source_material_path(source_path)
        title_mode = self._listed_file_title_mode(source_path)
        if title_mode == "eseq":
            return self._write_eseq_file_to_path(source_material_path, dest_path, title=new_title, order_key=order_key)
        if title_mode == "midi":
            return update_midi_title_to_path(source_material_path, new_title, dest_path)
        try:
            shutil.copy2(source_material_path, dest_path)
            return None
        except Exception as exc:
            return f"Error copying {os.path.basename(source_path)}: {exc}"

    def _write_eseq_file_to_path(self, source_path, dest_path, *, title=None, order_key=None):
        source_abs = os.path.abspath(source_path)
        dest_abs = os.path.abspath(dest_path)
        temp_path = ""
        try:
            if source_abs == dest_abs:
                temp_path = os.path.join(
                    os.path.dirname(dest_abs),
                    f".{os.path.basename(dest_abs)}.aps_{uuid.uuid4().hex}",
                )
                if title is not None:
                    error_msg = update_eseq_title_to_path(source_path, title, temp_path)
                else:
                    shutil.copy2(source_path, temp_path)
                    error_msg = None
                if error_msg:
                    return error_msg
                if order_key is not None:
                    error_msg = update_eseq_order_key(temp_path, order_key)
                    if error_msg:
                        return error_msg
                os.replace(temp_path, dest_path)
                temp_path = ""
                return None

            if title is not None:
                error_msg = update_eseq_title_to_path(source_path, title, dest_path)
            else:
                shutil.copy2(source_path, dest_path)
                error_msg = None
            if error_msg:
                return error_msg
            if order_key is not None:
                return update_eseq_order_key(dest_path, order_key)
            return None
        except Exception as exc:
            return f"Error updating {os.path.basename(source_path)}: {exc}"
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _image_export_relative_parts(self, image_path):
        parts = [part for part in image_path.replace("\\", "/").split("/") if part]
        if parts:
            return parts
        fallback_name = os.path.basename(image_path) or "image-file"
        return [fallback_name]

    def _write_image_row_to_destination(self, source_path, dest_path, *, order_key=None):
        source_host_path = self._pending_or_extracted_image_path(source_path)
        final_name = os.path.basename(self._final_image_path(source_path)) or os.path.basename(dest_path)
        if not source_host_path or not os.path.isfile(source_host_path):
            raise FloppyImageError(f"Could not prepare '{final_name}' for export.")

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        pending_title = self.pendingImageTitleEdits.get(source_path)
        title_mode = self._image_path_title_mode(source_path)

        if title_mode == "eseq":
            error_msg = self._write_eseq_file_to_path(
                source_host_path,
                dest_path,
                title=pending_title if pending_title is not None else None,
                order_key=order_key,
            )
        elif pending_title and title_mode == "midi":
            error_msg = update_midi_title_to_path(source_host_path, pending_title, dest_path)
        else:
            try:
                shutil.copy2(source_host_path, dest_path)
                error_msg = None
            except Exception as exc:
                error_msg = f"Error copying {final_name}: {exc}"

        if error_msg:
            raise FloppyImageError(error_msg)

    def _build_regular_pianodir_entries(self, path_remap=None):
        entries = []
        path_remap = path_remap or {}

        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) != "eseq":
                continue

            local_path = path_remap.get(full_path, full_path)
            if not local_path or not os.path.isfile(local_path):
                continue

            display_title = self._row_raw_title(row)
            entries.append(
                PianodirTrackEntry(
                    image_path=os.path.basename(local_path),
                    local_path=local_path,
                    title=display_title,
                )
            )

        return entries

    def _write_regular_pianodir(self, *, base_dir=None, path_remap=None):
        entries = self._build_regular_pianodir_entries(path_remap=path_remap)
        if not entries:
            raise FloppyImageError("No E-SEQ files were available to build PIANODIR.FIL.")

        output_path = self._regular_pianodir_path(base_dir=base_dir)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as handle:
            handle.write(
                build_pianodir_bytes(
                    entries,
                    metadata=self._regular_pianodir_metadata_for_save(),
                )
            )
        return output_path

    def _export_image_session_files_to_folder(self, dest_dir, progress_callback=None):
        export_rows = []
        order_key_edits = self._image_eseq_order_key_edits()
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            if self._is_special_pianodir_path(source_path) or source_path in self.pendingImageDeletes:
                continue
            export_rows.append((row, source_path))

        generate_pianodir = self._should_generate_pianodir(for_export=True)
        total_steps = len(export_rows) + (1 if generate_pianodir else 0)
        total_steps = max(1, total_steps)
        output_paths = []
        pianodir_entries = []

        for index, (row, source_path) in enumerate(export_rows, start=1):
            final_image_path = self._final_image_path(source_path)
            display_name = os.path.basename(final_image_path) or final_image_path
            if progress_callback is not None:
                progress_callback(index - 1, total_steps, f"Saving {display_name}...")

            dest_path = os.path.join(dest_dir, *self._image_export_relative_parts(final_image_path))
            self._write_image_row_to_destination(
                source_path,
                dest_path,
                order_key=order_key_edits.get(source_path),
            )
            output_paths.append(dest_path)

            if self._is_eseq_candidate(final_image_path, is_midi=self._image_path_is_midi(source_path)):
                display_title = self._row_raw_title(row)
                pianodir_entries.append(
                    PianodirTrackEntry(
                        image_path=final_image_path,
                        local_path=dest_path,
                        title=display_title,
                    )
                )

        if generate_pianodir:
            if progress_callback is not None:
                progress_callback(len(export_rows), total_steps, "Generating PIANODIR.FIL...")
            pianodir_path = os.path.join(dest_dir, PIANODIR_FILENAME)
            os.makedirs(os.path.dirname(pianodir_path), exist_ok=True)
            with open(pianodir_path, "wb") as handle:
                handle.write(
                    build_pianodir_bytes(
                        pianodir_entries,
                        metadata=self._image_pianodir_metadata_for_save(),
                    )
                )
            output_paths.append(pianodir_path)
        elif self.imageHasPianodir and not self.pendingDeletePianodir:
            existing_pianodir = self._existing_image_pianodir_host_path()
            if existing_pianodir and os.path.isfile(existing_pianodir):
                pianodir_path = os.path.join(dest_dir, PIANODIR_FILENAME)
                os.makedirs(os.path.dirname(pianodir_path), exist_ok=True)
                shutil.copy2(existing_pianodir, pianodir_path)
                output_paths.append(pianodir_path)

        if progress_callback is not None:
            progress_callback(total_steps, total_steps, "Finalizing exported files...")

        return output_paths

    def _apply_midi_mode_ui(self):
        self._apply_compact_button_labels()
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "Long", "Type"])
        self.choose_button.setText("Open MIDI Folder")
        self.choose_button.setToolTip("Select a folder to scan for .mid and .midi files.")
        self.open_image_button.setEnabled(True)
        self.open_image_button.setToolTip("Open a floppy image file for editing in Image Mode.")
        self.read_floppy_button.setEnabled(True)
        self.read_floppy_button.setToolTip(
            "Read a floppy from a USB floppy drive or from a Greaseweazle-connected drive."
        )
        self.table.setToolTip(
            "Drop MIDI files here, click a Title cell to edit, or click the clipboard icon to copy a filename."
        )
        self._set_rename_all_enabled(True)
        self._set_type0_enabled(True)
        self.convertEseqToMidiButton.setEnabled(False)
        self.convertMidiToEseqButton.setEnabled(False)
        self.table.setColumnHidden(6, False)
        self.saveButton.setVisible(True)
        self.saveAsButton.setVisible(True)
        self.saveAsImageButton.setVisible(True)
        self.saveButton.setToolTip("Write pending title edits to the currently listed files.")
        self.saveAsButton.setToolTip("Save copies with current titles to a selected destination folder.")
        self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self.clearButton.setToolTip("Remove all files from the current list.")
        self._set_mode_banner("MIDI Mode", self._regular_mode_context_label())
        self._update_compat_warning_ui()
        self._update_floppy_save_option_ui()
        self._update_image_pianodir_metadata_ui()
        self._refresh_regular_mode_action_state()
        self._refresh_eseq_reorder_buttons()
        self._refresh_disk_usage_bars()
        self._resize_table_columns_to_fill()

    def _apply_local_eseq_mode_ui(self):
        self._apply_compact_button_labels()
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "Long", "Type"])
        self.choose_button.setText("Open MIDI Folder")
        self.choose_button.setToolTip("Leave E-SEQ Mode and select a folder to scan for .mid and .midi files.")
        self.open_image_button.setEnabled(True)
        self.open_image_button.setToolTip("Open a floppy image file for editing in Image Mode.")
        self.read_floppy_button.setEnabled(True)
        self.read_floppy_button.setToolTip(
            "Read a floppy from a USB floppy drive or from a Greaseweazle-connected drive."
        )
        self.table.setToolTip(
            "E-SEQ Mode: edit local MIDI and E-SEQ titles, and manage the local PIANODIR.FIL row."
        )
        self._set_rename_all_enabled(False, "Rename 8.3 is available for MIDI folders only.")
        self._set_type0_enabled(False, "SMF1 -> SMF0 is available for MIDI folders only.")
        self.convertEseqToMidiButton.setEnabled(True)
        self.convertMidiToEseqButton.setEnabled(True)
        self.table.setColumnHidden(6, False)
        self.table.setSortingEnabled(False)
        self.saveButton.setVisible(True)
        self.saveAsButton.setVisible(True)
        self.saveAsImageButton.setVisible(True)
        self.saveButton.setToolTip("Write pending title edits to the currently listed local files and update PIANODIR.FIL.")
        self.saveAsButton.setToolTip("Save local E-SEQ files and PIANODIR.FIL to a selected destination folder.")
        self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self.clearButton.setToolTip("Remove all files from the current E-SEQ list.")
        self._set_mode_banner("E-SEQ Mode", self._regular_mode_context_label())
        self._update_compat_warning_ui()
        self._update_floppy_save_option_ui()
        self._update_image_pianodir_metadata_ui()
        self._refresh_regular_mode_action_state()
        self._refresh_eseq_reorder_buttons()
        self._refresh_disk_usage_bars()
        self._resize_table_columns_to_fill()

    def _load_midi_paths_into_list(self, midi_specs, status_text):
        self.table.setSortingEnabled(False)
        self._clear_regular_list_state()
        self._set_regular_mode_context(file_paths=[spec[0] for spec in midi_specs])
        self._apply_midi_mode_ui()

        sorted_specs = sorted(
            midi_specs,
            key=lambda spec: (spec[1].upper(), spec[0].upper()),
        )
        self._update_regular_centered_title_assumption(
            candidate_titles=[spec[2] for spec in sorted_specs if spec[2]]
        )

        for full_path, filename, title, midi_type in sorted_specs:
            self.add_table_row(full_path, filename, title, midi_type, title_mode="midi")

        self.table.setSortingEnabled(True)
        self.table.sortItems(3, order=Qt.AscendingOrder)
        self._refresh_regular_title_display_items()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()
        self.status_label.setText(status_text)

    def _apply_image_mode_ui(self):
        self._apply_compact_button_labels()
        mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        mode_banner = self._disk_mode_banner_headline()
        self.table.setHorizontalHeaderLabels(["X", "ImagePath", "📋", "Filename", "Title", "Long", "Type"])
        self.choose_button.setText("Open MIDI Folder")
        self.choose_button.setToolTip(f"Leave {mode_name} and select a folder to scan for .mid and .midi files.")
        self.open_image_button.setEnabled(True)
        self.open_image_button.setToolTip("Open another floppy image file for editing in Image Mode.")
        self.read_floppy_button.setEnabled(True)
        self.read_floppy_button.setToolTip(
            "Read another floppy from a USB floppy drive or a Greaseweazle-connected drive."
        )
        self.table.setToolTip(
            f"{mode_banner}: edit titles, rename files, remove rows to delete files on Save, or drop files to add them."
        )
        self._set_rename_all_enabled(False, "Rename 8.3 is available for MIDI folders only.")
        self._set_type0_enabled(False, "SMF1 -> SMF0 is available for MIDI folders only.")
        self.convertEseqToMidiButton.setEnabled(True)
        self.convertMidiToEseqButton.setEnabled(True)
        self.table.setColumnHidden(6, False)
        if self.image_session is not None and self.image_session.source_kind.startswith("floppy"):
            self.clearButton.setToolTip("Leave Floppy Mode and clear the current floppy list.")
        else:
            self.saveButton.setToolTip(
                "Save pending title edits, filename edits, removals, and additions back into the image."
            )
            self.clearButton.setToolTip("Leave Image Mode and clear the current image list.")
        self.saveButton.setVisible(True)
        self.saveAsButton.setVisible(True)
        self.saveAsButton.setToolTip(
            f"Save the current {mode_name.lower()}'s listed files to a destination folder and leave {mode_name}."
        )
        self.saveAsImageButton.setVisible(True)
        self.saveAsImageButton.setText("Save As Image")
        self.saveAsImageButton.setToolTip(f"Save the current {mode_name.lower()} as a separate image file.")
        self._set_mode_banner(mode_banner, self.image_session.source_name if self.image_session is not None else "")
        self._update_compat_warning_ui()
        self._update_floppy_save_option_ui()
        self._update_image_pianodir_metadata_ui()
        self._refresh_eseq_reorder_buttons()
        self._refresh_image_mode_action_state()
        self._refresh_disk_usage_bars()
        self._resize_table_columns_to_fill()

    def _image_mode_summary(self):
        if self.image_session is None:
            return ""
        listing = self.image_session.list_entries()
        return (
            f"{self._disk_mode_banner_headline()}: {self.image_session.source_name} "
            f"({self.image_session.disk_format.label}, {display_bytes(self.image_session.disk_format.size_bytes)}). "
            f"{len(listing.entries)} file(s), {display_bytes(listing.free_space)} free."
        )

    def _image_info_for_path(self, image_path):
        return self.imageFileInfo.get(image_path, {})

    def _set_image_file_info(self, image_path, *, is_midi=False, title="", midi_type="", size=0, title_mode="", order_key=b""):
        self.imageFileInfo[image_path] = {
            "is_midi": bool(is_midi),
            "title": title or "",
            "midi_type": midi_type or "",
            "size": int(size or 0),
            "title_mode": title_mode or "",
            "order_key": normalize_eseq_order_key(order_key),
        }

    def _pending_or_extracted_image_path(self, image_path):
        if image_path in self.pendingImageAdditions:
            return self.pendingImageAdditions[image_path]
        if image_path in self.pendingImageReplacements:
            return self.pendingImageReplacements[image_path]
        if self.image_session is None:
            return ""
        return self.image_session.extract_file(image_path)

    def _is_special_pianodir_path(self, image_path):
        return image_path == PIANODIR_ROW_PATH

    def _is_special_pianodir_row(self, row):
        path_item = self.table.item(row, 1)
        return bool(path_item and self._is_special_pianodir_path(path_item.text()))

    def _final_image_path(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return image_path
        return self.pendingImageRenames.get(image_path, image_path)

    def _row_final_image_path(self, row):
        path_item = self.table.item(row, 1)
        if path_item is None:
            return ""
        return self._final_image_path(path_item.text())

    def _image_path_is_midi(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return False
        info = self._image_info_for_path(image_path)
        if info:
            return bool(info.get("is_midi"))
        return os.path.splitext(image_path)[1].lower() in {".mid", ".midi"}

    def _image_path_title_mode(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return ""
        return self._image_info_for_path(image_path).get("title_mode", "")

    def _image_path_order_key(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return b""
        return normalize_eseq_order_key(self._image_info_for_path(image_path).get("order_key", b""))

    def _image_path_has_editable_title(self, image_path):
        return bool(self._image_path_title_mode(image_path))

    def _is_eseq_candidate(self, image_path, *, is_midi=None):
        if self._is_special_pianodir_path(image_path) or is_pianodir_path(image_path):
            return False
        info = self._image_info_for_path(image_path)
        if not info:
            normalized_target = image_path.replace("\\", "/").upper()
            for source_path, source_info in self.imageFileInfo.items():
                if self._final_image_path(source_path).replace("\\", "/").upper() == normalized_target:
                    info = source_info
                    break
        if info.get("title_mode") == "eseq":
            return True
        if is_midi is None:
            is_midi = self._image_path_is_midi(image_path)
        if not is_eseq_filename(image_path):
            return False
        return self.imageHasPianodir or bool(is_midi)

    def _find_pianodir_row(self):
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                return row
        return -1

    def _pianodir_row_colors(self, is_present):
        if is_dark_theme():
            if is_present:
                return QColor("#214D2E"), QColor("#E9F8EE")
            return QColor("#5A2326"), QColor("#FDEDEE")
        if is_present:
            return QColor("#D9F2D9"), QColor("#1C1C1C")
        return QColor("#FAD6D6"), QColor("#1C1C1C")

    def _update_image_eseq_mode(self):
        self.imageEseqMode = False
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                self.imageEseqMode = True
                return

    def _sync_pianodir_requirement(self):
        self._update_image_eseq_mode()
        if self.imageEseqMode:
            self.pendingDeletePianodir = False
            return
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = self.imageHasPianodir

    def _populate_pianodir_row(self, row):
        row_items = []
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(row, column, item)
            row_items.append(item)

        is_present = self.imageHasPianodir and not self.pendingDeletePianodir and self.imagePianodirPopulated
        is_missing = self.imageEseqMode and not is_present
        delete_text = ""
        refresh_on_save = self._should_generate_pianodir()
        title_text = "Present - will refresh on save" if (is_present and refresh_on_save) else ("Present" if is_present else "")
        if is_missing and self.pendingGeneratePianodir:
            title_text = "Missing - will generate on save"
        elif is_missing:
            title_text = "Missing - click to generate"

        row_items[0].setText(delete_text)
        row_items[0].setToolTip("PIANODIR.FIL is managed automatically.")
        row_items[1].setText(PIANODIR_ROW_PATH)
        row_items[2].setText("")
        row_items[2].setToolTip("PIANODIR.FIL is managed automatically.")
        row_items[3].setText(PIANODIR_FILENAME)
        row_items[3].setToolTip("Directory file for Yamaha E-SEQ disks.")
        row_items[4].setText(title_text)
        if is_missing:
            row_items[4].setToolTip("Click to offer PIANODIR.FIL generation.")
        elif refresh_on_save:
            row_items[4].setToolTip("PIANODIR.FIL will be refreshed on save because related E-SEQ metadata has changed.")
        else:
            row_items[4].setToolTip("PIANODIR.FIL is present and will be left unchanged unless E-SEQ metadata changes.")
        row_items[5].setText("")
        row_items[5].setToolTip("Not applicable.")
        row_items[6].setText("DIR")
        row_items[6].setTextAlignment(Qt.AlignCenter)
        row_items[6].setToolTip("Special Yamaha E-SEQ directory file.")

        bg_color, fg_color = self._pianodir_row_colors(is_present)
        for item in row_items:
            item.setBackground(bg_color)
            item.setForeground(fg_color)

    def _refresh_pianodir_row(self):
        self._sync_pianodir_requirement()
        should_show = (self.imageHasPianodir and not self.pendingDeletePianodir) or self.imageEseqMode
        row = self._find_pianodir_row()

        if not should_show:
            if row >= 0:
                self.table.removeRow(row)
            self._apply_image_mode_ui()
            self._update_image_pianodir_metadata_ui()
            return

        if row < 0:
            self.table.insertRow(0)
            row = 0
        elif row != 0:
            self.table.removeRow(row)
            self.table.insertRow(0)
            row = 0

        self._populate_pianodir_row(row)
        self._apply_image_mode_ui()
        self._update_image_pianodir_metadata_ui()

    def _probe_image_file(self, image_path, size, extraction_path):
        is_midi = is_midi_file(extraction_path)
        title = ""
        midi_type = self._kind_for_image_file(image_path)
        title_mode = ""
        order_key = b""
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")

        if (self._is_eseq_candidate(image_path, is_midi=is_midi) or is_eseq_file(extraction_path)) and has_eseq_title_metadata(extraction_path):
            title = extract_eseq_title_from_file(extraction_path)
            title_mode = "eseq"
            if title.startswith("Error:"):
                title = ""
            try:
                order_key = read_eseq_order_key_from_file(extraction_path)
            except Exception:
                order_key = build_eseq_order_key_from_path(image_path)
            eseq_kind = "ESQ" if ext == "esq" else "FIL"
            try:
                arrangement_type = read_eseq_arrangement_type_label_from_file(extraction_path)
            except Exception:
                arrangement_type = ""
            try:
                write_protected = read_eseq_write_protect_from_file(extraction_path)
            except Exception:
                write_protected = None
            midi_type = eseq_type_display_label(eseq_kind, arrangement_type, write_protected)

        if is_midi:
            if title_mode != "eseq":
                try:
                    title = extract_first_title_from_midi(extraction_path)
                    title_mode = "midi"
                except Exception as exc:
                    title = f"Error: {exc}"
            if title_mode != "eseq":
                try:
                    midi_type = extract_midi_type_label_from_midi(extraction_path)
                except Exception:
                    midi_type = "Error"

        self._set_image_file_info(
            image_path,
            is_midi=is_midi,
            title=title,
            midi_type=midi_type,
            size=size,
            title_mode=title_mode,
            order_key=order_key,
        )
        return is_midi, title, midi_type, title_mode, order_key

    def _make_stage_progress_callback(self, dialog):
        def callback(step, total, message):
            self._apply_stage_progress(dialog, step, total, message)

        return callback

    def _choose_floppy_source_mode(self):
        options = ["USB Floppy Drive", "Greaseweazle"]
        choice, ok = QInputDialog.getItem(
            self,
            "Choose Floppy Source",
            "Read floppy using:",
            options,
            0,
            False,
        )
        if not ok:
            return ""
        return choice

    def _choose_usb_floppy_drive(self):
        drives = list_floppy_drives()
        if not drives:
            QMessageBox.information(
                self,
                "No USB Floppy Found",
                "No 720K or 1.44M USB floppy drive was detected. Insert a disk and try again.",
            )
            return None

        labels = [drive.display_name for drive in drives]
        chosen_label, ok = QInputDialog.getItem(
            self,
            "Choose USB Floppy Drive",
            "USB floppy drive:",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        return drives[labels.index(chosen_label)]

    def _choose_greaseweazle_source(self):
        devices = list_greaseweazle_devices()
        if not devices:
            QMessageBox.information(
                self,
                "No Greaseweazle Found",
                "No Greaseweazle device was detected. Connect one and try again.",
            )
            return None

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Greaseweazle Options")
        dialog.setModal(True)
        dialog.setMinimumWidth(440)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        device_label = QLabel("Greaseweazle device:")
        layout.addWidget(device_label)

        device_combo = QComboBox(dialog)
        for device in devices:
            device_combo.addItem(device.display_name)
        layout.addWidget(device_combo)

        drive_label = QLabel("Drive:")
        layout.addWidget(drive_label)

        drive_combo = QComboBox(dialog)
        drive_options = ["A", "B", "0", "1", "2"]
        drive_combo.addItems(drive_options)
        layout.addWidget(drive_combo)

        format_label = QLabel("Editable disk format:")
        format_label.setToolTip(
            "Used for the working image import. With archival quality enabled, the raw SCP capture is converted using this format."
        )
        layout.addWidget(format_label)

        format_combo = QComboBox(dialog)
        format_labels = [f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})" for disk_format in DISK_FORMATS]
        format_combo.addItems(format_labels)
        layout.addWidget(format_combo)

        revs_label = QLabel("Read revs:")
        layout.addWidget(revs_label)

        revs_spin = QSpinBox(dialog)
        revs_spin.setRange(0, 20)
        revs_spin.setSpecialValueText("CLI default")
        revs_spin.setToolTip("Number of revolutions to read per track. Use 0 for Greaseweazle's default.")
        layout.addWidget(revs_spin)

        retries_label = QLabel("Read retries:")
        layout.addWidget(retries_label)

        retries_spin = QSpinBox(dialog)
        retries_spin.setRange(0, 20)
        retries_spin.setValue(3)
        retries_spin.setSpecialValueText("CLI default")
        retries_spin.setToolTip("Number of retries per seek-retry. Use 0 for Greaseweazle's default.")
        layout.addWidget(retries_spin)

        archival_checkbox = QCheckBox("Archival quality (raw SCP flux capture)")
        archival_checkbox.setToolTip(
            "Reads a completely raw magnetic flux capture to SCP first, without specifying a disk format."
        )
        layout.addWidget(archival_checkbox)

        archival_hint = QLabel(
            "When enabled, the Greaseweazle read is captured as raw flux in an SCP file first, then converted into the editable image using the selected format."
        )
        archival_hint.setWordWrap(True)
        layout.addWidget(archival_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        selected_device = devices[device_combo.currentIndex()]
        selected_drive = drive_options[drive_combo.currentIndex()]
        disk_format = DISK_FORMATS[format_combo.currentIndex()]
        return GreaseweazleFloppySource(
            device_path=selected_device.path,
            drive=selected_drive,
            disk_format=disk_format,
            archival_quality=archival_checkbox.isChecked(),
            revs=revs_spin.value(),
            retries=retries_spin.value(),
        )

    def _prepare_for_disk_load(self, source_label):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return False
        if self.is_image_mode() and not self._confirm_discard_image_changes():
            return False
        if not self.is_image_mode() and self.pendingEdits:
            reply = QMessageBox.question(
                self,
                "Discard Title Changes",
                f"Load {source_label} and discard pending file title changes?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        return True

    def _activate_disk_session(self, session, listing):
        old_session = self.image_session

        if old_session is not None:
            old_session.cleanup()

        self._cleanup_midi_scratch_dir()
        self.table.setSortingEnabled(False)
        self._clear_regular_list_state()
        self._reset_image_state(cleanup=False)
        self.image_session = session
        self.imageEntriesByPath = {entry.path: entry for entry in listing.entries}
        self._apply_image_mode_ui()
        self._load_image_rows(listing.entries, auto_enable_format=True)

        status = self._image_mode_summary()
        if session.repair_changed:
            status += "\n" + session.repair_note
        self.status_label.setText(status)

    def _offer_save_greaseweazle_capture(self):
        if not self.is_floppy_mode() or self.image_session.source_kind != "floppy_gw":
            return
        capture_path = getattr(self.image_session, "capture_path", None)
        capture_ext = (getattr(self.image_session, "capture_ext", "") or "").lower()
        is_archival_scp = (
            self.image_session.gw_source is not None
            and self.image_session.gw_source.archival_quality
            and capture_ext == "scp"
            and capture_path
            and os.path.isfile(capture_path)
        )
        prompt_text = "Save the imported Greaseweazle floppy as an image file now?"
        if is_archival_scp:
            prompt_text = "Save the raw archival Greaseweazle SCP flux capture now?"
        reply = QMessageBox.question(
            self,
            "Save Greaseweazle Capture",
            prompt_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        if is_archival_scp:
            drive_name = self.image_session.gw_source.drive.lower()
            default_path = os.path.join(
                os.path.expanduser("~"),
                f"gw_drive_{drive_name}_archival.scp",
            )
            output_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Raw SCP Capture",
                default_path,
                "SCP flux capture (*.scp *.SCP)",
            )
            if not output_path:
                return
            if image_extension(output_path) != "scp":
                output_path = f"{output_path}.scp"
            try:
                shutil.copy2(capture_path, output_path)
                QMessageBox.information(
                    self,
                    "SCP Capture Saved",
                    f"Raw SCP capture saved as {os.path.basename(output_path)}.",
                )
            except Exception as exc:
                QMessageBox.critical(self, "SCP Save Failed", str(exc))
            return
        self.save_image_as()

    def open_image_dialog(self):
        common_exts = ("img", "hfe", "bin")
        common_patterns = " ".join(
            pattern
            for ext in common_exts
            for pattern in (f"*.{ext}", f"*.{ext.upper()}")
        )
        all_exts = []
        seen_exts = set()
        for ext in common_exts:
            all_exts.append(ext)
            seen_exts.add(ext)
        for ext, _label in PREFERRED_OUTPUT_EXTENSIONS:
            if ext not in seen_exts:
                all_exts.append(ext)
                seen_exts.add(ext)
        all_patterns = " ".join(f"*.{ext}" for ext in all_exts)
        filters = (
            f"Common floppy images ({common_patterns});;"
            f"All supported images ({all_patterns});;"
            "All files (*)"
        )
        default_path = os.path.expanduser("~")
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Floppy Image",
            default_path,
            filters,
        )
        if not image_path:
            return
        self.load_image_file(image_path)

    def load_image_file(self, image_path, prevalidated=False):
        if not prevalidated and not self._prepare_for_disk_load("this floppy image"):
            return

        self._start_disk_load_worker(
            load_kind="image",
            source=image_path,
            progress_title="Preparing floppy image...",
            progress_total=4,
            initial_message="Preparing floppy image...",
            final_message="Loading floppy view...",
            failure_title="Image Load Failed",
        )

    def load_floppy_drive(self):
        if not self._prepare_for_disk_load("this floppy disk"):
            return

        source_mode = self._choose_floppy_source_mode()
        if not source_mode:
            return

        if source_mode == "USB Floppy Drive":
            selected_source = self._choose_usb_floppy_drive()
            if selected_source is None:
                return
            progress_title = "Reading USB Floppy"
            progress_total = 100
        else:
            selected_source = self._choose_greaseweazle_source()
            if selected_source is None:
                return
            progress_title = "Reading Floppy via Greaseweazle"
            progress_total = 4
            if selected_source.archival_quality:
                progress_title = "Reading Floppy via Greaseweazle (Archival SCP)"

        self._start_disk_load_worker(
            load_kind="floppy_usb" if source_mode == "USB Floppy Drive" else "floppy_gw",
            source=selected_source,
            progress_title=progress_title,
            progress_total=progress_total,
            initial_message=progress_title,
            final_message="Opening floppy contents...",
            failure_title="Floppy Load Failed",
            offer_greaseweazle_capture=(source_mode == "Greaseweazle"),
        )

    def _load_image_rows(self, entries, *, auto_enable_format=False):
        self.imageFileInfo.clear()
        self.imageHasPianodir = False
        self.imagePianodirPopulated = False
        self.imageEseqMode = False
        self.imageTitlesLikelyCentered = False
        loaded_pianodir_metadata = PianodirMetadata()

        pianodir_entries = [entry for entry in entries if is_pianodir_path(entry.path)]
        if pianodir_entries:
            self.imageHasPianodir = True
            self.imagePianodirPopulated = any(pianodir_is_populated(entry.size) for entry in pianodir_entries)
            for entry in pianodir_entries:
                try:
                    local_path = self.image_session.extract_file(entry.path)
                    loaded_pianodir_metadata = read_pianodir_metadata_from_file(local_path)
                    break
                except Exception:
                    loaded_pianodir_metadata = PianodirMetadata()

        row_specs = []
        for entry in entries:
            if is_pianodir_path(entry.path):
                continue

            midi_type = self._kind_for_image_file(entry.path)
            title = ""
            order_key = b""
            try:
                local_path = self.image_session.extract_file(entry.path)
                _, title, midi_type, _, order_key = self._probe_image_file(entry.path, entry.size, local_path)
            except Exception:
                self._set_image_file_info(
                    entry.path,
                    is_midi=False,
                    title="",
                    midi_type=midi_type,
                    size=entry.size,
                    title_mode="",
                    order_key=b"",
                )

            row_specs.append(
                {
                    "image_path": entry.path,
                    "filename": entry.name,
                    "size": entry.size,
                    "title": title,
                    "midi_type": midi_type,
                    "title_mode": self._image_path_title_mode(entry.path),
                    "order_key": order_key,
                }
            )

        self._update_image_centered_title_assumption(
            candidate_titles=[
                spec["title"]
                for spec in row_specs
                if spec.get("title") and spec.get("title_mode") in {"midi", "eseq"}
            ]
        )

        image_has_eseq_titles = self.imageHasPianodir or any(spec.get("title_mode") == "eseq" for spec in row_specs)
        if image_has_eseq_titles and auto_enable_format:
            self._enable_disklavier_screen_format_option()
        if image_has_eseq_titles:
            row_specs.sort(
                key=lambda spec: (
                    0 if spec.get("title_mode") == "eseq" else 1,
                    spec.get("order_key", b"") if spec.get("title_mode") == "eseq" else b"",
                    spec["filename"].upper(),
                )
            )
        else:
            row_specs.sort(key=lambda spec: spec["filename"].upper())
        for spec in row_specs:
            self.add_image_table_row(
                spec["image_path"],
                spec["filename"],
                spec["size"],
                title=spec["title"],
                midi_type=spec["midi_type"],
                order_key=spec.get("order_key", b""),
                is_pending_addition=False,
            )

        self._set_loaded_image_pianodir_metadata(loaded_pianodir_metadata)
        self._refresh_pianodir_row()
        self._resize_table_columns_to_fill()

    def _is_midi_image_path(self, image_path):
        return self._image_path_is_midi(image_path)

    def _kind_for_image_file(self, image_path):
        if self._is_special_pianodir_path(image_path) or is_pianodir_path(image_path):
            return "DIR"
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        if ext in {"mid", "midi"}:
            return "MIDI"
        if ext == "fil":
            return "FIL"
        if ext:
            return ext.upper()
        if self.is_image_mode() and self.imageHasPianodir and not self.pendingDeletePianodir:
            return "FIL"
        return "File"

    def add_image_table_row(self, image_path, filename, size, title="", midi_type="", order_key=b"", is_pending_addition=False):
        row = self.table.rowCount()
        self.table.insertRow(row)

        delete_item = QTableWidgetItem("X")
        delete_item.setTextAlignment(Qt.AlignCenter)
        delete_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        delete_item.setToolTip(
            "Cancel this pending addition."
            if is_pending_addition
            else "Remove this file from the image on Save."
        )
        self.table.setItem(row, 0, delete_item)

        path_item = QTableWidgetItem(image_path)
        path_item.setFlags(path_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 1, path_item)

        copy_item = QTableWidgetItem("📋")
        copy_item.setTextAlignment(Qt.AlignCenter)
        copy_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        copy_item.setToolTip("Copy filename to clipboard.")
        self.table.setItem(row, 2, copy_item)

        filename_item = QTableWidgetItem(filename)
        filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        filename_item.setToolTip("Double-click to rename this file inside the image.")
        self.table.setItem(row, 3, filename_item)

        title_mode = self._image_path_title_mode(image_path)
        is_midi = self._is_midi_image_path(image_path)
        raw_title = title if title != "" else (filename if title_mode == "midi" else "")
        title_item = self._make_title_item(raw_title, title_mode=title_mode, fallback_title=filename)
        self.table.setItem(row, 4, title_item)

        self._update_compat_indicator(row, raw_title)

        kind_item = QTableWidgetItem(midi_type or self._kind_for_image_file(filename))
        kind_item.setTextAlignment(Qt.AlignCenter)
        kind_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if title_mode == "eseq" or kind_item.text().startswith(("FIL", "ESQ")):
            kind_item.setToolTip("Yamaha E-SEQ type, arrangement, and write-protect information.")
        elif is_midi:
            kind_item.setToolTip("Detected MIDI file type from header bytes.")
        else:
            kind_item.setToolTip("File type from the image filename.")
        self.table.setItem(row, 6, kind_item)

    def _unique_backup_path(self, desired_path):
        if not os.path.exists(desired_path):
            return desired_path
        stem, ext = os.path.splitext(desired_path)
        counter = 2
        while True:
            candidate = f"{stem}_{counter}{ext}"
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def _get_backup_path(self, file_path):
        source_dir = os.path.dirname(os.path.abspath(file_path))
        backup_root = self.regularModeContextPath if not self.is_image_mode() else ""
        if not backup_root or not os.path.isdir(backup_root):
            backup_root = source_dir
        backup_dir = os.path.join(backup_root, "backup")
        os.makedirs(backup_dir, exist_ok=True)
        return self._unique_backup_path(os.path.join(backup_dir, os.path.basename(file_path)))

    def _get_image_backup_path(self, image_path):
        stem, ext = os.path.splitext(os.path.abspath(image_path))
        return self._unique_backup_path(f"{stem}_backup{ext}")

    def _centered_title_plain_text(self, title):
        if not title:
            return ""
        if not self._title_looks_centered(title):
            return title if len(title) > 32 else title.strip()
        padded_title = title[:32].ljust(32)
        first_half = padded_title[:16].strip()
        second_half = padded_title[16:32].strip()
        plain_text = " ".join(part for part in (first_half, second_half) if part)
        return plain_text or title.strip()

    def _centered_title_threshold(self, titled_count):
        if titled_count < 2:
            return 99
        return min(self.CENTERED_TITLE_DISK_THRESHOLD, titled_count)

    def _active_titles_likely_centered(self):
        if self.is_image_mode():
            return self.imageTitlesLikelyCentered
        return self.regularTitlesLikelyCentered

    def _update_image_centered_title_assumption(self, candidate_titles=None):
        titles = []
        if candidate_titles is not None:
            titles = [str(title) for title in candidate_titles if title]
        elif self.is_image_mode():
            for row in range(self.table.rowCount()):
                if self._is_special_pianodir_row(row):
                    continue
                raw_title = self._row_raw_title(row)
                if raw_title:
                    titles.append(raw_title)

        centered_count = sum(1 for title in titles if self._title_looks_centered(title))
        threshold = self._centered_title_threshold(len(titles))
        self.imageTitlesLikelyCentered = centered_count >= threshold
        return self.imageTitlesLikelyCentered

    def _update_regular_centered_title_assumption(self, candidate_titles=None):
        titles = []
        if candidate_titles is not None:
            titles = [str(title) for title in candidate_titles if title]
        elif not self.is_image_mode():
            for row in self._regular_file_rows():
                full_path_item = self.table.item(row, 1)
                if full_path_item is None:
                    continue
                title_mode = self._listed_file_title_mode(full_path_item.text())
                if title_mode not in {"midi", "eseq"}:
                    continue
                raw_title = self._row_raw_title(row)
                if raw_title:
                    titles.append(raw_title)

        centered_count = sum(1 for title in titles if self._title_looks_centered(title))
        threshold = self._centered_title_threshold(len(titles))
        self.regularTitlesLikelyCentered = centered_count >= threshold
        return self.regularTitlesLikelyCentered

    def _should_display_centered_title(self, raw_title, *, title_mode=""):
        if not raw_title:
            return False
        return self._title_looks_centered(raw_title)

    def _display_title_text(self, raw_title, *, title_mode="", fallback_title=""):
        if raw_title:
            return raw_title
        if title_mode == "midi":
            return fallback_title
        return ""

    def _title_item_tooltip(self, title_mode, raw_title=""):
        if title_mode == "eseq":
            tooltip = "Click to edit this E-SEQ title."
        elif title_mode == "midi":
            tooltip = "Click to edit this MIDI title."
        else:
            tooltip = "Only MIDI and E-SEQ files have editable title metadata."
        return tooltip

    def _make_title_item(self, raw_title, *, title_mode="", fallback_title=""):
        display_title = self._display_title_text(
            raw_title,
            title_mode=title_mode,
            fallback_title=fallback_title,
        )
        title_item = QTableWidgetItem(display_title)
        title_item.setFont(self.title_monospace_font)
        title_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        title_item.setData(self.TITLE_RAW_ROLE, raw_title)
        title_item.setToolTip(self._title_item_tooltip(title_mode, raw_title))
        return title_item

    def _row_raw_title(self, row):
        title_item = self.table.item(row, 4)
        if title_item is None:
            return ""
        raw_title = title_item.data(self.TITLE_RAW_ROLE)
        if raw_title is None:
            return title_item.text()
        return str(raw_title)

    def _refresh_image_title_display_items(self):
        if not self.is_image_mode():
            return
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            filename_item = self.table.item(row, 3)
            title_item = self.table.item(row, 4)
            if path_item is None or title_item is None:
                continue
            image_path = path_item.text()
            raw_title = self._row_raw_title(row)
            title_mode = self._image_path_title_mode(image_path)
            fallback_title = filename_item.text() if filename_item is not None else os.path.basename(image_path)
            title_item.setText(
                self._display_title_text(
                    raw_title,
                    title_mode=title_mode,
                    fallback_title=fallback_title,
                )
            )
            title_item.setToolTip(self._title_item_tooltip(title_mode, raw_title))

    def _refresh_regular_title_display_items(self):
        if self.is_image_mode():
            return
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            filename_item = self.table.item(row, 3)
            title_item = self.table.item(row, 4)
            if full_path_item is None or title_item is None:
                continue
            full_path = full_path_item.text()
            raw_title = self._row_raw_title(row)
            title_mode = self._listed_file_title_mode(full_path)
            fallback_title = filename_item.text() if filename_item is not None else os.path.basename(full_path)
            title_item.setText(
                self._display_title_text(
                    raw_title,
                    title_mode=title_mode,
                    fallback_title=fallback_title,
                )
            )
            title_item.setToolTip(self._title_item_tooltip(title_mode, raw_title))

    def _reapply_image_centered_title_assumption(self):
        if not self.is_image_mode():
            return
        self._update_image_centered_title_assumption()
        self._refresh_image_title_display_items()

    def _reapply_regular_centered_title_assumption(self):
        if self.is_image_mode():
            return
        self._update_regular_centered_title_assumption()
        self._refresh_regular_title_display_items()

    def _create_backup_if_enabled(self, file_path):
        if not self.backup_checkbox.isChecked():
            return None
        backup_path = self._get_backup_path(file_path)
        try:
            shutil.copy2(file_path, backup_path)
            return None
        except Exception as e:
            return f"Error creating backup for {os.path.basename(file_path)}: {str(e)}"

    def _create_image_backup_if_enabled(self, image_path):
        if not self.backup_checkbox.isChecked():
            return None
        backup_path = self._get_image_backup_path(image_path)
        try:
            shutil.copy2(image_path, backup_path)
            return None
        except Exception as e:
            return f"Error creating backup image for {os.path.basename(image_path)}: {str(e)}"

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
            if self._is_special_pianodir_row(row):
                continue
            self._update_compat_indicator(row, self._row_raw_title(row))

    def _update_midi_type_indicator(self, row, midi_type):
        indicator = QTableWidgetItem(midi_type if midi_type else "Unknown")
        indicator.setTextAlignment(Qt.AlignCenter)
        indicator.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if midi_type and midi_type.startswith(("FIL", "ESQ")):
            tooltip = "Yamaha E-SEQ type, arrangement, and write-protect information."
        elif midi_type:
            tooltip = "Detected MIDI file type from header bytes."
        else:
            tooltip = "MIDI type could not be determined for this file."
        indicator.setToolTip(tooltip)
        self.table.setItem(row, 6, indicator)

    def refresh_midi_type_indicators(self):
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                self._update_midi_type_indicator(row, "Unknown")
                continue
            info = self._listed_file_info(full_path_item.text())
            if info.get("title_mode") == "eseq":
                midi_type = info.get("midi_type") or "FIL"
            else:
                midi_type = info.get("midi_type") or extract_midi_type_label_from_midi(
                    self._regular_source_material_path(full_path_item.text())
                )
            self._update_midi_type_indicator(row, midi_type)
        self._resize_table_columns_to_fill()

    def browse_directory(self):
        leaving_image_mode = False
        if self.is_image_mode():
            if not self._confirm_discard_image_changes():
                return
            leaving_image_mode = True

        directory = QFileDialog.getExistingDirectory(self, "Open MIDI Folder")
        if directory:
            if leaving_image_mode:
                self._reset_image_state()
                self._apply_midi_mode_ui()
            self._cleanup_midi_scratch_dir()
            self.choose_button.setEnabled(False)
            self.open_image_button.setEnabled(False)
            self.read_floppy_button.setEnabled(False)
            self.table.setSortingEnabled(False)
            self._clear_regular_list_state()
            self.progressDialog = QProgressDialog("Processing MIDI files...", "Cancel", 0, 100, self)
            self._prepare_progress_dialog(self.progressDialog)
            self.worker = MidiProcessingWorker(directory)
            self.worker.progressChanged.connect(self.progressDialog.setValue)
            self.worker.fileProcessed.connect(self.add_table_row)
            self.worker.finished.connect(lambda: self.on_worker_finished(directory))
            self.worker.start()

    def on_worker_finished(self, directory):
        self.progressDialog.close()
        self.table.setSortingEnabled(True)
        self.table.sortItems(3, order=Qt.AscendingOrder)  # sort by filename (col 3)
        self._reapply_regular_centered_title_assumption()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()
        self._set_regular_mode_context(preferred_path=directory)
        self._set_mode_banner("MIDI Mode", self._regular_mode_context_label())
        self.status_label.setText(f"Selected Folder: \"{directory}\"")
        self.choose_button.setEnabled(True)
        self.open_image_button.setEnabled(True)
        self.read_floppy_button.setEnabled(True)
        self.worker = None
        gc.collect()

    def clear_list(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return
        if self.is_image_mode():
            if not self._confirm_discard_image_changes():
                return
            self._clear_regular_list_state()
            self._reset_image_state()
            self._apply_midi_mode_ui()
            self.status_label.setText("Image Mode closed.")
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

        self._clear_regular_list_state()
        self._refresh_regular_mode_action_state()
        self._cleanup_midi_scratch_dir()
        self.status_label.setText("List cleared.")

    def _apply_path_remap(self, old_to_new):
        if not old_to_new:
            return
        self.pendingEdits = {
            old_to_new.get(path, path): title
            for path, title in self.pendingEdits.items()
        }
        self.listedFileInfo = {
            old_to_new.get(path, path): info
            for path, info in self.listedFileInfo.items()
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

        row_count = self._regular_file_count()
        if row_count == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return

        all_paths = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if not full_path_item:
                continue
            all_paths.append(full_path_item.text())

        if not all_paths:
            QMessageBox.information(self, "No Valid Files", "No valid files are currently listed.")
            return
        if not self._regular_filenames_need_dos83_rename():
            QMessageBox.information(
                self,
                "Rename Not Needed",
                "All listed filenames are already 8.3 length or shorter.",
            )
            self._refresh_regular_mode_action_state()
            return

        message = (
            f"Rename all {len(all_paths)} listed file(s) to DOS 8.3 format?\n"
            "This applies 00/01/... prefixes and a .MID extension."
        )
        if self.backup_checkbox.isChecked():
            message += "\n\nBackups will be created in a backup folder beside the source files."
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
        self._refresh_regular_mode_action_state()

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
                "Backup recommendation: enable \"Back up before saving\" before running this utility."
            )
        )
        warning_box.setInformativeText(
            f"{backup_hint}\n\nDo you want to continue?"
        )
        dont_show_checkbox = QCheckBox("Do not show this warning again")
        warning_box.setCheckBox(dont_show_checkbox)
        warning_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        warning_box.setDefaultButton(QMessageBox.No)
        result = self._exec_child_dialog(warning_box)
        confirmed = result == QMessageBox.Yes
        if confirmed and dont_show_checkbox.isChecked():
            self.settings.setValue(self.SETTING_SKIP_TYPE0_WARNING, True)
        return confirmed

    def convert_all_to_type0(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return

        row_count = self._regular_file_count()
        if row_count == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return

        all_paths = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if not full_path_item:
                continue
            all_paths.append(full_path_item.text())

        if not all_paths:
            QMessageBox.information(self, "No Valid Files", "No valid files are currently listed.")
            return
        if not self._regular_midi_files_need_type0_conversion():
            QMessageBox.information(
                self,
                "Conversion Not Needed",
                "All listed MIDI files are already SMF0 / Type 0.",
            )
            self._refresh_regular_mode_action_state()
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
        self._refresh_regular_mode_action_state()

        if failed_count:
            max_rows = 10
            details = "\n".join(
                f"{os.path.basename(path)}: {error}"
                for path, error in result.failed[:max_rows]
            )
            if failed_count > max_rows:
                details += f"\n...and {failed_count - max_rows} more."
            QMessageBox.warning(self, "Type 0 Conversion Issues", details)

    def _converted_image_path_for_kind(self, image_path, target_kind):
        directory = os.path.dirname(image_path).replace("\\", "/")
        stem = os.path.splitext(os.path.basename(image_path))[0] or os.path.basename(image_path) or "FILE"
        extension = ".MID" if target_kind == "midi" else ".FIL"
        return self._join_image_path(directory, f"{stem.upper()}{extension}")

    def _image_row_current_title(self, row):
        return self._row_raw_title(row)

    def _apply_image_row_conversion(
        self,
        row,
        source_path,
        target_path,
        replacement_host_path,
        *,
        title,
        midi_type,
        is_midi,
        title_mode,
        size,
        order_key,
    ):
        path_item = self.table.item(row, 1)
        filename_item = self.table.item(row, 3)
        if path_item is None or filename_item is None:
            return

        info_key = source_path
        if source_path in self.pendingImageAdditions:
            self.pendingImageAdditions.pop(source_path, None)
            self.pendingImageAdditions[target_path] = replacement_host_path
            self.pendingImageReplacements.pop(source_path, None)
            self.pendingImageTitleEdits.pop(source_path, None)
            if source_path in self.imageFileInfo:
                self.imageFileInfo[target_path] = self.imageFileInfo.pop(source_path)
            path_item.setText(target_path)
            info_key = target_path
        else:
            self.pendingImageReplacements[source_path] = replacement_host_path
            self.pendingImageTitleEdits.pop(source_path, None)
            if target_path.upper() == source_path.upper():
                self.pendingImageRenames.pop(source_path, None)
            else:
                self.pendingImageRenames[source_path] = target_path

        display_filename = os.path.basename(target_path)
        filename_item.setText(display_filename)
        raw_title = title if title != "" else (display_filename if title_mode == "midi" else "")
        title_item = self._make_title_item(raw_title, title_mode=title_mode, fallback_title=display_filename)
        self.table.setItem(row, 4, title_item)

        self._set_image_file_info(
            info_key,
            is_midi=is_midi,
            title=title,
            midi_type=midi_type,
            size=size,
            title_mode=title_mode,
            order_key=order_key,
        )
        self._reapply_image_centered_title_assumption()
        kind_item = self.table.item(row, 6)
        if kind_item is None:
            kind_item = QTableWidgetItem("")
            kind_item.setTextAlignment(Qt.AlignCenter)
            kind_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, 6, kind_item)
        kind_item.setText(midi_type or self._kind_for_image_file(target_path))
        kind_item.setToolTip(
            "Detected MIDI file type from header bytes."
            if is_midi
            else "File type from the image filename."
        )
        self._update_compat_indicator(row, raw_title)

    def _queue_image_format_conversion(self, row, target_kind):
        if self.image_session is None:
            raise EseqConversionError("No image or floppy is currently loaded.")
        if self._is_special_pianodir_row(row):
            raise EseqConversionError("PIANODIR.FIL is managed automatically.")

        path_item = self.table.item(row, 1)
        if path_item is None:
            raise EseqConversionError("Could not locate the selected image file.")

        source_path = path_item.text()
        current_path = self._row_final_image_path(row)
        current_title = self._image_row_current_title(row)
        target_path = self._converted_image_path_for_kind(current_path, target_kind)
        if target_path.upper() in self._active_image_paths(exclude_row=row):
            raise EseqConversionError(f"'{os.path.basename(target_path)}' already exists in this image folder.")

        source_host_path = self._pending_or_extracted_image_path(source_path)
        if not source_host_path or not os.path.isfile(source_host_path):
            raise EseqConversionError(f"Could not prepare '{os.path.basename(current_path)}' for conversion.")

        output_host_path = os.path.join(
            self.image_session.patched_dir,
            f"{uuid.uuid4().hex}_{os.path.basename(target_path)}",
        )
        title_override = current_title or None
        if target_kind == "midi":
            convert_eseq_file_to_midi_path(source_host_path, output_host_path, title_override=title_override)
        else:
            convert_midi_file_to_eseq_path(
                source_host_path,
                output_host_path,
                title_override=title_override,
                filename_hint=os.path.basename(target_path),
            )

        size = os.path.getsize(output_host_path)
        is_midi, title, midi_type, title_mode, order_key = self._probe_image_file(target_path, size, output_host_path)
        self._apply_image_row_conversion(
            row,
            source_path,
            target_path,
            output_host_path,
            title=title,
            midi_type=midi_type,
            is_midi=is_midi,
            title_mode=title_mode,
            size=size,
            order_key=order_key,
        )
        return os.path.basename(current_path), os.path.basename(target_path)

    def _prompt_for_eseq_to_midi_mode_switch(self, converted_count):
        saved_choice = str(
            self.settings.value(self.SETTING_ESEQ_TO_MIDI_SWITCH_MODE, "ask")
        ).strip().lower()
        if saved_choice in {"switch", "export"}:
            return True

        mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        prompt_box = QMessageBox(self)
        apply_window_icon(prompt_box)
        prompt_box.setIcon(QMessageBox.Question)
        prompt_box.setWindowTitle("Convert and Exit")
        prompt_box.setText(
            f"Convert {converted_count} E-SEQ file(s) to MIDI and leave {mode_name}?"
        )
        prompt_box.setInformativeText(
            "You will choose a destination folder next.\n"
            "Converted MIDI files will be written there and then opened in regular MIDI Mode.\n"
            "Only MIDI files are carried over."
        )
        remember_checkbox = QCheckBox("Remember my choice and do not ask again")
        prompt_box.setCheckBox(remember_checkbox)
        prompt_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt_box.setDefaultButton(QMessageBox.Yes)
        prompt_box.button(QMessageBox.Yes).setText("Convert and Exit")
        prompt_box.button(QMessageBox.No).setText("Cancel")

        should_switch = self._exec_child_dialog(prompt_box) == QMessageBox.Yes
        if remember_checkbox.isChecked():
            self.settings.setValue(
                self.SETTING_ESEQ_TO_MIDI_SWITCH_MODE,
                "export" if should_switch else "ask",
            )
        return should_switch

    def _choose_eseq_to_midi_export_directory(self):
        mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        default_dir = os.path.expanduser("~")
        if self.image_session is not None and not self.image_session.source_kind.startswith("floppy"):
            default_dir = os.path.dirname(self.image_session.source_path) or default_dir
        return QFileDialog.getExistingDirectory(self, f"Choose {mode_name} MIDI Export Folder", default_dir)

    def _build_switched_midi_mode_files(self, conversion_rows, dest_dir):
        if self.image_session is None:
            raise EseqConversionError("No floppy image or floppy session is currently loaded.")

        conversion_rows = set(conversion_rows or [])
        midi_specs = []
        used_targets = set()
        visible_file_count = 0

        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue

            path_item = self.table.item(row, 1)
            if path_item is None:
                continue

            source_path = path_item.text()
            if source_path in self.pendingImageDeletes:
                continue
            visible_file_count += 1

            final_image_path = self._final_image_path(source_path)
            should_convert = row in conversion_rows
            should_export = should_convert or self._image_path_is_midi(source_path)
            if not should_export:
                continue

            if should_convert:
                export_image_path = self._converted_image_path_for_kind(final_image_path, "midi")
            else:
                export_image_path = final_image_path

            relative_parts = self._image_export_relative_parts(export_image_path)
            dest_path = os.path.join(dest_dir, *relative_parts)
            dest_key = os.path.normcase(dest_path)
            if dest_key in used_targets:
                raise EseqConversionError(f"'{os.path.basename(export_image_path)}' would be written more than once.")
            used_targets.add(dest_key)

            source_host_path = self._pending_or_extracted_image_path(source_path)
            if not source_host_path or not os.path.isfile(source_host_path):
                raise EseqConversionError(
                    f"Could not prepare '{os.path.basename(final_image_path)}' for MIDI export."
                )

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            current_title = self._image_row_current_title(row)
            title_override = current_title or None

            if should_convert:
                convert_eseq_file_to_midi_path(source_host_path, dest_path, title_override=title_override)
            else:
                self._write_image_row_to_destination(source_path, dest_path)

            title = extract_first_title_from_midi(dest_path)
            if title.startswith("Error:"):
                title = ""
            midi_type = extract_midi_type_label_from_midi(dest_path)
            midi_specs.append((dest_path, os.path.basename(dest_path), title, midi_type))

        omitted_count = max(0, visible_file_count - len(midi_specs))
        return midi_specs, omitted_count

    def _switch_to_midi_mode_after_eseq_conversion(self, conversion_rows):
        converted_count = len(conversion_rows)
        if converted_count <= 0 or not self._prompt_for_eseq_to_midi_mode_switch(converted_count):
            return

        dest_dir = self._choose_eseq_to_midi_export_directory()
        if not dest_dir:
            return
        export_dir = self._destination_with_album_subfolder(dest_dir)

        try:
            midi_specs, omitted_count = self._build_switched_midi_mode_files(conversion_rows, export_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Convert and Exit Failed", str(exc))
            return

        if not midi_specs:
            QMessageBox.information(
                self,
                "No MIDI Files",
                "No MIDI files were available to export into regular MIDI Mode.",
            )
            return

        source_mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        self._reset_image_state()

        status_text = (
            f"Converted {converted_count} E-SEQ file(s) to MIDI and left {source_mode_name}.\n"
            f"Current context moved to: \"{export_dir}\""
        )
        if omitted_count:
            status_text += f"\n{omitted_count} non-MIDI file(s) were not exported into MIDI Mode."
        self._load_midi_paths_into_list(midi_specs, status_text)

    def _converted_regular_filename_for_kind(self, full_path, target_kind):
        stem = os.path.splitext(os.path.basename(full_path))[0] or os.path.basename(full_path) or "FILE"
        extension = ".mid" if target_kind == "midi" else ".fil"
        return stem + extension

    def _converted_regular_path_for_kind(self, full_path, target_kind, *, output_dir=None):
        directory = output_dir or os.path.dirname(full_path)
        return os.path.join(directory, self._converted_regular_filename_for_kind(full_path, target_kind))

    def _apply_regular_row_pending_conversion(
        self,
        row,
        source_path,
        target_filename,
        temp_path,
        target_kind,
    ):
        title, midi_type, title_mode, is_midi, order_key = self._probe_regular_file(temp_path)
        self.pendingRegularConversions[source_path] = {
            "temp_path": temp_path,
            "target_kind": target_kind,
            "target_filename": target_filename,
        }
        self.pendingEdits.pop(source_path, None)
        self._set_listed_file_info(
            source_path,
            title_mode=title_mode,
            midi_type=midi_type,
            is_midi=is_midi,
            order_key=order_key,
        )

        filename_item = self.table.item(row, 3)
        if filename_item is None:
            filename_item = QTableWidgetItem(target_filename)
            filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, 3, filename_item)
        else:
            filename_item.setText(target_filename)
        filename_item.setToolTip("Pending converted filename. Use Save, Save As, or Save As Image to write it.")

        raw_title = title if title != "" else (target_filename if title_mode == "midi" else "")
        self.table.setItem(
            row,
            4,
            self._make_title_item(raw_title, title_mode=title_mode, fallback_title=target_filename),
        )
        self._update_compat_indicator(row, raw_title)
        self._update_midi_type_indicator(row, midi_type)

    def _convert_all_regular_rows(self, source_kind, target_kind):
        if self.is_image_mode():
            return False
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return True

        applicable_paths = []
        for row in range(self.table.rowCount()):
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            title_mode = self._listed_file_title_mode(full_path)
            if title_mode == source_kind:
                applicable_paths.append(full_path)

        if not applicable_paths:
            kind_label = "E-SEQ" if source_kind == "eseq" else "MIDI"
            QMessageBox.information(self, "Nothing To Convert", f"No {kind_label} files are currently listed.")
            return True

        if target_kind == "eseq" and not self._ensure_eseq_file_limit(
            len(applicable_paths),
            action_text="Converting these files to E-SEQ",
        ):
            return True

        reply = QMessageBox.question(
            self,
            f"Convert All {source_kind.upper()} to {target_kind.upper()}",
            (
                f"Convert {len(applicable_paths)} listed {source_kind.upper()} file(s) to {target_kind.upper()}?\n\n"
                "The converted files will be staged in the list only. Nothing will be written to disk until you use "
                "Save, Save As, or Save As Image."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return True

        if source_kind == "eseq" and target_kind == "midi":
            self.pendingExportPianodirMetadata = self._current_visible_pianodir_metadata()
        else:
            self.pendingExportPianodirMetadata = PianodirMetadata()

        progressDialog = QProgressDialog(
            f"Converting {source_kind.upper()} files...",
            "Cancel",
            0,
            len(applicable_paths),
            self,
        )
        self._prepare_progress_dialog(progressDialog)

        converted_count = 0
        errors = []
        scratch_dir = self._ensure_midi_scratch_dir()
        for index, full_path in enumerate(applicable_paths):
            if progressDialog.wasCanceled():
                break

            row = None
            for candidate_row in range(self.table.rowCount()):
                item = self.table.item(candidate_row, 1)
                if item is not None and item.text() == full_path:
                    row = candidate_row
                    break
            if row is None:
                continue

            target_filename = self._converted_regular_filename_for_kind(full_path, target_kind)
            output_temp_path = os.path.join(scratch_dir, f"{uuid.uuid4().hex}_{target_filename}")
            source_material_path = self._regular_source_material_path(full_path)
            current_title = self._row_raw_title(row)
            title_override = current_title or None
            try:
                if target_kind == "midi":
                    convert_eseq_file_to_midi_path(
                        source_material_path,
                        output_temp_path,
                        title_override=title_override,
                    )
                else:
                    convert_midi_file_to_eseq_path(
                        source_material_path,
                        output_temp_path,
                        title_override=title_override,
                        filename_hint=target_filename,
                    )
                self._apply_regular_row_pending_conversion(
                    row,
                    full_path,
                    target_filename,
                    output_temp_path,
                    target_kind,
                )
                converted_count += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(full_path)}: {exc}")

            progressDialog.setValue(index + 1)
            QApplication.processEvents()

        progressDialog.close()

        if target_kind == "eseq" and converted_count and not self.regularHasPianodir:
            self.pendingGeneratePianodir = True
        if converted_count and not any(
            info.get("title_mode") == "eseq"
            for info in self.listedFileInfo.values()
        ):
            self.regularHasPianodir = False
            self.regularPianodirPopulated = False
            self.regularPianodirSourcePath = ""
            self.loadedRegularPianodirMetadata = PianodirMetadata()
            self.pendingGeneratePianodir = False
        self._refresh_regular_pianodir_row()
        self._reapply_regular_centered_title_assumption()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()

        if converted_count:
            self.status_label.setText(
                f"Staged {converted_count} file(s) for {source_kind.upper()} -> {target_kind.upper()} conversion.\n"
                "Use Save, Save As, or Save As Image to write the converted files."
            )

        if errors:
            QMessageBox.warning(self, "Conversion Issues", "\n".join(errors[:10]))
        return True

    def _convert_all_image_rows(self, source_kind, target_kind):
        if not self.is_image_mode():
            QMessageBox.information(
                self,
                "Image Mode Only",
                "This conversion utility is available while editing a floppy image or floppy session.",
            )
            return
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return

        applicable_rows = []
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            if source_kind == "eseq":
                if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                    applicable_rows.append(row)
            elif self._image_path_is_midi(source_path):
                applicable_rows.append(row)

        if not applicable_rows:
            kind_label = "E-SEQ" if source_kind == "eseq" else "MIDI"
            QMessageBox.information(self, "Nothing To Convert", f"No {kind_label} files are currently listed.")
            return

        if target_kind == "eseq" and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Converting this floppy set to E-SEQ",
        ):
            return

        summary = (
            f"Queue conversion of {len(applicable_rows)} {source_kind.upper()} file(s) "
            f"to {target_kind.upper()} in the current {self.image_session.mode_name.lower()}?\n\n"
            "The converted files will stay pending until you Save."
        )
        if target_kind == "eseq":
            summary += "\n\nE-SEQ titles are limited to 32 characters. Longer titles will be truncated."
        if source_kind == "eseq":
            summary += "\n\nIf no E-SEQ files remain, PIANODIR.FIL will be removed on save."
        else:
            summary += "\n\nPIANODIR.FIL will be generated or refreshed when needed on save."
        reply = QMessageBox.question(
            self,
            f"Convert All {source_kind.upper()} to {target_kind.upper()}",
            summary,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        if source_kind == "eseq" and target_kind == "midi":
            self.pendingExportPianodirMetadata = self._current_visible_pianodir_metadata()
        else:
            self.pendingExportPianodirMetadata = PianodirMetadata()

        progressDialog = QProgressDialog(
            f"Converting {source_kind.upper()} files...",
            "Cancel",
            0,
            len(applicable_rows),
            self,
        )
        self._prepare_progress_dialog(progressDialog)

        converted = []
        errors = []
        for index, row in enumerate(applicable_rows):
            if progressDialog.wasCanceled():
                break
            try:
                converted.append(self._queue_image_format_conversion(row, target_kind))
            except Exception as exc:
                filename_item = self.table.item(row, 3)
                label = filename_item.text() if filename_item is not None else "Unknown file"
                errors.append(f"{label}: {exc}")
            progressDialog.setValue(index + 1)
            QApplication.processEvents()

        progressDialog.close()
        self._refresh_pianodir_row()

        status_parts = [f"Queued {len(converted)} file(s) for {source_kind.upper()} -> {target_kind.upper()} conversion."]
        remaining = self._pending_image_space_remaining()
        status_parts.append(f"Estimated free space after pending changes: {display_bytes(max(0, remaining))}.")
        if errors:
            status_parts.append(f"{len(errors)} file(s) could not be converted.")
        self.status_label.setText("\n".join(status_parts))

        if errors:
            QMessageBox.warning(self, "Conversion Issues", "\n".join(errors[:10]))

    def convert_all_eseq_to_midi(self):
        if self._convert_all_regular_rows("eseq", "midi"):
            return
        self._convert_all_image_rows("eseq", "midi")

    def convert_all_midi_to_eseq(self):
        if self._convert_all_regular_rows("midi", "eseq"):
            return
        self._convert_all_image_rows("midi", "eseq")

    def add_table_row(self, full_path, filename, title, midi_type="", title_mode="midi", order_key=b""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_listed_file_info(
            full_path,
            title_mode=title_mode,
            midi_type=midi_type,
            is_midi=(title_mode == "midi"),
            order_key=order_key,
        )

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
        stored_title = title if title != "" else (filename if title_mode == "midi" else "")
        title_item = self._make_title_item(stored_title, title_mode=title_mode, fallback_title=filename)
        self.table.setItem(row, 4, title_item)

        # Column 5: Compatibility indicator for titles > 32 characters
        self._update_compat_indicator(row, stored_title)

        # Column 6: MIDI type from file header bytes
        self._update_midi_type_indicator(row, midi_type)
        self._refresh_regular_mode_action_state()

    def handle_cell_clicked(self, row, column):
        if self.is_image_mode():
            self.handle_image_cell_clicked(row, column)
            return
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return

        # Column 0: remove from list
        if column == 0:
            full_path_item = self.table.item(row, 1)
            if full_path_item:
                full_path = full_path_item.text()
                self.pendingEdits.pop(full_path, None)
                self.listedFileInfo.pop(full_path, None)
            self.table.removeRow(row)
            self._reapply_regular_centered_title_assumption()
            self._refresh_regular_mode_action_state()
            self._refresh_regular_pianodir_row()
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
        if self.is_image_mode():
            self.handle_image_cell_double_clicked(row, column)
            return
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return

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

    def handle_image_cell_clicked(self, row, column):
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return
        if column == 0:
            self.remove_image_row(row)
            return
        if column == 2:
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename = filename_item.text()
                QApplication.clipboard().setText(filename)
                self.status_label.setText(f"'{filename}' copied to clipboard.")
            return
        if column == 4:
            self.edit_image_title(row)

    def handle_image_cell_double_clicked(self, row, column):
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return
        if column == 3:
            self.edit_image_filename(row)
            return
        if column == 4:
            self.edit_image_title(row)

    def _normalize_image_filename(self, filename):
        return filename.strip().upper()

    def _center_title_segment(self, text, *, enforce_limit=True):
        trimmed = text.strip()
        if enforce_limit:
            trimmed = trimmed[:16]
        field_width = max(16, len(trimmed)) if not enforce_limit else 16
        padding = field_width - len(trimmed)
        left_padding = padding // 2
        right_padding = padding - left_padding
        return (" " * left_padding) + trimmed + (" " * right_padding)

    def _compose_centered_title(self, first_text, second_text, *, enforce_limit=True):
        return self._center_title_segment(first_text, enforce_limit=enforce_limit) + self._center_title_segment(
            second_text,
            enforce_limit=enforce_limit,
        )

    def _split_title_for_center_fields(self, title, *, enforce_limit=True):
        if self._title_looks_centered(title):
            padded_title = title[:32].ljust(32)
            return padded_title[:16], padded_title[16:32]

        cleaned = title.strip()
        if not cleaned:
            return "", ""

        if enforce_limit:
            cleaned = cleaned[:32]
            if len(cleaned) <= 16:
                return self._center_title_segment(cleaned), ""

            midpoint = len(cleaned) / 2.0
            candidates = []
            for match in re.finditer(r"\s+", cleaned):
                left = cleaned[:match.start()].rstrip()
                right = cleaned[match.end():].lstrip()
                if not left or not right:
                    continue
                if len(left) > 16 or len(right) > 16:
                    continue
                candidates.append((abs(len(left) - midpoint), abs(len(left) - len(right)), left, right))
            if candidates:
                _, _, left, right = min(candidates)
                return self._center_title_segment(left), self._center_title_segment(right)

            return self._center_title_segment(cleaned[:16].strip()), self._center_title_segment(
                cleaned[16:32].strip(),
            )

        if len(cleaned) <= 16:
            return cleaned, ""

        midpoint = len(cleaned) / 2.0
        candidates = []
        for match in re.finditer(r"\s+", cleaned):
            left = cleaned[:match.start()].rstrip()
            right = cleaned[match.end():].lstrip()
            if not left or not right:
                continue
            candidates.append((abs(len(left) - midpoint), abs(len(left) - len(right)), left, right))
        if candidates:
            _, _, left, right = min(candidates)
            return left, right

        split_at = max(1, min(len(cleaned) - 1, len(cleaned) // 2))
        return cleaned[:split_at].strip(), cleaned[split_at:].strip()

    def _title_looks_centered(self, title):
        if not title or not title.strip():
            return False

        candidate = title.rstrip(" ")
        return len(candidate) < self.TITLE_COMPAT_LIMIT and candidate.startswith(" ")

    def _validate_image_filename(self, filename):
        if not filename:
            return "Filename cannot be empty."
        if filename.upper() == PIANODIR_FILENAME:
            return "PIANODIR.FIL is managed automatically."
        if filename in {".", ".."}:
            return "Filename cannot be '.' or '..'."
        if filename.endswith("."):
            return "Filename cannot end with '.'."
        if any(ch in self.IMAGE_FILENAME_INVALID_CHARS for ch in filename):
            return "Filename contains characters that are not valid in DOS/FAT names."
        if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in filename):
            return "Use printable ASCII characters only."

        stem, ext = os.path.splitext(filename)
        if not stem or stem.startswith("."):
            return "Filename must have a name before the extension."
        if "." in stem:
            return "Filename can only contain one extension separator."
        if len(stem) > 8:
            return "DOS/FAT filename base must be 8 characters or fewer."
        if ext:
            if len(ext) > 4:
                return "DOS/FAT extension must be 3 characters or fewer."
            if "." in ext[1:]:
                return "Filename can only contain one extension separator."
        if len(ext.lstrip(".")) > 3:
            return "DOS/FAT extension must be 3 characters or fewer."
        return None

    def _join_image_path(self, directory, filename):
        if directory:
            return f"{directory.rstrip('/')}/{filename}"
        return filename

    def _active_image_paths(self, exclude_row=None):
        paths = set()
        for row in range(self.table.rowCount()):
            if exclude_row is not None and row == exclude_row:
                continue
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if not path_item:
                continue
            source_path = path_item.text()
            active_path = self.pendingImageRenames.get(source_path, source_path)
            paths.add(active_path.upper())
        return paths

    def _image_entry_for_path(self, image_path):
        return self.imageEntriesByPath.get(image_path)

    def _prompt_for_image_filename(self, current_filename):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Rename Image File")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)

        prompt = QLabel("Enter new DOS filename:")
        dialog_layout.addWidget(prompt)

        editor = QLineEdit(current_filename)
        editor.setMinimumWidth(480)
        dialog_layout.addWidget(editor)

        warning_label = QLabel("")
        warning_label.setStyleSheet("color: #C62828;")
        warning_label.setVisible(False)
        dialog_layout.addWidget(warning_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        dialog_layout.addWidget(buttons)

        def update_state(text):
            normalized = self._normalize_image_filename(text)
            validation_error = self._validate_image_filename(normalized)
            unchanged = normalized == current_filename.upper()
            ok_button.setEnabled((validation_error is None and bool(normalized)) or unchanged)
            warning_label.setVisible(bool(validation_error and not unchanged))
            warning_label.setText(validation_error or "")

        editor.textChanged.connect(update_state)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        update_state(current_filename)
        editor.selectAll()
        editor.setFocus()

        if self._exec_child_dialog(dialog) == QDialog.Accepted:
            return self._normalize_image_filename(editor.text()), True
        return "", False

    def _handle_pianodir_row_clicked(self):
        if self.is_image_mode():
            eseq_mode = self.imageEseqMode
            has_pianodir = self.imageHasPianodir
            pianodir_populated = self.imagePianodirPopulated
            refresh_callback = self._refresh_pianodir_row
        elif self.is_local_eseq_mode():
            eseq_mode = self.regularEseqMode
            has_pianodir = self.regularHasPianodir
            pianodir_populated = self.regularPianodirPopulated
            refresh_callback = self._refresh_regular_pianodir_row
        else:
            return

        if not eseq_mode:
            return
        if has_pianodir and pianodir_populated:
            if self._should_generate_pianodir():
                message = "PIANODIR.FIL is present and will be refreshed on save."
            else:
                message = "PIANODIR.FIL is present and will be left unchanged unless related E-SEQ data changes."
            QMessageBox.information(
                self,
                "PIANODIR.FIL",
                message,
            )
            return
        if self.pendingGeneratePianodir:
            QMessageBox.information(
                self,
                "PIANODIR.FIL",
                "PIANODIR.FIL is missing and will be generated on save.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Generate PIANODIR.FIL",
            "Generate PIANODIR.FIL for these Yamaha E-SEQ files on save?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        self.pendingGeneratePianodir = True
        refresh_callback()
        self.status_label.setText("PIANODIR.FIL will be generated on save.")

    def _ensure_pianodir_generation_for_save(self):
        if self.is_image_mode():
            eseq_mode = self.imageEseqMode
            has_pianodir = self.imageHasPianodir
            refresh_callback = self._refresh_pianodir_row
        elif self.is_local_eseq_mode():
            eseq_mode = self.regularEseqMode
            has_pianodir = self.regularHasPianodir
            refresh_callback = self._refresh_regular_pianodir_row
        else:
            return True

        if not eseq_mode or has_pianodir or self.pendingGeneratePianodir:
            return True

        reply = QMessageBox.question(
            self,
            "Generate PIANODIR.FIL",
            "These files look like Yamaha E-SEQ files, but PIANODIR.FIL is missing.\n\n"
            "Generate PIANODIR.FIL while saving?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self.pendingGeneratePianodir = True
            refresh_callback()
        return True

    def _should_generate_pianodir(self, *, for_export=False):
        if self.is_local_eseq_mode():
            if not self.regularEseqMode:
                return False
            if self.pendingGeneratePianodir:
                return True
            return self._regular_pianodir_needs_refresh(for_export=for_export)
        return self.imageEseqMode and (
            self._image_pianodir_needs_refresh()
            or self.pendingGeneratePianodir
        )

    def _confirm_floppy_write(self):
        if self.image_session is None or not self.image_session.source_kind.startswith("floppy"):
            return True
        title = "Write USB Floppy" if self.image_session.source_kind == "floppy_usb" else "Write Greaseweazle Floppy"
        return self._confirm_with_optional_skip(
            setting_key=self.SETTING_SKIP_FLOPPY_WRITE_WARNING,
            title=title,
            message=(
                f"Save pending changes directly back to {self.image_session.source_name}?\n\n"
                "This will overwrite the floppy disk in the drive."
            ),
        )

    def edit_image_title(self, row):
        if self._is_special_pianodir_row(row):
            return
        path_item = self.table.item(row, 1)
        filename_item = self.table.item(row, 3)
        if path_item is None:
            return

        image_path = path_item.text()
        filename = filename_item.text() if filename_item else os.path.basename(image_path)
        title_mode = self._image_path_title_mode(image_path)
        if not title_mode:
            QMessageBox.information(self, "No Editable Title", "Only MIDI and E-SEQ files have editable title metadata.")
            return

        current_title = self._row_raw_title(row)
        new_title, ok = self._prompt_for_title(current_title, title_mode=title_mode)
        if not ok or not new_title.strip():
            return

        if new_title == current_title:
            return

        validation_error = validate_legacy_title_input(new_title)
        if validation_error:
            QMessageBox.warning(self, "Invalid Title", validation_error)
            return
        if title_mode == "eseq" and len(new_title.encode("latin1")) > 32:
            QMessageBox.warning(self, "Title Too Long", "E-SEQ titles must be 32 characters or fewer.")
            return

        self.pendingImageTitleEdits[image_path] = new_title
        new_title_item = self._make_title_item(new_title, title_mode=title_mode, fallback_title=filename)
        self.table.setItem(row, 4, new_title_item)
        self._update_compat_indicator(row, new_title)
        self._reapply_image_centered_title_assumption()

        warning = ""
        if self._compat_warning_is_active() and self._is_title_too_long(new_title):
            warning = f"\nCompatibility warning: over {self.TITLE_COMPAT_LIMIT} characters."
        title_kind = "E-SEQ title" if title_mode == "eseq" else "MIDI title"
        shown_title = self._display_title_text(new_title, title_mode=title_mode, fallback_title=filename)
        self.status_label.setText(
            f"Pending image change:\n{title_kind} for '{filename}' will be updated to '{shown_title}' on save.{warning}"
        )

        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)

    def edit_image_filename(self, row):
        if self._is_special_pianodir_row(row):
            return
        path_item = self.table.item(row, 1)
        current_item = self.table.item(row, 3)
        if path_item is None or current_item is None:
            return

        source_path = path_item.text()
        current_name = current_item.text()
        new_name, ok = self._prompt_for_image_filename(current_name)
        if not ok:
            return

        validation_error = self._validate_image_filename(new_name)
        if validation_error:
            QMessageBox.warning(self, "Invalid Filename", validation_error)
            return

        directory = os.path.dirname(self.pendingImageRenames.get(source_path, source_path)).replace("\\", "/")
        target_path = self._join_image_path(directory, new_name)
        current_target = self.pendingImageRenames.get(source_path, source_path)
        if target_path.upper() == current_target.upper():
            return

        if target_path.upper() in self._active_image_paths(exclude_row=row):
            QMessageBox.warning(self, "Name Already Exists", f"'{new_name}' already exists in this image folder.")
            return

        if source_path in self.pendingImageAdditions:
            host_path = self.pendingImageAdditions.pop(source_path)
            self.pendingImageAdditions[target_path] = host_path
            if source_path in self.pendingImageTitleEdits:
                self.pendingImageTitleEdits[target_path] = self.pendingImageTitleEdits.pop(source_path)
            if source_path in self.imageFileInfo:
                self.imageFileInfo[target_path] = self.imageFileInfo.pop(source_path)
            path_item.setText(target_path)
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename_item.setText(new_name)
            self.status_label.setText(f"Pending addition renamed to '{new_name}'.")
        else:
            if target_path.upper() == source_path.upper():
                self.pendingImageRenames.pop(source_path, None)
            else:
                self.pendingImageRenames[source_path] = target_path
            self.status_label.setText(
                f"Pending image rename:\n'{os.path.basename(source_path)}' will become '{new_name}' on save."
            )

        new_name_item = QTableWidgetItem(new_name)
        new_name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        new_name_item.setToolTip("Double-click to rename this file inside the image.")
        self.table.setItem(row, 3, new_name_item)
        kind_item = self.table.item(row, 6)
        if kind_item:
            info = self._image_info_for_path(path_item.text())
            if info.get("is_midi"):
                kind_item.setText(info.get("midi_type") or "MIDI")
            else:
                kind_item.setText(self._kind_for_image_file(new_name))

        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)
        self._refresh_pianodir_row()

    def remove_image_row(self, row):
        if self._is_special_pianodir_row(row):
            QMessageBox.information(self, "Managed File", "PIANODIR.FIL is managed automatically.")
            return
        path_item = self.table.item(row, 1)
        if path_item is None:
            return
        image_path = path_item.text()
        filename = os.path.basename(image_path)

        if image_path in self.pendingImageAdditions:
            self.pendingImageAdditions.pop(image_path, None)
            self.pendingImageTitleEdits.pop(image_path, None)
            self.imageFileInfo.pop(image_path, None)
            self.table.removeRow(row)
            self.status_label.setText(f"Pending addition '{filename}' canceled.")
            self._refresh_pianodir_row()
            self._reapply_image_centered_title_assumption()
            return

        container_label = "floppy disk" if self.is_floppy_mode() else "image"
        confirmed = self._confirm_with_optional_skip(
            setting_key=self.SETTING_SKIP_IMAGE_REMOVE_WARNING,
            title=f"Remove File From {container_label.title()}",
            message=(
                f"Remove '{filename}' from the listed files?\n\n"
                f"If you click Save, this will actually delete the file from the {container_label}.\n"
                f"If you click Save As, the file will simply be omitted from the exported folder and the {container_label} will not be changed."
            ),
        )
        if not confirmed:
            return

        self.pendingImageDeletes.add(image_path)
        self.pendingImageRenames.pop(image_path, None)
        self.pendingImageTitleEdits.pop(image_path, None)
        self.pendingImageReplacements.pop(image_path, None)
        self.table.removeRow(row)
        self.status_label.setText(
            f"Pending removal: '{filename}' will be deleted from the {container_label} on Save, "
            "or omitted from exported files on Save As."
        )
        self._refresh_pianodir_row()
        self._reapply_image_centered_title_assumption()

    def _build_default_image_filename(self, host_path, used_paths):
        return self._build_dos_image_filename(os.path.basename(host_path), used_paths)

    def _build_dos_image_filename(self, filename, used_paths):
        stem, ext = os.path.splitext(filename)
        ext = ext.lstrip(".")
        if ext.lower() == "midi":
            ext = "MID"

        clean_stem = "".join(
            ch.upper() if ch.isalnum() else "_"
            for ch in stem
            if ord(ch) < 128
        ).strip("_")
        clean_ext = "".join(
            ch.upper() if ch.isalnum() else "_"
            for ch in ext
            if ord(ch) < 128
        ).strip("_")
        if not clean_stem:
            clean_stem = "FILE"
        clean_ext = clean_ext[:3]

        for counter in range(0, 1000):
            suffix = "" if counter == 0 else str(counter)
            base_len = max(1, 8 - len(suffix))
            candidate_stem = (clean_stem[:base_len] + suffix)[:8]
            candidate = candidate_stem
            if clean_ext:
                candidate += f".{clean_ext}"
            validation_error = self._validate_image_filename(candidate)
            if validation_error:
                continue
            if candidate.upper() not in used_paths:
                return candidate

        raise ValueError(f"Could not create a unique DOS filename for {filename}.")

    def _pending_image_space_remaining(self, extra_additions=None):
        if self.image_session is None:
            return 0
        listing = self.image_session.list_entries()
        entries_by_path = {entry.path: entry for entry in listing.entries}
        cluster_size = listing.cluster_size
        free_space = listing.free_space

        freed = 0
        for image_path in self.pendingImageDeletes:
            entry = entries_by_path.get(image_path) or self._image_entry_for_path(image_path)
            if entry:
                freed += entry.packed_size or allocated_size(entry.size, cluster_size)
        if self.pendingDeletePianodir:
            for entry in listing.entries:
                if is_pianodir_path(entry.path):
                    freed += entry.packed_size or allocated_size(entry.size, cluster_size)
                    break

        additions = dict(self.pendingImageAdditions)
        if extra_additions:
            additions.update(extra_additions)

        used = 0
        for host_path in additions.values():
            if os.path.isfile(host_path):
                used += allocated_size(os.path.getsize(host_path), cluster_size)

        replacement_delta = 0
        for image_path, host_path in self.pendingImageReplacements.items():
            if image_path in self.pendingImageDeletes or not os.path.isfile(host_path):
                continue
            entry = entries_by_path.get(image_path) or self._image_entry_for_path(image_path)
            if entry is None:
                continue
            old_size = entry.packed_size or allocated_size(entry.size, cluster_size)
            new_size = allocated_size(os.path.getsize(host_path), cluster_size)
            replacement_delta += new_size - old_size

        if self.imageEseqMode and not self.imageHasPianodir and self.pendingGeneratePianodir:
            used += allocated_size(PIANODIR_TARGET_FILE_SIZE, cluster_size)

        return free_space + freed - used - replacement_delta

    def _pending_image_used_bytes(self):
        if self.image_session is None:
            return 0

        listing = self.image_session.list_entries()
        cluster_size = listing.cluster_size
        used = 0

        for entry in listing.entries:
            if entry.path in self.pendingImageDeletes:
                continue
            if self.pendingDeletePianodir and is_pianodir_path(entry.path):
                continue
            if entry.path in self.pendingImageReplacements:
                host_path = self.pendingImageReplacements[entry.path]
                if os.path.isfile(host_path):
                    used += allocated_size(os.path.getsize(host_path), cluster_size)
                    continue
            used += entry.packed_size or allocated_size(entry.size, cluster_size)

        for host_path in self.pendingImageAdditions.values():
            if os.path.isfile(host_path):
                used += allocated_size(os.path.getsize(host_path), cluster_size)

        if self.imageEseqMode and not self.imageHasPianodir and self.pendingGeneratePianodir:
            used += allocated_size(PIANODIR_TARGET_FILE_SIZE, cluster_size)

        return max(0, used)

    def _refresh_disk_usage_bars(self):
        if not hasattr(self, "diskUsageBarsWidget"):
            return
        show_bars = self.is_image_mode()
        self.diskUsageBarsWidget.setVisible(show_bars)
        if not show_bars or self.image_session is None:
            self.diskUsageBar.set_fraction(0.0)
            self.eseqCountBar.set_count(0)
            self.eseqCountBar.setVisible(False)
            return

        total_size = max(1, int(self.image_session.disk_format.size_bytes or 1))
        self.diskUsageBar.set_fraction(self._pending_image_used_bytes() / total_size)
        self.eseqCountBar.setVisible(bool(self.imageEseqMode))
        self.eseqCountBar.set_count(self._image_song_file_count() if self.imageEseqMode else 0)

    def queue_image_additions(self, file_paths):
        if not self.is_image_mode():
            return

        valid_files = [path for path in file_paths if os.path.isfile(path)]
        if not valid_files:
            self.status_label.setText("No files were added to the image.")
            return

        added = []
        skipped = []
        shortened = []
        used_paths = self._active_image_paths()
        pending_extra = {}
        for host_path in valid_files:
            original_name = os.path.basename(host_path)
            try:
                target_name = self._build_default_image_filename(host_path, used_paths)
            except ValueError as exc:
                skipped.append(f"{original_name}: {exc}")
                continue

            target_path = target_name
            pending_extra[target_path] = host_path
            if self._pending_image_space_remaining(pending_extra) < 0:
                pending_extra.pop(target_path, None)
                skipped.append(f"{original_name}: not enough free space in image")
                continue

            size = os.path.getsize(host_path)
            is_midi, title, midi_type, title_mode, order_key = self._probe_image_file(target_path, size, host_path)
            would_be_eseq_mode = self.imageEseqMode or title_mode == "eseq" or self._is_eseq_candidate(
                target_path,
                is_midi=is_midi,
            )
            if would_be_eseq_mode and (self._image_song_file_count() + 1) > self.ESEQ_FILE_LIMIT:
                pending_extra.pop(target_path, None)
                self.imageFileInfo.pop(target_path, None)
                skipped.append(
                    f"{original_name}: Yamaha E-SEQ supports at most {self.ESEQ_FILE_LIMIT} files"
                )
                continue
            used_paths.add(target_path.upper())
            self.pendingImageAdditions[target_path] = host_path
            if is_eseq_file(host_path) and target_name.upper() != original_name.upper():
                shortened.append(f"{original_name} -> {target_name}")
            if not title_mode:
                title = ""
            self.add_image_table_row(
                target_path,
                target_name,
                size,
                title=title,
                midi_type=midi_type,
                order_key=order_key,
                is_pending_addition=True,
            )
            added.append(target_path)

        self._refresh_pianodir_row()
        self._reapply_image_centered_title_assumption()
        self._resize_table_columns_to_fill()

        status_parts = []
        if added:
            status_parts.append(f"Queued {len(added)} file(s) to add to the image.")
        if shortened:
            status_parts.append(f"Shortened {len(shortened)} E-SEQ filename(s) to DOS 8.3.")
        if skipped:
            status_parts.append(f"Skipped {len(skipped)} file(s).")
        remaining = self._pending_image_space_remaining()
        status_parts.append(f"Estimated free space after pending additions: {display_bytes(max(0, remaining))}.")
        self.status_label.setText("\n".join(status_parts))

        if skipped:
            QMessageBox.warning(self, "Some Files Were Not Added", "\n".join(skipped[:10]))
        elif shortened:
            QMessageBox.information(self, "Filename Shortened", "\n".join(shortened[:10]))

    def _prompt_for_title(self, current_title, title_mode="midi"):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Edit Song Title")
        dialog.setModal(True)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(12, 10, 12, 10)
        dialog_layout.setSpacing(8)
        dialog_layout.setSizeConstraint(QLayout.SetFixedSize)
        enforce_eseq_limit = title_mode == "eseq"
        use_screen_format = bool(
            getattr(self, "format_disklavier_checkbox", None)
            and self.format_disklavier_checkbox.isChecked()
        )

        prompt = QLabel("Song title:")
        dialog_layout.addWidget(prompt)

        title_field_font = QFont("Courier New")
        title_field_font.setStyleHint(QFont.Monospace)
        title_font_metrics = QFontMetrics(title_field_font)
        title_field_width = title_font_metrics.horizontalAdvance("M" * 32) + 28
        centered_field_width = title_font_metrics.horizontalAdvance("M" * 16) + 28
        active_field_width = centered_field_width if use_screen_format else title_field_width

        editor = QLineEdit(current_title)
        editor.setFont(title_field_font)
        editor.setLayoutDirection(Qt.LeftToRight)
        editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        editor.setFixedWidth(title_field_width)
        if enforce_eseq_limit:
            editor.setMaxLength(self.TITLE_COMPAT_LIMIT)

        editor_page = QWidget(dialog)
        editor_page_layout = QVBoxLayout(editor_page)
        editor_page_layout.setContentsMargins(0, 0, 0, 0)
        editor_page_layout.setSpacing(0)
        editor_page_layout.addWidget(editor, alignment=Qt.AlignLeft)
        editor_page_layout.addStretch(1)

        centered_fields_widget = QWidget(dialog)
        centered_fields_layout = QVBoxLayout(centered_fields_widget)
        centered_fields_layout.setContentsMargins(0, 0, 0, 0)
        centered_fields_layout.setSpacing(6)

        first_field = QLineEdit()
        first_field.setPlaceholderText("Field 1")
        first_field.setFont(title_field_font)
        first_field.setLayoutDirection(Qt.LeftToRight)
        first_field.setMaxLength(16)
        first_field.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        first_field.setFixedWidth(centered_field_width)
        centered_fields_layout.addWidget(first_field, alignment=Qt.AlignLeft)

        second_field = QLineEdit()
        second_field.setPlaceholderText("Field 2")
        second_field.setFont(title_field_font)
        second_field.setLayoutDirection(Qt.LeftToRight)
        second_field.setMaxLength(16)
        second_field.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        second_field.setFixedWidth(centered_field_width)
        centered_fields_layout.addWidget(second_field, alignment=Qt.AlignLeft)

        field_stack = QStackedWidget(dialog)
        field_stack.addWidget(editor_page)
        field_stack.addWidget(centered_fields_widget)
        field_stack.setFixedWidth(active_field_width)
        field_stack.setFixedHeight(
            first_field.sizeHint().height()
            + second_field.sizeHint().height()
            + centered_fields_layout.spacing()
        )
        dialog_layout.addWidget(field_stack, alignment=Qt.AlignLeft)

        warning_label = QLabel("")
        warning_label.setWordWrap(True)
        warning_label.setFixedWidth(active_field_width)
        warning_label.setStyleSheet("color: #C62828;")
        warning_label.setVisible(False)
        dialog_layout.addWidget(warning_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        dialog_layout.addWidget(buttons)

        def composed_title():
            if use_screen_format:
                return first_field.text()[:16].ljust(16) + second_field.text()[:16].ljust(16)
            return editor.text()

        def update_state():
            title_text = composed_title()
            validation_error = validate_legacy_title_input(title_text)
            unchanged = title_text == current_title
            has_text = bool(first_field.text().strip() or second_field.text().strip()) if use_screen_format else bool(editor.text().strip())
            is_valid = validation_error is None or unchanged
            ok_button.setEnabled(has_text and is_valid)

            if has_text and validation_error and not unchanged:
                warning_label.setVisible(True)
                warning_label.setText(validation_error)
                return

            show_warning = self._compat_warning_is_active() and self._is_title_too_long(title_text)
            warning_label.setVisible(show_warning)
            if show_warning:
                warning_label.setText(
                    f"Compatibility warning: title is over {self.TITLE_COMPAT_LIMIT} characters."
                )
            else:
                warning_label.setText("")

        editor.textChanged.connect(lambda _text: update_state())
        first_field.textChanged.connect(lambda _text: update_state())
        second_field.textChanged.connect(lambda _text: update_state())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if use_screen_format:
            field_one, field_two = self._split_title_for_center_fields(
                current_title,
                enforce_limit=True,
            )
            first_field.setText(field_one)
            second_field.setText(field_two)
            field_stack.setCurrentWidget(centered_fields_widget)
        else:
            field_stack.setCurrentWidget(editor_page)
        update_state()
        if use_screen_format:
            first_field.selectAll()
            first_field.setFocus()
        else:
            editor.selectAll()
            editor.setFocus()

        if self._exec_child_dialog(dialog) == QDialog.Accepted:
            return composed_title(), True
        return "", False

    def edit_via_dialog(self, row):
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return
        full_path_item = self.table.item(row, 1)
        if full_path_item is None:
            return
        full_path = full_path_item.text()
        title_mode = self._listed_file_title_mode(full_path)
        if not title_mode:
            QMessageBox.information(self, "No Editable Title", "Only MIDI and E-SEQ files have editable title metadata.")
            return

        current_title = self._row_raw_title(row)
        if current_title == "No title found.":
            current_title = ""
        new_title, ok = self._prompt_for_title(current_title, title_mode=title_mode)
        if ok and new_title.strip():
            if new_title == current_title:
                return

            validation_error = validate_legacy_title_input(new_title)
            if validation_error:
                QMessageBox.warning(self, "Invalid Title", validation_error)
                return
            if title_mode == "eseq" and len(new_title.encode("latin1")) > 32:
                QMessageBox.warning(self, "Title Too Long", "E-SEQ titles must be 32 characters or fewer.")
                return
            self.pendingEdits[full_path] = new_title
            filename = self.table.item(row, 3).text() if self.table.item(row, 3) else "this file"
            new_title_item = self._make_title_item(new_title, title_mode=title_mode, fallback_title=filename)
            self.table.setItem(row, 4, new_title_item)
            self._update_compat_indicator(row, new_title)
            self._reapply_regular_centered_title_assumption()
            warning = ""
            if self._compat_warning_is_active() and self._is_title_too_long(new_title):
                warning = f"\nCompatibility warning: over {self.TITLE_COMPAT_LIMIT} characters."
            title_kind = "E-SEQ title" if title_mode == "eseq" else "MIDI title"
            shown_title = self._display_title_text(new_title, title_mode=title_mode, fallback_title=filename)
            self.status_label.setText(
                f"Pending change:\n{title_kind} for '{filename}' will be updated to '{shown_title}' on save.{warning}"
            )
        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)

    def _collect_image_operations(self):
        return (
            dict(self.pendingImageRenames),
            set(self.pendingImageDeletes),
            dict(self.pendingImageAdditions),
            dict(self.pendingImageReplacements),
            dict(self.pendingImageTitleEdits),
            bool(self.pendingDeletePianodir),
        )

    def _reload_image_table_after_commit(self):
        if self.image_session is None:
            return
        listing = self.image_session.list_entries()
        self.imageEntriesByPath = {entry.path: entry for entry in listing.entries}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._load_image_rows(listing.entries)

    def _confirm_image_save_deletions(self):
        delete_count = len(self.pendingImageDeletes) + (1 if self.pendingDeletePianodir else 0)
        if delete_count == 0:
            return True
        container_label = "floppy disk" if self.is_floppy_mode() else "image"
        return self._confirm_with_optional_skip(
            setting_key=self.SETTING_SKIP_IMAGE_DELETE_ON_SAVE_WARNING,
            title=f"Delete Files From {container_label.title()}",
            message=(
                f"Saving will permanently remove {delete_count} file(s) from the {container_label}.\n\n"
                "Continue?"
            ),
        )

    def save_image_changes(self):
        if self.image_session is None:
            return
        if not self._has_pending_image_changes():
            QMessageBox.information(self, "No Changes", "There are no pending image changes to save.")
            return
        if not self.is_floppy_mode() and not self._original_write_is_allowed():
            QMessageBox.information(
                self,
                "Save To Image Is Off",
                "Use Save As to export files, use Save As Image to create a separate image, or enable Overwrite Original in File Actions.",
            )
            return
        if self.is_floppy_mode() and not self._original_write_is_allowed():
            QMessageBox.information(
                self,
                "Save To Floppy Is Off",
                "Use Save As Image to save an image file, or enable Overwrite Original in File Actions.",
            )
            return
        if not self._confirm_image_save_deletions():
            return
        if not self._confirm_floppy_write():
            return
        if self.imageEseqMode and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Saving this E-SEQ floppy set",
        ):
            return
        if not self._ensure_pianodir_generation_for_save():
            return
        if self._pending_image_space_remaining() < 0:
            QMessageBox.warning(
                self,
                "Image Is Full",
                "Pending additions do not fit in the floppy image. Remove files or cancel additions before saving.",
            )
            return

        if not self.image_session.source_kind.startswith("floppy"):
            backup_error = self._create_image_backup_if_enabled(self.image_session.source_path)
            if backup_error:
                QMessageBox.critical(self, "Backup Failed", backup_error)
                return

        renames, deletes, additions, replacements, title_edits, delete_pianodir = self._collect_image_operations()
        order_key_edits = self._image_eseq_order_key_edits()
        if self.image_session.source_kind == "floppy_usb":
            progress_text = "Writing USB floppy..."
        elif self.image_session.source_kind == "floppy_gw":
            progress_text = "Writing floppy via Greaseweazle..."
        else:
            progress_text = "Saving floppy image..."
        progressDialog = QProgressDialog(progress_text, None, 0, 5, self)
        self._prepare_progress_dialog(progressDialog)
        progressDialog.setAutoClose(False)
        progressDialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progressDialog)
        progress_callback(0, 5, progress_text)
        QApplication.processEvents()
        try:
            self.image_session.commit_to_source(
                renames=renames,
                deletes=deletes,
                additions=additions,
                replacements=replacements,
                title_edits=title_edits,
                order_key_edits=order_key_edits,
                pianodir_metadata=self._image_pianodir_metadata_for_save(),
                generate_pianodir=self._should_generate_pianodir(),
                delete_pianodir=delete_pianodir,
                progress_callback=progress_callback,
            )
            self.pendingImageRenames.clear()
            self.pendingImageTitleEdits.clear()
            self.pendingImageDeletes.clear()
            self.pendingImageAdditions.clear()
            self.pendingImageReplacements.clear()
            self.pendingGeneratePianodir = False
            self.pendingDeletePianodir = False
            progress_callback(5, 5, "Reloading floppy view...")
            self._reload_image_table_after_commit()
            progressDialog.close()
            if self.image_session.source_kind.startswith("floppy"):
                QMessageBox.information(self, "Floppy Saved", "Floppy changes have been saved back to the disk.")
            else:
                QMessageBox.information(self, "Image Saved", "Floppy image changes have been saved.")
            self.status_label.setText(self._image_mode_summary())
        except Exception as exc:
            progressDialog.close()
            QMessageBox.critical(self, "Image Save Failed", str(exc))

    def show_about_dialog(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle(f"About {APP_TITLE_WITH_VERSION}")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        logo_label = QLabel(dialog)
        logo_label.setAlignment(Qt.AlignCenter)
        pixmap = pixmap_from_base64(embedded_logo_dt if is_dark_theme() else embedded_logo_lt)
        if not pixmap.isNull():
            logo_label.setPixmap(pixmap.scaled(220, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            logo_label.setText("APS MIDI Prep Tool")
        layout.addWidget(logo_label)

        title_label = QLabel(APP_TITLE_WITH_VERSION, dialog)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Helvetica", 13, QFont.Bold))
        layout.addWidget(title_label)

        website_label = QLabel(f'<a href="{APP_WEBSITE}">{APP_WEBSITE}</a>', dialog)
        website_label.setAlignment(Qt.AlignCenter)
        website_label.setOpenExternalLinks(True)
        website_label.setToolTip("Project website.")
        layout.addWidget(website_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)

        self._exec_child_dialog(dialog)

    def show_welcome_dialog(self):
        show_first_time_dialog(self.windowIcon(), parent=self, force_show=True)

    def _extension_from_filter(self, selected_filter):
        if "*." not in selected_filter:
            return ""
        return selected_filter.split("*.", 1)[1].split(")", 1)[0].strip().lower()

    def save_image_as(self):
        if self.image_session is None:
            return
        if self.imageEseqMode and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Saving this E-SEQ floppy set as a separate image",
        ):
            return
        if not self._ensure_pianodir_generation_for_save():
            return
        if self._pending_image_space_remaining() < 0:
            QMessageBox.warning(
                self,
                "Image Is Full",
                "Pending additions do not fit in the floppy image. Remove files or cancel additions before exporting.",
            )
            return

        default_ext = self.image_session.source_ext or "img"
        filters, fallback_ext = output_filters(default_ext)
        if self.image_session.source_kind.startswith("floppy"):
            source_dir = os.path.expanduser("~")
            source_stem = "floppy_capture"
            if self.image_session.source_kind == "floppy_gw":
                source_stem = f"gw_drive_{self.image_session.gw_source.drive.lower()}"
        else:
            source_dir = os.path.dirname(self.image_session.source_path)
            source_stem = os.path.splitext(os.path.basename(self.image_session.source_path))[0]
        default_path = os.path.join(source_dir, f"{source_stem}_edited.{default_ext or fallback_ext}")
        output_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save As Image",
            default_path,
            filters,
        )
        if not output_path:
            return

        selected_ext = image_extension(output_path) or self._extension_from_filter(selected_filter) or fallback_ext
        if not image_extension(output_path):
            output_path = f"{output_path}.{selected_ext}"

        renames, deletes, additions, replacements, title_edits, delete_pianodir = self._collect_image_operations()
        order_key_edits = self._image_eseq_order_key_edits()
        progressDialog = QProgressDialog("Exporting floppy image...", None, 0, 5, self)
        self._prepare_progress_dialog(progressDialog)
        progressDialog.setAutoClose(False)
        progressDialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progressDialog)
        progress_callback(0, 5, "Preparing floppy export...")
        QApplication.processEvents()
        try:
            self.image_session.export_to(
                output_path,
                selected_ext,
                renames=renames,
                deletes=deletes,
                additions=additions,
                replacements=replacements,
                title_edits=title_edits,
                order_key_edits=order_key_edits,
                pianodir_metadata=self._image_pianodir_metadata_for_save(),
                generate_pianodir=self._should_generate_pianodir(),
                delete_pianodir=delete_pianodir,
                progress_callback=progress_callback,
            )
            progress_callback(5, 5, "Finalizing floppy export...")
            progressDialog.close()
            session = FloppyImageSession.load(output_path)
            listing = session.list_entries()
            self._activate_disk_session(session, listing)
            QMessageBox.information(self, "Save As Image Complete", f"Image saved as {os.path.basename(output_path)}.")
            self.status_label.setText(self._image_mode_summary())
        except Exception as exc:
            progressDialog.close()
            QMessageBox.critical(self, "Image Export Failed", str(exc))

    def _basic_image_export_types(self):
        preferred = {ext: (ext, label) for ext, label in PREFERRED_OUTPUT_EXTENSIONS}
        return [
            preferred[ext]
            for ext in ("hfe", "img", "bin")
            if ext in preferred
        ]

    def _basic_disk_export_formats(self):
        return [
            disk_format
            for disk_format in DISK_FORMATS
            if disk_format.key in {"ibm.720", "ibm.1440"}
        ]

    def _prompt_for_save_image_options(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Save As Image")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)

        summary = QLabel(
            "Choose the output image type and floppy size.\n"
            "If the selected disk is too small, files will spill into numbered images in sequence."
        )
        summary.setWordWrap(True)
        dialog_layout.addWidget(summary)

        type_row = QHBoxLayout()
        type_label = QLabel("Image format:")
        type_label.setMinimumWidth(100)
        type_row.addWidget(type_label)
        type_combo = QComboBox(dialog)
        type_row.addWidget(type_combo, stretch=1)
        dialog_layout.addLayout(type_row)

        list_all_types_checkbox = QCheckBox("List all image formats")
        dialog_layout.addWidget(list_all_types_checkbox)

        disk_row = QHBoxLayout()
        disk_label = QLabel("Disk size:")
        disk_label.setMinimumWidth(100)
        disk_row.addWidget(disk_label)
        disk_combo = QComboBox(dialog)
        disk_row.addWidget(disk_combo, stretch=1)
        dialog_layout.addLayout(disk_row)

        list_all_disks_checkbox = QCheckBox("List all disk sizes")
        dialog_layout.addWidget(list_all_disks_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dialog_layout.addWidget(buttons)

        def refresh_type_combo():
            current_ext = type_combo.currentData()
            options = PREFERRED_OUTPUT_EXTENSIONS if list_all_types_checkbox.isChecked() else self._basic_image_export_types()
            type_combo.clear()
            selected_index = 0
            for index, (ext, label) in enumerate(options):
                type_combo.addItem(label, ext)
                if ext == current_ext:
                    selected_index = index
            type_combo.setCurrentIndex(selected_index)

        def refresh_disk_combo():
            current_key = disk_combo.currentData().key if disk_combo.currentData() is not None else "ibm.720"
            options = DISK_FORMATS if list_all_disks_checkbox.isChecked() else self._basic_disk_export_formats()
            disk_combo.clear()
            selected_index = 0
            for index, disk_format in enumerate(options):
                disk_combo.addItem(disk_format.label, disk_format)
                if disk_format.key == current_key:
                    selected_index = index
            disk_combo.setCurrentIndex(selected_index)

        list_all_types_checkbox.toggled.connect(refresh_type_combo)
        list_all_disks_checkbox.toggled.connect(refresh_disk_combo)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        refresh_type_combo()
        refresh_disk_combo()

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        output_ext = type_combo.currentData()
        disk_format = disk_combo.currentData()
        if not output_ext or disk_format is None:
            return None

        output_label = type_combo.currentText() or f"{output_ext.upper()} image"
        default_path = os.path.join(os.path.expanduser("~"), f"midi_floppy.{output_ext}")
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save As Image",
            default_path,
            f"{output_label} (*.{output_ext})",
        )
        if not output_path:
            return None

        base_path = os.path.splitext(output_path)[0]
        return f"{base_path}.{output_ext}", output_ext, disk_format

    def _stage_files_for_image_export(self, temp_dir, progress_callback=None):
        row_count = self._regular_file_count()
        file_specs = []
        used_names = set()
        regular_order_key_edits = self._regular_eseq_order_key_edits() if self.is_local_eseq_mode() else {}

        for index, row in enumerate(self._regular_file_rows(), start=1):
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue

            full_path = full_path_item.text()
            filename_item = self.table.item(row, 3)
            display_name = filename_item.text() if filename_item is not None else os.path.basename(full_path)
            display_title = self._row_raw_title(row) or display_name

            _notify = progress_callback
            if _notify is not None:
                _notify(index - 1, max(1, row_count), f"Preparing {display_name} for image export...")

            staged_path = os.path.join(
                temp_dir,
                f"{index:04d}_{os.path.basename(full_path)}",
            )
            error_msg = self._write_listed_file_to_path(
                full_path,
                display_title,
                staged_path,
                order_key=regular_order_key_edits.get(full_path),
            )
            if error_msg:
                raise FloppyImageError(error_msg)

            image_name = self._build_dos_image_filename(display_name, used_names)
            used_names.add(image_name.upper())
            file_specs.append(
                {
                    "host_path": staged_path,
                    "image_path": image_name,
                    "display_name": display_name,
                    "title": display_title,
                    "title_mode": self._listed_file_title_mode(full_path),
                }
            )

        if self.is_local_eseq_mode() and self._should_generate_pianodir(for_export=True):
            track_entries = [
                PianodirTrackEntry(
                    image_path=spec["image_path"],
                    local_path=spec["host_path"],
                    title=spec.get("title", ""),
                )
                for spec in file_specs
                if spec.get("title_mode") == "eseq"
            ]
            if track_entries:
                generated_path = os.path.join(temp_dir, PIANODIR_FILENAME)
                with open(generated_path, "wb") as handle:
                    handle.write(build_pianodir_bytes(track_entries))
                file_specs.append(
                    {
                        "host_path": generated_path,
                        "image_path": PIANODIR_FILENAME,
                        "display_name": PIANODIR_FILENAME,
                        "title": "",
                        "title_mode": "",
                    }
                )
        elif self.is_local_eseq_mode() and self.regularHasPianodir:
            existing_pianodir = self._existing_regular_pianodir_path()
            if existing_pianodir and os.path.isfile(existing_pianodir):
                staged_pianodir = os.path.join(temp_dir, PIANODIR_FILENAME)
                shutil.copy2(existing_pianodir, staged_pianodir)
                file_specs.append(
                    {
                        "host_path": staged_pianodir,
                        "image_path": PIANODIR_FILENAME,
                        "display_name": PIANODIR_FILENAME,
                        "title": "",
                        "title_mode": "",
                    }
                )

        if progress_callback is not None:
            progress_callback(len(file_specs), max(1, len(file_specs)), "Preparing floppy image export...")
        return file_specs

    def _materialize_export_context_files(self, file_specs, output_path):
        base_path = os.path.splitext(output_path)[0]
        context_dir = f"{base_path}_files"
        if os.path.isdir(context_dir):
            shutil.rmtree(context_dir, ignore_errors=True)
        os.makedirs(context_dir, exist_ok=True)

        context_paths = []
        for spec in file_specs:
            dest_path = os.path.join(context_dir, os.path.basename(spec["image_path"]))
            shutil.copy2(spec["host_path"], dest_path)
            context_paths.append(dest_path)
        return context_dir, context_paths

    def save_as_image(self):
        if self.is_image_mode():
            self.save_image_as()
            return
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return
        if self._regular_file_count() == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return
        if self.is_local_eseq_mode() and not self._ensure_eseq_file_limit(
            self._regular_file_count(),
            action_text="Saving this E-SEQ set as an image",
        ):
            return
        if self.is_local_eseq_mode() and not self._ensure_pianodir_generation_for_save():
            return

        options = self._prompt_for_save_image_options()
        if options is None:
            return

        output_path, output_ext, disk_format = options
        staging_dir = tempfile.mkdtemp(prefix="aps_save_image_")
        progressDialog = QProgressDialog("Preparing files for image export...", None, 0, max(1, self._regular_file_count()), self)
        self._prepare_progress_dialog(progressDialog)
        progressDialog.setAutoClose(False)
        progressDialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progressDialog)

        try:
            file_specs = self._stage_files_for_image_export(staging_dir, progress_callback=progress_callback)
            if not file_specs:
                progressDialog.close()
                QMessageBox.information(self, "No Files", "No valid files were available to export.")
                return

            output_paths = create_floppy_images_from_files(
                file_specs,
                output_path,
                output_ext,
                disk_format,
                progress_callback=progress_callback,
            )
            progressDialog.close()

            if len(output_paths) == 1:
                session = FloppyImageSession.load(output_paths[0])
                listing = session.list_entries()
                self._activate_disk_session(session, listing)
                QMessageBox.information(
                    self,
                    "Save As Image Complete",
                    f"Created {os.path.basename(output_paths[0])}.",
                )
                self.status_label.setText(self._image_mode_summary())
                return

            _, context_paths = self._materialize_export_context_files(file_specs, output_path)
            self._load_regular_files(
                context_paths,
                (
                    f"Created {len(output_paths)} sequential {disk_format.label} {output_ext.upper()} images.\n"
                    "Current context moved to the new exported source files."
                ),
            )
            preview = "\n".join(os.path.basename(path) for path in output_paths[:10])
            if len(output_paths) > 10:
                preview += f"\n...and {len(output_paths) - 10} more."
            QMessageBox.information(
                self,
                "Save As Image Complete",
                f"Created {len(output_paths)} image files:\n\n{preview}",
            )
        except Exception as exc:
            progressDialog.close()
            QMessageBox.critical(self, "Save As Image Failed", str(exc))
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _save_pending_regular_conversions(self, regular_order_key_edits):
        converted_items = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if full_path not in self.pendingRegularConversions:
                continue
            dest_path = os.path.join(
                os.path.dirname(full_path),
                self._regular_row_output_filename(row),
            )
            converted_items.append((row, full_path, dest_path))

        if not converted_items:
            self.pendingRegularConversions.clear()
            return []

        errors = []
        output_paths = []
        output_path_map = {}
        for _row, full_path, dest_path in converted_items:
            if os.path.normcase(os.path.abspath(dest_path)) == os.path.normcase(os.path.abspath(full_path)):
                errors.append(f"{os.path.basename(full_path)}: target path matches source path")
            elif os.path.exists(dest_path):
                errors.append(f"{os.path.basename(dest_path)} already exists")
        if errors:
            return errors

        progressDialog = QProgressDialog("Saving converted files...", "Cancel", 0, len(converted_items), self)
        self._prepare_progress_dialog(progressDialog)
        for index, (row, full_path, dest_path) in enumerate(converted_items, start=1):
            if progressDialog.wasCanceled():
                break

            backup_error = self._create_backup_if_enabled(full_path)
            if backup_error:
                errors.append(backup_error)
                progressDialog.setValue(index)
                QApplication.processEvents()
                continue

            title = self._row_raw_title(row)
            error_msg = self._write_listed_file_to_path(
                full_path,
                title,
                dest_path,
                order_key=regular_order_key_edits.get(full_path),
            )
            if error_msg:
                errors.append(error_msg)
            else:
                output_paths.append(dest_path)
                output_path_map[full_path] = dest_path
            progressDialog.setValue(index)
            QApplication.processEvents()
        progressDialog.close()

        if errors:
            return errors

        if self.is_local_eseq_mode() and self._should_generate_pianodir(for_export=True):
            try:
                target_dirs = [os.path.dirname(path) for path in output_paths]
                base_dir = os.path.commonpath(target_dirs) if target_dirs else self.regularModeContextPath
                if not os.path.isdir(base_dir):
                    base_dir = os.path.dirname(base_dir)
                output_paths.append(
                    self._write_regular_pianodir(
                        base_dir=base_dir,
                        path_remap=output_path_map,
                    )
                )
            except Exception as exc:
                return [str(exc)]

        status_text = f"Saved {len(output_path_map)} converted file(s)."
        if self.backup_checkbox.isChecked():
            status_text += "\nCreated backup file(s) for the original source files."
        self._cleanup_midi_scratch_dir()
        self._load_regular_files(output_paths, status_text)
        return []

    def save_pending_changes(self):
        if self.is_image_mode():
            self.save_image_changes()
            return

        if self.is_local_eseq_mode() and not self._ensure_pianodir_generation_for_save():
            return

        should_write_local_pianodir = self.is_local_eseq_mode() and self._should_generate_pianodir()
        regular_order_key_edits = self._regular_eseq_order_key_edits() if self.is_local_eseq_mode() else {}
        has_pending_conversions = bool(self.pendingRegularConversions)

        if not self.pendingEdits and not should_write_local_pianodir and not regular_order_key_edits and not has_pending_conversions:
            QMessageBox.information(self, "No Changes", "There are no pending changes to save.")
            return
        if self.is_local_eseq_mode() and not self._ensure_eseq_file_limit(
            self._regular_file_count(),
            action_text="Saving this E-SEQ set",
        ):
            return

        if has_pending_conversions:
            errors = self._save_pending_regular_conversions(regular_order_key_edits)
            if errors:
                QMessageBox.critical(self, "Errors Occurred", "\n".join(errors))
            else:
                QMessageBox.information(self, "Save Complete", "Converted files have been saved.")
            return

        errors = []
        file_updates = {}
        for full_path, new_title in self.pendingEdits.items():
            file_updates.setdefault(full_path, {})["title"] = new_title
        for full_path, order_key in regular_order_key_edits.items():
            file_updates.setdefault(full_path, {})["order_key"] = order_key

        if file_updates:
            progressDialog = QProgressDialog("Saving title and order changes...", "Cancel", 0, len(file_updates), self)
            self._prepare_progress_dialog(progressDialog)
            current = 0
            for full_path, update_spec in file_updates.items():
                new_title = update_spec.get("title")
                if new_title is not None:
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

                title_mode = self._listed_file_title_mode(full_path)
                if title_mode == "eseq":
                    error_msg = self._write_eseq_file_to_path(
                        full_path,
                        full_path,
                        title=new_title,
                        order_key=update_spec.get("order_key"),
                    )
                else:
                    error_msg = update_midi_title(full_path, new_title)
                if error_msg:
                    errors.append(error_msg)
                current += 1
                progressDialog.setValue(current)
                QApplication.processEvents()
                if progressDialog.wasCanceled():
                        break
            progressDialog.close()
            if not errors:
                for full_path, order_key in regular_order_key_edits.items():
                    if full_path in self.listedFileInfo:
                        self.listedFileInfo[full_path]["order_key"] = normalize_eseq_order_key(order_key)
            self.pendingEdits.clear()

        if not errors and should_write_local_pianodir:
            try:
                output_path = self._write_regular_pianodir()
                self.regularPianodirSourcePath = output_path
                self.regularHasPianodir = True
                self.regularPianodirPopulated = True
                self.loadedRegularPianodirMetadata = self._current_regular_pianodir_metadata()
                self.pendingGeneratePianodir = False
                self._refresh_regular_pianodir_row()
            except Exception as exc:
                errors.append(str(exc))
        
        if errors:
            QMessageBox.critical(self, "Errors Occurred", "\n".join(errors))
        else:
            QMessageBox.information(self, "Save Complete", "All pending changes have been saved.")

    def save_as_changes(self):
        if self.is_image_mode():
            if self.imageEseqMode and not self._ensure_eseq_file_limit(
                self._image_song_file_count(),
                action_text="Exporting this E-SEQ floppy set to a folder",
            ):
                return
            if not self._ensure_pianodir_generation_for_save():
                return

            dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if not dest_dir:
                return
            export_dir = self._destination_with_album_subfolder(dest_dir)

            progressDialog = QProgressDialog("Saving files to new folder...", None, 0, max(1, self.table.rowCount()), self)
            self._prepare_progress_dialog(progressDialog)
            progressDialog.setAutoClose(False)
            progressDialog.setCancelButton(None)
            progress_callback = self._make_stage_progress_callback(progressDialog)
            progress_callback(0, max(1, self.table.rowCount()), "Preparing exported files...")
            QApplication.processEvents()

            try:
                output_paths = self._export_image_session_files_to_folder(export_dir, progress_callback=progress_callback)
                progressDialog.close()
                self._cleanup_midi_scratch_dir()
                self._reset_image_state()
                self._load_regular_files(
                    output_paths,
                    f"Current context moved to: \"{export_dir}\"",
                )
                QMessageBox.information(self, "Save As Complete", "Files have been saved to the new folder.")
            except Exception as exc:
                progressDialog.close()
                QMessageBox.critical(self, "Save As Failed", str(exc))
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if not dest_dir:
            return
        if self.is_local_eseq_mode() and not self._ensure_eseq_file_limit(
            self._regular_file_count(),
            action_text="Exporting this E-SEQ set to a folder",
        ):
            return
        if self.is_local_eseq_mode() and not self._ensure_pianodir_generation_for_save():
            return
        export_dir = self._destination_with_album_subfolder(dest_dir)
        os.makedirs(export_dir, exist_ok=True)

        progressDialog = QProgressDialog("Saving files to new folder...", "Cancel", 0, max(1, self._regular_file_count()), self)
        self._prepare_progress_dialog(progressDialog)
        row_count = self._regular_file_count()
        regular_order_key_edits = self._regular_eseq_order_key_edits() if self.is_local_eseq_mode() else {}
        errors = []
        output_paths = []
        output_path_map = {}
        for i, row in enumerate(self._regular_file_rows()):
            full_path = self.table.item(row, 1).text()
            title = self._row_raw_title(row)
            dest_path = os.path.join(export_dir, self._regular_row_output_filename(row))
            error_msg = self._write_listed_file_to_path(
                full_path,
                title,
                dest_path,
                order_key=regular_order_key_edits.get(full_path),
            )
            if error_msg:
                errors.append(error_msg)
            else:
                output_paths.append(dest_path)
                output_path_map[full_path] = dest_path
            progressDialog.setValue(i + 1)
            QApplication.processEvents()
            if progressDialog.wasCanceled():
                break
        progressDialog.close()
        if not errors and self.is_local_eseq_mode() and self._should_generate_pianodir(for_export=True):
            try:
                output_paths.append(self._write_regular_pianodir(base_dir=export_dir, path_remap=output_path_map))
            except Exception as exc:
                errors.append(str(exc))
        elif not errors and self.is_local_eseq_mode() and self.regularHasPianodir:
            existing_pianodir = self._existing_regular_pianodir_path()
            if existing_pianodir and os.path.isfile(existing_pianodir):
                copied_pianodir = os.path.join(export_dir, PIANODIR_FILENAME)
                shutil.copy2(existing_pianodir, copied_pianodir)
                output_paths.append(copied_pianodir)
        if errors:
            QMessageBox.critical(self, "Errors Occurred", "\n".join(errors))
        else:
            self._load_regular_files(
                output_paths,
                f"Current context moved to: \"{export_dir}\"",
            )
            QMessageBox.information(self, "Save As Complete", "Files have been saved to the new folder.")
