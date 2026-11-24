"""Compact application information with a session-only administrator gesture."""

import math
import time

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QTextOption
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QLabel, QScrollArea, QSizePolicy,
    QTextBrowser, QWidget,
)

from .app_info import (
    APP_AUTHOR, APP_COMPANY, APP_COMPANY_ADDRESS, APP_COPYRIGHT_NOTICE,
    APP_LICENSE, APP_TITLE_WITH_VERSION, APP_WEBSITE,
)
from .icon_utils import apply_window_icon
from .logo_assets import embedded_logo_dt, embedded_logo_lt
from .message_catalog import normalize_language_code, tr, translate_text
from .onboarding_dialog import onboarding_text
from .responsive_button_box import ResponsiveDialogButtonBox
from .ui_utils import is_dark_theme, pixmap_from_base64, resize_dialog_to_screen, scrollable_dialog_layout


class _AboutText(QTextBrowser):
    """Wrap even long words and links, leaving scrolling to the dialog body."""

    def __init__(self, text, parent, *, rich_text=False):
        super().__init__(parent)
        self._source_text = text
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.setFocusPolicy(Qt.NoFocus)
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)
        option = QTextOption(Qt.AlignHCenter)
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        if rich_text:
            self.setHtml(text)
        else:
            self.setPlainText(text)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def text(self):
        return self._source_text

    def heightForWidth(self, width):
        # Measure independently: changing the live document's width here would
        # disturb its layout and scroll position during Qt's size negotiation.
        document = self.document().clone()
        document.setTextWidth(max(1, width))
        return math.ceil(document.size().height())

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def sizeHint(self):
        return QSize(400, self.heightForWidth(400))


