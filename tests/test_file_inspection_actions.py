"""Direct inspection edits update the selected source without preview filters."""

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.conversion_review import localize_music_error
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.midi_channel_merger import merge_midi_channels_to_channel0_bytes
from aps_midi_prep_tool_app.midi_type0_converter import _convert_midi_bytes_to_type0


class _SilentPlayer(QObject):
    """Keep rendered widget tests independent of host audio-server startup."""
    PlaybackState = main_window.QMediaPlayer.PlaybackState
    MediaStatus = main_window.QMediaPlayer.MediaStatus
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    mediaStatusChanged = Signal(object)
    playbackStateChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._position = 0
        self._source = QUrl()

    def setAudioOutput(self, output):
        self.audio_output = output

    def setSource(self, source):
        self._source = source

    def source(self):
        return self._source

    def setPlaybackRate(self, rate):
        self.rate = rate

    def setPosition(self, position):
        self._position = position

    def position(self):
        return self._position

    def duration(self):
        return 0

    def playbackState(self):
        return self.PlaybackState.StoppedState

    def stop(self):
        self._position = 0


class _SilentAudioOutput(QObject):
    def setVolume(self, value):
        self._volume = value

    def volume(self):
        return self._volume


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _midi(file_type=1, *, empty=False):
    first = b"\x00\xC0\x00\x00\x90\x3C\x40\x60\x80\x3C\x00\x00\xFF\x2F\x00"
    second = b"\x00\xC4\x28\x00\x94\x43\x50\x60\x84\x43\x00\x00\xFF\x2F\x00"
    if empty:
        tracks = [b"\x00\xFF\x2F\x00"]
    elif file_type == 0:
        tracks = [first]
    else:
        tracks = [first, second]
    return struct.pack(">4sIHHH", b"MThd", 6, file_type, len(tracks), 96) + b"".join(
        b"MTrk" + len(track).to_bytes(4, "big") + track for track in tracks
    )


def _item(path, *, row=0, source_path=None, session_id=None, title="Original"):
    return {
        "path": str(path), "source_path": str(source_path or path), "session_id": session_id,
        "row": row, "display_name": Path(path).name, "label": Path(path).name, "title": title,
    }


@pytest.fixture
def dialog_factory(application, monkeypatch):
    dialogs = []
    parents = []
    monkeypatch.setattr(main_window, "QMediaPlayer", _SilentPlayer)
    monkeypatch.setattr(main_window, "QAudioOutput", _SilentAudioOutput)
    monkeypatch.setattr(main_window, "_midi_output_ports", lambda: ([], ""))
    monkeypatch.setattr(main_window, "_available_soundfonts", lambda: [])
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: None)
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(main_window.QMessageBox, name, lambda *args, **kwargs: pytest.fail("Unexpected modal dialog"))

    def create(items, *, language="en", **kwargs):
        parent = QWidget()
        parent._language_code = lambda: language
        dialog = main_window.FileInspectionDialog(items, parent, **kwargs)
        parents.append(parent)
        dialogs.append(dialog)
        dialog.show()
        application.processEvents()
        return dialog

    yield create
    for dialog in dialogs:
        dialog.preview_render_worker = None
        dialog.inspection_render_worker = None
        dialog.close()
        dialog.deleteLater()
    for parent in parents:
        parent.deleteLater()
    application.processEvents()


def test_type0_click_stops_preview_updates_the_item_and_reloads_all_details(dialog_factory, tmp_path):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi())
    staged = tmp_path / "staged.mid"
    item = _item(source, row=12, session_id=77)
    calls = []

    def edit(current, action):
        calls.append((current, action))
        assert not dialog.convert_type0_button.isEnabled()
        assert not dialog.merge_piano_button.isEnabled()
        assert not dialog.file_tree.isEnabled()
        assert not dialog.playback_timer.isActive()
        assert not dialog.preview_audio_path
        assert not cache.exists()
        assert dialog.edit_status_label.text() == "Editing…"
        result, changed = _convert_midi_bytes_to_type0(Path(current["path"]).read_bytes())
        staged.write_bytes(result)
        return {"changed": changed, "item": {**current, "path": str(staged), "title": "Converted"}, "message": "Conversion complete"}

    dialog = dialog_factory([item], edit_callback=edit)
    cache = tmp_path / "preview.wav"
    cache.write_bytes(b"old preview")
    dialog.preview_audio_path = str(cache)
    dialog.playback_timer.start()
    old_details = dialog.details_box.toPlainText()

    QTest.mouseClick(dialog.convert_type0_button, Qt.LeftButton)

    assert calls == [(item, "type0")]
    assert dialog.items[0] == dialog._current_item()
    assert dialog._current_item()["source_path"] == str(source)
    assert dialog._current_item()["session_id"] == 77
    assert dialog._current_item()["path"] == str(staged)
    assert "Converted" in dialog.file_tree.currentItem().text(0)
    assert dialog.current_midi_bytes == staged.read_bytes()
    assert {note["channel"] for note in dialog.current_notes} == {1, 5}
    assert dialog.current_channel_info[5]["programs"] == [40]
    assert dialog.details_box.toPlainText() != old_details
    assert not dialog.convert_type0_button.isEnabled()
    assert dialog.merge_piano_button.isEnabled()
    assert dialog.file_tree.isEnabled()
    assert dialog.edit_status_label.text() == "Conversion complete"
    assert source.read_bytes() == _midi()


