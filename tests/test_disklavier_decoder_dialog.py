"""The standalone player decodes control data before any audio is played."""

from pathlib import Path
import os
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
from aps_midi_prep_tool_app import audio_tools
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES
from test_file_inspection_actions import _SilentAudioOutput, _SilentPlayer
from test_audio_output_routing import _FakeAudioDevice, _FakeMediaDevices
from test_disklavier_stem_player import _FakeDevice as _StreamingDevice, _FakeOutput as _StreamingOutput, fake_audio


class _DecoderPlayer(_SilentPlayer):
    """Transport spy; the stem player's separate tests exercise streamed PCM."""
    PlayingState = _SilentPlayer.PlaybackState.PlayingState
    LoadedMedia = _SilentPlayer.MediaStatus.LoadedMedia
    BufferedMedia = _SilentPlayer.MediaStatus.BufferedMedia
    EndOfMedia = _SilentPlayer.MediaStatus.EndOfMedia
    NoError = dialogs.QMediaPlayer.NoError
    errorOccurred = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = self.PlaybackState.StoppedState
        self._status = self.MediaStatus.NoMedia
        self._duration = 0
        self.loaded_stems = None
        self.balance = .5
        self.piano_only = False
        self.transport_calls = []

    def load(self, piano, accompaniment, *, allow_stereo=False):
        self.transport_calls.append(("load", str(piano), str(accompaniment)))
        self.loaded_stems = (str(piano), str(accompaniment))
        self.allow_stereo = allow_stereo
        durations = []
        for path in self.loaded_stems:
            with wave.open(path) as audio:
                durations.append(audio.getnframes() / audio.getframerate())
        self._duration = round(max(durations) * 1000)
        self._position = 0
        self._state = self.PlaybackState.StoppedState
        self._status = self.LoadedMedia
        self.durationChanged.emit(self._duration)
        self.positionChanged.emit(0)
        self.mediaStatusChanged.emit(self._status)

    def clear(self):
        self.transport_calls.append(("clear",))
        self.loaded_stems = None
        self._position = self._duration = 0
        self._state = self.PlaybackState.StoppedState
        self._status = self.MediaStatus.NoMedia
        self.durationChanged.emit(0)
        self.positionChanged.emit(0)
        self.playbackStateChanged.emit(self._state)
        self.mediaStatusChanged.emit(self._status)

    def setBalance(self, balance):
        self.balance = balance

    def setPianoOnly(self, piano_only):
        self.piano_only = piano_only

    def duration(self):
        return self._duration

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def play(self):
        self.transport_calls.append(("play",))
        self._state = self.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self._state)

    def pause(self):
        self.transport_calls.append(("pause",))
        self._state = self.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def stop(self):
        self.transport_calls.append(("stop",))
        self._state = self.PlaybackState.StoppedState
        self.setPosition(0)
        self.playbackStateChanged.emit(self._state)

    def setPosition(self, value):
        self.transport_calls.append(("seek", value))
        super().setPosition(value)
        self.positionChanged.emit(value)

    def advance(self, milliseconds):
        assert self._state == self.PlayingState
        self._position = min(self._duration, self._position + milliseconds)
        self.positionChanged.emit(self._position)

    def fail(self, message):
        self._state = self.PlaybackState.StoppedState
        self.playbackStateChanged.emit(self._state)
        self.errorOccurred.emit("unavailable", message)


@pytest.fixture(autouse=True)
def software_audio_device(monkeypatch):
    # Qt's audio-server startup cannot connect from the test sandbox. Exercise
    # real decoding/rendering and GUI control signals without host speakers.
    monkeypatch.setattr(dialogs, "DisklavierStemPlayer", _DecoderPlayer)
    monkeypatch.setattr(dialogs, "QAudioOutput", _SilentAudioOutput)
    monkeypatch.setattr(audio_output_routing, "QMediaDevices", _FakeMediaDevices)


