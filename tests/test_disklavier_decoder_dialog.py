"""The standalone player decodes control data before any audio is played."""

from pathlib import Path
import threading
import time
import wave

import mido
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import disklavier_decoder as decoder
from aps_midi_prep_tool_app import disklavier_decoder_dialog as dialogs
from aps_midi_prep_tool_app import disklavier_decoder_preview as preview
from aps_midi_prep_tool_app import audio_output_routing
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from test_file_inspection_actions import _SilentAudioOutput, _SilentPlayer
from test_audio_output_routing import _FakeAudioDevice, _FakeMediaDevices


class _DecoderPlayer(_SilentPlayer):
    PlayingState = _SilentPlayer.PlaybackState.PlayingState
    LoadedMedia = _SilentPlayer.MediaStatus.LoadedMedia
    BufferedMedia = _SilentPlayer.MediaStatus.BufferedMedia
    EndOfMedia = _SilentPlayer.MediaStatus.EndOfMedia
    NoError = dialogs.QMediaPlayer.NoError
    errorOccurred = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = self.PlaybackState.StoppedState

    def setSource(self, source):
        super().setSource(source)
        duration = 0
        if not source.isEmpty():
            with wave.open(source.toLocalFile()) as audio:
                duration = round(audio.getnframes() / audio.getframerate() * 1000)
        self.durationChanged.emit(duration)
        self.mediaStatusChanged.emit(self.LoadedMedia)

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self.LoadedMedia

    def play(self):
        self._state = self.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self._state)

    def pause(self):
        self._state = self.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def stop(self):
        self._state = self.PlaybackState.StoppedState
        self.setPosition(0)
        self.playbackStateChanged.emit(self._state)

    def setPosition(self, value):
        super().setPosition(value)
        self.positionChanged.emit(value)


@pytest.fixture(autouse=True)
def software_audio_device(monkeypatch):
    # Qt's audio-server startup cannot connect from the test sandbox. Exercise
    # real decoding/rendering and GUI control signals without host speakers.
    monkeypatch.setattr(dialogs, "QMediaPlayer", _DecoderPlayer)
    monkeypatch.setattr(dialogs, "QAudioOutput", _SilentAudioOutput)
    monkeypatch.setattr(audio_output_routing, "QMediaDevices", _FakeMediaDevices)


