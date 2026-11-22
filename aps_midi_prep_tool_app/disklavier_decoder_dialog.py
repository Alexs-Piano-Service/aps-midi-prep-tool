"""Listen to encoded Disklavier audio or matching MIDI/audio pairs."""

from pathlib import Path
import math
import tempfile
import threading

from PySide6.QtCore import QRectF, QThread, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QSlider, QStyle, QVBoxLayout, QWidget,
)

from .disklavier_decoder_preview import prepare_preview
from .disklavier_stem_player import DisklavierStemPlayer
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
    """A peak envelope of safe accompaniment on the shared timeline."""

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
        from .disklavier_decoder import DisklavierDecodingCancelled
        from .disklavier_playback_source import prepare_playback_source
        try:
            decoded = prepare_playback_source(
                self.source, self.directory, cancel=self.cancelled,
                progress=self.progressChanged.emit,
            )
            result = prepare_preview(decoded, self.directory, cancel=self.cancelled,
                                     progress=self.progressChanged.emit)
            if self.cancelled.is_set():
                raise DisklavierDecodingCancelled("Disklavier playback preparation canceled.")
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc), self.cancelled.is_set() or isinstance(exc, DisklavierDecodingCancelled))


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
        self.setWindowTitle(self.text("action").rstrip("."))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(5)
        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText(self.text("choose_hint"))
        self.source_edit.setAccessibleName(self.text("source"))
        self.source_edit.setAccessibleDescription(self.text("description"))
        self.source_edit.setToolTip(self.text("description"))
        source_row.addWidget(self.source_edit, 1)
        self.open_button = QPushButton(self.text("open"))
        self.open_button.setToolTip(self.text("description"))
        self.open_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self.choose_file)
        source_row.addWidget(self.open_button)
        outer.addLayout(source_row)

        self.input_summary = QLabel()
        self.input_summary.setWordWrap(True)
        self.input_summary.setTextFormat(Qt.PlainText)
        self.input_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.input_summary.hide()
        outer.addWidget(self.input_summary)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.hide()
        outer.addWidget(self.summary)
        self.overview = DecodedPianoOverview(self)
        self.overview.setAccessibleName(self.text("piano_roll"))
        self.overview.setToolTip(self.text("ready") + "\n" + self.text("seek_hint"))
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
        self.player = DisklavierStemPlayer(self)
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
            self.text("file_filter"),
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
        self.summary.show()
        self.progress.setValue(0)
        self._progress_changed("inspect", 0)
        self._update_controls()
        self.worker.start()

    def _clear_result(self):
        self.player.clear()
        self.result = None
        self.summary.clear()
        self.summary.hide()
        self.input_summary.clear()
        self.input_summary.hide()
        self.overview.set_notes([], 0)
        self.overview.setToolTip(self.text("ready") + "\n" + self.text("seek_hint"))
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
        start, span = {"inspect": (0, 5), "mp3": (5, 15), "check_audio": (20, 25),
                       "decode": (20, 25), "audio": (20, 25),
                       "piano": (45, 45), "mix": (90, 10)}[phase]
        # Format detection may try another receiver or prepare a companion.
        # Keep those steps within one forward-moving preparation phase.
        self.progress.setValue(max(self.progress.value(), round(start + max(0, min(1, fraction)) * span)))

    def _prepared(self, result):
        if self.worker is not None and self.worker.cancelled.is_set():
            self._failed("", True)
            return
        self.result = result
        mp3_source = Path(result.get("source_audio_path", "")).suffix.casefold() == ".mp3"
        if result.get("source_kind") == "pair":
            files = " + ".join(Path(result[key]).name for key in ("source_midi_path", "source_audio_path"))
            self.input_summary.setText(self.text("input_pair_mp3" if mp3_source else "input_pair") + ": " + files)
        else:
            self.input_summary.setText(self.text("input_encoded_mp3" if mp3_source else "input_encoded"))
        self.input_summary.show()
        self.overview.setToolTip(self.text("seek_hint"))
        self.overview.set_notes(result["notes"], result["duration_seconds"])
        self.waveform.set_waveform(result.get("accompaniment_waveform"))
        self.status.setText(self.text("prepared"))
        try:
            self.player.setBalance(self.balance_slider.value() / 100)
            self.player.setPianoOnly(self.mode_combo.currentData() == "piano_path")
            # Only prepared piano and validated accompaniment reach the device.
            # Both stay on one timeline for the lifetime of this loaded song.
            self.player.load(result["piano_path"], result["accompaniment_path"],
                             allow_stereo=result.get("source_kind") == "pair")
        except (OSError, ValueError) as exc:
            self.player.clear()
            self.result = None
            self.input_summary.hide()
            self._failed(str(exc), False)

    def _failed(self, error, cancelled):
        self.summary.setText(self.text("cancelled" if cancelled else "failed"))
        self.summary.show()
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

    def _playback_ready(self):
        return self.result is not None and not self.is_busy

    def _update_controls(self, *_args):
        ready = self._playback_ready()
        self.open_button.setEnabled(not self.is_busy)
        for widget in (self.play_button, self.stop_button,
                       self.mode_combo, self.position_slider, self.overview, self.waveform):
            widget.setEnabled(ready)
        self.balance_slider.setEnabled(ready and self.mode_combo.currentData() == "mixed_path")
        can_export = self._admin_mode_enabled and (self.result is None or self.result.get("source_kind") != "pair")
        self.save_button.setVisible(can_export)
        self.save_button.setEnabled(ready and not self.is_busy and can_export)
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_button.setText(translate_text("Pause" if playing else "Play", self.language))
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay))
        self.close_button.setText(translate_text("Cancel" if self.is_busy and not ready else "Close", self.language))
        self.close_button.setEnabled(not self.is_busy or not self.worker.cancelled.is_set())

    def _change_mix(self, *_args):
        if self.result is None:
            return
        self.player.setPianoOnly(self.mode_combo.currentData() == "piano_path")
        self._update_controls()

    def _balance_changed(self, value, *, persist=True):
        if persist:
            self.settings.setValue("disklavier_wav_balance", value)
        description = self.text("balance_value", piano=100 - value, accompaniment=value)
        self.balance_slider.setToolTip(self.text("balance_hint") + "\n" + description)
        self.balance_slider.setAccessibleDescription(description)
        if self.result is not None:
            self.player.setBalance(value / 100)

    def _media_status_changed(self, status):
        self._update_controls()

    def toggle_play(self):
        if not self._playback_ready():
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.audio_output_follower.refresh()
            self.player.play()

    def stop(self):
        self.player.stop()
        self._update_controls()

    def seek(self, position):
        if self._playback_ready():
            position = max(0, min(position, self.position_slider.maximum()))
            self.player.setPosition(position)

    def _slider_changed(self, value):
        if not self.position_slider.isSliderDown() and value != self.player.position():
            self.seek(value)

    def _duration_changed(self, duration):
        self.position_slider.blockSignals(True)
        self.position_slider.setRange(0, max(0, duration))
        self.position_slider.blockSignals(False)
        if self.result is not None and duration > 0:
            self.overview.set_duration(duration)
            self.waveform.set_duration(duration)
            self.summary.setText(self.text("summary", notes=len(self.result["notes"]),
                                           duration=_time_label(duration)))
            self.summary.show()
        self._position_changed(self.player.position())

    def _position_changed(self, position):
        if not self.position_slider.isSliderDown():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)
        self.overview.set_position(position)
        self.waveform.set_position(position)
        self.time_label.setText(f"{_time_label(position)} / {_time_label(self.position_slider.maximum())}")

    def _audio_error(self, error, message):
        if error != QMediaPlayer.NoError:
            self.status.setText(self.text("audio_error", error=message))
            self.status.show()
            self._update_controls()

    def save_midi(self):
        if (not self._admin_mode_enabled or self.result is None or self.is_busy
                or self.result.get("source_kind") == "pair"):
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
