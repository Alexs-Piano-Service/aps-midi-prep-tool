import sys

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .ui_utils import center_dialog_on_parent
from .app_info import (
    APP_TITLE_WITH_VERSION,
    APP_WEBSITE,
    COPYRIGHT_HOLDER,
    COPYRIGHT_YEAR,
    SETTINGS_APP,
    SETTINGS_ORG,
)


def _workflow_page(title, body_html, body_font_stack):
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    title_label = QLabel(title)
    title_label.setTextFormat(Qt.PlainText)
    title_label.setAlignment(Qt.AlignCenter)
    title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
    layout.addWidget(title_label)

    body_label = QLabel(f"""<html>
      <head>
        <style type="text/css">
          body {{ font-family: {body_font_stack}; }}
          p {{ margin: 8px 0; }}
          ul {{ margin: 6px 20px 10px 20px; }}
          li {{ margin-bottom: 6px; }}
          a {{ text-decoration: none; }}
          a:hover {{ text-decoration: underline; }}
        </style>
      </head>
      <body>{body_html}</body>
    </html>""")
    body_label.setTextFormat(Qt.RichText)
    body_label.setWordWrap(True)
    body_label.setOpenExternalLinks(True)
    body_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    layout.addWidget(body_label, stretch=1)
    return page


def show_first_time_dialog(app_icon: QIcon | None = None, parent=None, *, force_show=False):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    skip_dialog = settings.value("skip_first_time_dialog", False, type=bool)

    if force_show or not skip_dialog:
        if sys.platform.startswith("win"):
            body_font_stack = '"Segoe UI", "Arial", sans-serif'
        elif sys.platform == "darwin":
            body_font_stack = '"Helvetica Neue", "Helvetica", sans-serif'
        else:
            body_font_stack = '"Noto Sans", "DejaVu Sans", "Arial", sans-serif'

        dialog = QDialog(parent)
        if parent is not None:
            dialog.setWindowModality(Qt.WindowModal)
        if app_icon is not None and not app_icon.isNull():
            dialog.setWindowIcon(app_icon)
        dialog.setWindowTitle(f"Welcome to {APP_TITLE_WITH_VERSION}")
        dialog.setModal(True)
        dialog.setMinimumSize(560, 390)

        pages = [
            (
                "Overview",
                f"""
                <p><strong>APS MIDI Prep Tool</strong> prepares MIDI and Yamaha Disklavier E-SEQ
                music files for folders, floppy images, and real disks.</p>
                <p>Pick a workflow above, or use <strong>Next</strong>, to see the path that
                matches what you are trying to do.</p>
                <ul>
                  <li>Edit MIDI and E-SEQ title metadata.</li>
                  <li>Copy or back up Yamaha floppies and floppy images.</li>
                  <li>Prepare HFE images for Nalbantov emulators.</li>
                  <li>Convert E-SEQ to MIDI, MIDI to E-SEQ, and SMF1 to SMF0.</li>
                </ul>
                <p><a href="{APP_WEBSITE}">alexanderpeppe.com</a></p>
                """,
            ),
            (
                "Edit Titles",
                """
                <p>Use <strong>Choose MIDI Folder</strong>, or drag files into the table, to edit
                local MIDI or E-SEQ titles.</p>
                <ul>
                  <li>Click the <strong>Title</strong> column to edit a song title.</li>
                  <li>Use <strong>Format for Disklavier screen</strong> for two 16-character E-SEQ rows.</li>
                  <li>Use <strong>Save</strong> for the current files, or <strong>Save As</strong> for copies.</li>
                </ul>
                """,
            ),
            (
                "Copy Or Back Up Yamaha Floppies",
                """
                <p>Use <strong>Read Floppy</strong> for a USB floppy drive or Greaseweazle, or
                <strong>Open Image</strong> for IMG, HFE, BIN, and related image files.</p>
                <ul>
                  <li>The app repairs Yamaha copy-protected boot sectors in the working copy.</li>
                  <li><strong>Save As</strong> copies the listed files to a folder.</li>
                  <li><strong>Save As Image</strong> creates a new floppy image without touching the original.</li>
                  <li>For fragile or difficult disks, use Greaseweazle and choose archival SCP when you want a raw flux capture.</li>
                  <li>Keep the backup image unchanged, then make edited copies from it when needed.</li>
                </ul>
                <p>Related articles: <a href="https://www.alexanderpeppe.com/disklavier-floppy-backups/">Using PPFBU to Back Up Disks</a>
                and <a href="https://www.alexanderpeppe.com/making-archival-copies-of-disks-using-a-greaseweazle-v4/">Backing Up Yamaha Disklavier Floppy Disks with a Greaseweazle</a>.</p>
                """,
            ),
            (
                "Save For Nalbantov",
                """
                <p>Use <strong>Save As Image</strong> and choose <strong>HFE (Nalbantov)</strong>
                when preparing a USB stick for a Nalbantov floppy disk emulator.</p>
                <ul>
                  <li>Copy the finished HFE file to a USB stick formatted for the emulator.</li>
                  <li>To replace a virtual disk slot, rename or copy the output over one of the existing <strong>DSKA####.hfe</strong> files on the Nalbantov USB stick.</li>
                  <li>For older E-SEQ-only Disklaviers, convert MIDI to E-SEQ and let the tool generate PIANODIR.FIL.</li>
                  <li>Do not mix MIDI files with E-SEQ files and PIANODIR.FIL on the same disk image.</li>
                </ul>
                <p>Related articles: <a href="https://www.alexanderpeppe.com/why-your-usb-stick-doesnt-show-on-a-nalbantov-and-how-to-format-it-fat32/">Nalbantov USB formatting</a>
                and <a href="https://www.alexanderpeppe.com/eseq-and-pianodir-fil/">Converting MIDI Files and Creating PIANODIR.FIL</a>.</p>
                """,
            ),
            (
                "Convert E-SEQ to MIDI",
                """
                <p>Open an E-SEQ folder, floppy image, or floppy disk, then use
                <strong>E-SEQ -&gt; MIDI</strong>.</p>
                <ul>
                  <li>Conversions are staged in the file list first.</li>
                  <li>Song titles, timing, and Yamaha PIANODIR information are preserved where possible.</li>
                  <li>Nothing is written until you choose <strong>Save</strong>, <strong>Save As</strong>, or <strong>Save As Image</strong>.</li>
                </ul>
                """,
            ),
            (
                "Convert MIDI to E-SEQ",
                """
                <p>Open a MIDI folder, or drag MIDI files into the table, then use
                <strong>MIDI -&gt; E-SEQ</strong> to prepare Yamaha E-SEQ files.</p>
                <ul>
                  <li>E-SEQ titles are limited to 32 characters.</li>
                  <li>The tool can generate or refresh <strong>PIANODIR.FIL</strong>.</li>
                  <li>E-SEQ disks support up to 60 songs, and floppy/image size limits still apply.</li>
                </ul>
                """,
            ),
            (
                "Convert SMF1 to SMF0",
                """
                <p>Some Yamaha workflows need Standard MIDI File Type 0, also called SMF0.</p>
                <ul>
                  <li>Open a MIDI folder, or drag MIDI files directly into the table.</li>
                  <li>Use <strong>SMF1 -&gt; SMF0</strong> to convert Type 1 files to single-track MIDI.</li>
                  <li>Files that are already Type 0 are left unchanged.</li>
                </ul>
                """,
            ),
            (
                "Save Safely",
                """
                <p>The app is cautious with originals, especially floppies and disk images.</p>
                <ul>
                  <li><strong>Save</strong> writes back to the current source only when overwrite is allowed.</li>
                  <li><strong>Save As</strong> writes files to a selected folder.</li>
                  <li><strong>Save As Image</strong> creates a new image file.</li>
                  <li><strong>Back up before saving</strong> creates backups before overwriting.</li>
                </ul>
                """,
            ),
        ]

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        heading = QLabel(APP_TITLE_WITH_VERSION)
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)

        selector_layout = QHBoxLayout()
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(8)
        selector_label = QLabel("Workflow")
        workflow_selector = QComboBox(dialog)
        for page_title, _ in pages:
            workflow_selector.addItem(page_title)
        selector_layout.addWidget(selector_label)
        selector_layout.addWidget(workflow_selector, stretch=1)
        layout.addLayout(selector_layout)

        page_stack = QStackedWidget(dialog)
        for page_title, page_html in pages:
            page_stack.addWidget(_workflow_page(page_title, page_html, body_font_stack))
        layout.addWidget(page_stack, stretch=1)

        dont_show_checkbox = QCheckBox("Do not show this dialog again")
        layout.addWidget(dont_show_checkbox)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 0, 0, 0)
        page_count_label = QLabel(dialog)
        back_button = QPushButton("Back", dialog)
        next_button = QPushButton("Next", dialog)
        close_button = QPushButton("Close", dialog)
        nav_layout.addWidget(page_count_label)
        nav_layout.addStretch()
        nav_layout.addWidget(back_button)
        nav_layout.addWidget(next_button)
        nav_layout.addWidget(close_button)
        layout.addLayout(nav_layout)

        def set_page(index):
            index = max(0, min(index, len(pages) - 1))
            page_stack.setCurrentIndex(index)
            if workflow_selector.currentIndex() != index:
                workflow_selector.setCurrentIndex(index)
            back_button.setEnabled(index > 0)
            next_button.setEnabled(index < len(pages) - 1)
            page_count_label.setText(f"{index + 1} of {len(pages)}")

        workflow_selector.currentIndexChanged.connect(set_page)
        back_button.clicked.connect(lambda: set_page(page_stack.currentIndex() - 1))
        next_button.clicked.connect(lambda: set_page(page_stack.currentIndex() + 1))
        close_button.clicked.connect(dialog.accept)
        set_page(0)

        center_dialog_on_parent(dialog, parent)
        dialog.exec()
        if dont_show_checkbox.isChecked():
            settings.setValue("skip_first_time_dialog", True)