def _wait(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        # Yield to Python audio workers as the application's main event loop
        # does; a tight sequence of native qWait calls can starve their work.
        time.sleep(0.01)
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
def paired_files(tmp_path):
    midi_path, wav_path = tmp_path / "My Song.mid", tmp_path / "My Song.WAV"
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.MetaMessage("set_tempo", tempo=1000000, time=720),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", note=67, velocity=90, time=480),
        mido.Message("note_off", note=67, time=480),
    ]))
    midi.save(midi_path)
    audio = np.zeros((2 * 44100, 2), dtype="<i2")
    audio[22050:55125, 0] = 8000
    audio[22050:55125, 1] = -4000
    _write_wav(wav_path, audio)
    return midi_path, wav_path


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
    audio = _read_wav(result["accompaniment_path"])
    assert audio.shape[1] == 1
    assert dialog.player.loaded_stems == (result["piano_path"], result["accompaniment_path"])
    assert dialog.player.allow_stereo is False
    assert dialog.player.balance == .5
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.player.piano_only is True
    assert str(encoded) not in dialog.player.loaded_stems
    assert encoded.read_bytes() == original


@pytest.mark.parametrize("selected", [0, 1])
def test_pair_opens_from_either_file_with_stereo_audio_and_shared_timing(dialog, paired_files, selected):
    midi_path, wav_path = paired_files
    originals = [path.read_bytes() for path in paired_files]
    dialog.open_file(paired_files[selected])
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None, dialog.status.text()
    result = dialog.result
    assert result["source_kind"] == "pair"
    assert dialog.text("input_pair") in dialog.input_summary.text()
    assert midi_path.name in dialog.input_summary.text()
    assert wav_path.name in dialog.input_summary.text()
    assert dialog.input_summary.isVisible()
    assert result["notes"][0]["start_sec"] == pytest.approx(.5)
    assert result["notes"][0]["end_sec"] == pytest.approx(1.25)
    assert result["notes"][0]["pitch"] == 67
    assert dialog.overview.duration == dialog.waveform.duration == 2000
    assert result["accompaniment_waveform"]["duration_seconds"] == 2
    assert max(result["accompaniment_waveform"]["maximums"]) == 8000 / 32768
    assert min(result["accompaniment_waveform"]["minimums"]) == -4000 / 32768
    assert Path(result["accompaniment_path"]).is_relative_to(Path(dialog._workspace.name))
    dialog.seek(700)
    dialog.toggle_play()
    _set_balance_and_wait(dialog, 100)
    np.testing.assert_array_equal(_read_wav(dialog.player.loaded_stems[1]), _read_wav(wav_path))
    assert dialog.player.allow_stereo is True
    assert dialog.player.balance == 1
    assert dialog.player.position() == dialog.waveform.position == dialog.overview.position == 700
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.player.position() == 700
    assert dialog.player.piano_only is True
    assert [path.read_bytes() for path in paired_files] == originals


@pytest.mark.parametrize("selected", ["midi", "mp3"])
def test_mp3_pair_opens_from_either_file_with_balance_and_timing(dialog, paired_files, selected):
    if not audio_tools.find_lame_command():
        if os.environ.get("APS_REQUIRE_AUDIO_TESTS") == "1":
            pytest.fail("LAME is required for MP3 player integration tests")
        pytest.skip("LAME is not available")
    midi_path, wav_path = paired_files
    mp3_path = wav_path.with_suffix(".MP3")
    samples = np.zeros((2 * 44100, 2), dtype="<i2")
    times = np.arange(55125 - 22050) / 44100
    samples[22050:55125, 0] = np.rint(8000 * np.sin(2 * np.pi * 440 * times))
    samples[22050:55125, 1] = np.rint(4000 * np.sin(2 * np.pi * 660 * times))
    _write_wav(wav_path, samples)
    audio_tools.encode_mp3(wav_path, mp3_path)
    wav_path.unlink()
    originals = midi_path.read_bytes(), mp3_path.read_bytes()
    dialog.open_file(midi_path if selected == "midi" else mp3_path)
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None, dialog.status.text()
    result = dialog.result
    assert result["source_kind"] == "pair"
    assert result["source_audio_path"] == str(mp3_path)
    assert dialog.text("input_pair_mp3") in dialog.input_summary.text()
    assert midi_path.name in dialog.input_summary.text()
    assert mp3_path.name in dialog.input_summary.text()
    assert result["notes"][0]["start_sec"] == pytest.approx(.5)
    assert result["notes"][0]["end_sec"] == pytest.approx(1.25)
    assert dialog.overview.duration == dialog.waveform.duration == 2000
    assert result["accompaniment_waveform"]["duration_seconds"] == 2
    accompaniment = _read_wav(result["accompaniment_path"])
    assert accompaniment.shape == (2 * 44100, 2)
    region = accompaniment[round(.6 * 44100):round(.7 * 44100)].astype(float)
    assert np.sqrt(np.mean(region[:, 0] ** 2)) > 4000
    assert 2000 < np.sqrt(np.mean(region[:, 1] ** 2)) < 4000
    dialog.seek(700)
    dialog.toggle_play()
    _set_balance_and_wait(dialog, 100)
    np.testing.assert_array_equal(_read_wav(dialog.player.loaded_stems[1]), accompaniment)
    assert dialog.player.balance == 1
    assert dialog.player.position() == dialog.overview.position == dialog.waveform.position == 700
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState
    assert (midi_path.read_bytes(), mp3_path.read_bytes()) == originals