def test_piano_click_merges_hidden_channels_and_keeps_smf_type(dialog_factory, tmp_path, monkeypatch):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi())
    staged = tmp_path / "piano.mid"
    calls = []

    def edit(item, action):
        calls.append(action)
        converted, changed = merge_midi_channels_to_channel0_bytes(Path(item["path"]).read_bytes())
        staged.write_bytes(converted)
        return {"changed": changed, "item": {**item, "path": str(staged)}, "message": "Piano merge complete"}

    dialog = dialog_factory([_item(source)], edit_callback=edit)
    dialog.channel_checkboxes[5].setChecked(False)
    assert {note["channel"] for note in dialog.visible_notes} == {1}
    monkeypatch.setattr(dialog, "_filtered_midi_bytes_for_preview", lambda **kwargs: pytest.fail("Preview filter used for source edit"))

    QTest.mouseClick(dialog.merge_piano_button, Qt.LeftButton)

    assert calls == ["piano"]
    assert len(dialog.all_notes) == 2
    assert {note["channel"] for note in dialog.all_notes} == {1}
    assert dialog.current_channel_info[1]["programs"] == [0]
    assert int.from_bytes(dialog.current_midi_bytes[8:10], "big") == 1
    assert dialog.convert_type0_button.isEnabled()
    assert source.read_bytes() == _midi()


def test_callback_error_reloads_the_original_and_shows_inline_feedback(dialog_factory, tmp_path):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi())

    def edit(_item, _action):
        raise ValueError("These actions require a MIDI file.")

    dialog = dialog_factory([_item(source)], edit_callback=edit)
    previous = list(dialog.all_notes)

    QTest.mouseClick(dialog.convert_type0_button, Qt.LeftButton)

    assert dialog.current_midi_bytes == source.read_bytes()
    assert dialog.all_notes == previous
    assert dialog.items[0]["path"] == str(source)
    assert dialog.edit_status_label.text() == "Could not update this file: These actions require a MIDI file."
    assert dialog.edit_status_label.isVisible()
    assert dialog.convert_type0_button.isEnabled()
    assert dialog.file_tree.isEnabled()


def test_type0_is_disabled_for_already_type0_and_piano_noop_remains_reviewable(dialog_factory, tmp_path):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi(0))
    calls = []

    def edit(item, action):
        calls.append(action)
        return {"changed": False, "item": item, "message": "Already prepared"}

    dialog = dialog_factory([_item(source)], edit_callback=edit)
    assert not dialog.convert_type0_button.isEnabled()
    QTest.mouseClick(dialog.convert_type0_button, Qt.LeftButton)
    assert not calls
    QTest.mouseClick(dialog.merge_piano_button, Qt.LeftButton)
    assert calls == ["piano"]
    assert dialog.edit_status_label.text() == "Already prepared"
    assert dialog.current_midi_bytes == source.read_bytes()


@pytest.mark.parametrize("kind, expected", [("type2", (False, True)), ("empty", (False, False)), ("malformed", (False, False)), ("eseq", (False, False)), ("no_callback", (False, False))])
def test_actions_check_actual_source_format_and_content(dialog_factory, tmp_path, kind, expected):
    payload = _midi(2 if kind == "type2" else 1, empty=kind == "empty")
    if kind == "malformed":
        payload = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x00\x60MTrk\x00\x00\x00\x20"
    if kind == "eseq":
        payload = convert_midi_bytes_to_eseq_bytes(_midi(0), timing_policy="preserve", pedal_policy="preserve")
    source = tmp_path / ("source.FIL" if kind == "eseq" else "source.mid")
    source.write_bytes(payload)
    callback = None if kind == "no_callback" else lambda *_args: pytest.fail("Disabled action called")
    dialog = dialog_factory([_item(source)], edit_callback=callback)

    assert (dialog.convert_type0_button.isEnabled(), dialog.merge_piano_button.isEnabled()) == expected
    if kind == "eseq":
        assert dialog.current_midi_bytes.startswith(b"MThd")
        assert dialog.all_notes
    for button in (dialog.convert_type0_button, dialog.merge_piano_button):
        if not button.isEnabled():
            QTest.mouseClick(button, Qt.LeftButton)


