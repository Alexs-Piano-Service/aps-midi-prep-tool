"""A separate utility for listening to decoded Disklavier WAV performances."""

from pathlib import Path
import math
import tempfile
import threading

from PySide6.QtCore import QRectF, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QSlider, QStyle, QVBoxLayout, QWidget,
)

from .disklavier_decoder_preview import prepare_preview
from .audio_output_routing import DefaultAudioOutputFollower
from .icon_utils import apply_window_icon
from .helpers.atomic_file import atomic_write_bytes
from .message_catalog import normalize_language_code, tr, translate_text
from .responsive_button_box import ResponsiveDialogButtonBox
from .ui_utils import resize_dialog_to_screen


def _time_label(milliseconds):
    seconds = max(0, int(milliseconds)) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class _PlaybackOverview(QWidget):
    """Shared time-to-pixel mapping for the piano and accompaniment views."""

    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0
        self.position = 0
        self.setCursor(Qt.PointingHandCursor)

    def set_duration(self, milliseconds):
        self.duration = max(0, milliseconds)
        self.update()

    def set_position(self, position):
        self.position = position
        self.update()

    def _time_x(self, milliseconds):
        return milliseconds / max(1, self.duration) * self.width()

    def _paint_playhead(self, painter):
        if self.duration:
            # Fractional display scaling makes a two-logical-pixel stroke
            # alternate in width. Snap the filled marker to physical pixels,
            # including the widget's offset within the backing surface.
            transform = painter.deviceTransform()
            scale, origin = transform.m11(), transform.dx()
            first = math.ceil(origin)
            last = math.floor(origin + self.width() * scale)
            if last <= first:
                return
            width = min(last - first, max(1, math.floor(2 * scale + .5)))
            left = round(origin + self._time_x(self.position) * scale - width / 2)
            left = max(first, min(last - width, left))
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.fillRect(QRectF((left - origin) / scale, 0, width / scale, self.height()),
                             self.palette().text().color())
            painter.restore()

    def _signal_color(self):
        palette = self.palette()
        color = QColor(palette.highlight().color())
        # Keep data visible even when a theme uses subdued selection colors.
        lightness = (max(170, color.lightness()) if palette.base().color().lightness() < 128
                     else min(110, color.lightness()))
        color.setHsl(color.hslHue(), color.hslSaturation(), lightness)
        return color

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.duration:
            self.seekRequested.emit(round(min(1, max(0, event.position().x() / max(1, self.width())))
                                          * self.duration))
        super().mousePressEvent(event)