class AboutDialog(QDialog):
    admin_mode_activated = Signal()
    disclaimer_requested = Signal()
    CLICK_INTERVAL_SECONDS = 0.8
    REQUIRED_CLICKS = 5

    def __init__(self, parent=None, *, language_code="en", admin_mode_enabled=False):
        super().__init__(parent)
        self.language_code = normalize_language_code(language_code)
        self._admin_mode_enabled = bool(admin_mode_enabled)
        self._background_click_count = 0
        self._last_background_click = None
        self._text_fit_timer = QTimer(self)
        self._text_fit_timer.setSingleShot(True)
        self._text_fit_timer.timeout.connect(self._fit_wrapped_text)
        apply_window_icon(self)
        self.setWindowTitle(tr("dialog.about.title", self.language_code, app=APP_TITLE_WITH_VERSION))
        self.setModal(True)
        layout, content = scrollable_dialog_layout(self, width=600, height=400, spacing=8)
        layout.setContentsMargins(16, 12, 16, 12)
        content.setAlignment(Qt.AlignTop)
        self.scroll_area = self.findChild(QScrollArea)
        self._blank_surfaces = (self, self.scroll_area.viewport(), self.scroll_area.widget())

        self.logo_label = QLabel(self)
        self.logo_label.setAlignment(Qt.AlignCenter)
        pixmap = pixmap_from_base64(embedded_logo_dt if is_dark_theme() else embedded_logo_lt)
        if not pixmap.isNull():
            self.logo_label.setPixmap(pixmap.scaled(220, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.logo_label.setText("APS MIDI Prep Tool")
        content.addWidget(self.logo_label, 0, Qt.AlignHCenter)

        self.title_label = _AboutText(APP_TITLE_WITH_VERSION, self)
        font = QFont(self.font())
        font.setBold(True)
        self.title_label.setFont(font)
        content.addWidget(self.title_label)

        self.website_label = _AboutText(f'<a href="{APP_WEBSITE}">{APP_WEBSITE}</a>', self, rich_text=True)
        self.website_label.setOpenExternalLinks(True)
        self.website_label.setToolTip(self.t("Project website."))
        content.addWidget(self.website_label)

        self.info_label = _AboutText(
            f"{APP_COPYRIGHT_NOTICE}<br>"
            f"{self.t('Author')}: {APP_AUTHOR}<br>"
            f"{APP_COMPANY}<br>"
            f"{APP_COMPANY_ADDRESS}<br>"
            f"{self.t('License')}: {APP_LICENSE}", self, rich_text=True,
        )
        content.addWidget(self.info_label)

        self.notice_label = _AboutText(onboarding_text("notice", self.language_code), self)
        content.addWidget(self.notice_label)

        self.admin_status = _AboutText(tr("about.admin_enabled", self.language_code), self)
        self.admin_status.setObjectName("aboutAdminStatus")
        self.admin_status.setFont(font)
        self.admin_status.setVisible(self._admin_mode_enabled)
        content.addWidget(self.admin_status)
        self._wrapping_labels = (
            self.title_label, self.website_label, self.info_label,
            self.notice_label, self.admin_status,
        )

        self.buttons = ResponsiveDialogButtonBox(QDialogButtonBox.Close, self)
        self.buttons.button(QDialogButtonBox.Close).setText(self.t("Close"))
        disclaimer = self.buttons.addButton(self.t("Disclaimer"), QDialogButtonBox.ActionRole)
        disclaimer.clicked.connect(self.disclaimer_requested)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # All ordinary content fits without stretching the space above the footer.
        # The scroll area remains available for larger fonts and smaller screens.
        content_width = self.width() - layout.contentsMargins().left() - layout.contentsMargins().right()
        content_height = content.totalHeightForWidth(content_width)
        if content_height < 0:
            content_height = content.sizeHint().height()
        initial_height = content_height + self.buttons.sizeHint().height() + 24 + layout.spacing()
        resize_dialog_to_screen(self, width=600, height=min(520, initial_height))
        for widget in (self, *self.findChildren(QWidget)):
            widget.installEventFilter(self)

    def t(self, text):
        return translate_text(text, self.language_code)

    def _reset_gesture(self):
        self._background_click_count = 0
        self._last_background_click = None

    @staticmethod
    def _fit_wrapped_label(label):
        # Recompute from the text so a wider window or smaller font can shrink
        # the previous minimum height again.
        label.setMinimumHeight(max(0, label.heightForWidth(label.width())))

    def _fit_wrapped_text(self):
        # Reserve the complete document height at the assigned width after
        # layout events have finished. Only the outer body should scroll.
        for label in self._wrapping_labels:
            if label.isHidden():
                continue
            self._fit_wrapped_label(label)
        self.scroll_area.widget().layout().activate()

    def eventFilter(self, watched, event):
        event_type = event.type()
        width_changed = (event_type == QEvent.Resize
                         and event.size().width() != event.oldSize().width())
        if width_changed and watched in self._wrapping_labels and not watched.isHidden():
            # A vertical scrollbar can narrow labels during the queued fit.
            # Apply the new height immediately at that assigned width instead
            # of leaving clipped text until another timer callback runs.
            self._fit_wrapped_label(watched)
        if (event_type in (QEvent.FontChange, QEvent.StyleChange, QEvent.Show)
                or width_changed):
            # Do not fit from LayoutRequest or height-only resize events: the
            # resulting minimum-height updates would otherwise schedule forever.
            if not self._text_fit_timer.isActive():
                self._text_fit_timer.start(0)
        if event_type not in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            return super().eventFilter(watched, event)
        position = self.mapFromGlobal(event.globalPosition().toPoint())
        target = self.childAt(position) or self
        if event.button() != Qt.LeftButton or target not in self._blank_surfaces:
            self._reset_gesture()
            return False
        if self._admin_mode_enabled:
            return False
        now = time.monotonic()
        if self._last_background_click is None or now - self._last_background_click > self.CLICK_INTERVAL_SECONDS:
            self._background_click_count = 0
        self._background_click_count += 1
        self._last_background_click = now
        if self._background_click_count == self.REQUIRED_CLICKS:
            self._admin_mode_enabled = True
            self._reset_gesture()
            self.admin_status.show()
            if not self.isMaximized():
                status_height = self.admin_status.heightForWidth(self.scroll_area.viewport().width())
                resize_dialog_to_screen(
                    self, width=self.width(),
                    height=self.height() + max(status_height, self.admin_status.minimumSizeHint().height())
                    + self.scroll_area.widget().layout().spacing(),
                )
            self.scroll_area.widget().layout().activate()
            self.scroll_area.ensureWidgetVisible(self.admin_status)
            self.admin_mode_activated.emit()
        # Consume accepted background presses so event propagation cannot count
        # the same physical click again on the viewport, body, and dialog.
        return True

    def hideEvent(self, event):
        self._reset_gesture()
        super().hideEvent(event)