@pytest.mark.parametrize("method", ["_set_preview_rendering", "_set_inspection_rendering"])
def test_rendering_disables_actions_and_completion_restores_them(dialog_factory, tmp_path, method):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi())
    dialog = dialog_factory([_item(source)], edit_callback=lambda *_args: pytest.fail("Busy action called"))

    getattr(dialog, method)(True)
    assert not dialog.convert_type0_button.isEnabled()
    assert not dialog.merge_piano_button.isEnabled()
    dialog._edit_current_file("type0")
    getattr(dialog, method)(False)

    assert dialog.convert_type0_button.isEnabled()
    assert dialog.merge_piano_button.isEnabled()


def test_window_activation_tracks_stable_identity_across_undo_and_reordering(dialog_factory, tmp_path, application):
    source = tmp_path / "source.mid"
    original = _midi()
    source.write_bytes(original)
    other = tmp_path / "other.mid"
    other.write_bytes(_midi(0))
    saved = tmp_path / "staged.mid"
    saved.write_bytes(_convert_midi_bytes_to_type0(original)[0])
    first = _item(source, row=4, session_id=7)
    second = _item(other, row=5, session_id=7)
    state = [first, second]
    calls = []
    dialog = dialog_factory(state, edit_callback=lambda item, action: calls.append((item, action)), items_callback=lambda: list(state))
    assert dialog._current_item()["source_path"] == str(source)
    state[:] = [{**second, "row": 4}, {**first, "row": 5, "path": str(saved)}]

    application.sendEvent(dialog, QEvent(QEvent.WindowActivate))

    assert dialog.file_tree.indexOfTopLevelItem(dialog.file_tree.currentItem()) == 1
    assert dialog._current_item()["source_path"] == str(source)
    assert dialog.current_midi_bytes == saved.read_bytes()
    assert not dialog.convert_type0_button.isEnabled()
    state[1] = {**first, "row": 5}
    application.sendEvent(dialog, QEvent(QEvent.WindowActivate))
    assert dialog.current_midi_bytes == original
    assert dialog.convert_type0_button.isEnabled()

    # A new image session containing the same source path is a different song.
    state[1] = {**first, "session_id": 8}
    application.sendEvent(dialog, QEvent(QEvent.WindowActivate))
    assert dialog.file_tree.currentItem() is None
    assert not dialog.current_midi_bytes
    assert not dialog.all_notes
    assert not dialog.details_box.toPlainText()
    assert not dialog.convert_type0_button.isEnabled()
    assert not dialog.merge_piano_button.isEnabled()
    assert not calls


def test_unchanged_or_busy_activation_does_not_reload_the_preview(dialog_factory, tmp_path, monkeypatch, application):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi())
    state = [_item(source)]
    dialog = dialog_factory(state, edit_callback=lambda *_args: None, items_callback=lambda: list(state))
    calls = []
    monkeypatch.setattr(dialog, "_load_current_file", lambda: calls.append(True))

    application.sendEvent(dialog, QEvent(QEvent.WindowActivate))
    assert calls == []
    state[0] = {**state[0], "title": "Changed"}
    dialog._set_inspection_rendering(True)
    application.sendEvent(dialog, QEvent(QEvent.WindowActivate))
    assert calls == []
    dialog._set_inspection_rendering(False)
    application.sendEvent(dialog, QEvent(QEvent.WindowActivate))
    assert calls == [True]


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_rendered_actions_and_inline_errors_are_localized(dialog_factory, tmp_path, language):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi())
    error = "MIDI format 2 files are not supported for Type 0 conversion."
    pending = []

    def edit(_item, _action):
        pending.append(dialog.edit_status_label.text())
        raise ValueError(error)

    dialog = dialog_factory([_item(source)], language=language, edit_callback=edit)
    assert dialog.convert_type0_button.text() == translate_text("Convert to Type 0", language)
    assert dialog.merge_piano_button.text() == translate_text("Merge Channels to Piano", language)
    type0_tooltip = "Combine all tracks into MIDI Type 0 while keeping channels and instruments."
    piano_tooltip = "Merge all channels into MIDI channel 1 using Acoustic Grand Piano."
    assert dialog.convert_type0_button.toolTip() == translate_text(type0_tooltip, language)
    assert dialog.merge_piano_button.toolTip() == translate_text(piano_tooltip, language)
    assert dialog.convert_type0_button.isVisible()
    assert dialog.merge_piano_button.isVisible()

    QTest.mouseClick(dialog.convert_type0_button, Qt.LeftButton)

    assert pending == [translate_text("Editing…", language)]
    expected = translate_text("Could not update this file: {error}", language, error=localize_music_error(error, language))
    assert dialog.edit_status_label.text() == expected
    if language != "en":
        assert dialog.convert_type0_button.text() != "Convert to Type 0"
        assert dialog.merge_piano_button.text() != "Merge Channels to Piano"
        assert dialog.convert_type0_button.toolTip() != type0_tooltip
        assert dialog.merge_piano_button.toolTip() != piano_tooltip
        assert error not in expected
