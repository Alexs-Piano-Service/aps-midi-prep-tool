import datetime
import codecs
import os
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .message_catalog import DEFAULT_LANGUAGE, normalize_language_code, translate_text


class ConsoleLogBus(QObject):
    chunk_written = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self._chunks = []
        self._lock = threading.RLock()
        self._total_text_chars = 0

    def append(self, stream_name, text):
        if not text:
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        text = str(text)
        with self._lock:
            self._chunks.append((timestamp, stream_name, text))
            self._total_text_chars += len(text)
        try:
            self.chunk_written.emit(timestamp, stream_name, text)
        except RuntimeError:
            pass

    def chunks(self):
        with self._lock:
            return list(self._chunks)

    def plain_text(self):
        with self._lock:
            return "".join(text for _timestamp, _stream_name, text in self._chunks)

    def total_text_chars(self):
        with self._lock:
            return self._total_text_chars

    def tail_text(self, max_chars):
        try:
            remaining = max(0, int(max_chars or 0))
        except (TypeError, ValueError):
            remaining = 0
        if remaining <= 0:
            return ""
        pieces = []
        with self._lock:
            for _timestamp, _stream_name, text in reversed(self._chunks):
                if remaining <= 0:
                    break
                if len(text) <= remaining:
                    pieces.append(text)
                    remaining -= len(text)
                else:
                    pieces.append(text[-remaining:])
                    remaining = 0
        return "".join(reversed(pieces))


class ConsoleCaptureStream:
    def __init__(self, original, stream_name, bus):
        self._original = original
        self._stream_name = stream_name
        self._bus = bus

    def write(self, text):
        if text is None:
            return 0
        text = str(text)
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                pass
        try:
            self._bus.append(self._stream_name, text)
        except Exception:
            pass
        return len(text)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(getattr(self._original, "isatty", lambda: False)())

    def fileno(self):
        if self._original is None:
            raise OSError("No console stream is available.")
        return self._original.fileno()

    @property
    def encoding(self):
        return getattr(self._original, "encoding", None) or "utf-8"

    @property
    def errors(self):
        return getattr(self._original, "errors", None) or "replace"

    def __getattr__(self, name):
        if self._original is None:
            raise AttributeError(name)
        return getattr(self._original, name)


_console_log_bus = None
_original_stdout = None
_original_stderr = None
_capture_installed = False
_fd_capture_threads = []
_original_fds = {}


def get_console_log_bus():
    global _console_log_bus
    if _console_log_bus is None:
        _console_log_bus = ConsoleLogBus()
    return _console_log_bus


def _stream_encoding(stream):
    return getattr(stream, "encoding", None) or "utf-8"


def _flush_stream(stream):
    if stream is None:
        return
    try:
        stream.flush()
    except Exception:
        pass


def _make_stream_realtime(stream):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(line_buffering=True, write_through=True)
    except Exception:
        pass


def _read_fd_to_log(read_fd, original_fd, stream_name, bus, encoding):
    try:
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    except LookupError:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            try:
                data = os.read(read_fd, 4096)
            except OSError:
                break
            if not data:
                break
            try:
                os.write(original_fd, data)
            except OSError:
                pass
            text = decoder.decode(data)
            if text:
                try:
                    bus.append(stream_name, text)
                except Exception:
                    pass
        remainder = decoder.decode(b"", final=True)
        if remainder:
            try:
                bus.append(stream_name, remainder)
            except Exception:
                pass
    finally:
        for fd in (read_fd, original_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def _install_fd_capture(fd, stream_name, bus, encoding):
    try:
        original_fd = os.dup(fd)
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, fd)
        os.close(write_fd)
    except OSError:
        return False
    _original_fds[stream_name] = original_fd
    thread = threading.Thread(
        target=_read_fd_to_log,
        args=(read_fd, original_fd, stream_name, bus, encoding),
        name=f"ConsoleLogCapture-{stream_name}",
        daemon=True,
    )
    thread.start()
    _fd_capture_threads.append(thread)
    return True