class DecodedPianoOverview(_PlaybackOverview):
    """A compact, seekable piano roll that fits the complete recording."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.notes = []
        self.setMinimumHeight(80)

    def set_notes(self, notes, duration):
        # Bound painting cost for unusually dense recordings.
        step = max(1, (len(notes) + 19999) // 20000)
        self.notes = list(notes)[::step]
        self.set_duration(max(0, round(duration * 1000)))
        self.set_position(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        if not self.notes or not self.duration:
            return
        low = min(21, min(note["pitch"] for note in self.notes))
        high = max(108, max(note["pitch"] for note in self.notes))
        height = self.height() / (high - low + 1)
        for pitch in range(low, high + 1):
            if pitch % 12 in (1, 3, 6, 8, 10):
                painter.fillRect(0, round((high - pitch) * height), self.width(), max(1, round(height)),
                                 palette.alternateBase())
        painter.setPen(Qt.NoPen)
        note_color = self._signal_color()
        for note in self.notes:
            color = QColor(note_color)
            color.setAlpha(180 + round(note["velocity"] / 127 * 75))
            painter.setBrush(color)
            x = self._time_x(note["start_sec"] * 1000)
            width = self._time_x((note["end_sec"] - note["start_sec"]) * 1000)
            painter.drawRect(round(x), round((high - note["pitch"]) * height),
                             max(2, round(width)), max(2, round(height)))
        self._paint_playhead(painter)


class AccompanimentWaveform(_PlaybackOverview):
    """A peak envelope of the original audio channel on the shared timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.waveform = None
        self.setMinimumHeight(56)
        self.setMaximumHeight(104)

    def set_waveform(self, waveform):
        self.waveform = waveform
        self.set_position(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if not self.waveform or not self.duration:
            return
        center = self.height() / 2
        amplitude = max(1, center - 4)
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.drawLine(0, round(center), self.width(), round(center))
        color = self._signal_color()
        color.setAlpha(210)
        data = self.waveform
        frames_per_bin = data["frames_per_bin"]
        milliseconds_per_frame = 1000 / data["sample_rate"]
        for index, (low, high) in enumerate(zip(data["minimums"], data["maximums"])):
            start = index * frames_per_bin
            end = min(start + frames_per_bin, data["frame_count"])
            x = self._time_x(start * milliseconds_per_frame)
            if x >= self.width():
                break
            width = self._time_x((end - start) * milliseconds_per_frame)
            # Include zero so that even a one-sample transient remains visible.
            top = center - max(0, high) * amplitude
            bottom = center - min(0, low) * amplitude
            if bottom > top:
                painter.fillRect(QRectF(x, top, max(1, width), bottom - top), color)
        self._paint_playhead(painter)


class DisklavierPreviewWorker(QThread):
    progressChanged = Signal(str, float)
    succeeded = Signal(object)
    failed = Signal(str, bool)

    def __init__(self, source, directory, parent=None):
        super().__init__(parent)
        self.source = Path(source)
        self.directory = Path(directory)
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        from .disklavier_decoder import decode_disklavier_wav, DisklavierDecodingCancelled
        try:
            decoded = decode_disklavier_wav(
                self.source, self.directory / "decoded.mid", self.directory / "accompaniment.wav",
                cancel=self.cancelled,
                progress=lambda value: self.progressChanged.emit("decode", value),
            )
            result = prepare_preview(decoded, self.directory, cancel=self.cancelled,
                                     progress=self.progressChanged.emit)
            if self.cancelled.is_set():
                raise DisklavierDecodingCancelled("Disklavier playback preparation canceled.")
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc), self.cancelled.is_set() or isinstance(exc, DisklavierDecodingCancelled))


class DisklavierBalanceWorker(QThread):
    succeeded = Signal(int, str)
    failed = Signal(str, bool)

    def __init__(self, result, destination, balance, parent=None):
        super().__init__(parent)
        self.result = result
        self.destination = Path(destination)
        self.balance = balance
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        from .disklavier_decoder_preview import _mix_preview
        try:
            _mix_preview(self.result["piano_path"], self.result["accompaniment_path"],
                         self.destination, balance=self.balance / 100, cancel=self.cancelled)
            self.succeeded.emit(self.balance, str(self.destination))
        except Exception as exc:
            try:
                self.destination.unlink(missing_ok=True)
            except OSError:
                pass  # The temporary workspace will retry cleanup on close.
            self.failed.emit(str(exc), self.cancelled.is_set())


