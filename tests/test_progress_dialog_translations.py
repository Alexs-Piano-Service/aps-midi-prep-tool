import html
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QProgressDialog, QPushButton, QWidget

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr, translate_text
from aps_midi_prep_tool_app.onboarding_dialog import onboarding_text


class _DialogHarness(QWidget):
    _prepare_progress_dialog = MidiTitleWindow._prepare_progress_dialog
    _progress_dialog_title = MidiTitleWindow._progress_dialog_title
    _translate_dialog_tree = MidiTitleWindow._translate_dialog_tree
    _translate_dialog_button_box = MidiTitleWindow._translate_dialog_button_box
    _make_dialog_button_box = MidiTitleWindow._make_dialog_button_box
    _set_progress_dialog_message = MidiTitleWindow._set_progress_dialog_message

    def __init__(self, code):
        super().__init__()
        self.code = code
        self.disclaimer_opened = False

    def _language_code(self):
        return self.code

    def _lt(self, source, **fields):
        return translate_text(source, self.code).format(**fields)

    def _t(self, message_id, **fields):
        return tr(message_id, self.code, **fields)

    def _center_child_dialog(self, _dialog, **_kwargs):
        pass

    def _make_heading_font(self, reference_point_size=18):
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * reference_point_size / 12)
        font.setWeight(QFont.Bold)
        return font

    def show_disclaimer_dialog(self):
        self.disclaimer_opened = True


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_progress_initial_title_stage_and_cancel_are_localized(code):
    app = QApplication.instance() or QApplication([])
    window = _DialogHarness(code)
    dialog = QProgressDialog("Creating new image...", "Cancel", 0, 4, window)
    dialog.setWindowTitle("")
    window._prepare_progress_dialog(dialog)
    expected_initial = translate_text("Creating new image...", code)
    assert dialog.labelText() == expected_initial
    assert dialog.windowTitle() == expected_initial.rstrip(".")
    assert any(button.text() == translate_text("Cancel", code) for button in dialog.findChildren(QPushButton))
    window._set_progress_dialog_message(dialog, "Exporting floppy image...")
    assert dialog.labelText() == translate_text("Exporting floppy image...", code)
    window._set_progress_dialog_message(dialog, "Cancelling...")
    assert dialog.labelText() == translate_text("Cancelling...", code)
    diagnostic = "gw: track 2.0: 9/9 sectors"
    window._set_progress_dialog_message(dialog, diagnostic)
    assert dialog.labelText() == diagnostic
    window.close()
    app.processEvents()


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_about_dialog_renders_translated_notice_and_opens_disclaimer(code):
    app = QApplication.instance() or QApplication([])
    window = _DialogHarness(code)
    inspected = []

    def inspect(dialog):
        dialog.show()
        app.processEvents()
        labels = dialog.findChildren(QLabel)
        expected_notice = html.escape(onboarding_text("notice", code))
        assert any(expected_notice in label.text() for label in labels)
        assert any(window._lt("Author") in label.text() and window._lt("License") in label.text() for label in labels)
        buttons = dialog.findChild(QDialogButtonBox)
        disclaimer = next(button for button in buttons.buttons() if button.text() == window._lt("Disclaimer"))
        disclaimer.click()
        assert window.disclaimer_opened
        for label in labels:
            if label.wordWrap():
                assert label.height() >= label.heightForWidth(label.width())
        inspected.append(True)
        dialog.close()

    window._exec_child_dialog = inspect
    MidiTitleWindow.show_about_dialog(window)
    assert inspected == [True]
    window.close()