def install_console_capture():
    global _capture_installed, _original_stdout, _original_stderr
    bus = get_console_log_bus()
    if _capture_installed:
        return bus
    _original_stdout = sys.stdout
    _original_stderr = sys.stderr
    _flush_stream(_original_stdout)
    _flush_stream(_original_stderr)
    stdout_captured = _install_fd_capture(1, "stdout", bus, _stream_encoding(_original_stdout))
    stderr_captured = _install_fd_capture(2, "stderr", bus, _stream_encoding(_original_stderr))
    _make_stream_realtime(_original_stdout)
    _make_stream_realtime(_original_stderr)
    if not stdout_captured:
        sys.stdout = ConsoleCaptureStream(_original_stdout, "stdout", bus)
    if not stderr_captured:
        sys.stderr = ConsoleCaptureStream(_original_stderr, "stderr", bus)
    _capture_installed = True
    return bus


class ConsoleLogDialog(QDialog):
    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._paused = False
        self._pending_chunks = []
        self._line_count = 0
        self._char_count = 0

        self.setWindowTitle(self._lt("View Logs"))
        self.resize(980, 620)
        self._build_ui()
        self._replay_existing_output()
        self.bus.chunk_written.connect(self._append_chunk, Qt.QueuedConnection)

    def _language_code(self):
        parent = self.parent()
        language_method = getattr(parent, "_language_code", None)
        if callable(language_method):
            return normalize_language_code(language_method())
        return DEFAULT_LANGUAGE

    def _lt(self, text):
        return translate_text(text, self._language_code())

    def closeEvent(self, event):
        try:
            self.bus.chunk_written.disconnect(self._append_chunk)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        title = QLabel(self._lt("Console output"))
        title_font = QFont(title.font())
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        header_layout.addWidget(title)
        self.live_label = QLabel(self._lt("Live"))
        self.live_label.setAlignment(Qt.AlignCenter)
        self.live_label.setStyleSheet(
            "QLabel { padding: 3px 8px; border-radius: 8px; "
            "background: #DCFCE7; color: #166534; font-weight: 700; }"
        )
        header_layout.addWidget(self.live_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        controls_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self._lt("Find in logs"))
        self.search_edit.returnPressed.connect(self.find_next)
        controls_layout.addWidget(self.search_edit, stretch=1)

        self.find_button = QPushButton(self._lt("Find Next"))
        self.find_button.clicked.connect(self.find_next)
        controls_layout.addWidget(self.find_button)

        self.wrap_checkbox = QCheckBox(self._lt("Wrap"))
        self.wrap_checkbox.toggled.connect(self._set_line_wrap)
        controls_layout.addWidget(self.wrap_checkbox)

        self.follow_checkbox = QCheckBox(self._lt("Follow Output"))
        self.follow_checkbox.setChecked(True)
        controls_layout.addWidget(self.follow_checkbox)

        self.pause_checkbox = QCheckBox(self._lt("Pause"))
        self.pause_checkbox.toggled.connect(self._set_paused)
        controls_layout.addWidget(self.pause_checkbox)
        layout.addLayout(controls_layout)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAcceptRichText(False)
        self.log_view.setUndoRedoEnabled(False)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        self.log_view.setFont(QFont("Courier New", 10))
        self.log_view.setStyleSheet(
            """
            QTextEdit {
                background: #0F1419;
                color: #E5E7EB;
                border: 1px solid #2F3A45;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #2563EB;
                selection-color: #FFFFFF;
            }
            """
        )
        layout.addWidget(self.log_view, stretch=1)

        footer_layout = QHBoxLayout()
        self.summary_label = QLabel("")
        footer_layout.addWidget(self.summary_label)
        footer_layout.addStretch()

        self.copy_button = QPushButton(self._lt("Copy All"))
        self.copy_button.clicked.connect(self.copy_all)
        footer_layout.addWidget(self.copy_button)

        self.save_button = QPushButton(self._lt("Save Log..."))
        self.save_button.clicked.connect(self.save_log)
        footer_layout.addWidget(self.save_button)

        self.clear_button = QPushButton(self._lt("Clear View"))
        self.clear_button.clicked.connect(self.clear_view)
        footer_layout.addWidget(self.clear_button)

        self.close_button = QPushButton(self._lt("Close"))
        self.close_button.clicked.connect(self.close)
        footer_layout.addWidget(self.close_button)
        layout.addLayout(footer_layout)

    def _replay_existing_output(self):
        for timestamp, stream_name, text in self.bus.chunks():
            self._write_chunk(timestamp, stream_name, text)
        self._scroll_to_end()
        self._update_summary()

    def _stream_format(self, stream_name):
        fmt = QTextCharFormat()
        if stream_name == "stderr":
            fmt.setForeground(QColor("#FCA5A5"))
        else:
            fmt.setForeground(QColor("#D1D5DB"))
        return fmt

    def _append_chunk(self, timestamp, stream_name, text):
        if self._paused:
            self._pending_chunks.append((timestamp, stream_name, text))
            self._update_live_label()
            return
        self._write_chunk(timestamp, stream_name, text)
        if self.follow_checkbox.isChecked():
            self._scroll_to_end()
        self._update_summary()

    def _write_chunk(self, timestamp, stream_name, text):
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.setCharFormat(self._stream_format(stream_name))
        cursor.insertText(text)
        self.log_view.setTextCursor(cursor)
        self._char_count += len(text)
        self._line_count += text.count("\n")

    def _set_paused(self, paused):
        self._paused = bool(paused)
        if not self._paused and self._pending_chunks:
            pending = self._pending_chunks
            self._pending_chunks = []
            for timestamp, stream_name, text in pending:
                self._write_chunk(timestamp, stream_name, text)
            if self.follow_checkbox.isChecked():
                self._scroll_to_end()
        self._update_live_label()
        self._update_summary()

    def _set_line_wrap(self, enabled):
        mode = QTextEdit.WidgetWidth if enabled else QTextEdit.NoWrap
        self.log_view.setLineWrapMode(mode)

    def _scroll_to_end(self):
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_live_label(self):
        if self._paused:
            count = len(self._pending_chunks)
            paused_text = self._lt("Paused")
            self.live_label.setText(f"{paused_text} ({count})" if count else paused_text)
            self.live_label.setStyleSheet(
                "QLabel { padding: 3px 8px; border-radius: 8px; "
                "background: #FEF3C7; color: #92400E; font-weight: 700; }"
            )
        else:
            self.live_label.setText(self._lt("Live"))
            self.live_label.setStyleSheet(
                "QLabel { padding: 3px 8px; border-radius: 8px; "
                "background: #DCFCE7; color: #166534; font-weight: 700; }"
            )

    def _update_summary(self):
        pending = len(self._pending_chunks)
        summary = f"{self._line_count:,} {self._lt('lines')}  |  {self._char_count:,} {self._lt('characters')}"
        if pending:
            summary += f"  |  {pending:,} {self._lt('buffered chunks')}"
        self.summary_label.setText(summary)

    def find_next(self):
        needle = self.search_edit.text()
        if not needle:
            return
        if self.log_view.find(needle):
            return
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.log_view.setTextCursor(cursor)
        self.log_view.find(needle)

    def copy_all(self):
        self.log_view.selectAll()
        self.log_view.copy()
        cursor = self.log_view.textCursor()
        cursor.clearSelection()
        self.log_view.setTextCursor(cursor)

    def save_log(self):
        default_name = f"aps-midi-prep-tool-log-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self._lt("Save Log"),
            os.path.join(os.path.expanduser("~"), default_name),
            f"{self._lt('Text Files')} (*.txt);;{self._lt('All Files')} (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", errors="replace") as handle:
                handle.write(self.bus.plain_text())
        except OSError as exc:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Critical)
            dialog.setWindowTitle(self._lt("Save Log Failed"))
            dialog.setText(str(exc))
            dialog.setStandardButtons(QMessageBox.Ok)
            ok_button = dialog.button(QMessageBox.Ok)
            if ok_button is not None:
                ok_button.setText(self._lt("OK"))
            dialog.exec()

    def clear_view(self):
        self.log_view.clear()
        self._line_count = 0
        self._char_count = 0
        self._pending_chunks = []
        self._update_live_label()
        self._update_summary()
