"""Play validated preview stems through one continuously mixed PCM stream."""

import math
import sys
import wave

import numpy as np
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices, QMediaPlayer


_RATE = 44100
_FRAME_BYTES = 4
_BUFFER_FRAMES = round(_RATE * .08)
_RAMP_FRAMES = round(_RATE * .02)


class _StemMixer:
    """Bounded PCM reads with a single frame cursor and a continuous gain ramp."""

    def __init__(self, piano, accompaniment, *, allow_stereo=False, balance=.5):
        self.sources = []
        try:
            for path in (piano, accompaniment):
                self.sources.append(wave.open(str(path), "rb"))
            for index, source in enumerate(self.sources):
                channels = (1, 2) if index == 0 or allow_stereo else (1,)
                if (source.getnchannels() not in channels or source.getsampwidth() != 2
                        or source.getframerate() != _RATE or source.getcomptype() != "NONE"):
                    raise ValueError("Preview stems require validated 44.1 kHz, 16-bit PCM audio.")
            self.frames = max(source.getnframes() for source in self.sources)
            if not self.frames:
                raise ValueError("The preview audio is empty.")
        except (wave.Error, EOFError) as exc:
            self.close()
            raise ValueError(f"The preview audio WAV cannot be read: {exc}") from exc
        except BaseException:
            self.close()
            raise
        self.frame = 0
        self.balance = self.target = float(balance)
        self.ramp_left = 0

    def close(self):
        for source in self.sources:
            source.close()
        self.sources.clear()

    def seek(self, frame):
        self.frame = min(self.frames, max(0, int(frame)))
        for source in self.sources:
            source.setpos(min(self.frame, source.getnframes()))

    def set_balance(self, value):
        if value != self.target:
            self.target = value
            self.ramp_left = _RAMP_FRAMES

    def read(self, frames):
        count = min(max(0, int(frames)), self.frames - self.frame)
        if not count:
            return b""
        stems = []
        for source in self.sources:
            available = min(count, max(0, source.getnframes() - self.frame))
            raw = source.readframes(available)
            if len(raw) != available * source.getnchannels() * 2:
                raise ValueError("The preview audio WAV is truncated.")
            samples = np.zeros((count, 2), dtype=np.float64)
            if available:
                samples[:available] = np.frombuffer(raw, dtype="<i2").reshape(-1, source.getnchannels())
            stems.append(samples)
        gains = np.full(count, self.target, dtype=np.float64)
        ramp = min(count, self.ramp_left)
        if ramp:
            gains[:ramp] = self.balance + (self.target - self.balance) * (
                np.arange(1, ramp + 1, dtype=np.float64) / self.ramp_left)
            self.balance = float(gains[ramp - 1])
            self.ramp_left -= ramp
        else:
            self.balance = self.target
        self.frame += count
        mixed = stems[0] * (1 - gains[:, None]) + stems[1] * gains[:, None]
        return np.clip(np.rint(mixed), -32768, 32767).astype("<i2").tobytes()


