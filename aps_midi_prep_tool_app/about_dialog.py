"""Compact application information with a session-only administrator gesture."""

import time

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QScrollArea, QWidget

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

        self.title_label = QLabel(APP_TITLE_WITH_VERSION, self)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        font = QFont(self.font())
        font.setBold(True)
        self.title_label.setFont(font)
        content.addWidget(self.title_label)

        self.website_label = QLabel(f'<a href="{APP_WEBSITE}">{APP_WEBSITE}</a>', self)
        self.website_label.setAlignment(Qt.AlignCenter)
        self.website_label.setWordWrap(True)
        self.website_label.setOpenExternalLinks(True)
        self.website_label.setToolTip(self.t("Project website."))
        content.addWidget(self.website_label)

        self.info_label = QLabel(
            f"{APP_COPYRIGHT_NOTICE}<br>"
            f"{self.t('Author')}: {APP_AUTHOR}<br>"
            f"{APP_COMPANY}<br>"
            f"{APP_COMPANY_ADDRESS}<br>"
            f"{self.t('License')}: {APP_LICENSE}", self,
        )
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setWordWrap(True)
        content.addWidget(self.info_label)

        self.notice_label = QLabel(onboarding_text("notice", self.language_code), self)
        self.notice_label.setTextFormat(Qt.PlainText)
        self.notice_label.setAlignment(Qt.AlignCenter)
        self.notice_label.setWordWrap(True)
        content.addWidget(self.notice_label)

        self.admin_status = QLabel(tr("about.admin_enabled", self.language_code), self)
        self.admin_status.setObjectName("aboutAdminStatus")
        self.admin_status.setAlignment(Qt.AlignCenter)
        self.admin_status.setWordWrap(True)
        self.admin_status.setFont(font)
        self.admin_status.setVisible(self._admin_mode_enabled)
        content.addWidget(self.admin_status)

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

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
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
