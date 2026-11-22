"""Consume the actual streaming PCM; no host audio device is opened."""

from types import SimpleNamespace
import wave

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat

from aps_midi_prep_tool_app import disklavier_stem_player as stem


class _FakeDevice:
    def __init__(self, name="speakers", formats=None):
        self.name = name
        self.formats = formats or {QAudioFormat.Int16, QAudioFormat.Float}

    def isNull(self):
        return not self.name

    def isFormatSupported(self, audio_format):
        return audio_format.sampleFormat() in self.formats

    def __eq__(self, other):
        return isinstance(other, _FakeDevice) and self.name == other.name


class _FakeOutput(QObject):
    volumeChanged = Signal(float)
    deviceChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device = _FakeDevice()
        self._volume = .7

    def device(self):
        return self._device

    def setDevice(self, device):
        if device != self._device:
            self._device = device
            self.deviceChanged.emit()

    def volume(self):
        return self._volume

    def setVolume(self, value):
        self._volume = value
        self.volumeChanged.emit(value)


class _FakeSink(QObject):
    stateChanged = Signal(object)
    instances = []
    max_write = None
    buffer_multiplier = 1
    clock_counts_submitted = False
    require_prebuffer = False

    def __init__(self, device, audio_format, parent=None):
        super().__init__(parent)
        self.device = device
        self.format = audio_format
        self.frame_bytes = audio_format.bytesPerFrame()
        self._capacity = 0
        self._state = QAudio.StoppedState
        self._error = QAudio.NoError
        self.queue = bytearray()
        self.captured = bytearray()
        self.played = bytearray()
        self.start_count = self.reset_count = self.suspend_count = self.resume_count = 0
        self.instances.append(self)

    def setBufferSize(self, value):
        self._capacity = value * self.buffer_multiplier

    def bufferSize(self):
        return self._capacity

    def setVolume(self, value):
        self.volume = value

    def _change(self, value):
        self._state = value
        self.stateChanged.emit(value)

    def start(self):
        self.start_count += 1
        self._change(QAudio.IdleState)
        return self

    def write(self, data):
        count = min(len(data), self.bytesFree())
        if self.max_write is not None:
            count = min(count, self.max_write)
        self.queue.extend(data[:count])
        self.captured.extend(data[:count])
        if count and (not self.require_prebuffer or len(self.queue) == self._capacity):
            self._change(QAudio.ActiveState)
        return count

    def consume(self, frames):
        if self._state != QAudio.ActiveState:
            return b""
        count = min(len(self.queue), frames * self.frame_bytes)
        result = bytes(self.queue[:count])
        del self.queue[:count]
        self.played.extend(result)
        if not self.queue:
            self._change(QAudio.IdleState)
        return result

    def bytesFree(self):
        return self._capacity - len(self.queue) if self._state in (QAudio.ActiveState, QAudio.IdleState) else 0

    def processedUSecs(self):
        count = len(self.captured) if self.clock_counts_submitted else len(self.played)
        return round(count / self.frame_bytes / 44100 * 1000000)

    def error(self):
        return self._error

    def state(self):
        return self._state

    def reset(self):
        self.reset_count += 1
        self.queue.clear()
        self._change(QAudio.StoppedState)

    def suspend(self):
        self.suspend_count += 1
        self._change(QAudio.SuspendedState)

    def resume(self):
        self.resume_count += 1
        self._change(QAudio.ActiveState if self.queue else QAudio.IdleState)


@pytest.fixture
def fake_audio(monkeypatch, qt_application):
    monkeypatch.setattr(stem, "sys", SimpleNamespace(platform="linux"))
    _FakeSink.instances = []
    for name, value in {"max_write": None, "buffer_multiplier": 1,
                        "clock_counts_submitted": False, "require_prebuffer": False}.items():
        monkeypatch.setattr(_FakeSink, name, value)
    monkeypatch.setattr(stem, "QAudioSink", _FakeSink)
    return _FakeSink


