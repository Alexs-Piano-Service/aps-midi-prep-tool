import html
import sys

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .message_catalog import DEFAULT_LANGUAGE, normalize_language_code, tr, translate_text
from .onboarding_translations import ONBOARDING_TRANSLATIONS
from .ui_utils import center_dialog_on_parent
from .app_info import (
    APP_COMPANY,
    APP_TITLE_WITH_VERSION,
    APP_WEBSITE,
    SETTINGS_APP,
    SETTINGS_ORG,
)


SETTING_LANGUAGE = "language"


ONBOARDING_PAGES = (
    ("Overview", "overview"),
    ("Extract Files From Floppy", "extract"),
    ("Bulk Extraction", "bulk"),
    ("Build Emulator Disk Sets", "builder"),
    ("Format Or Fill A Floppy", "floppy"),
    ("Save For Nalbantov", "nalbantov"),
    ("Convert E-SEQ to MIDI", "eseq_to_midi"),
    ("Convert MIDI to E-SEQ", "midi_to_eseq"),
    ("Edit Titles", "titles"),
    ("Convert SMF1 to SMF0", "smf"),
    ("Save Safely", "safe"),
)

# @ prefixes identify catalog message IDs; other items are translated labels.
ONBOARDING_MENU_PATHS = {
    "next": ("Next",),
    "read": ("Disk", "Read Floppy..."),
    "open_image": ("File", "Open", "Open Image..."),
    "save": ("File", "Save"),
    "save_as": ("File", "Save As..."),
    "save_image": ("File", "Save As Image..."),
    "bulk": ("Utilities", "@bulk.action"),
    "builder": ("Utilities", "@emulator.action"),
    "folders": ("One album per folder",),
    "fill": ("Fill disks automatically",),
    "song_lists": ("@emulator.include_song_lists",),
    "format": ("Disk", "Format Floppy Disk..."),
    "write_image": ("Disk", "Write Current Image to Floppy..."),
    "eseq_midi": ("Utilities", "Convert", "Convert All E-SEQ to MIDI"),
    "midi_eseq": ("Utilities", "Convert", "Convert All MIDI to E-SEQ"),
    "title_column": ("Title",),
    "smf": ("Utilities", "Convert", "Convert All SMF1 to SMF0"),
    "write_protect": ("File", "Write Protection", "Write-Protect Original"),
    "backups": ("File", "Save Options", "Back up before Saving"),
}


def onboarding_text(message_id, language_code, **fields):
    language_code = normalize_language_code(language_code)
    return ONBOARDING_TRANSLATIONS[language_code][message_id].format(**fields)


def build_onboarding_pages(language_code):
    """Render translated prose with the same menu labels shown in the app."""
    language_code = normalize_language_code(language_code)
    menu_fields = {}
    for name, labels in ONBOARDING_MENU_PATHS.items():
        translated_labels = [
            tr(label[1:], language_code) if label.startswith("@")
            else translate_text(label, language_code)
            for label in labels
        ]
        menu_fields[name] = "<strong>" + html.escape(" → ".join(translated_labels)) + "</strong>"

    pages = []
    for title, message_id in ONBOARDING_PAGES:
        # Escape prose before inserting trusted markup, so translated text is
        # never interpreted as HTML and menu labels retain their emphasis.
        paragraphs = ONBOARDING_TRANSLATIONS[language_code][message_id].split("\n\n")
        body_html = "".join(
            f"<p>{html.escape(paragraph).format(**menu_fields)}</p>"
            for paragraph in paragraphs
        )
        if message_id == "overview":
            body_html += f'<p><a href="{html.escape(APP_WEBSITE, quote=True)}">{html.escape(APP_COMPANY)}</a></p>'
        pages.append((translate_text(title, language_code), body_html))
    return pages