class DisklavierWavPlayerDialog(QDialog):
    def __init__(self, settings, parent=None, *, admin_mode=False):
        super().__init__(parent)
        apply_window_icon(self)
        self._admin_mode_enabled = bool(admin_mode)
        self.settings = settings
        self.language = normalize_language_code(settings.value("language", "en"))
        self.worker = None
        self.result = None
        self._workspace = None
        self._close_pending = False
        self._source_path = None
        self._pending_position = 0
        self._pending_play = False
        self._pending_paused = False
        self._source_loading = False
        self._balance_paths = {}
        self._obsolete_mixes = set()
        self._mix_serial = 0
        self._failed_balance = None
        self._balance_timer = QTimer(self)
        self._balance_timer.setSingleShot(True)
        self._balance_timer.setInterval(150)
        self._balance_timer.timeout.connect(self._start_balance_mix)
        self.setWindowTitle(self.text("action").rstrip("."))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(5)
        description = QLabel(self.text("description"))
        description.setWordWrap(True)
        outer.addWidget(description)

        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText(self.text("choose_hint"))
        self.source_edit.setAccessibleName(self.text("source"))
        source_row.addWidget(self.source_edit, 1)
        self.open_button = QPushButton(self.text("open"))
        self.open_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self.choose_file)
        source_row.addWidget(self.open_button)
        outer.addLayout(source_row)

        self.summary = QLabel(self.text("ready"))
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        outer.addWidget(self.summary)
        self.overview = DecodedPianoOverview(self)
        self.overview.setAccessibleName(self.text("piano_roll"))
        self.overview.setToolTip(self.text("seek_hint"))
        self.overview.seekRequested.connect(self.seek)
        outer.addWidget(self.overview, 3)
        self.waveform = AccompanimentWaveform(self)
        self.waveform.setAccessibleName(self.text("waveform"))
        self.waveform.setToolTip(self.text("waveform") + "\n" + self.text("seek_hint"))
        self.waveform.seekRequested.connect(self.seek)
        outer.addWidget(self.waveform, 1)

        seek_row = QHBoxLayout()
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setAccessibleName(self.text("position"))
        self.position_slider.sliderReleased.connect(lambda: self.seek(self.position_slider.value()))
        self.position_slider.valueChanged.connect(self._slider_changed)
        seek_row.addWidget(self.position_slider, 1)
        self.time_label = QLabel("0:00 / 0:00")
        seek_row.addWidget(self.time_label)
        outer.addLayout(seek_row)

        listen_row = QHBoxLayout()
        label = QLabel(self.text("listen"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.text("piano_audio"), "mixed_path")
        self.mode_combo.addItem(self.text("piano_only"), "piano_path")
        label.setBuddy(self.mode_combo)
        self.mode_combo.setAccessibleName(self.text("listen"))
        self.mode_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo.setMinimumContentsLength(12)
        self.mode_combo.currentIndexChanged.connect(self._change_mix)
        listen_row.addWidget(label)
        listen_row.addWidget(self.mode_combo, 1)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setMaximumWidth(120)
        self.volume_slider.setAccessibleName(translate_text("Preview volume", self.language))
        self.volume_slider.setToolTip(translate_text("Audio preview volume.", self.language))
        listen_row.addWidget(self.volume_slider)
        outer.addLayout(listen_row)

        balance_row = QHBoxLayout()
        self.piano_balance_label = QLabel(self.text("balance_piano"))
        self.accompaniment_balance_label = QLabel(self.text("balance_accompaniment"))
        self.balance_slider = QSlider(Qt.Horizontal)
        self.balance_slider.setRange(0, 100)
        self.balance_slider.setSingleStep(1)
        self.balance_slider.setPageStep(10)
        self.balance_slider.setValue(max(0, min(100, settings.value("disklavier_wav_balance", 50, type=int))))
        self.balance_slider.setTickPosition(QSlider.TicksBelow)
        self.balance_slider.setTickInterval(50)
        self.balance_slider.setAccessibleName(self.text("balance"))
        self.piano_balance_label.setBuddy(self.balance_slider)
        self.balance_slider.valueChanged.connect(self._balance_changed)
        self._balance_changed(self.balance_slider.value(), persist=False)
        balance_row.addWidget(self.piano_balance_label)
        balance_row.addWidget(self.balance_slider, 1)
        balance_row.addWidget(self.accompaniment_balance_label)
        outer.addLayout(balance_row)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.hide()
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        outer.addWidget(self.progress)

        buttons = ResponsiveDialogButtonBox()
        self.play_button = QPushButton(translate_text("Play", self.language))
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setDefault(True)
        self.play_button.clicked.connect(self.toggle_play)
        self.stop_button = QPushButton(translate_text("Stop", self.language))
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.clicked.connect(self.stop)
        self.save_button = QPushButton(self.text("save_midi"))
        self.save_button.clicked.connect(self.save_midi)
        self.close_button = QPushButton(translate_text("Close", self.language))
        self.close_button.clicked.connect(self.cancel_or_close)
        for button in (self.play_button, self.stop_button, self.save_button):
            if button is not self.play_button:
                button.setAutoDefault(False)
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        self.close_button.setAutoDefault(False)
        buttons.addButton(self.close_button, QDialogButtonBox.RejectRole)
        outer.addWidget(buttons)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output_follower = DefaultAudioOutputFollower(self)
        self.audio_output_follower.add_output(self.audio_output, self.player)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._update_controls)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._audio_error)
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        self._update_controls()
        resize_dialog_to_screen(self, width=800, height=600)

    def text(self, key, **kwargs):
        return tr(f"dwav.{key}", self.language, **kwargs)

    @property
    def is_busy(self):
        return self.worker is not None

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.text("open"), str(self.settings.value("disklavier_wav_folder", "") or ""),
            "WAV (*.wav *.WAV)",
        )
        if path:
            self.open_file(path)

    def open_file(self, path):
        if self.is_busy:
            return
        self._clear_result()
        self._source_path = Path(path)
        self.source_edit.setText(str(path))
        self.source_edit.setCursorPosition(0)
        self.source_edit.setToolTip(str(path))
        self.settings.setValue("disklavier_wav_folder", str(self._source_path.parent))
        self._workspace = tempfile.TemporaryDirectory(prefix="aps-disklavier-player-", ignore_cleanup_errors=True)
        self.worker = DisklavierPreviewWorker(path, self._workspace.name, self)
        self.worker.progressChanged.connect(self._progress_changed)
        self.worker.succeeded.connect(self._prepared)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.summary.setText(self.text("preparing"))
        self._progress_changed("decode", 0)
        self._update_controls()
        self.worker.start()

    def _clear_result(self):
        self._balance_timer.stop()
        self._pending_play = False
        self._pending_paused = False
        self._pending_position = 0
        self._source_loading = False
        self.player.stop()
        self.player.setSource(QUrl())
        self.result = None
        self._balance_paths.clear()
        self._obsolete_mixes.clear()
        self._failed_balance = None
        self.overview.set_notes([], 0)
        self.waveform.set_waveform(None)
        self.waveform.set_duration(0)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        if self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None

    def _progress_changed(self, phase, fraction):
        if self.worker is not None and self.worker.cancelled.is_set():
            return
        self.status.setText(self.text("phase_" + phase))
        self.status.show()
        self.progress.show()
        self.progress.setRange(0, 0 if fraction < 0 else 100)
        start, span = {"decode": (0, 45), "piano": (45, 45), "mix": (90, 10)}[phase]
        self.progress.setValue(round(start + max(0, min(1, fraction)) * span))

    def _prepared(self, result):
        if self.worker is not None and self.worker.cancelled.is_set():
            self._failed("", True)
            return
        self.result = result
        self._balance_paths = {50: result["mixed_path"]}
        self.waveform.set_waveform(result.get("accompaniment_waveform"))
        self.status.setText(self.text("prepared"))
        self._change_mix()

    def _failed(self, error, cancelled):
        self.summary.setText(self.text("cancelled" if cancelled else "failed"))
        self.status.setText(self.text("cancelled") if cancelled else error)
        self.status.show()

    def _finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.progress.hide()
        if self.result is None and self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None
        self._update_controls()
        if self._close_pending:
            self._close_pending = False
            self.reject()
        elif self.result is not None:
            self._balance_timer.start()

    def _playback_ready(self):
        return self.result is not None and (not self.is_busy or isinstance(self.worker, DisklavierBalanceWorker))

    def _update_controls(self, *_args):
        ready = self._playback_ready()
        self.open_button.setEnabled(not self.is_busy)
        for widget in (self.play_button, self.stop_button,
                       self.mode_combo, self.position_slider, self.overview, self.waveform):
            widget.setEnabled(ready)
        self.balance_slider.setEnabled(ready and self.mode_combo.currentData() == "mixed_path")
        self.save_button.setVisible(self._admin_mode_enabled)
        self.save_button.setEnabled(ready and not self.is_busy and self._admin_mode_enabled)
        playing = self._pending_play if self._source_loading else self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_button.setText(translate_text("Pause" if playing else "Play", self.language))
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay))
        self.close_button.setText(translate_text("Cancel" if self.is_busy and not ready else "Close", self.language))
        self.close_button.setEnabled(not self.is_busy or not self.worker.cancelled.is_set())

    def _change_mix(self, *_args):
        if self.result is None:
            return
        mode = self.mode_combo.currentData()
        path = self.result[mode]
        if mode == "mixed_path":
            path = self._balance_paths.get(self.balance_slider.value(), path)
            self._balance_timer.start()
        elif isinstance(self.worker, DisklavierBalanceWorker):
            self.worker.cancel()
        self._update_controls()
        if self.player.source().toLocalFile() == path:
            return
        if not self._source_loading:
            self._pending_position = self.player.position()
            self._pending_play = self.player.playbackState() == QMediaPlayer.PlayingState
            self._pending_paused = self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        self._source_loading = True
        self.player.stop()
        duration = self.result.get("preview_durations", {}).get(mode, self.result["duration_seconds"])
        self.overview.set_notes(self.result["notes"], duration)
        self._duration_changed(round(duration * 1000))
        self._pending_position = min(self._pending_position, self.position_slider.maximum())
        # Only these derived files are ever exposed to an audio device.
        self.player.setSource(QUrl.fromLocalFile(path))
        self._update_controls()

    def _balance_changed(self, value, *, persist=True):
        if persist:
            self.settings.setValue("disklavier_wav_balance", value)
        description = self.text("balance_value", piano=100 - value, accompaniment=value)
        self.balance_slider.setToolTip(self.text("balance_hint") + "\n" + description)
        self.balance_slider.setAccessibleDescription(description)
        self._failed_balance = None
        if isinstance(self.worker, DisklavierBalanceWorker) and self.worker.balance != value:
            self.worker.cancel()
        if self.result is not None:
            self._balance_timer.start()

    def _start_balance_mix(self):
        if self.is_busy or self.result is None or self._close_pending or self.mode_combo.currentData() != "mixed_path":
            return
        balance = self.balance_slider.value()
        if balance in self._balance_paths:
            path = self._balance_paths[balance]
            if self.player.source().toLocalFile() != path:
                self._change_mix()
                self.status.setText(self.text("prepared"))
            return
        if balance == self._failed_balance:
            return
        self._mix_serial += 1
        destination = Path(self._workspace.name) / f"balance-{self._mix_serial}.wav"
        self.worker = DisklavierBalanceWorker(self.result, destination, balance, self)
        self.worker.succeeded.connect(self._balance_prepared)
        self.worker.failed.connect(self._balance_failed)
        self.worker.finished.connect(self._finished)
        self._update_controls()
        self.worker.start()

    def _balance_prepared(self, balance, path):
        if self._close_pending or self.worker.cancelled.is_set() or balance != self.balance_slider.value():
            self._obsolete_mixes.add(path)
            self._cleanup_obsolete_mixes()
            return
        for old_balance in list(self._balance_paths):
            if old_balance != 50:
                self._obsolete_mixes.add(self._balance_paths.pop(old_balance))
        self._balance_paths[balance] = path
        self._change_mix()
        self.status.setText(self.text("prepared"))
        self._cleanup_obsolete_mixes()

    def _balance_failed(self, error, cancelled):
        if not cancelled:
            self._failed_balance = self.worker.balance
            self.status.setText(self.text("balance_failed", error=error))
            self.status.show()

    def _cleanup_obsolete_mixes(self):
        current = self.player.source().toLocalFile()
        for path in list(self._obsolete_mixes):
            if path != current:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    continue  # A backend may release the old file after loading.
                self._obsolete_mixes.discard(path)

    def _media_status_changed(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            if self._source_loading:
                self._source_loading = False
                position, self._pending_position = self._pending_position, 0
                playing, self._pending_play = self._pending_play, False
                paused, self._pending_paused = self._pending_paused, False
                self.player.setPosition(position)
                if playing:
                    self.audio_output_follower.refresh()
                    self.player.play()
                elif paused:
                    self.player.pause()
            self._cleanup_obsolete_mixes()
            self._update_controls()

    def toggle_play(self):
        if not self._playback_ready():
            return
        if self._source_loading:
            self._pending_play = not self._pending_play
            self._pending_paused = not self._pending_play
            self._update_controls()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.audio_output_follower.refresh()
            self.player.play()

    def stop(self):
        self._pending_play = False
        self._pending_paused = False
        self._pending_position = 0
        self.player.stop()
        self.player.setPosition(0)
        self._update_controls()

    def seek(self, position):
        if self._playback_ready():
            position = max(0, min(position, self.position_slider.maximum()))
            if self._source_loading:
                self._pending_position = position
                self._position_changed(position)
            else:
                self.player.setPosition(position)

    def _slider_changed(self, value):
        if not self.position_slider.isSliderDown() and value != self.player.position():
            self.seek(value)

    def _duration_changed(self, duration):
        if self._source_loading and duration <= 0:
            return  # Backends may briefly clear the duration while changing WAVs.
        self.position_slider.blockSignals(True)
        self.position_slider.setRange(0, max(0, duration))
        self.position_slider.blockSignals(False)
        if self.result is not None and duration > 0:
            self.overview.set_duration(duration)
            self.waveform.set_duration(duration)
            self.summary.setText(self.text("summary", notes=len(self.result["notes"]),
                                           duration=_time_label(duration)))
        self._position_changed(self.player.position())

    def _position_changed(self, position):
        if self._source_loading:
            position = self._pending_position
        if not self.position_slider.isSliderDown():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)
        self.overview.set_position(position)
        self.waveform.set_position(position)
        self.time_label.setText(f"{_time_label(position)} / {_time_label(self.position_slider.maximum())}")

    def _audio_error(self, error, message):
        if error != QMediaPlayer.NoError:
            self._source_loading = False
            self._pending_play = False
            self._pending_paused = False
            self._pending_position = 0
            self.status.setText(self.text("audio_error", error=message))
            self.status.show()
            self._update_controls()

    def save_midi(self):
        if not self._admin_mode_enabled or self.result is None or self.is_busy:
            return
        suggestion = self._source_path.with_name(self._source_path.stem + "-decoded.mid")
        path, _ = QFileDialog.getSaveFileName(self, self.text("save_midi"), str(suggestion), "MIDI (*.mid)")
        if path:
            chosen_suffix = bool(Path(path).suffix)
            if not Path(path).suffix:
                path += ".mid"

            def keep_source(destination):
                target = Path(destination)
                if (target.resolve() == self._source_path.resolve()
                        or target.exists() and target.samefile(self._source_path)):
                    raise OSError(self.text("keep_source"))

            try:
                keep_source(path)
                atomic_write_bytes(path, Path(self.result["midi_path"]).read_bytes(),
                                   before_replace=keep_source, replace_existing=chosen_suffix)
            except OSError as exc:
                self.status.setText(self.text("save_failed", error=str(exc)))
            else:
                self.status.setText(self.text("saved", path=path))
            self.status.show()

    def cancel_or_close(self):
        if self.is_busy:
            if isinstance(self.worker, DisklavierBalanceWorker):
                self._close_pending = True
                self._balance_timer.stop()
                self.stop()
            self.worker.cancel()
            self.status.setText(self.text("cancelling"))
            self._update_controls()
        else:
            self.reject()

    def reject(self):
        if self.is_busy:
            self._close_pending = True
            self.cancel_or_close()
            return
        self._clear_result()
        super().reject()

    def closeEvent(self, event):
        if self.is_busy:
            self._close_pending = True
            self.cancel_or_close()
            event.ignore()
        else:
            self._clear_result()
            super().closeEvent(event)