def test_file_picker_accepts_midi_wav_and_mp3_without_a_format_mode(dialog, paired_files, monkeypatch):
    def choose(_parent, _title, _folder, file_filter):
        for pattern in ("*.mid", "*.midi", "*.MID", "*.MIDI", "*.wav", "*.WAV", "*.mp3", "*.MP3"):
            assert pattern in file_filter
        return str(paired_files[0]), file_filter

    monkeypatch.setattr(dialogs.QFileDialog, "getOpenFileName", choose)
    dialog.choose_file()
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None, dialog.status.text()
    assert dialog.result["source_kind"] == "pair"


def test_encoded_wav_uses_embedded_notes_even_with_same_name_midi(dialog, encoded, paired_files):
    companion = encoded.with_suffix(".mid")
    companion.write_bytes(paired_files[0].read_bytes())
    original = companion.read_bytes()
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None, dialog.status.text()
    assert dialog.result["source_kind"] == "encoded"
    assert dialog.result["notes"][0]["pitch"] == 60
    assert dialog.input_summary.text() == dialog.text("input_encoded")
    assert companion.read_bytes() == original


def test_missing_companion_clears_old_playback_and_explains_problem(dialog, encoded, paired_files):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    midi_path, wav_path = paired_files
    wav_path.unlink()
    original = midi_path.read_bytes()
    dialog.open_file(midi_path)
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is None
    assert dialog.player.loaded_stems is None
    assert dialog.input_summary.isHidden()
    assert not dialog.input_summary.text()
    assert "WAV" in dialog.status.text()
    assert "MP3" in dialog.status.text()
    assert dialog.open_button.isEnabled()
    assert not dialog.play_button.isEnabled()
    assert dialog._workspace is None
    assert midi_path.read_bytes() == original


@pytest.mark.parametrize("dialog", [True], indirect=True)
def test_admin_pair_does_not_offer_to_export_existing_midi(dialog, paired_files, monkeypatch):
    dialog.open_file(paired_files[0])
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is not None, dialog.status.text()
    assert dialog.save_button.isHidden()
    assert not dialog.save_button.isEnabled()
    monkeypatch.setattr(dialogs.QFileDialog, "getSaveFileName",
                        lambda *_a: pytest.fail("A pair already contains its MIDI"))
    dialog.save_midi()


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
    assert dialog.player.loaded_stems is None
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
    _wait(lambda: not dialog.is_busy and dialog.player.balance == value / 100)