class DisklavierStemPlayer(QObject):
    """A small media-player interface over one clock and one output sink.

    QAudioOutput is only the device/volume settings object. Changing gain never
    replaces the sink or changes either stem's frame position.
    """

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)
    PlaybackState = QMediaPlayer.PlaybackState
    MediaStatus = QMediaPlayer.MediaStatus
    PlayingState = QMediaPlayer.PlayingState
    PausedState = QMediaPlayer.PausedState
    StoppedState = QMediaPlayer.StoppedState
    LoadedMedia = QMediaPlayer.LoadedMedia
    BufferedMedia = QMediaPlayer.BufferedMedia
    EndOfMedia = QMediaPlayer.EndOfMedia
    NoError = QMediaPlayer.NoError

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mixer = None
        self._output = None
        self._sink = self._writer = None
        self._balance = .5
        self._piano_only = False
        self._state = self.StoppedState
        self._status = self.MediaStatus.NoMedia
        self._frame = self._anchor = self._submitted = 0
        self._pending = b""
        self._frame_bytes = _FRAME_BYTES
        self._float_output = False
        self._queue_limit = _BUFFER_FRAMES * _FRAME_BYTES
        self._padding_left = 0
        self._last_position = 0
        self._pumping = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._pump)

    def load(self, piano, accompaniment, *, allow_stereo=False):
        mixer = _StemMixer(piano, accompaniment, allow_stereo=allow_stereo,
                           balance=0 if self._piano_only else self._balance)
        self.clear()
        self._mixer = mixer
        self._set_status(self.LoadedMedia)
        self.durationChanged.emit(self.duration())

    def clear(self):
        self._dispose_sink()
        if self._mixer is not None:
            self._mixer.close()
            self._mixer = None
        self._frame = self._anchor = self._submitted = 0
        self._pending = b""
        self._set_state(self.StoppedState)
        self._set_status(self.MediaStatus.NoMedia)
        self._publish_position(force=True)
        self.durationChanged.emit(0)

    def setBalance(self, value):
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Preview balance must be between 0 and 1.")
        self._balance = value
        if self._mixer is not None and not self._piano_only:
            self._mixer.set_balance(value)

    def setPianoOnly(self, enabled):
        self._piano_only = bool(enabled)
        if self._mixer is not None:
            self._mixer.set_balance(0 if enabled else self._balance)

    def audioOutput(self):
        return self._output

    def setAudioOutput(self, output):
        if output is self._output:
            return
        self._freeze_transport()
        if self._output is not None:
            for signal, slot in ((self._output.volumeChanged, self._volume_changed),
                                 (self._output.deviceChanged, self._device_changed)):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._output = output
        if output is not None:
            output.volumeChanged.connect(self._volume_changed)
            output.deviceChanged.connect(self._device_changed)
            if self._state == self.PlayingState:
                self._start_sink()

    def duration(self):
        return round(self._mixer.frames * 1000 / _RATE) if self._mixer is not None else 0

    def position(self):
        return round(self._heard_frame() * 1000 / _RATE)

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.mediaStatusChanged.emit(status)

    def _heard_frame(self):
        if self._sink is None or self._state != self.PlayingState:
            return self._frame
        if self._sink.state() not in (QAudio.ActiveState, QAudio.IdleState):
            return self._frame
        processed = max(0, self._sink.processedUSecs() * _RATE // 1000000)
        # Windows/ALSA may count submitted input while PulseAudio accounts for
        # playback latency. Bound either clock by data no longer in the queue;
        # do not subtract queue latency twice from an already adjusted clock.
        queued = min(self._submitted, max(0, self._sink.bufferSize() - self._sink.bytesFree()))
        drained = (self._submitted - queued) // self._frame_bytes
        self._frame = max(self._frame, min(self._mixer.frames, self._anchor + min(processed, drained)))
        return self._frame

    def _publish_position(self, *, force=False):
        value = self.position()
        if force or value != self._last_position:
            self._last_position = value
            self.positionChanged.emit(value)

    def _dispose_sink(self):
        self._timer.stop()
        sink, self._sink = self._sink, None
        self._writer = None
        if sink is not None:
            sink.stateChanged.disconnect(self._audio_state_changed)
            sink.reset()
            sink.deleteLater()

    def _freeze_transport(self):
        self._frame = self._heard_frame()
        self._dispose_sink()
        self._pending = b""
        self._anchor, self._submitted = self._frame, 0
        if self._mixer is not None:
            self._mixer.seek(self._frame)

    def _device_changed(self, *_args):
        self._freeze_transport()
        if self._state == self.PlayingState:
            self._start_sink()

    def _volume_changed(self, *_args):
        if self._sink is not None and self._output is not None:
            self._sink.setVolume(self._output.volume())

    def _start_sink(self):
        if self._mixer is None or self._output is None:
            return
        try:
            device = self._output.device()
            if device.isNull():
                device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                raise RuntimeError("No audio output device is available.")
            audio_format = QAudioFormat()
            audio_format.setSampleRate(_RATE)
            audio_format.setChannelCount(2)
            audio_format.setSampleFormat(QAudioFormat.Int16)
            self._float_output = False
            if not device.isFormatSupported(audio_format) and sys.platform != "win32":
                audio_format.setSampleFormat(QAudioFormat.Float)
                if not device.isFormatSupported(audio_format):
                    raise RuntimeError("The audio output does not support 44.1 kHz stereo PCM playback.")
                self._float_output = True
            # Qt's Windows sink resamples to the WASAPI mix format internally,
            # even when the advertised native format excludes this input.
            self._frame_bytes = 8 if self._float_output else _FRAME_BYTES
            sink = QAudioSink(device, audio_format, self)
            self._sink = sink
            sink.setBufferSize(_BUFFER_FRAMES * self._frame_bytes)
            sink.setVolume(self._output.volume())
            sink.stateChanged.connect(self._audio_state_changed)
            self._anchor, self._submitted = self._frame, 0
            self._writer = sink.start()
            if self._writer is None or sink.error() != QAudio.NoError:
                raise RuntimeError("The audio output could not be opened.")
            # Some outputs require a larger initial prebuffer than requested.
            # Respect that capacity so the first fill can start the device.
            self._queue_limit = max(_BUFFER_FRAMES * self._frame_bytes, sink.bufferSize())
            # Push-mode backends may wait for a complete startup prebuffer.
            # A short clip (or a seek near EOF) gets only enough trailing
            # silence to start it; its reported media duration stays unchanged.
            self._padding_left = max(0, self._queue_limit
                                     - (self._mixer.frames - self._anchor) * self._frame_bytes)
            if sys.platform == "win32":
                # Qt's Windows resampler does not explicitly flush the native
                # transform. A finite silent tail releases its final samples.
                # Bound it by the queue budget: the media clock stops at EOF.
                self._padding_left = max(self._padding_left,
                                         min(_BUFFER_FRAMES * self._frame_bytes, self._queue_limit))
            self._timer.start()
            self._pump()
        except Exception as exc:
            self._fail(exc)

    def play(self):
        if self._mixer is None or self._state == self.PlayingState:
            return
        if self._status == self.EndOfMedia:
            self.setPosition(0)
        self._set_state(self.PlayingState)
        self._set_status(self.BufferedMedia)
        if self._sink is not None:
            self._sink.resume()
            self._timer.start()
            self._pump()
        else:
            self._start_sink()

    def pause(self):
        if self._state != self.PlayingState:
            return
        self._frame = self._heard_frame()
        self._set_state(self.PausedState)
        self._timer.stop()
        if self._sink is not None:
            self._sink.suspend()
        self._publish_position(force=True)

    def stop(self):
        self._freeze_transport()
        self._set_state(self.StoppedState)
        self.setPosition(0)

    def setPosition(self, milliseconds):
        if self._mixer is None:
            return
        playing = self._state == self.PlayingState
        self._dispose_sink()
        self._frame = min(self._mixer.frames, max(0, round(float(milliseconds) * _RATE / 1000)))
        self._anchor, self._submitted = self._frame, 0
        self._pending = b""
        self._mixer.seek(self._frame)
        self._set_status(self.LoadedMedia)
        self._publish_position(force=True)
        if self._frame == self._mixer.frames:
            self._set_state(self.StoppedState)
            self._set_status(self.EndOfMedia)
        elif playing:
            self._start_sink()

    def _pump(self):
        if self._pumping or self._state != self.PlayingState or self._writer is None:
            return
        self._pumping = True
        try:
            while self._writer is not None:
                heard = self._heard_frame() - self._anchor
                queued = max(0, self._submitted - heard * self._frame_bytes)
                capacity = min(self._sink.bytesFree(), self._queue_limit - queued)
                if capacity <= 0:
                    break
                if not self._pending:
                    frames = min(2048, capacity // self._frame_bytes)
                    if not frames:
                        break
                    self._pending = self._mixer.read(frames)
                    if not self._pending:
                        if not self._padding_left:
                            break
                        padding = min(frames * self._frame_bytes, self._padding_left)
                        self._pending = bytes(padding)
                        self._padding_left -= padding
                    elif self._float_output:
                        self._pending = (np.frombuffer(self._pending, dtype="<i2").astype("<f4") / 32768).tobytes()
                written = self._writer.write(self._pending[:capacity])
                if written < 0:
                    raise RuntimeError("Writing audio to the output device failed.")
                if not written:
                    break
                self._pending = self._pending[written:]
                self._submitted += written
            self._publish_position()
            self._finish_if_drained()
        except Exception as exc:
            self._fail(exc)
        finally:
            self._pumping = False

    def _finish_if_drained(self):
        if (self._sink is not None and self._submitted > 0 and not self._pending
                and not self._padding_left
                and self._mixer.frame == self._mixer.frames and self._sink.state() == QAudio.IdleState
                and self._sink.bytesFree() >= self._sink.bufferSize()):
            self._frame = self._mixer.frames
            self._dispose_sink()
            self._set_state(self.StoppedState)
            self._set_status(self.EndOfMedia)
            self._publish_position(force=True)

    def _sink_state(self, sink, state):
        if sink is not self._sink:
            return
        if state == QAudio.StoppedState and sink.error() != QAudio.NoError:
            self._fail(RuntimeError("The audio output stopped unexpectedly."))
        elif state == QAudio.IdleState and not self._pumping:
            self._finish_if_drained()

    def _audio_state_changed(self, state):
        self._sink_state(self.sender(), state)

    def _fail(self, error):
        self._freeze_transport()
        self._set_state(self.StoppedState)
        self.errorOccurred.emit(QMediaPlayer.ResourceError, str(error))
