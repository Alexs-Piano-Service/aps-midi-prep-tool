"""Batch audio recovery is available only in the current Admin Mode session."""

from types import SimpleNamespace

from PySide6.QtGui import QCloseEvent

from aps_midi_prep_tool_app import audio_decode_batch_dialog
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr
from test_window_menu_behavior import window


def _activate_admin(window, monkeypatch):
    def execute(about, *, resize_to_contents):
        assert not resize_to_contents
        about.admin_mode_activated.emit()

    with monkeypatch.context() as patch:
        patch.setattr(window, "_exec_child_dialog", execute)
        window.show_about_dialog()


def test_batch_decode_requires_admin_even_with_saved_shortcut(window, monkeypatch):
    opened = []
    monkeypatch.setattr(audio_decode_batch_dialog, "AudioDecodeBatchDialog",
                        lambda *_args: opened.append(True))
    action = window.utilitiesAudioDecodeBatchAction
    shortcut_id = "utilities.audio_decode_batch"
    window.settings.setValue(window._shortcut_settings_key(shortcut_id), "Ctrl+Alt+9")
    window._setup_keyboard_shortcuts()
    window._update_menu_actions()
    assert not action.isVisible()
    assert not action.isEnabled()
    assert shortcut_id not in window.keyboardShortcutObjects
    assert shortcut_id not in {spec["id"] for spec in window._keyboard_shortcut_specs()}
    window.show_audio_decode_batch_utility()
    window._trigger_keyboard_shortcut("utilitiesAudioDecodeBatchAction")
    assert not opened

    saved = {key: window.settings.value(key) for key in window.settings.allKeys()}
    _activate_admin(window, monkeypatch)
    assert action.isVisible()
    assert action.isEnabled()
    assert action in window.utilitiesMenu.actions()
    assert shortcut_id in window.keyboardShortcutObjects
    assert {key: window.settings.value(key) for key in window.settings.allKeys()} == saved


def test_batch_decode_opens_without_loaded_songs_and_reuses_dialog(window, monkeypatch):
    _activate_admin(window, monkeypatch)
    opened, raised, disposed = [], [], []

    class Dialog:
        def __init__(self, settings, parent):
            assert settings is window.settings
            assert parent is window

        def raise_(self):
            raised.append(self)

        def activateWindow(self):
            pass

        def deleteLater(self):
            disposed.append(self)

    def execute(dialog, *, resize_to_contents):
        assert not resize_to_contents
        assert window.audioDecodeBatchDialog is dialog
        opened.append(dialog)
        window.show_audio_decode_batch_utility()

    monkeypatch.setattr(audio_decode_batch_dialog, "AudioDecodeBatchDialog", Dialog)
    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    window.utilitiesAudioDecodeBatchAction.trigger()
    assert len(opened) == 1
    assert opened == raised == disposed
    assert window.audioDecodeBatchDialog is None


def test_batch_decode_does_not_start_while_disk_worker_is_busy(window, monkeypatch):
    _activate_admin(window, monkeypatch)
    monkeypatch.setattr(audio_decode_batch_dialog, "AudioDecodeBatchDialog",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("Opened during disk work")))
    with monkeypatch.context() as patch:
        patch.setattr(window, "_disk_worker_busy", lambda: True)
        window.show_audio_decode_batch_utility()


def test_main_window_waits_for_batch_decode_cancellation(window, monkeypatch):
    cancelled = []
    window.audioDecodeBatchDialog = SimpleNamespace(is_busy=True, reject=lambda: cancelled.append(True))
    event = QCloseEvent()
    with monkeypatch.context() as patch:
        patch.setattr(window, "_cleanup_for_close",
                      lambda: (_ for _ in ()).throw(AssertionError("Cleanup during decode")))
        window.closeEvent(event)
    assert not event.isAccepted()
    assert cancelled == [True]
    window.audioDecodeBatchDialog = None


def test_batch_decode_action_tracks_application_language(window, monkeypatch):
    _activate_admin(window, monkeypatch)
    for language in SUPPORTED_LANGUAGES:
        window.currentLanguage = language.code
        window._refresh_static_action_text()
        assert window.utilitiesAudioDecodeBatchAction.text() == tr("abd.action", language.code)