@pytest.mark.parametrize("balance", [0, 25, 50, 75, 100])
def test_balance_updates_live_gains_without_changing_transport_or_volume(dialog, encoded, balance):
    original = encoded.read_bytes()
    assert dialog.balance_slider.value() == 50
    assert not dialog.balance_slider.isEnabled()
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    duration = dialog.position_slider.maximum()
    dialog.volume_slider.setValue(37)
    dialog.seek(800)
    dialog.toggle_play()
    loaded = dialog.player.loaded_stems
    dialog.player.transport_calls.clear()
    _set_balance_and_wait(dialog, balance)

    assert dialog.player.transport_calls == []
    assert dialog.player.loaded_stems == loaded
    assert dialog.player.balance == balance / 100
    assert not dialog.is_busy
    assert not list(Path(dialog._workspace.name).glob("balance-*.wav"))
    assert dialog.position_slider.maximum() == duration
    assert dialog.player.position() == dialog.overview.position == dialog.waveform.position == 800
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState
    assert dialog.volume_slider.value() == 37
    assert dialog.audio_output.volume() == pytest.approx(.37)
    assert dialog.settings.value("disklavier_wav_balance", 50, type=int) == balance
    assert encoded.read_bytes() == original


def test_balance_is_remembered_for_next_player_and_restored_after_piano_only(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    _set_balance_and_wait(dialog, 75)
    dialog.player.transport_calls.clear()
    dialog.mode_combo.setCurrentIndex(1)
    assert not dialog.balance_slider.isEnabled()
    assert dialog.player.piano_only is True
    dialog.mode_combo.setCurrentIndex(0)
    assert dialog.balance_slider.isEnabled()
    assert dialog.player.piano_only is False
    assert dialog.player.balance == .75
    assert dialog.player.transport_calls == []
    settings = dialog.settings
    dialog.close()
    reopened = dialogs.DisklavierWavPlayerDialog(settings)
    try:
        assert reopened.balance_slider.value() == 75
        reopened.open_file(encoded)
        _wait(lambda: not reopened.is_busy)
        assert reopened.player.balance == .75
        assert reopened.player.loaded_stems is not None
        assert len([call for call in reopened.player.transport_calls if call[0] == "load"]) == 1
    finally:
        reopened.close()
        _wait(lambda: not reopened.is_busy)


@pytest.mark.parametrize("state", ["playing", "paused", "stopped"])
def test_balance_and_mode_changes_preserve_current_transport_intent(dialog, encoded, state):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(500)
    if state != "stopped":
        dialog.toggle_play()
    if state == "paused":
        dialog.toggle_play()
    original_state = dialog.player.playbackState()
    loaded = dialog.player.loaded_stems
    dialog.player.transport_calls.clear()
    for value in (25, 75, 0, 100, 50):
        dialog.balance_slider.setValue(value)
        dialog.mode_combo.setCurrentIndex(1)
        dialog.mode_combo.setCurrentIndex(0)
        assert dialog.play_button.isEnabled()
        assert dialog.position_slider.isEnabled()
        assert dialog.balance_slider.isEnabled()
        assert not dialog.is_busy
    assert dialog.player.transport_calls == []
    assert dialog.player.loaded_stems == loaded
    assert dialog.player.playbackState() == original_state
    assert dialog.player.position() == dialog.overview.position == dialog.waveform.position == 500


def test_rapid_balance_drag_keeps_advancing_without_reload_or_background_mix(dialog, encoded, monkeypatch):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    workspace = Path(dialog._workspace.name)
    files_before = sorted(path.relative_to(workspace) for path in workspace.rglob("*"))
    dialog.seek(300)
    dialog.toggle_play()
    dialog.player.transport_calls.clear()
    monkeypatch.setattr(preview, "_mix_preview", lambda *_a, **_kw: pytest.fail("Balance rendered another WAV"))
    monkeypatch.setattr(decoder, "decode_disklavier_wav", lambda *_a, **_kw: pytest.fail("Decode repeated"))
    monkeypatch.setattr(preview, "_render_soundfont", lambda *_a, **_kw: pytest.fail("Piano synthesis repeated"))
    for index, value in enumerate(range(0, 101, 5), 1):
        dialog.balance_slider.setValue(value)
        dialog.player.advance(10)
        assert not dialog.is_busy
        assert dialog.player.position() == dialog.overview.position == dialog.waveform.position == 300 + index * 10
        assert dialog.player.playbackState() == _DecoderPlayer.PlayingState
    # Also catch the old delayed 150 ms timer starting work after the drag.
    QTest.qWait(250)
    assert dialog.player.transport_calls == []
    assert sorted(path.relative_to(workspace) for path in workspace.rglob("*")) == files_before
    assert dialog.player.balance == 1


def test_balance_slider_changes_real_streamed_pcm_without_interrupting_ui_timeline(
        qt_application, tmp_path, paired_files, monkeypatch, fake_audio):
    from aps_midi_prep_tool_app.disklavier_stem_player import DisklavierStemPlayer

    class Devices(_FakeMediaDevices):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.default = _StreamingDevice()

    def render(_midi, destination, _cancel):
        _write_wav(destination, np.full((2 * 44100, 2), 2000, dtype="<i2"))
        return True

    monkeypatch.setattr(dialogs, "DisklavierStemPlayer", DisklavierStemPlayer)
    monkeypatch.setattr(dialogs, "QAudioOutput", _StreamingOutput)
    monkeypatch.setattr(audio_output_routing, "QMediaDevices", Devices)
    monkeypatch.setattr(preview, "_render_soundfont", render)
    settings = QSettings(str(tmp_path / "streamed-player.ini"), QSettings.IniFormat)
    instance = dialogs.DisklavierWavPlayerDialog(settings)
    instance.show()
    try:
        instance.open_file(paired_files[0])
        _wait(lambda: not instance.is_busy)
        assert instance.result is not None, instance.status.text()
        instance.seek(700)
        instance.toggle_play()
        instance.player._timer.stop()  # Consume the output deterministically below.
        sink = fake_audio.instances[-1]
        initial_pcm = np.frombuffer(bytes(sink.captured), dtype="<i2").reshape(-1, 2)
        np.testing.assert_array_equal(initial_pcm, np.tile([5000, -1000], (len(initial_pcm), 1)))
        positions = []
        with monkeypatch.context() as guards:
            def transport_changed(*_args, **_kwargs):
                pytest.fail("Changing live gains restarted or repositioned audio")

            for name in ("load", "clear", "play", "pause", "stop", "setPosition"):
                guards.setattr(instance.player, name, transport_changed)
            for name in ("start", "reset", "suspend", "resume"):
                guards.setattr(sink, name, transport_changed)
            instance.balance_slider.setValue(100)
            for tick in range(1, 13):
                assert len(sink.consume(441)) == 441 * 4
                instance.player._pump()
                position = instance.player.position()
                positions.append(position)
                assert position == instance.overview.position == instance.waveform.position == 700 + tick * 10
                assert instance.player.playbackState() == DisklavierStemPlayer.PlayingState
                assert not instance.is_busy
        played = np.frombuffer(bytes(sink.played), dtype="<i2").reshape(-1, 2)
        np.testing.assert_array_equal(played[:441], np.tile([5000, -1000], (441, 1)))
        np.testing.assert_array_equal(played[-441:], np.tile([8000, -4000], (441, 1)))
        assert positions == sorted(positions)
        assert len(fake_audio.instances) == 1
        assert sink.start_count == 1 and sink.reset_count == 0
        assert instance.volume_slider.value() == 70
        assert instance.audio_output.volume() == pytest.approx(.7)
        assert not list(Path(instance._workspace.name).glob("balance-*.wav"))
    finally:
        instance.close()
        qt_application.processEvents()


@pytest.mark.parametrize("action", ["pause", "stop", "seek"])
def test_transport_requests_during_balance_drag_take_effect_immediately(dialog, encoded, action):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(500)
    dialog.toggle_play()
    dialog.balance_slider.setValue(25)
    if action == "pause":
        dialog.toggle_play()
    elif action == "stop":
        dialog.stop()
    else:
        dialog.seek(900)
    expected_position = {"pause": 500, "stop": 0, "seek": 900}[action]
    expected_state = {"pause": _DecoderPlayer.PlaybackState.PausedState,
                      "stop": _DecoderPlayer.PlaybackState.StoppedState,
                      "seek": _DecoderPlayer.PlaybackState.PlayingState}[action]
    dialog.player.transport_calls.clear()
    dialog.balance_slider.setValue(75)
    QTest.qWait(200)
    assert dialog.player.transport_calls == []
    assert dialog.player.position() == dialog.overview.position == dialog.waveform.position == expected_position
    assert dialog.player.playbackState() == expected_state


def test_close_after_balance_drag_stops_audio_before_removing_workspace(dialog, encoded, monkeypatch):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    directory = Path(dialog._workspace.name)
    dialog.toggle_play()
    dialog.balance_slider.setValue(25)
    cleared = []
    clear = dialog.player.clear

    def checked_clear():
        assert directory.exists()
        assert all(Path(path).exists() for path in dialog.player.loaded_stems)
        clear()
        cleared.append(True)

    monkeypatch.setattr(dialog.player, "clear", checked_clear)
    dialog.cancel_or_close()
    assert cleared == [True]
    assert not dialog.is_busy
    assert dialog.player.loaded_stems is None
    assert not dialog.isVisible()
    assert not directory.exists()
    monkeypatch.setattr(dialog.player, "clear", clear)


def test_audio_device_error_leaves_balance_and_play_recoverable(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.seek(500)
    dialog.toggle_play()
    loaded = dialog.player.loaded_stems
    dialog.player.fail("Audio device unavailable")
    assert dialog.play_button.text() == "Play"
    assert "Audio device unavailable" in dialog.status.text()
    dialog.player.transport_calls.clear()
    dialog.balance_slider.setValue(75)
    assert dialog.player.transport_calls == []
    assert dialog.player.loaded_stems == loaded
    assert dialog.player.position() == 500
    assert dialog.play_button.isEnabled()
    assert dialog.balance_slider.isEnabled()
    dialog.toggle_play()
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState
    assert dialog.player.transport_calls == [("play",)]


def test_failed_stem_load_restores_open_controls_and_cleans_workspace(dialog, encoded, monkeypatch):
    def fail_load(*_args, **_kwargs):
        raise ValueError("Prepared accompaniment is unreadable")

    monkeypatch.setattr(dialog.player, "load", fail_load)
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    assert dialog.result is None
    assert dialog.player.loaded_stems is None
    assert dialog.open_button.isEnabled()
    assert not dialog.play_button.isEnabled()
    assert "Prepared accompaniment is unreadable" in dialog.status.text()
    assert dialog._workspace is None


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
    original_source = dialog.player.loaded_stems
    original_volume = dialog.audio_output.volume()
    monitor = dialog.audio_output_follower._devices

    monitor.use("headphones")
    monitor.use("")
    monitor.use("headphones")

    assert dialog.audio_output.device() == _FakeAudioDevice("headphones")
    assert dialog.player.audioOutput() is dialog.audio_output
    assert dialog.player.playbackState() == original_state
    assert dialog.player.position() == 800
    assert dialog.player.loaded_stems == original_source
    assert dialog.audio_output.volume() == original_volume


def test_play_refreshes_current_headphones_before_resuming(dialog, encoded):
    dialog.open_file(encoded)
    _wait(lambda: not dialog.is_busy)
    dialog.audio_output_follower._devices.default = _FakeAudioDevice("headphones")
    dialog.toggle_play()
    assert dialog.audio_output.device() == _FakeAudioDevice("headphones")
    assert dialog.player.playbackState() == _DecoderPlayer.PlayingState


@pytest.mark.parametrize("piano_frames", [44100, 3 * 44100])
def test_both_listening_modes_keep_full_stem_duration_and_position(dialog, encoded, monkeypatch, piano_frames):
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
    duration = round(max(original_duration, piano_frames / 44100) * 1000)
    dialog.seek(duration)
    dialog.player.transport_calls.clear()
    for mode in (1, 0, 1):
        dialog.mode_combo.setCurrentIndex(mode)
        assert dialog.overview.duration == duration
        assert dialog.waveform.duration == duration
        assert dialog.waveform.waveform["duration_seconds"] == original_duration
        assert dialog.position_slider.maximum() == duration
        assert dialog.summary.text().endswith(dialogs._time_label(duration))
        assert dialog.time_label.text().endswith(dialogs._time_label(duration))
        assert dialog.overview.position == duration
        assert dialog.waveform.position == duration
        assert dialog.player.position() == duration
    assert dialog.player.transport_calls == []


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
