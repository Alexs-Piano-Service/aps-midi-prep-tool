"""A persistent, optional review invitation after successful physical reads."""

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from .message_catalog import tr


REVIEW_URL = "https://www.alexanderpeppe.com/share-your-experience-with-aps-midi-prep-tool/"
SETTING_SUCCESSFUL_DISK_READS = "successful_disk_reads"
SETTING_REVIEW_PROMPT_AFTER_READS = "review_prompt_after_reads"
SETTING_NEVER_ASK_FOR_REVIEW = "never_ask_for_review"
REMINDER_INTERVAL = 3


class ReviewPrompt(QObject):
    def __init__(self, settings, window):
        super().__init__(window)
        self.settings = settings
        self.window = window
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.show_when_idle)

    def record_successful_read(self, source_kind):
        # Opening/converting an image or reopening an existing raw capture is
        # not a new physical disk read. Failure/cancellation paths never call us.
        if source_kind not in {"floppy_usb", "floppy_gw"}:
            return
        count = self.settings.value(SETTING_SUCCESSFUL_DISK_READS, 0, type=int) + 1
        self.settings.setValue(SETTING_SUCCESSFUL_DISK_READS, count)
        self.settings.sync()
        if self.is_due() and not self.timer.isActive():
            self.timer.start(0)

    def is_due(self):
        return (
            not self.settings.value(SETTING_NEVER_ASK_FOR_REVIEW, False, type=bool)
            and self.settings.value(SETTING_SUCCESSFUL_DISK_READS, 0, type=int)
            >= self.settings.value(SETTING_REVIEW_PROMPT_AFTER_READS, REMINDER_INTERVAL, type=int)
        )

    def show_when_idle(self):
        if not self.is_due() or not self.window.isVisible():
            return
        if (
            self.window._disk_worker_busy()
            or QApplication.activeModalWidget() is not None
            or QApplication.activePopupWidget() is not None
        ):
            self.timer.start(500)
            return

        # Closing the dialog is equivalent to Remind me later. Save the next
        # threshold before entering a nested event loop so reads cannot re-prompt.
        count = self.settings.value(SETTING_SUCCESSFUL_DISK_READS, 0, type=int)
        self.settings.setValue(SETTING_REVIEW_PROMPT_AFTER_READS, count + REMINDER_INTERVAL)
        self.settings.sync()
        language = self.settings.value("language", "en")
        dialog = QMessageBox(self.window)
        dialog.setIcon(QMessageBox.Information)
        dialog.setWindowTitle(tr("review_prompt.title", language))
        dialog.setTextFormat(Qt.PlainText)
        dialog.setText(tr("review_prompt.message", language))
        review = dialog.addButton(tr("review_prompt.review", language), QMessageBox.AcceptRole)
        later = dialog.addButton(tr("review_prompt.later", language), QMessageBox.RejectRole)
        never = dialog.addButton(tr("review_prompt.never", language), QMessageBox.NoRole)
        dialog.setDefaultButton(later)
        dialog.setEscapeButton(later)
        dialog.exec()
        choice = dialog.clickedButton()
        if choice is never or (choice is review and QDesktopServices.openUrl(QUrl(REVIEW_URL))):
            self.settings.setValue(SETTING_NEVER_ASK_FOR_REVIEW, True)
            self.settings.sync()
        dialog.deleteLater()