def _settings_language_code(settings):
    return normalize_language_code(settings.value(SETTING_LANGUAGE, DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)


def _workflow_page(title, body_html, body_font_stack, notice_text=""):
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    title_label = QLabel(title)
    title_label.setTextFormat(Qt.PlainText)
    title_label.setAlignment(Qt.AlignCenter)
    title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
    layout.addWidget(title_label)

    notice_html = ""
    if notice_text:
        notice_html = f'<p class="notice">{html.escape(str(notice_text))}</p>'

    body_label = QLabel(f"""<html>
      <head>
        <style type="text/css">
          body {{ font-family: {body_font_stack}; }}
          p {{ margin: 8px 0; }}
          ul {{ margin: 6px 20px 10px 20px; }}
          li {{ margin-bottom: 6px; }}
          .notice {{ margin-top: 12px; font-size: 11px; }}
          a {{ text-decoration: none; }}
          a:hover {{ text-decoration: underline; }}
        </style>
      </head>
      <body>{body_html}{notice_html}</body>
    </html>""")
    body_label.setTextFormat(Qt.RichText)
    body_label.setWordWrap(True)
    body_label.setOpenExternalLinks(True)
    body_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    layout.addWidget(body_label)
    return page


def show_first_time_dialog(app_icon: QIcon | None = None, parent=None, *, force_show=False):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    language_code = _settings_language_code(settings)
    t = lambda text: translate_text(text, language_code)
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
        dialog.setWindowTitle(onboarding_text("welcome", language_code, app=APP_TITLE_WITH_VERSION))
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        pages = build_onboarding_pages(language_code)

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
        selector_label = QLabel(onboarding_text("workflow", language_code))
        workflow_selector = QComboBox(dialog)
        for page_title, _ in pages:
            workflow_selector.addItem(page_title)
        selector_layout.addWidget(selector_label)
        selector_layout.addWidget(workflow_selector, stretch=1)
        layout.addLayout(selector_layout)

        page_stack = QStackedWidget(dialog)
        page_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for page_title, page_html in pages:
            page_stack.addWidget(
                _workflow_page(
                    page_title,
                    page_html,
                    body_font_stack,
                    notice_text=onboarding_text("notice", language_code),
                )
            )
        layout.addWidget(page_stack)

        # Close onboarding before launching a task so its instructions never
        # block the existing file pickers or preparation dialog.
        launch_layout = QHBoxLayout()
        launch_actions = (
            ("Read Floppy...", "load_floppy_drive"),
            ("Preparing for...", "choose_preparation_profile"),
            ("Edit Titles", "browse_directory"),
        )
        for label, method_name in launch_actions:
            callback = getattr(parent, method_name, None)
            if callback is None:
                continue
            button = QPushButton(t(label), dialog)
            button.setObjectName("launch_" + method_name)

            def launch(_checked=False, action=callback):
                dialog.accept()
                QTimer.singleShot(0, action)

            button.clicked.connect(launch)
            launch_layout.addWidget(button)
        layout.addLayout(launch_layout)

        dont_show_checkbox = QCheckBox(t("Do not show this dialog again"))
        layout.addWidget(dont_show_checkbox)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 0, 0, 0)
        page_count_label = QLabel(dialog)
        back_button = QPushButton(t("Back"), dialog)
        next_button = QPushButton(t("Next"), dialog)
        close_button = QPushButton(t("Close"), dialog)
        nav_layout.addWidget(page_count_label)
        nav_layout.addStretch()
        nav_layout.addWidget(back_button)
        nav_layout.addWidget(next_button)
        nav_layout.addWidget(close_button)
        layout.addLayout(nav_layout)

        def set_page(index):
            index = max(0, min(index, len(pages) - 1))
            page_stack.setCurrentIndex(index)
            current_page = page_stack.currentWidget()
            if current_page is not None:
                page_stack.setFixedHeight(current_page.sizeHint().height())
            if workflow_selector.currentIndex() != index:
                workflow_selector.setCurrentIndex(index)
            back_button.setEnabled(index > 0)
            next_button.setEnabled(index < len(pages) - 1)
            page_count_label.setText(onboarding_text(
                "page_count", language_code, current=index + 1, total=len(pages)
            ))
            dialog.adjustSize()
            center_dialog_on_parent(dialog, parent)

        workflow_selector.currentIndexChanged.connect(set_page)
        back_button.clicked.connect(lambda: set_page(page_stack.currentIndex() - 1))
        next_button.clicked.connect(lambda: set_page(page_stack.currentIndex() + 1))
        close_button.clicked.connect(dialog.accept)
        set_page(0)

        center_dialog_on_parent(dialog, parent)
        dialog.exec()
        if dont_show_checkbox.isChecked():
            settings.setValue("skip_first_time_dialog", True)
