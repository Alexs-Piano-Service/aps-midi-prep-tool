"""Review timing, persistence, browser actions, and successful-read integration."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from aps_midi_prep_tool_app import main_window, review_prompt
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr
from aps_midi_prep_tool_app.review_prompt import (
    REVIEW_URL, SETTING_NEVER_ASK_FOR_REVIEW, SETTING_REVIEW_PROMPT_AFTER_READS,
    SETTING_SUCCESSFUL_DISK_READS, ReviewPrompt,
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings_path = str(tmp_path / "review.ini")
    settings = QSettings(settings_path, QSettings.IniFormat)
    window = QWidget()
    window._disk_worker_busy = lambda: False
    window.show()
    prompt = ReviewPrompt(settings, window)
    state = SimpleNamespace(
        app=app, window=window, prompt=prompt, settings=settings, settings_path=settings_path,
        choice=QMessageBox.RejectRole, dialogs=[], urls=[], browser_ok=True,
    )
    original_exec = QMessageBox.exec
    def execute(dialog):
        state.dialogs.append(dialog.text())
        assert dialog.defaultButton().text() == tr("review_prompt.later", settings.value("language", "en"))
        if state.choice is None:
            QTimer.singleShot(0, dialog.close)
        else:
            button = next(button for button in dialog.buttons() if dialog.buttonRole(button) == state.choice)
            QTimer.singleShot(0, button.click)
        return original_exec(dialog)
    def open_url(url):
        state.urls.append(url.toString())
        return state.browser_ok
    monkeypatch.setattr(QMessageBox, "exec", execute)
    monkeypatch.setattr(review_prompt.QDesktopServices, "openUrl", open_url)
    yield state
    prompt.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


def read(state, kind="floppy_usb"):
    state.prompt.record_successful_read(kind)
    state.app.processEvents()


def test_prompts_on_third_read_and_reminds_after_three_more_across_restarts(setup):
    state = setup
    read(state)
    read(state, "floppy_gw")
    assert state.dialogs == []
    read(state)
    assert len(state.dialogs) == 1
    assert state.settings.value(SETTING_REVIEW_PROMPT_AFTER_READS, type=int) == 6
    assert not state.urls
    state.prompt.timer.stop()
    state.prompt = ReviewPrompt(QSettings(state.settings_path, QSettings.IniFormat), state.window)
    read(state)
    read(state)
    assert len(state.dialogs) == 1
    read(state)
    assert len(state.dialogs) == 2


@pytest.mark.parametrize("choice", [QMessageBox.NoRole, QMessageBox.AcceptRole])
def test_never_and_review_choices_stop_future_requests(setup, choice):
    state = setup
    state.choice = choice
    for _ in range(3):
        read(state)
    assert state.settings.value(SETTING_NEVER_ASK_FOR_REVIEW, type=bool) is True
    assert state.urls == ([REVIEW_URL] if choice == QMessageBox.AcceptRole else [])
    state.prompt = ReviewPrompt(QSettings(state.settings_path, QSettings.IniFormat), state.window)
    for _ in range(9):
        read(state)
    assert len(state.dialogs) == 1


@pytest.mark.parametrize("kind", ["image", "image_convert", "floppy_gw_capture", "", None])
def test_image_operations_do_not_count_as_physical_reads(setup, kind):
    for _ in range(4):
        read(setup, kind)
    assert not setup.dialogs
    assert setup.settings.value(SETTING_SUCCESSFUL_DISK_READS, 0, type=int) == 0


@pytest.mark.parametrize("choice", [None, QMessageBox.AcceptRole])
def test_close_or_failed_browser_open_retains_later_reminder(setup, choice):
    setup.choice = choice
    setup.browser_ok = False
    for _ in range(3):
        read(setup)
    assert len(setup.dialogs) == 1
    assert setup.settings.value(SETTING_REVIEW_PROMPT_AFTER_READS, type=int) == 6
    assert not setup.settings.value(SETTING_NEVER_ASK_FOR_REVIEW, False, type=bool)


def test_prompt_waits_for_disk_work_and_other_dialogs(setup, monkeypatch):
    state = setup
    state.window._disk_worker_busy = lambda: True
    for _ in range(3):
        read(state)
    assert not state.dialogs
    assert state.prompt.timer.isActive()
    state.window._disk_worker_busy = lambda: False
    with monkeypatch.context() as patch:
        patch.setattr(QApplication, "activeModalWidget", lambda: state.window)
        state.prompt.show_when_idle()
        assert not state.dialogs
    state.prompt.timer.stop()
    state.prompt.show_when_idle()
    assert len(state.dialogs) == 1


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_review_dialog_is_localized(setup, language):
    setup.settings.setValue("language", language)
    for _ in range(3):
        read(setup)
    assert setup.dialogs == [tr("review_prompt.message", language)]
    assert "review_prompt." not in setup.dialogs[0]


def test_only_successfully_opened_physical_reads_increment_counter(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "success.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    window = main_window.MidiTitleWindow()
    monkeypatch.setattr(window, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_show_operation_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_activate_disk_session", lambda *_args: None)
    monkeypatch.setattr(window, "_offer_post_load_sequence_conversions", lambda: None)
    session = SimpleNamespace(source_kind="floppy_usb", source_name="Test floppy", cleanup=lambda: None)
    listing = SimpleNamespace(entries=[])
    try:
        window.diskLoadContext = {"load_kind": "image"}
        window._on_disk_load_success(session, listing)
        assert settings.value(SETTING_SUCCESSFUL_DISK_READS, 0, type=int) == 0
        window.diskLoadContext = {"load_kind": "floppy_usb"}
        window._on_disk_load_success(session, listing)
        assert settings.value(SETTING_SUCCESSFUL_DISK_READS, type=int) == 1
        def fail(*_args):
            raise OSError("Could not open the disk")
        monkeypatch.setattr(window, "_activate_disk_session", fail)
        window._on_disk_load_success(session, listing)
        assert settings.value(SETTING_SUCCESSFUL_DISK_READS, type=int) == 1
        window._on_disk_load_cancelled("Cancelled")
        window._on_disk_load_failure("Read failed")
        assert settings.value(SETTING_SUCCESSFUL_DISK_READS, type=int) == 1
    finally:
        window.close()
        app.processEvents()