def _write(path, samples):
    samples = np.asarray(samples, dtype="<i2")
    if samples.ndim == 1:
        samples = samples[:, None]
    with wave.open(str(path), "wb") as output:
        output.setparams((samples.shape[1], 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    return path


def _player(tmp_path, piano, audio, *, balance=.5, allow_stereo=True):
    first = _write(tmp_path / "piano.wav", piano)
    second = _write(tmp_path / "audio.wav", audio)
    player = stem.DisklavierStemPlayer()
    output = _FakeOutput(player)
    player.setAudioOutput(output)
    player.setBalance(balance)
    player.load(first, second, allow_stereo=allow_stereo)
    return player


@pytest.mark.parametrize("submitted_clock", [False, True])
@pytest.mark.parametrize("partial", [None, 7])
def test_rapid_balance_and_mode_changes_never_duplicate_or_drop_pcm_frames(
        tmp_path, fake_audio, submitted_clock, partial):
    fake_audio.clock_counts_submitted = submitted_clock
    fake_audio.max_write = partial
    frames = 22050
    values = np.arange(frames, dtype=np.int16)
    pcm = np.column_stack((values, -values))
    player = _player(tmp_path, pcm, pcm)
    player.play()
    sink = fake_audio.instances[-1]
    assert player.position() == 0  # Prebuffering is not audible progress.
    positions = []
    for tick in range(100):
        player.setBalance((tick % 11) / 10)
        player.setPianoOnly(tick % 3 == 0)
        sink.consume(441)
        player._pump()
        positions.append(player.position())
        if player.mediaStatus() == player.EndOfMedia:
            break
    assert len(fake_audio.instances) == 1 and sink.start_count == 1
    assert np.array_equal(np.frombuffer(sink.played, dtype="<i2").reshape(-1, 2), pcm)
    assert positions == sorted(positions)
    assert player.position() == player.duration() == 500
    assert player.mediaStatus() == player.EndOfMedia
    player.clear()


def test_balance_ramp_is_smooth_across_blocks_and_rapid_retargeting(tmp_path):
    first = _write(tmp_path / "p.wav", np.zeros(5000))
    second = _write(tmp_path / "a.wav", np.full((5000, 2), [12000, -6000]))
    mixer = stem._StemMixer(first, second, allow_stereo=True, balance=0)
    mixer.set_balance(1)
    parts = [mixer.read(137), mixer.read(211)]
    mixer.set_balance(.25)
    parts.extend([mixer.read(300), mixer.read(700)])
    data = np.frombuffer(b"".join(parts), dtype="<i2").reshape(-1, 2)
    assert np.max(np.abs(np.diff(data[:, 0].astype(int)))) <= 14
    assert data[347, 0] == round(12000 * 348 / 882)
    assert data[-1].tolist() == [3000, -1500]
    assert mixer.frame == len(data)
    mixer.close()


@pytest.mark.parametrize("balance", [0, .5, 1])
def test_mono_stereo_and_shorter_tail_keep_shared_duration(tmp_path, balance):
    piano = np.array([0, 10000, -10000])
    audio = np.array([[0, 0], [20000, -12000], [-16000, 4000], [8000, -4000]])
    mixer = stem._StemMixer(_write(tmp_path / "p.wav", piano), _write(tmp_path / "a.wav", audio),
                            allow_stereo=True, balance=balance)
    expected = np.pad(piano[:, None], ((0, 1), (0, 0))) * (1 - balance) + audio * balance
    actual = np.frombuffer(mixer.read(10), dtype="<i2").reshape(-1, 2)
    assert np.array_equal(actual, np.rint(expected))
    assert mixer.frames == 4 and mixer.read(1) == b""
    mixer.close()


def test_pause_resume_seek_stop_and_device_switch_use_heard_cursor(tmp_path, fake_audio):
    pcm = np.column_stack((np.arange(22050), -np.arange(22050)))
    player = _player(tmp_path, pcm, pcm)
    player.play()
    first = fake_audio.instances[-1]
    first.consume(4410)
    player._pump()
    heard = player.position()
    assert 0 < heard < player._mixer.frame / 44.1
    player.pause()
    assert player.position() == heard
    assert first.consume(4410) == b""
    player.setBalance(.8)
    player.play()
    assert fake_audio.instances == [first] and first.resume_count == 1
    assert player.position() == heard
    player.setPosition(200)
    second = fake_audio.instances[-1]
    assert second is not first and player.position() == 200
    assert np.frombuffer(second.captured, dtype="<i2").reshape(-1, 2)[0].tolist() == [8820, -8820]
    second.consume(441)
    player._pump()
    player.audioOutput().setDevice(_FakeDevice("headphones"))
    third = fake_audio.instances[-1]
    assert player.position() == 210
    assert np.frombuffer(third.captured, dtype="<i2").reshape(-1, 2)[0].tolist() == [9261, -9261]
    output = player.audioOutput()
    player.setAudioOutput(None)
    assert player.position() == 210 and player.playbackState() == player.PlayingState
    player.setAudioOutput(output)
    assert player.position() == 210 and player.playbackState() == player.PlayingState
    player.stop()
    assert player.position() == 0 and player.playbackState() == player.StoppedState
    player.clear()


def test_end_waits_for_drain_and_seek_to_end_has_defined_state(tmp_path, fake_audio):
    player = _player(tmp_path, np.ones(2205), np.ones(2205))
    player.play()
    sink = fake_audio.instances[-1]
    assert player._mixer.frame == 2205
    assert player.mediaStatus() != player.EndOfMedia
    sink.consume(2204)
    player._pump()
    assert player.mediaStatus() != player.EndOfMedia
    sink.consume(1)
    assert player.position() == 50
    assert np.frombuffer(sink.queue, dtype="<i2").tolist() == [0] * (len(sink.queue) // 2)
    sink.consume(44100)  # Only the finite startup padding remains.
    assert player.mediaStatus() == player.EndOfMedia
    player.play()
    assert player.position() == 0
    player.setPosition(player.duration())
    assert player.mediaStatus() == player.EndOfMedia and player.playbackState() == player.StoppedState
    player.play()
    player.pause()
    player.setPosition(player.duration())
    player.play()
    assert player.position() == 0 and player.playbackState() == player.PlayingState
    player.clear()


def test_larger_platform_prebuffer_does_not_stall_playback(tmp_path, fake_audio):
    fake_audio.buffer_multiplier = 4
    fake_audio.require_prebuffer = True
    player = _player(tmp_path, np.ones(44100), np.ones(44100))
    player.play()
    sink = fake_audio.instances[-1]
    assert len(sink.queue) == sink.bufferSize() and sink.state() == QAudio.ActiveState
    sink.consume(441)
    player._pump()
    assert player.position() == 10
    player.clear()


def test_float_fallback_retains_channels_and_pcm_timeline(tmp_path, fake_audio, monkeypatch):
    monkeypatch.setattr(stem, "sys", SimpleNamespace(platform="linux"))
    pcm = np.full((4410, 2), [16384, -8192])
    player = _player(tmp_path, pcm, pcm)
    player.audioOutput().setDevice(_FakeDevice("float speakers", formats={QAudioFormat.Float}))
    player.play()
    sink = fake_audio.instances[-1]
    assert sink.frame_bytes == 8
    actual = np.frombuffer(sink.captured, dtype="<f4").reshape(-1, 2)
    assert np.array_equal(actual, np.tile([.5, -.25], (len(actual), 1)))
    sink.consume(441)
    player._pump()
    assert player.position() == 10
    player.clear()


@pytest.mark.parametrize("seek_tail", [False, True])
def test_short_startup_tail_fills_prebuffer_without_extending_media_time(tmp_path, fake_audio, seek_tail):
    fake_audio.buffer_multiplier = 4
    fake_audio.require_prebuffer = True
    frames = 44100 if seek_tail else 441
    pcm = np.full((frames, 2), [10000, -10000])
    player = _player(tmp_path, pcm, pcm)
    if seek_tail:
        player.setPosition(990)
    player.play()
    sink = fake_audio.instances[-1]
    assert sink.state() == QAudio.ActiveState
    assert len(sink.captured) == sink.bufferSize()
    actual = np.frombuffer(sink.captured, dtype="<i2").reshape(-1, 2)
    assert np.array_equal(actual[:441], pcm[:441])
    assert not actual[441:].any()
    sink.consume(sink.bufferSize() // sink.frame_bytes)
    assert player.position() == player.duration() == (1000 if seek_tail else 10)
    assert player.mediaStatus() == player.EndOfMedia
    player.clear()


def test_temporary_underrun_recovers_and_device_error_keeps_last_heard_position(tmp_path, fake_audio):
    player = _player(tmp_path, np.ones(44100), np.ones(44100))
    player.play()
    sink = fake_audio.instances[-1]
    sink.consume(44100)
    assert sink.state() == QAudio.IdleState and player.mediaStatus() != player.EndOfMedia
    player._pump()
    assert sink.state() == QAudio.ActiveState and player.position() == 80
    errors = []
    player.errorOccurred.connect(lambda code, message: errors.append(message))
    sink._error = QAudio.FatalError
    sink._change(QAudio.StoppedState)
    assert errors and player.position() == 80
    assert player.playbackState() == player.StoppedState
    player.clear()


@pytest.mark.parametrize("frames", [441, 22050])
@pytest.mark.parametrize("buffer_multiplier", [1, 4])
@pytest.mark.parametrize("submitted_clock", [False, True])
def test_windows_resampler_gets_finite_tail_and_drains_without_changing_timeline(
        tmp_path, fake_audio, monkeypatch, frames, buffer_multiplier, submitted_clock):
    monkeypatch.setattr(stem, "sys", SimpleNamespace(platform="win32"))
    fake_audio.buffer_multiplier = buffer_multiplier
    fake_audio.clock_counts_submitted = submitted_clock
    fake_audio.require_prebuffer = True
    pcm = np.column_stack((np.arange(frames), -np.arange(frames)))
    player = _player(tmp_path, pcm, pcm)
    # WASAPI can accept canonical PCM through Qt's native resampler even when
    # its advertised hardware format only lists Float.
    player.audioOutput().setDevice(_FakeDevice("native float", {QAudioFormat.Float}))
    player.play()
    sink = fake_audio.instances[-1]
    assert sink.format.sampleFormat() == QAudioFormat.Int16
    for _ in range(200):
        sink.consume(441)
        player._pump()
        if player.mediaStatus() == player.EndOfMedia:
            break
    actual = np.frombuffer(sink.played, dtype="<i2").reshape(-1, 2)
    expected_tail = max(stem._BUFFER_FRAMES, sink.bufferSize() // 4 - frames)
    assert len(actual) == frames + expected_tail
    assert np.array_equal(actual[:frames], pcm)
    assert not actual[frames:].any()
    assert expected_tail <= sink.bufferSize() // 4
    assert player.mediaStatus() == player.EndOfMedia
    assert player.position() == player.duration() == round(frames / 44.1)
    assert len(fake_audio.instances) == 1
    player.clear()


def test_unsafe_stereo_and_truncated_audio_fail_without_playing(tmp_path, fake_audio):
    first = _write(tmp_path / "p.wav", np.zeros(4410))
    second = _write(tmp_path / "a.wav", np.zeros((4410, 2)))
    player = stem.DisklavierStemPlayer()
    with pytest.raises(ValueError, match="validated"):
        player.load(first, second)
    first.write_bytes(b"not a WAV")
    with pytest.raises(ValueError, match="cannot be read"):
        player.load(first, second, allow_stereo=True)
    _write(first, np.zeros(4410))
    second.write_bytes(second.read_bytes()[:-4000])
    player.setAudioOutput(_FakeOutput(player))
    player.load(first, second, allow_stereo=True)
    errors = []
    player.errorOccurred.connect(lambda _code, message: errors.append(message))
    player.play()
    assert errors and "truncated" in errors[0]
    assert player.playbackState() == player.StoppedState
    player.clear()
