"""The About gesture grants only an explicit, in-memory administrator session."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from aps_midi_prep_tool_app import about_dialog
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr
from test_window_menu_behavior import window


@pytest.fixture
def dialog():
    app = QApplication.instance() or QApplication([])
    instance = about_dialog.AboutDialog()
    instance.show()
    app.processEvents()
    yield instance
    instance.close()
    instance.deleteLater()
    app.processEvents()


def click_background(dialog, *, button=Qt.LeftButton):
    QTest.mouseClick(dialog, button, pos=QPoint(5, 5))


def test_five_successive_background_clicks_activate_once(dialog):
    activated = QSignalSpy(dialog.admin_mode_activated)
    for _ in range(4):
        click_background(dialog)
    assert activated.count() == 0
    assert not dialog.admin_status.isVisible()
    click_background(dialog)
    assert activated.count() == 1
    assert dialog.admin_status.isVisible()
    for _ in range(6):
        click_background(dialog)
    assert activated.count() == 1


def test_double_click_press_counts_as_one_physical_click(dialog):
    activated = QSignalSpy(dialog.admin_mode_activated)
    # Qt replaces the second press with a double-click event. It must count
    # once, including when users click faster than the double-click interval.
    click_background(dialog)
    QTest.mouseDClick(dialog, Qt.LeftButton, pos=QPoint(5, 5))
    click_background(dialog)
    QTest.mouseDClick(dialog, Qt.LeftButton, pos=QPoint(5, 5))
    assert activated.count() == 0
    click_background(dialog)
    assert activated.count() == 1


def test_background_child_event_does_not_count_again_when_propagated(dialog):
    activated = QSignalSpy(dialog.admin_mode_activated)
    body = dialog.scroll_area.widget()
    # An eight-pixel layout gap is genuine open space inside the scroll body.
    position = QPoint(body.width() // 2, dialog.logo_label.geometry().bottom() + 3)
    assert body.childAt(position) is None
    for _ in range(4):
        QTest.mouseClick(body, Qt.LeftButton, pos=position)
    assert activated.count() == 0
    QTest.mouseClick(body, Qt.LeftButton, pos=position)
    assert activated.count() == 1


def test_open_space_beside_the_logo_accepts_the_gesture(dialog):
    activated = QSignalSpy(dialog.admin_mode_activated)
    body = dialog.scroll_area.widget()
    position = QPoint(8, dialog.logo_label.geometry().center().y())
    assert body.childAt(position) is None
    for _ in range(5):
        QTest.mouseClick(body, Qt.LeftButton, pos=position)
    assert activated.count() == 1


def test_pause_breaks_the_click_sequence(dialog, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(about_dialog.time, "monotonic", lambda: clock[0])
    activated = QSignalSpy(dialog.admin_mode_activated)
    for _ in range(4):
        click_background(dialog)
        clock[0] += 0.1
    clock[0] += 0.81
    click_background(dialog)
    assert activated.count() == 0
    for _ in range(4):
        click_background(dialog)
    assert activated.count() == 1


@pytest.mark.parametrize("target", ["logo_label", "title_label", "website_label", "info_label", "notice_label", "disclaimer", "right_click"])
def test_content_and_nonleft_clicks_break_the_sequence(dialog, target):
    activated = QSignalSpy(dialog.admin_mode_activated)
    for _ in range(4):
        click_background(dialog)
    if target == "right_click":
        click_background(dialog, button=Qt.RightButton)
    else:
        if target == "disclaimer":
            widget = next(button for button in dialog.buttons.buttons()
                          if dialog.buttons.buttonRole(button) == QDialogButtonBox.ActionRole)
        else:
            widget = getattr(dialog, target)
            if target == "website_label":
                widget.setOpenExternalLinks(False)
        QTest.mouseClick(widget, Qt.LeftButton)
    for _ in range(4):
        click_background(dialog)
    assert activated.count() == 0
    click_background(dialog)
    assert activated.count() == 1


def test_dismissal_breaks_an_unfinished_sequence(dialog):
    activated = QSignalSpy(dialog.admin_mode_activated)
    for _ in range(4):
        click_background(dialog)
    dialog.reject()
    dialog.show()
    for _ in range(4):
        click_background(dialog)
    assert activated.count() == 0
    click_background(dialog)
    assert activated.count() == 1


def test_compact_initial_size_preserves_branding_and_footer(dialog):
    assert dialog.height() < 400
    assert not dialog.scroll_area.verticalScrollBar().isVisible()
    assert not dialog.scroll_area.horizontalScrollBar().isVisible()
    assert dialog.title_label.text() == about_dialog.APP_TITLE_WITH_VERSION
    assert about_dialog.APP_COMPANY_ADDRESS in dialog.info_label.text()
    assert about_dialog.APP_LICENSE in dialog.info_label.text()
    assert dialog.website_label.openExternalLinks()
    assert dialog.buttons.button(QDialogButtonBox.Close).isVisible()
    blank_below_notice = dialog.scroll_area.widget().height() - dialog.notice_label.geometry().bottom()
    assert blank_below_notice < 24


def test_activation_remains_visible_when_about_reopens_without_saving_settings(window, monkeypatch):
    saved_settings = {key: window.settings.value(key) for key in window.settings.allKeys()}
    visits = []

    def execute(instance, *, resize_to_contents):
        assert not resize_to_contents
        instance.show()
        QApplication.processEvents()
        if not visits:
            assert not instance.admin_status.isVisible()
            for _ in range(5):
                click_background(instance)
            assert window._admin_mode_enabled
        assert instance.admin_status.isVisible()
        visits.append(True)
        instance.reject()

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    assert not window._admin_mode_enabled
    window.show_about_dialog()
    window.show_about_dialog()
    assert len(visits) == 2
    assert {key: window.settings.value(key) for key in window.settings.allKeys()} == saved_settings


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
@pytest.mark.parametrize("point_size", [14, 24])
def test_admin_confirmation_is_localized_and_large_fonts_remain_accessible(language, point_size):
    app = QApplication.instance() or QApplication([])
    original = QFont(app.font())
    app.setFont(QFont(original.family(), point_size))
    instance = about_dialog.AboutDialog(language_code=language, admin_mode_enabled=True)
    try:
        instance.resize(500, 400)
        instance.show()
        app.processEvents()
        assert instance.admin_status.text() == tr("about.admin_enabled", language)
        assert instance.admin_status.isVisible()
        assert instance.scroll_area.horizontalScrollBar().maximum() == 0
        for label in (instance.title_label, instance.info_label, instance.notice_label, instance.admin_status):
            assert label.height() >= label.heightForWidth(label.width())
        assert instance.rect().contains(instance.buttons.geometry())
        assert instance.buttons.button(QDialogButtonBox.Close).isVisible()
    finally:
        instance.close()
        instance.deleteLater()
        app.setFont(original)
        app.processEvents()


def test_wrapped_text_refits_when_resized_and_font_changes(dialog):
    font = QFont(dialog.font())
    font.setPointSize(24)
    dialog.setFont(font)
    dialog.resize(500, 400)
    QTest.qWait(20)
    for label in (dialog.info_label, dialog.notice_label):
        assert label.height() >= label.heightForWidth(label.width())
    narrow_height = dialog.info_label.height()
    assert dialog.scroll_area.verticalScrollBar().maximum() > 0

    dialog.resize(750, 500)
    QTest.qWait(20)
    assert dialog.info_label.height() < narrow_height
    wide_height = dialog.info_label.height()
    font.setPointSize(12)
    dialog.setFont(font)
    QTest.qWait(20)
    assert dialog.info_label.height() < wide_height
    for label in (dialog.info_label, dialog.notice_label):
        assert label.height() >= label.heightForWidth(label.width())
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert dialog.rect().contains(dialog.buttons.geometry())

    # Repeated layout requests from minimum-height changes must settle rather
    # than continuously requeueing fits after the window stops changing.
    fits = QSignalSpy(dialog._text_fit_timer.timeout)
    QTest.qWait(30)
    assert fits.count() == 0