def _wait(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        QTest.qWait(10)
    QApplication.instance().processEvents()
    assert condition()


def _write_wav(path, pcm):
    pcm = np.asarray(pcm, dtype="<i2")
    with wave.open(str(path), "wb") as output:
        output.setparams((pcm.shape[1], 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(pcm.tobytes())


def _read_wav(path):
    with wave.open(str(path)) as source:
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").reshape(-1, source.getnchannels())


@pytest.fixture
def encoded(tmp_path):
    accompaniment, encoded = tmp_path / "music.wav", tmp_path / "encoded.wav"
    _write_wav(accompaniment, np.full((44100, 2), 8000, dtype="<i2"))
    encode_disklavier_wav([
        (0, b"\xc0\x20"), (0.1, b"\x90\x3c\x60"),
        (0.3, b"\xb0\x40\x7f"), (0.4, b"\x80\x3c\x40"),
        (0.7, b"\xb0\x40\x00"),
    ], accompaniment, encoded)
    return encoded


@pytest.fixture
def dialog(tmp_path, qt_application, monkeypatch, request):
    monkeypatch.setattr(preview, "_render_soundfont", lambda *_a: False)
    settings = QSettings(str(tmp_path / "player.ini"), QSettings.IniFormat)
    instance = dialogs.DisklavierWavPlayerDialog(settings, admin_mode=getattr(request, "param", False))
    instance.show()
    yield instance
    if instance.is_busy:
        instance.cancel_or_close()
        _wait(lambda: not instance.is_busy)
    instance.close()
    qt_application.processEvents()


def test_open_decodes_and_renders_piano_without_playing_control_signal(dialog, encoded):
    original = encoded.read_bytes()
    dialog.open_file(encoded)
    assert dialog.is_busy
    assert not dialog.play_button.isEnabled()
    assert not dialog.open_button.isEnabled()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None, dialog.status.text()
    result = dialog.result
    assert dialog.waveform.waveform is result["accompaniment_waveform"]
    assert dialog.waveform.duration == dialog.overview.duration
    assert dialog.waveform.isEnabled()
    assert dialog.play_button.isEnabled()
    assert not dialog.save_button.isVisible()
    assert not dialog.save_button.isEnabled()
    assert result["renderer"] == "builtin"
    assert len(dialog.overview.notes) == 1
    # Recovered note completion and CD audio already share the 500 ms preroll.
    note = result["notes"][0]
    assert note["start_sec"] == pytest.approx(.6, abs=.001)
    assert note["end_sec"] == pytest.approx(1.2, abs=.001)  # sustain, not key release
    piano = _read_wav(result["piano_path"])
    assert not piano[:round(.59 * 44100)].any()
    assert np.abs(piano[round(.61 * 44100):round(.7 * 44100)]).max() > 100
    mixed = _read_wav(result["mixed_path"])
    audio = _read_wav(result["accompaniment_path"])
    expected = np.clip(piano.astype(float) * .5 + audio.astype(float) * .5, -32768, 32767).astype("<i2")
    assert np.array_equal(mixed[:, 0], expected[:, 0])
    assert np.array_equal(mixed[:, 0], mixed[:, 1])
    assert dialog.player.source().toLocalFile() == result["mixed_path"]
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.player.source().toLocalFile() == result["piano_path"]
    assert dialog.player.source().toLocalFile() != str(encoded)
    assert encoded.read_bytes() == original


def test_bad_wav_restores_open_controls_and_keeps_source(dialog, tmp_path):
    source = tmp_path / "ordinary.wav"
    _write_wav(source, np.zeros((44100, 2), dtype="<i2"))
    original = source.read_bytes()
    dialog.open_file(source)
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is None
    assert dialog.open_button.isEnabled()
    assert not dialog.play_button.isEnabled()
    assert not dialog.progress.isVisible()
    assert dialog.summary.text() == dialog.text("failed")
    assert source.read_bytes() == original
    assert dialog._workspace is None


def test_cancellation_finishes_worker_then_enables_controls(dialog, encoded, monkeypatch):
    entered = threading.Event()
    def slow_decode(*_args, cancel, progress):
        entered.set()
        while not cancel.wait(.01):
            progress(.1)
        raise decoder.DisklavierDecodingCancelled("Canceled")
    monkeypatch.setattr(decoder, "decode_disklavier_wav", slow_decode)
    dialog.open_file(encoded)
    _wait(entered.is_set)
    directory = Path(dialog._workspace.name)
    dialog.cancel_or_close()
    assert dialog.status.text() == dialog.text("cancelling")
    dialog._progress_changed("decode", .3)
    assert dialog.status.text() == dialog.text("cancelling")
    _wait(lambda: not dialog.is_busy)
    assert dialog.open_button.isEnabled()
    assert not directory.exists()
    assert dialog.isVisible()


def test_close_waits_for_worker_cleanup(dialog, encoded, monkeypatch):
    entered = threading.Event()
    def slow_decode(*_args, cancel, progress):
        entered.set()
        cancel.wait(5)
        raise decoder.DisklavierDecodingCancelled("Canceled")
    monkeypatch.setattr(decoder, "decode_disklavier_wav", slow_decode)
    dialog.open_file(encoded)
    _wait(entered.is_set)
    directory = Path(dialog._workspace.name)
    dialog.close()
    _wait(lambda: not dialog.is_busy)
    assert not dialog.isVisible()
    assert not directory.exists()


def test_second_open_clears_previous_audio_and_workspace(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    old_directory = Path(dialog._workspace.name)
    dialog.open_file(encoded)
    assert dialog.player.source().isEmpty()
    assert dialog.waveform.waveform is None
    assert dialog.waveform.duration == dialog.waveform.position == 0
    assert not dialog.waveform.isEnabled()
    assert not old_directory.exists()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None


def test_transport_seek_pause_stop_and_mix_switch_keep_the_timeline(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(800)
    assert dialog.player.position() == 800
    assert dialog.waveform.position == dialog.overview.position == 800
    dialog.toggle_play()
    assert dialog.play_button.text() == "Pause"
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.player.position() == 800
    assert dialog.waveform.position == dialog.overview.position == 800
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState
    dialog.toggle_play()
    assert dialog.play_button.text() == "Play"
    dialog.stop()
    assert dialog.player.position() == 0
    assert dialog.overview.position == 0
    assert dialog.waveform.position == 0


def _set_balance_and_wait(dialog, value):
    dialog.balance_slider.setValue(value)
    _wait(lambda: not dialog.is_busy and value in dialog._balance_paths
          and dialog.player.source().toLocalFile() == dialog._balance_paths[value])


@pytest.mark.parametrize("balance", [0, 25, 50, 75, 100])
def test_balance_blends_decoded_stems_without_changing_timing_or_master_volume(dialog, encoded, balance):
    original = encoded.read_bytes()
    assert dialog.balance_slider.value() == 50
    assert not dialog.balance_slider.isEnabled()
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    duration = dialog.position_slider.maximum()
    dialog.volume_slider.setValue(37)
    dialog.seek(800)
    _set_balance_and_wait(dialog, balance)

    piano = _read_wav(dialog.result["piano_path"])
    accompaniment = _read_wav(dialog.result["accompaniment_path"])
    mixed = _read_wav(dialog.player.source().toLocalFile())
    expected = np.clip(piano.astype(float) * (1 - balance / 100)
                       + accompaniment.astype(float) * (balance / 100), -32768, 32767).astype("<i2")
    np.testing.assert_array_equal(mixed[:, 0], expected[:, 0])
    np.testing.assert_array_equal(mixed[:, 1], expected[:, 0])
    assert dialog.position_slider.maximum() == duration
    assert dialog.player.position() == dialog.overview.position == dialog.waveform.position == 800
    assert dialog.volume_slider.value() == 37
    assert dialog.audio_output.volume() == pytest.approx(.37)
    assert dialog.settings.value("disklavier_wav_balance", 50, type=int) == balance
    assert encoded.read_bytes() == original


def test_balance_is_remembered_for_next_player_and_restored_after_piano_only(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    _set_balance_and_wait(dialog, 75)
    dialog.mode_combo.setCurrentIndex(1)
    assert not dialog.balance_slider.isEnabled()
    assert dialog.player.source().toLocalFile() == dialog.result["piano_path"]
    dialog.mode_combo.setCurrentIndex(0)
    assert dialog.balance_slider.isEnabled()
    assert dialog.player.source().toLocalFile() == dialog._balance_paths[75]
    settings = dialog.settings
    dialog.close()
    reopened = dialogs.DisklavierWavPlayerDialog(settings)
    try:
        assert reopened.balance_slider.value() == 75
        reopened.open_file(encoded)
        _wait(lambda: not reopened.is_busy and 75 in reopened._balance_paths
              and reopened.player.source().toLocalFile() == reopened._balance_paths[75])
    finally:
        reopened.close()
        _wait(lambda: not reopened.is_busy)


@pytest.mark.parametrize("action", ["play", "pause", "stop", "seek"])
def test_balance_applies_current_transport_intent_after_background_work(dialog, encoded, monkeypatch, action):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(500)
    if action != "play":
        dialog.toggle_play()
    entered, release = threading.Event(), threading.Event()
    mix = preview._mix_preview

    def blocked_mix(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return mix(*args, **kwargs)

    monkeypatch.setattr(preview, "_mix_preview", blocked_mix)
    dialog.balance_slider.setValue(25)
    try:
        _wait(entered.is_set)
        assert dialog.play_button.isEnabled()
        assert dialog.position_slider.isEnabled()
        assert dialog.balance_slider.isEnabled()
        if action in ("play", "pause"):
            dialog.toggle_play()
        elif action == "stop":
            dialog.stop()
        else:
            dialog.seek(900)
        state, position = dialog.player.playbackState(), dialog.player.position()
    finally:
        release.set()
    _set_balance_and_wait(dialog, 25)
    assert dialog.player.playbackState() == state
    assert dialog.player.position() == position
    assert dialog.waveform.position == dialog.overview.position == position


def test_rapid_balance_changes_cancel_stale_mix_and_reuse_prepared_stems(dialog, encoded, monkeypatch):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    initial_source = dialog.player.source()
    entered, cancelled = threading.Event(), threading.Event()
    mix = preview._mix_preview
    requested = []

    def slow_first_mix(*args, balance, cancel, **kwargs):
        requested.append(balance)
        if balance == .25:
            entered.set()
            assert cancel.wait(5)
            cancelled.set()
        return mix(*args, balance=balance, cancel=cancel, **kwargs)

    monkeypatch.setattr(preview, "_mix_preview", slow_first_mix)
    monkeypatch.setattr(decoder, "decode_disklavier_wav", lambda *_a, **_kw: pytest.fail("Decode repeated"))
    monkeypatch.setattr(preview, "_render_soundfont", lambda *_a, **_kw: pytest.fail("Piano synthesis repeated"))
    dialog.balance_slider.setValue(25)
    _wait(entered.is_set)
    dialog.balance_slider.setValue(75)
    _wait(cancelled.is_set)
    assert dialog.player.source() == initial_source
    _set_balance_and_wait(dialog, 75)
    assert requested == [.25, .75]
    assert 25 not in dialog._balance_paths
    previous = Path(dialog.player.source().toLocalFile())
    _set_balance_and_wait(dialog, 100)
    assert not previous.exists()
    assert set(dialog._balance_paths) == {50, 100}
    assert len(list(Path(dialog._workspace.name).glob("balance-*.wav"))) == 1


def test_failed_balance_keeps_playable_source_without_retry_loop(dialog, encoded, monkeypatch):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    initial_source = dialog.player.source()
    dialog.seek(700)
    attempts = []

    def fail_mix(_piano, _accompaniment, destination, **_kwargs):
        attempts.append(destination)
        destination.write_bytes(b"incomplete")
        raise OSError("Disk is full")

    monkeypatch.setattr(preview, "_mix_preview", fail_mix)
    dialog.balance_slider.setValue(25)
    _wait(lambda: not dialog.is_busy and dialog._failed_balance == 25)
    QTest.qWait(400)
    assert len(attempts) == 1
    assert not attempts[0].exists()
    assert dialog.player.source() == initial_source
    assert dialog.player.position() == 700
    assert dialog.balance_slider.isEnabled()
    assert dialog.play_button.isEnabled()
    assert "Disk is full" in dialog.status.text()


def test_close_cancels_balance_before_cleaning_workspace(dialog, encoded, monkeypatch):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    directory = Path(dialog._workspace.name)
    entered = threading.Event()
    mix = preview._mix_preview

    def blocked_mix(*args, cancel, **kwargs):
        entered.set()
        assert cancel.wait(5)
        return mix(*args, cancel=cancel, **kwargs)

    monkeypatch.setattr(preview, "_mix_preview", blocked_mix)
    dialog.balance_slider.setValue(25)
    _wait(entered.is_set)
    dialog.cancel_or_close()
    _wait(lambda: not dialog.is_busy)
    assert not dialog.isVisible()
    assert not directory.exists()


@pytest.mark.parametrize("action", ["pause", "stop", "seek"])
def test_balance_keeps_transport_requests_across_delayed_media_loads(dialog, encoded, monkeypatch, action):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(500)
    dialog.toggle_play()
    duration = dialog.position_slider.maximum()
    load_source = dialog.player.setSource

    def delayed_source(source):
        _SilentPlayer.setSource(dialog.player, source)
        # Some real multimedia backends reset these before the WAV has loaded.
        dialog.player.durationChanged.emit(0)
        dialog.player.positionChanged.emit(0)

    monkeypatch.setattr(dialog.player, "setSource", delayed_source)
    _set_balance_and_wait(dialog, 25)
    assert dialog._source_loading
    assert dialog.position_slider.maximum() == duration
    assert dialog.waveform.position == dialog.overview.position == 500
    if action == "pause":
        dialog.toggle_play()
    elif action == "stop":
        dialog.stop()
    else:
        dialog.seek(900)
    _set_balance_and_wait(dialog, 75)
    monkeypatch.setattr(dialog.player, "setSource", load_source)
    load_source(dialog.player.source())
    assert not dialog._source_loading
    expected_position = {"pause": 500, "stop": 0, "seek": 900}[action]
    expected_state = {"pause": _DecoderPlayer.PlaybackState.PausedState,
                      "stop": _DecoderPlayer.PlaybackState.StoppedState,
                      "seek": _DecoderPlayer.PlaybackState.PlayingState}[action]
    assert dialog.player.position() == dialog.waveform.position == dialog.overview.position == expected_position
    assert dialog.player.playbackState() == expected_state


def test_failed_media_load_does_not_trap_play_in_pending_state(dialog, encoded, monkeypatch):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(500)
    dialog.toggle_play()
    load_source = dialog.player.setSource
    monkeypatch.setattr(dialog.player, "setSource", lambda source: _SilentPlayer.setSource(dialog.player, source))
    _set_balance_and_wait(dialog, 25)
    assert dialog._source_loading
    dialog.player.errorOccurred.emit("unavailable", "Audio device unavailable")
    assert not dialog._source_loading
    assert not dialog._pending_play
    assert dialog.play_button.text() == "Play"
    assert "Audio device unavailable" in dialog.status.text()
    monkeypatch.setattr(dialog.player, "setSource", load_source)
    dialog.toggle_play()
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState


def test_waveform_seeks_on_the_same_timeline_as_the_piano_roll(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    assert dialog.waveform.x() == dialog.overview.x()
    assert dialog.waveform.width() == dialog.overview.width()
    assert dialog.waveform.y() > dialog.overview.geometry().bottom()
    x = dialog.waveform.width() // 3
    target = round(x / dialog.waveform.width() * dialog.overview.duration)
    QTest.mouseClick(dialog.waveform, Qt.LeftButton, pos=QPoint(x, dialog.waveform.height() // 2))
    assert dialog.player.position() == target
    assert dialog.waveform.position == dialog.overview.position == target
    assert dialog.waveform._time_x(target) == dialog.overview._time_x(target)
    assert dialog.position_slider.value() == target


@pytest.mark.parametrize("duration", [2, 6])
@pytest.mark.parametrize("width", [300, 600])
def test_waveform_peaks_keep_source_timing_when_display_duration_changes(qt_application, duration, width):
    widget = dialogs.AccompanimentWaveform()
    palette = widget.palette()
    palette.setColor(QPalette.Base, QColor(15, 22, 26))
    palette.setColor(QPalette.Highlight, QColor(10, 50, 65))
    palette.setColor(QPalette.Text, QColor(240, 240, 240))
    widget.setPalette(palette)
    widget.resize(width, 80)
    # Four seconds of source audio with a pulse at 1.00–1.25 seconds.
    widget.set_waveform({
        "minimums": [0] * 4 + [-.75] + [0] * 11,
        "maximums": [0] * 4 + [.5] + [0] * 11,
        "frames_per_bin": 25, "frame_count": 400, "sample_rate": 100,
        "duration_seconds": 4,
    })
    widget.set_duration(duration * 1000)
    widget.set_position(1500)
    widget.show()
    qt_application.processEvents()
    snapshot = widget.grab().toImage()
    y = widget.height() // 2 - 8
    pulse_x = round(1.125 / duration * width)
    silence_x = round(.6 / duration * width)
    assert snapshot.pixelColor(pulse_x, y).lightness() - snapshot.pixelColor(silence_x, y).lightness() > 90
    assert snapshot.pixelColor(round(1.5 / duration * width), 8) == palette.text().color()
    if duration > 4:
        assert snapshot.pixelColor(round(5 / duration * width), y) == palette.base().color()
    widget.close()


@pytest.mark.parametrize("state", ["playing", "paused", "stopped"])
def test_headphone_changes_and_reconnection_keep_user_transport_state(dialog, encoded, state):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(800)
    if state != "stopped":
        dialog.toggle_play()
    if state == "paused":
        dialog.toggle_play()
    original_state = dialog.player.playbackState()
    original_source = dialog.player.source()
    original_volume = dialog.audio_output.volume()
    monitor = dialog.audio_output_follower._devices

    monitor.use("headphones")
    monitor.use("")
    monitor.use("headphones")

    assert dialog.audio_output.device() == _FakeAudioDevice("headphones")
    assert dialog.player.audioOutput() is dialog.audio_output
    assert dialog.player.playbackState() == original_state
    assert dialog.player.position() == 800
    assert dialog.player.source() == original_source
    assert dialog.audio_output.volume() == original_volume


def test_play_refreshes_current_headphones_before_resuming(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.audio_output_follower._devices.default = _FakeAudioDevice("headphones")
    dialog.toggle_play()
    assert dialog.audio_output.device() == _FakeAudioDevice("headphones")
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState


@pytest.mark.parametrize("piano_frames", [44100, 3 * 44100])
def test_rendered_duration_drives_summary_overview_and_seek_in_each_mode(dialog, encoded, monkeypatch, piano_frames):
    def render(_midi, destination, _cancel):
        # A renderer may stop before the accompaniment, or append a release tail.
        _write_wav(destination, np.zeros((piano_frames, 2), dtype="<i2"))
        return True
    monkeypatch.setattr(preview, "_render_soundfont", render)
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    result = dialog.result
    assert result is not None
    with wave.open(str(encoded)) as source:
        original_duration = source.getnframes() / source.getframerate()
    assert result["duration_seconds"] == original_duration  # original source remains available
    for mode, path_key in ((0, "mixed_path"), (1, "piano_path")):
        dialog.mode_combo.setCurrentIndex(mode)
        with wave.open(result[path_key]) as audio:
            seconds = audio.getnframes() / audio.getframerate()
        duration = round(seconds * 1000)
        assert result["preview_durations"][path_key] == seconds
        assert dialog.overview.duration == duration
        assert dialog.waveform.duration == duration
        assert dialog.waveform.waveform["duration_seconds"] == original_duration
        assert dialog.position_slider.maximum() == duration
        assert dialog.summary.text().endswith(dialogs._time_label(duration))
        assert dialog.time_label.text().endswith(dialogs._time_label(duration))
        dialog.seek(duration)
        assert dialog.overview.position == duration
        assert dialog.waveform.position == duration


def test_piano_notes_are_visible_against_a_dark_theme(qt_application):
    widget = dialogs.DecodedPianoOverview()
    palette = widget.palette()
    palette.setColor(QPalette.Base, QColor(15, 22, 26))
    palette.setColor(QPalette.AlternateBase, QColor(24, 32, 39))
    palette.setColor(QPalette.Highlight, QColor(10, 50, 65))
    widget.setPalette(palette)
    widget.resize(300, 176)
    widget.set_notes([{"pitch": 60, "velocity": 30, "start_sec": 1, "end_sec": 2}], 3)
    widget.show()
    qt_application.processEvents()
    snapshot = widget.grab().toImage()
    note, background = snapshot.pixelColor(150, 97), snapshot.pixelColor(90, 97)
    assert note.lightness() - background.lightness() > 90
    widget.close()


def test_save_handler_is_unavailable_outside_admin_mode(dialog, encoded, tmp_path, monkeypatch):
    midi = tmp_path / "decoded.mid"
    midi.write_bytes(b"MThd-decoded")
    dialog.result = {"midi_path": str(midi)}
    dialog._source_path = encoded
    dialog._update_controls()
    monkeypatch.setattr(dialogs.QFileDialog, "getSaveFileName",
                        lambda *_a: pytest.fail("Normal playback must not offer MIDI export"))
    dialog.save_midi()
    assert dialog.save_button.isHidden()
    assert not dialog.save_button.isEnabled()


def test_admin_export_permission_is_not_saved_in_settings(qt_application, tmp_path):
    settings = QSettings(str(tmp_path / "session.ini"), QSettings.IniFormat)
    original_keys = settings.allKeys()
    admin = dialogs.DisklavierWavPlayerDialog(settings, admin_mode=True)
    admin.show()
    assert admin.save_button.isVisible()
    admin.close()
    assert settings.allKeys() == original_keys
    normal = dialogs.DisklavierWavPlayerDialog(settings)
    normal.show()
    assert normal.save_button.isHidden()
    normal.close()


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
@pytest.mark.parametrize("dialog", [True], indirect=True)
def test_save_never_overwrites_original_wav_or_alias(dialog, encoded, tmp_path, monkeypatch, alias):
    midi = tmp_path / "decoded.mid"
    midi.write_bytes(b"MThd-decoded")
    dialog.result = {"midi_path": str(midi)}
    dialog._source_path = encoded
    target = encoded
    if alias != "same":
        target = tmp_path / "alias.mid"
        try:
            target.symlink_to(encoded) if alias == "symlink" else target.hardlink_to(encoded)
        except OSError:
            pytest.skip("Links are unavailable on this filesystem")
    original = encoded.read_bytes()
    monkeypatch.setattr(dialogs.QFileDialog, "getSaveFileName", lambda *_a: (str(target), ""))
    dialog.save_midi()
    assert encoded.read_bytes() == original
    assert target.read_bytes() == original
    assert dialog.text("keep_source") in dialog.status.text()


@pytest.mark.parametrize("dialog", [True], indirect=True)
def test_save_exports_decoded_midi_atomically(dialog, encoded, tmp_path, monkeypatch):
    assert dialog.save_button.isVisible()
    assert not dialog.save_button.isEnabled()
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    assert dialog.save_button.isEnabled()
    target = tmp_path / "saved.mid"
    target.write_bytes(b"Previous file")
    monkeypatch.setattr(dialogs.QFileDialog, "getSaveFileName", lambda *_a: (str(target), ""))
    dialog.save_midi()
    assert target.read_bytes() == Path(dialog.result["midi_path"]).read_bytes()
    assert mido.MidiFile(target).length > 1
    assert not list(tmp_path.glob(".aps_write_*"))


@pytest.mark.parametrize("dialog", [True], indirect=True)
def test_save_without_extension_does_not_overwrite_unconfirmed_mid(dialog, encoded, tmp_path, monkeypatch):
    target = tmp_path / "saved.mid"
    target.write_bytes(b"Keep existing MIDI")
    dialog.result = {"midi_path": str(target)}
    dialog._source_path = encoded
    monkeypatch.setattr(dialogs.QFileDialog, "getSaveFileName", lambda *_a: (str(target.with_suffix("")), ""))
    dialog.save_midi()
    assert target.read_bytes() == b"Keep existing MIDI"
    assert "Could not save" in dialog.status.text()


def test_software_piano_forces_programs_without_changing_note_timing(tmp_path):
    source = tmp_path / "input.mid"
    result = tmp_path / "piano.mid"
    midi = mido.MidiFile(type=0, ticks_per_beat=1000)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=1000000),
        mido.Message("program_change", channel=9, program=40),
        mido.Message("control_change", channel=9, control=0, value=12),
        mido.Message("note_on", channel=9, note=60, velocity=90, time=600),
        mido.Message("note_off", channel=9, note=60, velocity=0, time=500),
    ]))
    midi.save(source)
    assert preview._piano_midi(source, result)
    events = read_midi_events(result)
    assert all((message[0] & 15) != 9 for _, message in events)
    assert [seconds for seconds, message in events if message[0] & 0xF0 in (0x80, 0x90)] == [.6, 1.1]
    assert all(message[1] == 0 for _, message in events if message[0] & 0xF0 == 0xC0)


def test_builtin_piano_does_not_truncate_after_five_minutes(tmp_path):
    target = tmp_path / "long-piano.wav"
    notes = [{"start_sec": 300.1, "end_sec": 300.2, "pitch": 60, "velocity": 100}]
    preview._write_builtin_piano(notes, target, 301)
    with wave.open(str(target)) as result:
        assert result.getnframes() == 301 * 44100
        result.setpos(300 * 44100)
        assert np.abs(np.frombuffer(result.readframes(44100), dtype="<i2")).max() > 100


def test_builtin_render_can_cancel_between_blocks(tmp_path):
    cancelled = threading.Event()
    with pytest.raises(decoder.DisklavierDecodingCancelled):
        preview._write_builtin_piano([], tmp_path / "piano.wav", 10, cancel=cancelled,
                                     progress=lambda _value: cancelled.set())


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
@pytest.mark.parametrize("point_size,width,height", [(12, 800, 520), (18, 900, 700)])
@pytest.mark.parametrize("admin_mode", [False, True])
def test_player_layout_contains_controls_at_larger_fonts(qt_application, tmp_path, language, point_size, width, height, admin_mode):
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    dialog = dialogs.DisklavierWavPlayerDialog(settings, admin_mode=admin_mode)
    try:
        font = QFont(dialog.font())
        font.setPointSize(point_size)
        dialog.setFont(font)
        dialog.resize(width, height)
        dialog.show()
        qt_application.processEvents()
        assert dialog.width() <= width
        assert dialog.height() <= height
        controls = [dialog.open_button, dialog.play_button, dialog.stop_button,
                    dialog.close_button, dialog.mode_combo, dialog.volume_slider,
                    dialog.balance_slider, dialog.piano_balance_label,
                    dialog.accompaniment_balance_label, dialog.overview, dialog.waveform]
        assert dialog.balance_slider.accessibleName() == dialog.text("balance")
        assert dialog.waveform.accessibleName() == dialog.text("waveform")
        assert dialog.waveform.width() == dialog.overview.width()
        assert dialog.save_button.isVisible() == admin_mode
        if admin_mode:
            controls.append(dialog.save_button)
        for control in controls:
            assert control.isVisible()
            top_left = control.mapTo(dialog, control.rect().topLeft())
            bottom_right = control.mapTo(dialog, control.rect().bottomRight())
            assert dialog.rect().contains(top_left)
            assert dialog.rect().contains(bottom_right)
    finally:
        dialog.close()
