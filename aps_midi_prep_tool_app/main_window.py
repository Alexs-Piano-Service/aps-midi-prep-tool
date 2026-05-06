import datetime
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import wave
import glob
from array import array
from math import exp, pi, sin
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QEvent, QSettings, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPalette, QPen, QPolygon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QHeaderView,
    QSizePolicy,
    QProgressDialog,
    QProgressBar,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QGroupBox,
    QToolButton,
    QToolTip,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QComboBox,
    QSlider,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QLayout,
    QPlainTextEdit,
)

from .midi_metadata import (
    extract_eseq_title_from_file,
    update_eseq_title_to_path,
    update_midi_title,
    update_midi_title_to_path,
    update_midi_title_to_destination,
    validate_legacy_title_input,
    extract_first_title_from_midi,
    extract_midi_type_label_from_midi,
    has_eseq_title_metadata,
    is_midi_file,
)
from .eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    ESEQ_CONTAINER_DISKLAVIER,
    EseqConversionError,
    convert_eseq_bytes_to_midi_bytes,
    convert_eseq_file_to_midi_path,
    convert_midi_file_to_eseq_path,
    is_eseq_file,
)
from .dos83_renamer import apply_midi_dos83_plan, build_midi_dos83_plan, validate_midi_dos83_plan
from .midi_type0_converter import _encode_vlq, _parse_midi_chunks, _parse_track_events, convert_midi_file_to_type0_path
from .ui_utils import (
    center_dialog_on_parent,
    embedded_logo_dt,
    embedded_logo_lt,
    is_dark_theme,
    pixmap_from_base64,
)
from .drop_table_widget import DropTableWidget
from .disk_session_worker import (
    DiskSessionCommitWorker,
    DiskSessionFormatWorker,
    DiskSessionLoadWorker,
    DiskSessionRecoveryWorker,
    DiskSessionWriteTargetWorker,
)
from .icon_utils import apply_window_icon
from .onboarding_dialog import show_first_time_dialog
from .additional_formats import mpc_seq_to_midi, v50_nseq_to_midi
from .floppy_image import (
    DISK_FORMATS,
    PREFERRED_OUTPUT_EXTENSIONS,
    FloppyImageError,
    FloppyDriveInfo,
    FloppyImageSession,
    FloppyRecoverySource,
    GreaseweazleFloppySource,
    ImageLoadSource,
    ImageRecoverySource,
    allocated_size,
    create_floppy_images_from_files,
    display_bytes,
    image_extension,
    list_greaseweazle_devices,
    list_floppy_drives,
    output_filters,
)
from .eseq_pianodir import (
    ESEQ_VARIANT_CLAVINOVA,
    ESEQ_VARIANT_DISKLAVIER,
    CLAVINOVA_MUSICDIR_MAX_TRACKS,
    CLAVINOVA_MUSICDIR_HEADER_SIZE,
    CLAVINOVA_MUSICDIR_RECORD_SIZE,
    MUSICDIR_FILENAME,
    PIANODIR_FILENAME,
    PIANODIR_DISK_METADATA_SIZE,
    PIANODIR_MAX_TRACKS,
    PIANODIR_ROW_PATH,
    PIANODIR_TARGET_FILE_SIZE,
    PianodirMetadata,
    PianodirTrackEntry,
    build_eseq_order_key_from_path,
    build_music_dir_bytes,
    build_pianodir_bytes,
    clavinova_music_order_key,
    eseq_type_display_label,
    is_clavinova_mda_file,
    is_eseq_directory_path,
    normalize_pianodir_catalog_number,
    normalize_eseq_order_key,
    read_music_dir_order_keys_from_file,
    read_eseq_order_key_from_file,
    read_eseq_arrangement_type_label_from_file,
    read_eseq_write_protect_from_file,
    is_eseq_filename,
    is_musicdir_path,
    is_pianodir_path,
    musicdir_is_populated,
    pianodir_is_populated,
    read_pianodir_metadata_from_file,
    update_eseq_order_key,
    update_eseq_order_key_to_path,
)
from .app_info import (
    APP_AUTHOR,
    APP_COMPANY,
    APP_COMPANY_ADDRESS,
    APP_COPYRIGHT_NOTICE,
    APP_LICENSE,
    APP_NAME,
    APP_TITLE_WITH_VERSION,
    APP_VERSION,
    APP_WEBSITE,
    SETTINGS_APP as APP_SETTINGS_APP,
    SETTINGS_ORG as APP_SETTINGS_ORG,
    UPDATE_CHECK_URL,
)
from .subprocess_utils import windows_subprocess_kwargs


class TitleOverflowDelegate(QStyledItemDelegate):
    RAW_TITLE_ROLE = Qt.UserRole + 1

    def __init__(self, limit, parent=None):
        super().__init__(parent)
        self.limit = limit
        self.warning_color = QColor("#F5B041")
        self.highlight_enabled = True

    def set_highlight_enabled(self, enabled):
        self.highlight_enabled = bool(enabled)

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole) or ""
        raw_text = index.data(self.RAW_TITLE_ROLE)
        measured_text = str(raw_text) if raw_text is not None else text
        if (
            not self.highlight_enabled
            or index.column() != 4
            or len(measured_text) <= self.limit
            or len(text) <= self.limit
            or option.state & QStyle.State_Selected
        ):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        full_text = opt.text
        normal_text = full_text[:self.limit]
        overflow_text = full_text[self.limit:]

        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget).adjusted(4, 0, -2, 0)
        if text_rect.width() <= 0:
            return

        painter.save()
        painter.setClipRect(text_rect)
        fm = opt.fontMetrics
        baseline = text_rect.top() + (text_rect.height() + fm.ascent() - fm.descent()) // 2
        x = text_rect.left()

        painter.setPen(opt.palette.color(QPalette.Text))
        painter.drawText(x, baseline, normal_text)
        x += fm.horizontalAdvance(normal_text)

        painter.setPen(self.warning_color)
        painter.drawText(x, baseline, overflow_text)
        painter.restore()


class GreaseweazleSectorGrid(QWidget):
    def __init__(self, sector_map, parent=None):
        super().__init__(parent)
        self.rows = list((sector_map or {}).get("rows") or [])
        self.column_count = max((len(row.get("statuses", "")) for row in self.rows), default=0)
        self.cell_size = 6
        self.dot_size = 4
        self.padding = 8
        self.success_color = QColor("#2FA866")
        self.failure_color = QColor("#D14D4D")
        self.empty_color = QColor("#69737C")
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumSize(self.sizeHint())
        self.setMaximumSize(self.sizeHint())

    def sizeHint(self):
        if not self.rows or self.column_count <= 0:
            return QSize(420, 42)
        return QSize(
            self.padding * 2 + self.column_count * self.cell_size,
            self.padding * 2 + len(self.rows) * self.cell_size,
        )

    def _status_at_position(self, pos):
        if not self.rows or self.column_count <= 0:
            return None
        col = int((pos.x() - self.padding) // self.cell_size)
        row_index = int((pos.y() - self.padding) // self.cell_size)
        if row_index < 0 or row_index >= len(self.rows) or col < 0 or col >= self.column_count:
            return None
        row = self.rows[row_index]
        statuses = row.get("statuses", "")
        status = statuses[col] if col < len(statuses) else " "
        return row, col, status

    def mouseMoveEvent(self, event):
        hit = self._status_at_position(event.pos())
        if hit is None:
            QToolTip.hideText()
            return super().mouseMoveEvent(event)
        row, col, status = hit
        sector = int(row.get("sector", 0)) + 1
        head = row.get("head", 0)
        if status == ".":
            message = f"Cylinder {col}, head {head}, sector {sector}: read successfully."
        else:
            label = status if str(status).strip() else "missing"
            message = f"Cylinder {col}, head {head}, sector {sector}: {label}."
        global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
        QToolTip.showText(global_pos, message, self)
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        return super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().color(QPalette.Base))
        painter.setPen(QPen(self.palette().color(QPalette.Mid), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if not self.rows or self.column_count <= 0:
            painter.setPen(self.palette().color(QPalette.Text))
            painter.drawText(self.rect(), Qt.AlignCenter, "No sector map reported.")
            return

        offset = (self.cell_size - self.dot_size) // 2
        painter.setPen(Qt.NoPen)
        for row_index, row in enumerate(self.rows):
            statuses = row.get("statuses", "")
            y = self.padding + row_index * self.cell_size + offset
            for col in range(self.column_count):
                status = statuses[col] if col < len(statuses) else " "
                if status == ".":
                    color = self.success_color
                elif str(status).strip():
                    color = self.failure_color
                else:
                    color = self.empty_color
                x = self.padding + col * self.cell_size + offset
                painter.setBrush(color)
                painter.drawEllipse(x, y, self.dot_size, self.dot_size)


class VerticalUsageBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction = 0.0
        self.setFixedWidth(14)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_fraction(self, fraction):
        fraction = max(0.0, min(float(fraction or 0.0), 1.0))
        if abs(self._fraction - fraction) < 0.001:
            return
        self._fraction = fraction
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(2, 2, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        if is_dark_theme():
            border = QColor("#64707A")
            background = QColor("#12171B")
            fill = QColor("#3E8CC7")
        else:
            border = QColor("#7E8992")
            background = QColor("#F4F6F8")
            fill = QColor("#2E7DB2")

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRect(rect)

        inner = rect.adjusted(2, 2, -2, -2)
        fill_height = int(round(inner.height() * self._fraction))
        if fill_height > 0:
            fill_rect = inner.adjusted(0, inner.height() - fill_height, 0, 0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRect(fill_rect)


class SegmentedEseqCountBar(QWidget):
    def __init__(self, segment_limit, parent=None):
        super().__init__(parent)
        self.segment_limit = int(segment_limit)
        self._count = 0
        self.setFixedWidth(14)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_count(self, count):
        count = max(0, min(int(count or 0), self.segment_limit))
        if self._count == count:
            return
        self._count = count
        self.update()

    def set_segment_limit(self, segment_limit):
        segment_limit = max(1, int(segment_limit or 1))
        if self.segment_limit == segment_limit:
            return
        self.segment_limit = segment_limit
        self._count = max(0, min(self._count, self.segment_limit))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(2, 2, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0 or self.segment_limit <= 0:
            return

        if is_dark_theme():
            border = QColor("#64707A")
            empty = QColor("#151A1E")
            filled = QColor("#3B8B5A")
        else:
            border = QColor("#7E8992")
            empty = QColor("#F4F6F8")
            filled = QColor("#3E9A62")

        painter.setPen(QPen(border, 1))
        painter.setBrush(empty)
        painter.drawRect(rect)

        inner = rect.adjusted(2, 2, -2, -2)
        gap = 1
        total_gap = gap * (self.segment_limit - 1)
        raw_segment_height = (inner.height() - total_gap) / self.segment_limit
        if raw_segment_height < 1:
            gap = 0
            raw_segment_height = inner.height() / self.segment_limit

        painter.setPen(Qt.NoPen)
        for index in range(self.segment_limit):
            segment_from_bottom = index
            y_bottom = inner.bottom() - int(round(segment_from_bottom * (raw_segment_height + gap)))
            y_top = y_bottom - max(1, int(round(raw_segment_height))) + 1
            color = filled if index < self._count else empty
            painter.setBrush(color)
            painter.drawRect(inner.left(), y_top, inner.width(), max(1, y_bottom - y_top + 1))


def _read_vlq_from_event(data, offset):
    value = 0
    pos = int(offset)
    for _ in range(4):
        if pos >= len(data):
            return value, pos
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if (byte & 0x80) == 0:
            return value, pos
    return value, pos


def _midi_meta_payload(raw):
    if len(raw) < 3 or raw[0] != 0xFF:
        return 0, b""
    meta_type = raw[1]
    meta_len, payload_pos = _read_vlq_from_event(raw, 2)
    return meta_type, raw[payload_pos:payload_pos + meta_len]


def _decode_midi_text(payload):
    for encoding in ("latin1", "utf-8"):
        try:
            return payload.decode(encoding, errors="replace").replace("\x00", "").strip()
        except Exception:
            continue
    return ""


def _format_duration(seconds):
    seconds = max(0.0, float(seconds or 0.0))
    minutes = int(seconds // 60)
    rem = int(round(seconds - minutes * 60))
    if rem >= 60:
        minutes += 1
        rem -= 60
    return f"{minutes}:{rem:02d}"


def _tick_seconds_converter(tempo_events, division):
    division = max(1, int(division or 1))
    tempos = sorted((max(0, int(tick)), max(1, int(mpqn))) for tick, mpqn in tempo_events)
    if not tempos or tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))

    def tick_to_seconds(target_tick):
        target_tick = max(0, int(target_tick or 0))
        elapsed = 0.0
        last_tick = tempos[0][0]
        current_mpqn = tempos[0][1]
        for tempo_tick, mpqn in tempos[1:]:
            if tempo_tick >= target_tick:
                break
            elapsed += ((tempo_tick - last_tick) * current_mpqn) / (division * 1000000.0)
            last_tick = tempo_tick
            current_mpqn = mpqn
        elapsed += ((target_tick - last_tick) * current_mpqn) / (division * 1000000.0)
        return elapsed

    return tick_to_seconds


GM_PROGRAM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]


def _program_name(program):
    program = int(program)
    if 0 <= program < len(GM_PROGRAM_NAMES):
        return f"{program + 1}: {GM_PROGRAM_NAMES[program]}"
    return f"Program {program + 1}"


def _channel_event_channel(raw):
    if raw and 0x80 <= raw[0] <= 0xEF:
        return (raw[0] & 0x0F) + 1
    return 0


def _filter_midi_bytes_to_channels(midi_bytes, channels):
    allowed = {int(channel) for channel in (channels or []) if 1 <= int(channel) <= 16}
    if not allowed:
        raise ValueError("No MIDI channels are selected for preview.")
    if len(allowed) == 16:
        return midi_bytes

    header_end, _format_type, _declared_tracks, chunks = _parse_midi_chunks(midi_bytes)
    rebuilt = bytearray(midi_bytes[:header_end])
    for chunk in chunks:
        if chunk["id"] != b"MTrk":
            rebuilt.extend(midi_bytes[chunk["start"]:chunk["data_end"]])
            continue

        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, end_tick = _parse_track_events(track_data)
        filtered_track = bytearray()
        prev_tick = 0
        for tick, _order, raw in events:
            channel = _channel_event_channel(raw)
            if channel and channel not in allowed:
                continue
            filtered_track.extend(_encode_vlq(tick - prev_tick))
            filtered_track.extend(raw)
            prev_tick = tick
        filtered_track.extend(_encode_vlq(max(0, end_tick - prev_tick)))
        filtered_track.extend(b"\xFF\x2F\x00")
        rebuilt.extend(b"MTrk")
        rebuilt.extend(len(filtered_track).to_bytes(4, "big"))
        rebuilt.extend(filtered_track)
    return bytes(rebuilt)


def _inspect_midi_bytes(midi_bytes, *, source_label=""):
    header_end, format_type, declared_tracks, chunks = _parse_midi_chunks(midi_bytes)
    del header_end
    division = int.from_bytes(midi_bytes[12:14], "big")
    track_chunks = [chunk for chunk in chunks if chunk["id"] == b"MTrk"]
    tempo_events = [(0, 500000)]
    metadata = []
    control_changes = []
    program_changes = {}
    notes_pending = {}
    notes = []
    channels = set()
    max_tick = 0

    meta_names = {
        0x01: "Text",
        0x02: "Copyright",
        0x03: "Track Name",
        0x04: "Instrument Name",
        0x05: "Lyric",
        0x06: "Marker",
        0x07: "Cue",
        0x51: "Tempo",
        0x58: "Time Signature",
        0x59: "Key Signature",
    }

    for track_index, chunk in enumerate(track_chunks, start=1):
        track_data = midi_bytes[chunk["data_start"]:chunk["data_end"]]
        events, end_tick = _parse_track_events(track_data)
        max_tick = max(max_tick, end_tick)
        for tick, _order, raw in events:
            max_tick = max(max_tick, tick)
            if not raw:
                continue
            if raw[0] == 0xFF:
                meta_type, payload = _midi_meta_payload(raw)
                name = meta_names.get(meta_type, f"Meta 0x{meta_type:02X}")
                if meta_type == 0x51 and len(payload) == 3:
                    mpqn = int.from_bytes(payload, "big")
                    tempo_events.append((tick, mpqn))
                    bpm = 60000000.0 / mpqn if mpqn else 0.0
                    metadata.append((track_index, tick, name, f"{bpm:.2f} BPM"))
                elif meta_type == 0x58 and len(payload) >= 2:
                    denominator = 2 ** payload[1]
                    metadata.append((track_index, tick, name, f"{payload[0]}/{denominator}"))
                elif meta_type in meta_names:
                    text = _decode_midi_text(payload) if meta_type not in {0x59} else payload.hex(" ").upper()
                    if text:
                        metadata.append((track_index, tick, name, text))
                continue

            status = raw[0]
            if 0x80 <= status <= 0xEF:
                channel = (status & 0x0F) + 1
                channels.add(channel)
                message = status & 0xF0
                if message == 0x90 and len(raw) >= 3 and raw[2] > 0:
                    notes_pending.setdefault((channel, raw[1]), []).append((tick, raw[2], track_index))
                elif message in {0x80, 0x90} and len(raw) >= 3:
                    stack = notes_pending.get((channel, raw[1])) or []
                    if stack:
                        start_tick, velocity, start_track = stack.pop(0)
                        if tick > start_tick:
                            notes.append(
                                {
                                    "start_tick": start_tick,
                                    "end_tick": tick,
                                    "pitch": raw[1],
                                    "velocity": velocity,
                                    "channel": channel,
                                    "track": start_track,
                                }
                            )
                elif message == 0xB0 and len(raw) >= 3:
                    control_changes.append(
                        {
                            "track": track_index,
                            "tick": tick,
                            "channel": channel,
                            "controller": raw[1],
                            "value": raw[2],
                        }
                    )
                elif message == 0xC0 and len(raw) >= 2:
                    program_changes.setdefault(channel, []).append(raw[1])

    for (channel, pitch), stack in notes_pending.items():
        for start_tick, velocity, track_index in stack:
            end_tick = max(max_tick, start_tick + max(1, division))
            notes.append(
                {
                    "start_tick": start_tick,
                    "end_tick": end_tick,
                    "pitch": pitch,
                    "velocity": velocity,
                    "channel": channel,
                    "track": track_index,
                }
            )

    tick_to_seconds = _tick_seconds_converter(tempo_events, division)
    for note in notes:
        note["start_sec"] = tick_to_seconds(note["start_tick"])
        note["end_sec"] = max(note["start_sec"] + 0.05, tick_to_seconds(note["end_tick"]))

    duration = max([tick_to_seconds(max_tick)] + [note["end_sec"] for note in notes] + [0.0])
    pitches = [note["pitch"] for note in notes]
    note_counts_by_channel = {}
    for note in notes:
        channel = int(note.get("channel", 0))
        if channel:
            note_counts_by_channel[channel] = note_counts_by_channel.get(channel, 0) + 1
    control_counts_by_channel = {}
    controller_values_by_channel = {}
    for event in control_changes:
        channel = int(event["channel"])
        control_counts_by_channel[channel] = control_counts_by_channel.get(channel, 0) + 1
        controller_values_by_channel.setdefault((channel, event["controller"]), []).append(event["value"])

    piano_channels = set()
    for channel, note_count in note_counts_by_channel.items():
        if note_count <= 0 or channel == 10:
            continue
        programs = program_changes.get(channel, [])
        if not programs or any(0 <= program <= 7 for program in programs):
            piano_channels.add(channel)

    mute_notes = []
    for channel in sorted(note_counts_by_channel):
        for controller, label in ((7, "volume"), (11, "expression")):
            values = controller_values_by_channel.get((channel, controller), [])
            if values and min(values) == 0:
                restored = any(value > 0 for value in values[values.index(0) + 1:])
                if restored:
                    mute_notes.append(
                        f"Channel {channel}: CC{controller} {label} reaches 0 and later returns above 0."
                    )
                else:
                    mute_notes.append(
                        f"Channel {channel}: CC{controller} {label} reaches 0; generic MIDI playback may mute that channel."
                    )

    lines = [
        f"File: {source_label or 'Selected file'}",
        f"MIDI type: Type {format_type}",
        f"Tracks: {len(track_chunks)} (declared {declared_tracks})",
        f"Channels: {', '.join(str(ch) for ch in sorted(channels)) if channels else 'None detected'}",
        f"Notes: {len(notes)}",
        f"Duration: {_format_duration(duration)}",
    ]
    if pitches:
        lines.append(f"Pitch range: {min(pitches)}-{max(pitches)}")
    lines.append("")
    lines.append("Channel Summary:")
    if note_counts_by_channel or control_counts_by_channel:
        for channel in sorted(set(note_counts_by_channel) | set(control_counts_by_channel) | set(program_changes)):
            programs = program_changes.get(channel, [])
            if programs:
                program_text = ", ".join(
                    _program_name(program)
                    for program in sorted(set(programs))
                )
            elif note_counts_by_channel.get(channel, 0):
                program_text = "No program change; GM default is Acoustic Grand Piano"
            else:
                program_text = "No program change"
            piano_marker = " piano candidate" if channel in piano_channels else ""
            lines.append(
                f"Channel {channel}:{piano_marker} "
                f"{note_counts_by_channel.get(channel, 0)} note(s), "
                f"{control_counts_by_channel.get(channel, 0)} control change(s), {program_text}"
            )
    else:
        lines.append("No MIDI channel events found.")
    if mute_notes:
        lines.append("Mute / Volume Notes:")
        lines.extend(mute_notes[:12])
        if len(mute_notes) > 12:
            lines.append(f"...and {len(mute_notes) - 12} more mute/volume note(s).")
    lines.append("Channel toggles affect this inspection preview only; they do not edit the file.")
    lines.append("")
    lines.append("Pedals / Controllers:")
    pedal_controller_names = {
        64: "Damper/Sustain Pedal",
        66: "Sostenuto Pedal",
        67: "Soft Pedal",
    }
    pedal_events = [
        event
        for event in control_changes
        if event["controller"] in pedal_controller_names
    ]
    if pedal_events:
        summary = {}
        for event in pedal_events:
            key = (event["controller"], event["channel"])
            bucket = summary.setdefault(
                key,
                {"count": 0, "on": 0, "off": 0, "values": set()},
            )
            bucket["count"] += 1
            bucket["values"].add(event["value"])
            if event["value"] >= 64:
                bucket["on"] += 1
            else:
                bucket["off"] += 1
        for (controller, channel), bucket in sorted(summary.items()):
            values = sorted(bucket["values"])
            value_text = f"{values[0]}-{values[-1]}" if values else "none"
            lines.append(
                f"Channel {channel}: {pedal_controller_names[controller]} "
                f"(CC{controller}) - {bucket['count']} event(s), "
                f"{bucket['on']} on/pressed, {bucket['off']} off/released, values {value_text}"
            )
    else:
        lines.append("No damper/sustain, sostenuto, or soft-pedal controller events found.")

    other_controller_counts = {}
    for event in control_changes:
        if event["controller"] in pedal_controller_names:
            continue
        key = (event["controller"], event["channel"])
        other_controller_counts[key] = other_controller_counts.get(key, 0) + 1
    if other_controller_counts:
        lines.append("Other control changes:")
        for (controller, channel), count in sorted(other_controller_counts.items())[:25]:
            lines.append(f"Channel {channel}: CC{controller} - {count} event(s)")
        if len(other_controller_counts) > 25:
            lines.append(f"...and {len(other_controller_counts) - 25} more controller/channel combination(s).")

    lines.append("")
    lines.append("Metadata:")
    if metadata:
        for track_index, tick, name, value in metadata[:200]:
            lines.append(f"Track {track_index}, tick {tick}: {name}: {value}")
        if len(metadata) > 200:
            lines.append(f"...and {len(metadata) - 200} more metadata event(s).")
    else:
        lines.append("No text, title, tempo, or signature metadata found.")

    return {
        "notes": notes,
        "metadata_text": "\n".join(lines),
        "duration": duration,
        "channels": channels,
        "track_count": len(track_chunks),
        "channel_info": {
            channel: {
                "note_count": note_counts_by_channel.get(channel, 0),
                "control_count": control_counts_by_channel.get(channel, 0),
                "programs": sorted(set(program_changes.get(channel, []))),
                "piano_candidate": channel in piano_channels,
                "mute_note": next(
                    (note for note in mute_notes if note.startswith(f"Channel {channel}:")),
                    "",
                ),
            }
            for channel in sorted(set(channels) | set(note_counts_by_channel) | set(control_counts_by_channel))
        },
        "piano_channels": piano_channels,
    }


def _resource_roots():
    module_root = os.path.dirname(os.path.abspath(__file__))
    roots = [module_root]
    executable_dir = os.path.dirname(sys.executable) if getattr(sys, "executable", "") else ""
    if executable_dir:
        roots.extend(
            [
                os.path.join(executable_dir, "aps_midi_prep_tool_app"),
                executable_dir,
            ]
        )
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        roots.extend(
            [
                os.path.join(bundle_root, "aps_midi_prep_tool_app"),
                bundle_root,
            ]
        )
    appdir = os.environ.get("APPDIR", "")
    if appdir:
        roots.extend(
            [
                os.path.join(appdir, "usr", "share", "aps-midi-prep-tool"),
                os.path.join(appdir, "usr", "share", "sounds"),
            ]
        )
    return roots


def _preview_soundfont_candidates():
    candidates = []
    for root in _resource_roots():
        candidates.extend(
            [
                os.path.join(root, "soundfonts", "default.sf2"),
                os.path.join(root, "soundfonts", "default.sf3"),
                os.path.join(root, "soundfonts", "FluidR3_GM.sf2"),
                os.path.join(root, "soundfonts", "TimGM6mb.sf2"),
            ]
        )

    env_path = os.environ.get("APS_MIDI_PREP_SOUNDFONT", "").strip()
    if env_path:
        candidates.append(env_path)

    candidates.extend(
        [
            "/usr/share/sounds/sf2/FluidR3_GM.sf2",
            "/usr/share/sounds/sf2/default-GM.sf2",
            "/usr/share/sounds/sf2/TimGM6mb.sf2",
            "/usr/share/sounds/sf3/default-GM.sf3",
            "/usr/share/soundfonts/FluidR3_GM.sf2",
            "/usr/share/soundfonts/default.sf2",
            "/usr/share/mscore-*/sound/FluidR3Mono_GM.sf3",
            "/usr/share/mscore-*/sound/MuseScore_General.sf3",
        ]
    )
    return candidates


def _find_preview_soundfont():
    seen = set()
    for candidate in _preview_soundfont_candidates():
        for expanded in glob.glob(os.path.expanduser(candidate)):
            normalized = os.path.abspath(os.path.expanduser(expanded))
            if normalized in seen:
                continue
            seen.add(normalized)
            if os.path.isfile(normalized):
                return normalized
    return ""


def _find_fluidsynth_command():
    env_path = os.environ.get("APS_MIDI_PREP_FLUIDSYNTH", "").strip()
    candidates = [env_path] if env_path else []
    for root in _resource_roots():
        candidates.extend(
            [
                os.path.join(root, "bin", "fluidsynth.exe"),
                os.path.join(root, "bin", "fluidsynth"),
                os.path.join(root, "fluidsynth.exe"),
                os.path.join(root, "fluidsynth"),
                os.path.join(root, "usr", "bin", "fluidsynth.exe"),
                os.path.join(root, "usr", "bin", "fluidsynth"),
            ]
        )
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("fluidsynth") or shutil.which("fluidsynth.exe") or ""


def _write_preview_wav(notes, output_path, duration, progress_callback=None, cancel_callback=None):
    sample_rate = 44100
    max_duration = min(max(float(duration or 0.0), 0.5), 300.0)
    sample_count = max(1, int(sample_rate * max_duration))
    samples = array("h", [0]) * sample_count
    notes = list(notes or [])
    total_notes = max(1, len(notes))
    if progress_callback is not None:
        progress_callback(10, 100, "Preparing built-in piano preview...")

    for note_index, note in enumerate(notes):
        if cancel_callback is not None and cancel_callback():
            raise RuntimeError("Preview generation cancelled.")
        if progress_callback is not None and (note_index % 8 == 0 or note_index == total_notes - 1):
            progress_callback(10 + int((note_index / total_notes) * 80), 100, "Rendering built-in piano preview...")

        start = max(0, int(float(note.get("start_sec", 0.0)) * sample_rate))
        end = min(sample_count, int(float(note.get("end_sec", 0.0)) * sample_rate))
        if end <= start:
            end = min(sample_count, start + int(sample_rate * 0.18))
        if start >= sample_count or end <= start:
            continue

        pitch = int(note.get("pitch", 60))
        velocity = max(1, min(int(note.get("velocity", 64)), 127))
        frequency = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
        amplitude = 2100.0 * (velocity / 127.0)
        for index in range(start, end):
            local = index - start
            t = local / sample_rate
            attack = min(1.0, local / max(1, int(sample_rate * 0.008)))
            decay = exp(-t / 0.85)
            release = max(0.0, min(1.0, (end - index) / max(1, int(sample_rate * 0.12))))
            phase = 2.0 * pi * frequency * t
            tone = (
                sin(phase)
                + 0.42 * sin(phase * 2.01 + 0.2)
                + 0.18 * sin(phase * 3.02 + 0.4)
                + 0.08 * sin(phase * 4.01)
            )
            value = samples[index] + int(amplitude * attack * decay * release * tone)
            samples[index] = max(-32768, min(32767, value))

    if progress_callback is not None:
        progress_callback(95, 100, "Writing preview WAV...")
    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())


class MidiPreviewRenderWorker(QThread):
    progressChanged = Signal(int, int, str)
    previewReady = Signal(str, str)
    previewFailed = Signal(str)

    def __init__(self, midi_bytes, notes, duration, output_path, parent=None):
        super().__init__(parent)
        self.midi_bytes = bytes(midi_bytes or b"")
        self.notes = list(notes or [])
        self.duration = float(duration or 0.0)
        self.output_path = output_path
        self._process = None

    def cancel(self):
        self.requestInterruption()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def _cancel_requested(self):
        return self.isInterruptionRequested()

    def _emit_progress(self, step, total, message):
        if self._cancel_requested():
            raise RuntimeError("Preview generation cancelled.")
        self.progressChanged.emit(int(step or 0), int(total or 0), str(message or ""))
        if self._cancel_requested():
            raise RuntimeError("Preview generation cancelled.")

    def _render_with_fluidsynth(self, midi_path, soundfont_path):
        command = _find_fluidsynth_command()
        if not command or not soundfont_path:
            return False, ""

        self._emit_progress(
            25,
            0,
            f"Rendering SoundFont preview with {os.path.basename(soundfont_path)}...",
        )
        args = [
            command,
            "-ni",
            "-q",
            "-g",
            "0.8",
            "-F",
            self.output_path,
            "-T",
            "wav",
            "-r",
            "44100",
            soundfont_path,
            midi_path,
        ]
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **windows_subprocess_kwargs(),
        )
        while self._process.poll() is None:
            if self._cancel_requested():
                self.cancel()
                raise RuntimeError("Preview generation cancelled.")
            self.msleep(75)
        stdout, stderr = self._process.communicate()
        return_code = self._process.returncode
        self._process = None
        if return_code != 0:
            detail = (stderr or stdout or "").strip()
            if detail:
                detail = f": {detail.splitlines()[-1]}"
            raise RuntimeError(f"FluidSynth could not render the preview{detail}")
        return True, f"FluidSynth + {os.path.basename(soundfont_path)}"

    def run(self):
        midi_path = ""
        try:
            self._emit_progress(5, 100, "Preparing MIDI preview...")
            midi_handle, midi_path = tempfile.mkstemp(prefix="aps_preview_", suffix=".mid")
            with os.fdopen(midi_handle, "wb") as handle:
                handle.write(self.midi_bytes)

            soundfont_path = _find_preview_soundfont()
            rendered = False
            engine_label = ""
            try:
                rendered, engine_label = self._render_with_fluidsynth(midi_path, soundfont_path)
            except Exception as exc:
                if "cancelled" in str(exc).lower():
                    raise
                self._emit_progress(
                    10,
                    100,
                    f"FluidSynth preview unavailable; rendering built-in piano preview...",
                )
            if not rendered:
                message = "Rendering built-in piano preview..."
                if not _find_fluidsynth_command():
                    message = "FluidSynth was not found; rendering built-in piano preview..."
                elif not soundfont_path:
                    message = "No SoundFont was found; rendering built-in piano preview..."
                self._emit_progress(10, 100, message)
                _write_preview_wav(
                    self.notes,
                    self.output_path,
                    self.duration,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._cancel_requested,
                )
                engine_label = "Built-in piano preview"

            if not os.path.isfile(self.output_path) or os.path.getsize(self.output_path) <= 0:
                raise RuntimeError("Preview WAV was not created.")
            self._emit_progress(100, 100, "Preview ready.")
            self.previewReady.emit(self.output_path, engine_label)
        except Exception as exc:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError:
                    pass
            self.previewFailed.emit(str(exc))
        finally:
            if midi_path and os.path.exists(midi_path):
                try:
                    os.remove(midi_path)
                except OSError:
                    pass


class PianoRollWidget(QWidget):
    seekRequested = Signal(float)
    MAX_DISPLAY_NOTES = 50000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.notes = []
        self.display_notes = []
        self.duration = 1.0
        self.playhead_sec = 0.0
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    @classmethod
    def _notes_for_display(cls, notes):
        notes = list(notes or [])
        if len(notes) <= cls.MAX_DISPLAY_NOTES:
            return notes

        ordered = sorted(
            notes,
            key=lambda note: (
                float(note.get("start_sec", 0.0)),
                float(note.get("end_sec", 0.0)),
                int(note.get("pitch", 0)),
            ),
        )
        step = (len(ordered) - 1) / max(1, cls.MAX_DISPLAY_NOTES - 1)
        selected = []
        last_index = -1
        for position in range(cls.MAX_DISPLAY_NOTES):
            index = int(round(position * step))
            if index == last_index:
                continue
            selected.append(ordered[index])
            last_index = index
        return selected

    def set_notes(self, notes, duration):
        self.notes = list(notes or [])
        self.display_notes = self._notes_for_display(self.notes)
        self.duration = max(0.1, float(duration or 0.1))
        self.playhead_sec = 0.0
        self.update()

    def set_playhead(self, seconds):
        self.playhead_sec = max(0.0, min(float(seconds or 0.0), self.duration))
        self.update()

    def _content_rect(self):
        return self.rect().adjusted(8, 8, -8, -8)

    def _seek_from_x(self, x_pos):
        rect = self._content_rect()
        if rect.width() <= 0:
            return
        fraction = max(0.0, min(1.0, (float(x_pos) - rect.left()) / rect.width()))
        self.seekRequested.emit(fraction * self.duration)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._seek_from_x(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._seek_from_x(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        rect = self._content_rect()
        painter.fillRect(self.rect(), QColor("#15191D") if is_dark_theme() else QColor("#F7F9FB"))
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter.setPen(QPen(QColor("#303942") if is_dark_theme() else QColor("#D2D8DE"), 1))
        for index in range(0, 9):
            y = rect.top() + int((rect.height() * index) / 8)
            painter.drawLine(rect.left(), y, rect.right(), y)
        for index in range(0, 9):
            x = rect.left() + int((rect.width() * index) / 8)
            painter.drawLine(x, rect.top(), x, rect.bottom())

        if not self.notes:
            painter.setPen(QColor("#DDE4EA") if is_dark_theme() else QColor("#4B5560"))
            painter.drawText(rect, Qt.AlignCenter, "No note events to display")
            return

        min_pitch = min(note["pitch"] for note in self.notes)
        max_pitch = max(note["pitch"] for note in self.notes)
        pitch_span = max(1, max_pitch - min_pitch)
        colors = [
            QColor("#2E86AB"),
            QColor("#3AA76D"),
            QColor("#D08A2D"),
            QColor("#C84C4C"),
            QColor("#7D6BC4"),
            QColor("#C05C9A"),
        ]
        painter.setPen(Qt.NoPen)
        for note in self.display_notes:
            start = float(note.get("start_sec", 0.0))
            end = float(note.get("end_sec", start + 0.05))
            x = rect.left() + int((start / self.duration) * rect.width())
            x2 = rect.left() + int((end / self.duration) * rect.width())
            width = max(2, x2 - x)
            pitch = int(note.get("pitch", min_pitch))
            y = rect.bottom() - int(((pitch - min_pitch) / pitch_span) * rect.height())
            height = max(3, rect.height() // max(18, pitch_span + 1))
            color = colors[(int(note.get("channel", 1)) - 1) % len(colors)]
            painter.setBrush(color)
            painter.drawRect(x, max(rect.top(), y - height), width, height)

        playhead_x = rect.left() + int((self.playhead_sec / self.duration) * rect.width())
        playhead_color = QColor("#FFCF33") if is_dark_theme() else QColor("#B55300")
        painter.setPen(QPen(playhead_color, 2))
        painter.drawLine(playhead_x, rect.top(), playhead_x, rect.bottom())
        painter.setBrush(playhead_color)
        painter.drawPolygon(
            QPolygon(
                [
                    QPoint(playhead_x, rect.top()),
                    QPoint(playhead_x - 5, rect.top() - 7),
                    QPoint(playhead_x + 5, rect.top() - 7),
                ]
            )
        )


class FileInspectionDialog(QDialog):
    def __init__(self, items, parent=None, initial_row=None):
        super().__init__(parent)
        self.items = list(items or [])
        self.current_notes = []
        self.all_notes = []
        self.visible_notes = []
        self.current_duration = 0.0
        self.current_midi_bytes = b""
        self.current_channel_info = {}
        self.current_piano_channels = set()
        self.preview_audio_path = ""
        self.preview_render_worker = None
        self.preview_engine_label = ""
        self._position_slider_dragging = False
        self._closing = False
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.35)
        self.player.setAudioOutput(self.audio_output)

        apply_window_icon(self)
        self.setWindowTitle("File Inspection")
        self.resize(940, 640)
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal, self)
        layout.addWidget(splitter, stretch=1)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabel("Loaded Files")
        self.file_tree.setMinimumWidth(220)
        self.file_tree.setUniformRowHeights(True)
        self.file_tree.setRootIsDecorated(False)
        initial_tree_item = None
        for item in self.items:
            tree_item = QTreeWidgetItem([item.get("label", "File")])
            tree_item.setData(0, Qt.UserRole, item)
            tree_item.setToolTip(0, item.get("path", ""))
            self.file_tree.addTopLevelItem(tree_item)
            if initial_row is not None and item.get("row") == initial_row:
                initial_tree_item = tree_item

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)

        self.channel_group = QGroupBox("Channels", self)
        channel_layout = QVBoxLayout(self.channel_group)
        self.show_piano_only_checkbox = QCheckBox("Show Piano Channels Only", self)
        self.show_piano_only_checkbox.setToolTip("Limit the piano roll and preview to channels that look like piano parts.")
        channel_layout.addWidget(self.show_piano_only_checkbox)
        channel_grid = QGridLayout()
        self.channel_checkboxes = {}
        for channel in range(1, 17):
            checkbox = QCheckBox(str(channel), self)
            checkbox.setChecked(True)
            checkbox.setVisible(False)
            checkbox.setToolTip(f"Show and preview MIDI channel {channel}.")
            checkbox.toggled.connect(self._update_visible_channels)
            self.channel_checkboxes[channel] = checkbox
            channel_grid.addWidget(checkbox, (channel - 1) // 8, (channel - 1) % 8)
        channel_layout.addLayout(channel_grid)
        right_layout.addWidget(self.channel_group)

        self.piano_roll = PianoRollWidget(self)
        right_layout.addWidget(self.piano_roll, stretch=2)

        position_row = QHBoxLayout()
        self.elapsed_label = QLabel("0:00", self)
        self.position_slider = QSlider(Qt.Horizontal, self)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setSingleStep(5)
        self.position_slider.setPageStep(50)
        self.position_slider.setTracking(True)
        self.duration_label = QLabel("0:00", self)
        position_row.addWidget(self.elapsed_label)
        position_row.addWidget(self.position_slider, stretch=1)
        position_row.addWidget(self.duration_label)
        right_layout.addLayout(position_row)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play", self)
        self.stop_button = QPushButton("Stop", self)
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        self.volume_label = QLabel("Preview volume", self)
        self.volume_label.setToolTip("Playback volume for the generated preview.")
        self.volume_slider = QSlider(Qt.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(35)
        self.volume_slider.setSingleStep(5)
        self.volume_slider.setPageStep(10)
        self.volume_slider.setFixedWidth(150)
        self.volume_slider.setToolTip("Playback volume for the generated preview.")
        self.volume_value_label = QLabel("35%", self)
        self.volume_value_label.setMinimumWidth(42)
        self.volume_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        controls.addWidget(self.volume_label)
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.volume_value_label)
        right_layout.addLayout(controls)

        self.preview_progress_label = QLabel("", self)
        self.preview_progress_label.setVisible(False)
        right_layout.addWidget(self.preview_progress_label)
        self.preview_progress_bar = QProgressBar(self)
        self.preview_progress_bar.setVisible(False)
        right_layout.addWidget(self.preview_progress_bar)

        self.details_box = QPlainTextEdit(self)
        self.details_box.setReadOnly(True)
        right_layout.addWidget(self.details_box, stretch=2)

        splitter.addWidget(self.file_tree)
        splitter.addWidget(right_panel)
        splitter.setSizes([260, 680])

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton("Close", self)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

        self.file_tree.currentItemChanged.connect(lambda _current, _previous: self._load_current_file())
        self.play_button.clicked.connect(self._play_current_file)
        self.stop_button.clicked.connect(self._stop_playback)
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.show_piano_only_checkbox.toggled.connect(self._update_visible_channels)
        self.position_slider.sliderPressed.connect(self._on_position_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_position_slider_released)
        self.position_slider.valueChanged.connect(self._on_position_slider_changed)
        self.piano_roll.seekRequested.connect(self._seek_to_seconds)
        close_button.clicked.connect(self.close)
        if self.file_tree.topLevelItemCount():
            self.file_tree.setCurrentItem(initial_tree_item or self.file_tree.topLevelItem(0))
        self._load_current_file()

    def _current_item(self):
        current = self.file_tree.currentItem()
        if current is None:
            return {}
        item = current.data(0, Qt.UserRole)
        return item if isinstance(item, dict) else {}

    def _clear_preview_audio(self):
        self.player.stop()
        if self.preview_audio_path and os.path.exists(self.preview_audio_path):
            try:
                os.remove(self.preview_audio_path)
            except OSError:
                pass
        self.preview_audio_path = ""
        self.preview_engine_label = ""

    def _set_preview_rendering(self, rendering):
        self.file_tree.setEnabled(not rendering)
        self.channel_group.setEnabled(not rendering)
        self.play_button.setEnabled((not rendering) and bool(self.current_notes))
        self.stop_button.setEnabled(not rendering)
        if not rendering:
            self.preview_progress_bar.setVisible(False)
            self.preview_progress_label.setVisible(bool(self.preview_engine_label))
            if self.preview_engine_label:
                self.preview_progress_label.setText(f"Preview renderer: {self.preview_engine_label}")

    def _load_current_file(self):
        if self.preview_render_worker is not None:
            self.preview_render_worker.cancel()
        self._clear_preview_audio()
        item = self._current_item()
        path = item.get("path", "")
        label = item.get("label", os.path.basename(path))
        try:
            with open(path, "rb") as handle:
                payload = handle.read()
            if is_eseq_file(path):
                payload = convert_eseq_bytes_to_midi_bytes(payload)
            inspection = _inspect_midi_bytes(payload, source_label=label)
            self.current_midi_bytes = bytes(payload)
            self.all_notes = inspection["notes"]
            self.current_channel_info = dict(inspection.get("channel_info") or {})
            self.current_piano_channels = set(inspection.get("piano_channels") or set())
            self.current_notes = list(self.all_notes)
            self.visible_notes = list(self.all_notes)
            self.current_duration = inspection["duration"]
            self.position_slider.setValue(0)
            self.elapsed_label.setText("0:00")
            self.duration_label.setText(_format_duration(self.current_duration))
            self.details_box.setPlainText(inspection["metadata_text"])
            self._update_channel_controls()
            self._update_visible_channels(reset_preview=False)
            self.play_button.setEnabled(bool(self.visible_notes))
            self.stop_button.setEnabled(True)
            self.preview_progress_label.setVisible(False)
            self.preview_progress_bar.setVisible(False)
        except Exception as exc:
            self.current_midi_bytes = b""
            self.all_notes = []
            self.visible_notes = []
            self.current_channel_info = {}
            self.current_piano_channels = set()
            self.current_notes = []
            self.current_duration = 0.0
            self.piano_roll.set_notes([], 0.0)
            self.position_slider.setValue(0)
            self.elapsed_label.setText("0:00")
            self.duration_label.setText("0:00")
            self.details_box.setPlainText(f"Could not inspect {label}.\n\nDetails: {exc}")
            self.play_button.setEnabled(False)

    def _update_channel_controls(self):
        used_channels = set(self.current_channel_info) | {
            int(note.get("channel", 0))
            for note in self.all_notes
            if int(note.get("channel", 0))
        }
        self.show_piano_only_checkbox.blockSignals(True)
        self.show_piano_only_checkbox.setEnabled(bool(self.current_piano_channels))
        self.show_piano_only_checkbox.setChecked(False)
        self.show_piano_only_checkbox.blockSignals(False)
        for channel, checkbox in self.channel_checkboxes.items():
            info = self.current_channel_info.get(channel, {})
            checkbox.blockSignals(True)
            checkbox.setVisible(channel in used_channels)
            checkbox.setEnabled(channel in used_channels)
            checkbox.setChecked(channel in used_channels)
            label_parts = [f"Channel {channel}"]
            if info.get("piano_candidate"):
                label_parts.append("piano candidate")
            note_count = int(info.get("note_count", 0) or 0)
            control_count = int(info.get("control_count", 0) or 0)
            label_parts.append(f"{note_count} note(s)")
            label_parts.append(f"{control_count} control change(s)")
            programs = info.get("programs") or []
            if programs:
                program_text = ", ".join(_program_name(program) for program in programs[:6])
                if len(programs) > 6:
                    program_text += f", and {len(programs) - 6} more"
                label_parts.append(f"Programs: {program_text}")
            mute_note = info.get("mute_note") or ""
            if mute_note:
                label_parts.append(mute_note)
            checkbox.setToolTip(". ".join(label_parts))
            checkbox.blockSignals(False)

    def _selected_channels(self):
        return {
            channel
            for channel, checkbox in self.channel_checkboxes.items()
            if not checkbox.isHidden() and checkbox.isChecked()
        }

    def _preview_channels(self):
        channels = self._selected_channels()
        if self.show_piano_only_checkbox.isChecked() and self.current_piano_channels:
            channels &= self.current_piano_channels
        return channels

    def _reset_preview_for_filter_change(self):
        self._clear_preview_audio()
        self.position_slider.setValue(0)
        self.elapsed_label.setText("0:00")
        self.piano_roll.set_playhead(0.0)
        self.preview_progress_label.setVisible(False)
        self.preview_progress_bar.setVisible(False)

    def _update_visible_channels(self, *args, reset_preview=True):
        del args
        channels = self._preview_channels()
        self.visible_notes = [
            note
            for note in self.all_notes
            if int(note.get("channel", 0)) in channels
        ]
        self.current_notes = list(self.visible_notes)
        self.piano_roll.set_notes(self.visible_notes, self.current_duration)
        if reset_preview:
            self._reset_preview_for_filter_change()
        self.play_button.setEnabled(bool(self.visible_notes) and self.preview_render_worker is None)

    def _filtered_midi_bytes_for_preview(self):
        return _filter_midi_bytes_to_channels(self.current_midi_bytes, self._preview_channels())

    def _play_current_file(self):
        if not self.visible_notes or self.preview_render_worker is not None:
            return
        if self.preview_audio_path and os.path.exists(self.preview_audio_path):
            self._start_preview_playback()
            return

        handle, path = tempfile.mkstemp(prefix="aps_preview_", suffix=".wav")
        os.close(handle)
        self.preview_audio_path = path
        worker = MidiPreviewRenderWorker(
            self._filtered_midi_bytes_for_preview(),
            self.visible_notes,
            self.current_duration,
            path,
            parent=self,
        )
        worker.progressChanged.connect(self._on_preview_render_progress)
        worker.previewReady.connect(self._on_preview_render_ready)
        worker.previewFailed.connect(self._on_preview_render_failed)
        worker.finished.connect(self._on_preview_render_finished)
        self.preview_render_worker = worker
        self._set_preview_rendering(True)
        self._on_preview_render_progress(0, 100, "Preparing preview...")
        worker.start()

    def _on_preview_render_progress(self, step, total, message):
        self.preview_progress_label.setVisible(True)
        self.preview_progress_label.setText(message or "Preparing preview...")
        self.preview_progress_bar.setVisible(True)
        if total <= 0:
            self.preview_progress_bar.setRange(0, 0)
        else:
            self.preview_progress_bar.setRange(0, int(total))
            self.preview_progress_bar.setValue(max(0, min(int(step), int(total))))

    def _on_preview_render_ready(self, path, engine_label):
        self.preview_audio_path = path
        self.preview_engine_label = engine_label
        self._start_preview_playback()

    def _on_preview_render_failed(self, message):
        self._clear_preview_audio()
        self.preview_progress_label.setVisible(False)
        self.preview_progress_bar.setVisible(False)
        if self._closing or "cancelled" in str(message).lower():
            return
        QMessageBox.warning(self, "Playback Failed", f"Could not prepare playback preview.\n\nDetails: {message}")

    def _on_preview_render_finished(self):
        worker = self.preview_render_worker
        self.preview_render_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_preview_rendering(False)

    def _start_preview_playback(self):
        if not self.preview_audio_path or not os.path.exists(self.preview_audio_path):
            return
        self.player.setSource(QUrl.fromLocalFile(self.preview_audio_path))
        seek_ms = self._current_slider_position_ms()
        if seek_ms > 0:
            self.player.setPosition(seek_ms)
        self.player.play()

    def _current_slider_position_ms(self):
        fraction = self.position_slider.value() / max(1, self.position_slider.maximum())
        duration = self.player.duration()
        if duration <= 0:
            duration = int(max(0.0, self.current_duration) * 1000)
        return int(max(0.0, min(1.0, fraction)) * max(0, duration))

    def _slider_seconds(self):
        fraction = self.position_slider.value() / max(1, self.position_slider.maximum())
        return max(0.0, min(1.0, fraction)) * max(0.0, self.current_duration)

    def _seek_to_seconds(self, seconds):
        seconds = max(0.0, min(float(seconds or 0.0), max(0.0, self.current_duration)))
        maximum = max(1, self.position_slider.maximum())
        value = int((seconds / max(0.1, self.current_duration)) * maximum) if self.current_duration > 0 else 0
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(max(0, min(maximum, value)))
        self.position_slider.blockSignals(False)
        self.elapsed_label.setText(_format_duration(seconds))
        self.piano_roll.set_playhead(seconds)
        if self.player.duration() > 0:
            self.player.setPosition(int(seconds * 1000))

    def _on_position_slider_pressed(self):
        self._position_slider_dragging = True

    def _on_position_slider_released(self):
        self._position_slider_dragging = False
        self._seek_to_seconds(self._slider_seconds())

    def _on_position_slider_changed(self, _value):
        seconds = self._slider_seconds()
        self.elapsed_label.setText(_format_duration(seconds))
        self.piano_roll.set_playhead(seconds)
        if self._position_slider_dragging and self.player.duration() > 0:
            self.player.setPosition(int(seconds * 1000))

    def _on_player_position_changed(self, position_ms):
        duration_ms = self.player.duration()
        if duration_ms <= 0:
            duration_ms = int(max(0.0, self.current_duration) * 1000)
        seconds = max(0.0, position_ms / 1000.0)
        if not self._position_slider_dragging and duration_ms > 0:
            value = int((position_ms / duration_ms) * self.position_slider.maximum())
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(max(0, min(self.position_slider.maximum(), value)))
            self.position_slider.blockSignals(False)
        self.elapsed_label.setText(_format_duration(seconds))
        self.piano_roll.set_playhead(seconds)

    def _on_player_duration_changed(self, duration_ms):
        if duration_ms > 0:
            self.duration_label.setText(_format_duration(duration_ms / 1000.0))
        else:
            self.duration_label.setText(_format_duration(self.current_duration))

    def _on_volume_changed(self, value):
        percent = max(0, min(int(value or 0), 100))
        self.audio_output.setVolume(percent / 100.0)
        self.volume_value_label.setText(f"{percent}%")

    def _stop_playback(self):
        self.player.stop()
        self._seek_to_seconds(0.0)

    def closeEvent(self, event):
        self._closing = True
        if self.preview_render_worker is not None:
            self.preview_render_worker.cancel()
            self.preview_render_worker.wait(3000)
        self._clear_preview_audio()
        super().closeEvent(event)


class WriteProtectToggle(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._target_label = "original"
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(30, 50)
        self.setAccessibleName("Allow saving to original media")
        self.setFocusPolicy(Qt.StrongFocus)
        self.toggled.connect(self._refresh_tooltip)
        self._refresh_tooltip()

    def set_target_label(self, target_label):
        self._target_label = str(target_label or "original")
        self._refresh_tooltip()

    def _refresh_tooltip(self):
        if self.isChecked():
            self.setToolTip(
                f"Write enabled for this {self._target_label}. Save will modify the original."
            )
        else:
            self.setToolTip(
                f"Write protected for this {self._target_label}. Use Save As or Save As Image instead."
            )

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(3, 3, -3, -3)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        write_enabled = self.isChecked()
        if is_dark_theme():
            border = QColor("#7B8792")
            fill = QColor("#A63E3E") if write_enabled else QColor("#286B48")
            thumb = QColor("#DDE4EA")
            thumb_edge = QColor("#283038")
        else:
            border = QColor("#5F6870")
            fill = QColor("#C94842") if write_enabled else QColor("#2F8A58")
            thumb = QColor("#FFFFFF")
            thumb_edge = QColor("#55606A")

        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 2, 2)

        mid = rect.center().y()
        thumb_rect = rect.adjusted(5, 5, -5, -5)
        if write_enabled:
            thumb_rect.setTop(mid + 2)
        else:
            thumb_rect.setBottom(mid - 2)
        painter.setPen(QPen(thumb_edge, 1))
        painter.setBrush(thumb)
        painter.drawRect(thumb_rect)


def _version_key(version):
    text = str(version or "").strip().lstrip("vV")
    parts = re.split(r"[^0-9A-Za-z]+", text)
    key = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key) or ((0, 0),)


def _is_newer_version(latest_version, current_version):
    latest_key = list(_version_key(latest_version))
    current_key = list(_version_key(current_version))
    max_len = max(len(latest_key), len(current_key))
    latest_key.extend([(0, 0)] * (max_len - len(latest_key)))
    current_key.extend([(0, 0)] * (max_len - len(current_key)))
    return latest_key > current_key


class UpdateCheckWorker(QThread):
    updateChecked = Signal(dict)
    updateCheckFailed = Signal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = str(url or "")

    def run(self):
        try:
            request = urllib.request.Request(
                self.url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = response.read(1024 * 1024)
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("The update document did not contain a JSON object.")
            latest_version = str(
                data.get("latest_version")
                or data.get("version")
                or ""
            ).strip()
            if not latest_version:
                raise ValueError("The update document does not include latest_version.")
            self.updateChecked.emit(data)
        except urllib.error.URLError as exc:
            self.updateCheckFailed.emit(str(getattr(exc, "reason", exc)))
        except Exception as exc:
            self.updateCheckFailed.emit(str(exc))


class MidiTitleWindow(QMainWindow):
    TITLE_COMPAT_LIMIT = 32
    ESEQ_FILE_LIMIT = PIANODIR_MAX_TRACKS
    TITLE_RAW_ROLE = TitleOverflowDelegate.RAW_TITLE_ROLE
    CENTERED_TITLE_DISK_THRESHOLD = 3
    SETTINGS_ORG = APP_SETTINGS_ORG
    SETTINGS_APP = APP_SETTINGS_APP
    SETTING_SHOW_COMPAT_WARNING = "show_compat_warning"
    SETTING_STORE_BACKUPS = "store_backups"
    SETTING_SKIP_TYPE0_WARNING = "skip_type0_warning"
    SETTING_SKIP_IMAGE_REMOVE_WARNING = "skip_image_remove_warning"
    SETTING_SKIP_IMAGE_DELETE_ON_SAVE_WARNING = "skip_image_delete_on_save_warning"
    SETTING_SKIP_FLOPPY_WRITE_WARNING = "skip_floppy_write_warning"
    SETTING_ALLOW_FLOPPY_SAVE = "allow_floppy_save"
    SETTING_CONFIRM_IMAGE_SAVE = "confirm_image_save"
    SETTING_AUTO_WRITE_PROTECT_ON_LOAD = "auto_write_protect_on_load"
    SETTING_FORMAT_DISKLAVIER_SCREEN = "format_disklavier_screen"
    SETTING_ESEQ_EXPORT_ALBUM_SUBFOLDER = "eseq_export_album_subfolder"
    SETTING_ESEQ_TO_MIDI_SWITCH_MODE = "eseq_to_midi_switch_mode"
    SETTING_GREASEWEAZLE_DEVICE_PATH = "greaseweazle_device_path"
    SETTING_GREASEWEAZLE_DRIVE = "greaseweazle_drive"
    SETTING_READ_FLOPPY_SOURCE_KIND = "read_floppy_source_kind"
    SETTING_READ_FLOPPY_GW_ARCHIVAL = "read_floppy_gw_archival"
    SETTING_READ_FLOPPY_GW_FORMAT = "read_floppy_gw_format"
    SETTING_READ_FLOPPY_GW_REVS = "read_floppy_gw_revs"
    SETTING_READ_FLOPPY_GW_RETRIES = "read_floppy_gw_retries"
    SETTING_READ_FLOPPY_CONVERT_TO_MIDI = "read_floppy_convert_to_midi"
    SETTING_CHECK_UPDATES_AT_STARTUP = "check_updates_at_startup"
    SETTING_SKIP_UPDATE_REMINDERS = "skip_update_reminders"
    SETTING_WRITE_TAG_SIDECARS = "write_tag_sidecars"
    IMAGE_FILENAME_INVALID_CHARS = set('\\/:*?"<>|+,;=[]')
    EXPORT_FOLDER_INVALID_CHARS = set('\\/:*?"<>|')
    TYPE_COLUMN_MIN_WIDTH = 70
    TYPE_COLUMN_MAX_WIDTH = 420
    TYPE_COLUMN_ESEQ_DETAIL_MIN_WIDTH = 240
    FILENAME_COLUMN_CHARS = 9
    FILENAME_COLUMN_PADDING = 22
    TITLE_COLUMN_MIN_CHARS = 32
    TITLE_COLUMN_PADDING = 30
    USER_RESIZABLE_EDGE_COLUMNS = {3, 4, 5, 6}

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        apply_window_icon(self)
        self.resize(860, 800)
        self.pendingEdits = {}         # keys: full file paths, values: new titles
        self.image_session = None
        self.pendingImageRenames = {}  # keys: image paths, values: target image paths
        self.pendingImageTitleEdits = {}  # keys: image paths, values: new MIDI titles
        self.pendingImageDeletes = set()
        self.pendingImageAdditions = {}  # keys: target image paths, values: host file paths
        self.pendingImageReplacements = {}  # keys: image paths, values: replacement host file paths
        self.imageEntriesByPath = {}
        self.imageFileInfo = {}
        self.imageEseqMode = False
        self.imageEseqVariant = ESEQ_VARIANT_DISKLAVIER
        self.imageTitlesLikelyCentered = False
        self.imageHasPianodir = False
        self.imagePianodirPopulated = False
        self.loadedImagePianodirMetadata = PianodirMetadata()
        self.pendingExportPianodirMetadata = PianodirMetadata()
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = False
        self.midiScratchDir = None
        self.listedFileInfo = {}
        self.pendingRegularConversions = {}
        self.pendingRegularRenames = {}
        self.regularModeContextPath = ""
        self.regularEseqMode = False
        self.regularEseqVariant = ESEQ_VARIANT_DISKLAVIER
        self.regularTitlesLikelyCentered = False
        self.regularHasPianodir = False
        self.regularPianodirPopulated = False
        self.regularPianodirSourcePath = ""
        self.loadedRegularPianodirMetadata = PianodirMetadata()
        self.loadedRegularEseqPaths = tuple()
        self.regularDropBatchPrepared = False
        self.regularDropBatchPromotesToEseq = False
        self.regularDropConflictChoice = ""
        self.regularDropCancelled = False
        self.diskLoadWorker = None
        self.diskLoadProgressDialog = None
        self.diskLoadFailureTitle = "Disk Load Failed"
        self.diskLoadShouldOfferCapture = False
        self.diskLoadContext = {}
        self.pendingFloppyReadConvertToMidi = False
        self.v50NseqPromptedSessionPath = ""
        self.mpcSeqPromptedSessionPath = ""
        self.pendingGwConversionDetails = None
        self.pendingGwCapture = None
        self.pendingDiskRecoveryRequest = None
        self.diskRecoveryWorker = None
        self.diskRecoveryProgressDialog = None
        self.diskRecoveryContext = {}
        self.diskFormatWorker = None
        self.diskFormatProgressDialog = None
        self.diskFormatContext = {}
        self.diskCommitWorker = None
        self.diskCommitProgressDialog = None
        self.diskWriteTargetWorker = None
        self.diskWriteTargetProgressDialog = None
        self.updateCheckWorker = None
        self.updateCheckManual = False
        self.updateCheckStartupScheduled = False
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._did_apply_initial_column_sizing = False
        self._is_adjusting_columns = False
        self._manual_column_widths = {}
        self.title_monospace_font = QFont("Courier New")
        self.title_monospace_font.setStyleHint(QFont.Monospace)

        # Main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self.setCentralWidget(main_widget)

        # Top: source buttons
        source_layout = QHBoxLayout()
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(10)

        self.choose_button = QPushButton("Open MIDI Folder")
        self.choose_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.choose_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.choose_button.setToolTip(
            "Select a folder to scan for .mid and .midi files."
        )
        self.choose_button.clicked.connect(self.browse_directory)
        source_layout.addWidget(self.choose_button, stretch=1)

        self.open_image_button = QPushButton("Open Image")
        self.open_image_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.open_image_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.open_image_button.setToolTip(
            "Open a floppy image file for editing in Image Mode."
        )
        self.open_image_button.clicked.connect(self.open_image_dialog)
        source_layout.addWidget(self.open_image_button, stretch=1)

        self.read_floppy_button = QPushButton("Read Floppy")
        self.read_floppy_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.read_floppy_button.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.read_floppy_button.setToolTip(
            "Read a floppy from a floppy drive or from a Greaseweazle-connected drive."
        )
        self.read_floppy_button.clicked.connect(self.load_floppy_drive)
        source_layout.addWidget(self.read_floppy_button, stretch=1)

        main_layout.addLayout(source_layout)

        # Middle: Table for displaying MIDI files (using our DropTableWidget subclass)
        # Column order:
        # 0: Delete ("X"), 1: FullPath (hidden), 2: 📋, 3: Filename, 4: Title, 5: Compat warning (>32), 6: MIDI type
        self.table = DropTableWidget(0, 7)
        self.table.setStyleSheet("QTableWidget::item:selected { background-color: #FFB347; }")
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "Long", "Type"])
        self.table.setToolTip(
            "Drop MIDI files here, click a Title cell to edit, or click the clipboard icon to copy a filename."
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(40)
        header.sectionResized.connect(self._handle_section_resized)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(2, 50)
        self.table.setColumnWidth(3, self._default_filename_column_width())
        self.table.setColumnWidth(4, 260)
        self.table.setColumnWidth(5, 65)
        self.table.setColumnWidth(6, self.TYPE_COLUMN_MIN_WIDTH)
        self.table.setColumnHidden(1, True)  # Hide the full path column
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self.handle_cell_clicked)
        self.table.cellDoubleClicked.connect(self.handle_cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._refresh_eseq_reorder_buttons)
        self.title_delegate = TitleOverflowDelegate(self.TITLE_COMPAT_LIMIT, self.table)
        self.table.setItemDelegateForColumn(4, self.title_delegate)
        header_tooltips = {
            0: "Remove this row from the list (does not delete the file on disk).",
            1: "Internal full file path (hidden).",
            2: "Copy filename to clipboard.",
            3: "Filename on disk.",
            4: "MIDI title metadata. Click to edit.",
            5: f"Shows if title exceeds {self.TITLE_COMPAT_LIMIT} characters.",
            6: "Detected MIDI type from the file header. Double-click to inspect this song.",
        }
        for column, tooltip in header_tooltips.items():
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip(tooltip)

        file_list_layout = QHBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(6)
        file_list_layout.addWidget(self.table, stretch=1)

        self.diskUsageBarsWidget = QWidget()
        usage_bars_layout = QHBoxLayout(self.diskUsageBarsWidget)
        usage_bars_layout.setContentsMargins(0, 0, 0, 0)
        usage_bars_layout.setSpacing(3)
        self.diskUsageBar = VerticalUsageBar(self.diskUsageBarsWidget)
        self.eseqCountBar = SegmentedEseqCountBar(self.ESEQ_FILE_LIMIT, self.diskUsageBarsWidget)
        self.diskUsageBar.setToolTip("Floppy image space used.")
        self.eseqCountBar.setToolTip("Yamaha E-SEQ file slots used.")
        usage_bars_layout.addWidget(self.diskUsageBar)
        usage_bars_layout.addWidget(self.eseqCountBar)
        self.diskUsageBarsWidget.setVisible(False)
        file_list_layout.addWidget(self.diskUsageBarsWidget)
        main_layout.addLayout(file_list_layout, stretch=1)

        self.eseqReorderWidget = QWidget()
        reorder_layout = QHBoxLayout(self.eseqReorderWidget)
        reorder_layout.setContentsMargins(0, 0, 0, 0)
        reorder_layout.setSpacing(8)
        reorder_layout.addStretch()

        self.moveEseqUpButton = QToolButton()
        self.moveEseqUpButton.setArrowType(Qt.UpArrow)
        self.moveEseqUpButton.setToolTip("Move the selected Yamaha E-SEQ file earlier in the directory order.")
        self.moveEseqUpButton.setFixedSize(34, 28)
        self.moveEseqUpButton.clicked.connect(lambda: self.move_selected_eseq_row(-1))
        reorder_layout.addWidget(self.moveEseqUpButton)

        self.moveEseqDownButton = QToolButton()
        self.moveEseqDownButton.setArrowType(Qt.DownArrow)
        self.moveEseqDownButton.setToolTip("Move the selected Yamaha E-SEQ file later in the directory order.")
        self.moveEseqDownButton.setFixedSize(34, 28)
        self.moveEseqDownButton.clicked.connect(lambda: self.move_selected_eseq_row(1))
        reorder_layout.addWidget(self.moveEseqDownButton)
        reorder_layout.addStretch()
        self.eseqReorderWidget.setVisible(False)
        main_layout.addWidget(self.eseqReorderWidget)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(42)
        self.status_label.setToolTip("Operation status, warnings, and progress messages.")
        main_layout.addWidget(self.status_label)

        # Controls area: grouped into equally spaced sections for clarity.
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        options_group = QGroupBox("Options")
        options_group.setToolTip("Display and compatibility preferences for the file list.")
        options_layout = QVBoxLayout(options_group)
        options_layout.setContentsMargins(10, 14, 10, 10)
        options_layout.setSpacing(6)

        show_compat_warning = self.settings.value(self.SETTING_SHOW_COMPAT_WARNING, True, type=bool)
        self.compat_warning_checkbox = QCheckBox("Long title warning")
        self.compat_warning_checkbox.setChecked(show_compat_warning)
        self.compat_warning_checkbox.setToolTip(
            "Highlight title characters beyond the 32-character legacy compatibility limit."
        )
        self.compat_warning_checkbox.toggled.connect(self.toggle_compat_warnings)
        self.title_delegate.set_highlight_enabled(show_compat_warning)
        options_layout.addWidget(self.compat_warning_checkbox, alignment=Qt.AlignLeft)

        format_disklavier_screen = self.settings.value(
            self.SETTING_FORMAT_DISKLAVIER_SCREEN, False, type=bool
        )
        self.format_disklavier_checkbox = QCheckBox("Format for Disklavier screen")
        self.format_disklavier_checkbox.setChecked(format_disklavier_screen)
        self.format_disklavier_checkbox.setToolTip(
            "When editing titles, use the Disklavier's two 16-character screen rows."
        )
        self.format_disklavier_checkbox.toggled.connect(self.toggle_format_disklavier_screen)
        options_layout.addWidget(self.format_disklavier_checkbox, alignment=Qt.AlignLeft)

        store_backups = self.settings.value(self.SETTING_STORE_BACKUPS, False, type=bool)
        self.backup_checkbox = QCheckBox("Back up before saving")
        self.backup_checkbox.setChecked(store_backups)
        self.backup_checkbox.setToolTip(
            "Before overwriting, back up images beside the image and individual files into a backup folder."
        )
        self.backup_checkbox.toggled.connect(self.toggle_store_backups)
        options_layout.addWidget(self.backup_checkbox, alignment=Qt.AlignLeft)

        options_layout.addStretch()

        self.modeBannerLabel = QLabel("MIDI MODE")
        self.modeBannerLabel.setAlignment(Qt.AlignCenter)
        mode_font = QFont("Helvetica", 14, QFont.Bold)
        self.modeBannerLabel.setFont(mode_font)
        self.modeBannerLabel.setWordWrap(True)
        self.modeBannerLabel.setToolTip("Shows the current editing mode and active source.")

        utilities_group = QGroupBox("Utilities")
        utilities_group.setToolTip("Batch tools that run across every listed file immediately.")
        utilities_layout = QVBoxLayout(utilities_group)
        utilities_layout.setContentsMargins(10, 14, 10, 10)
        utilities_layout.setSpacing(6)

        utilities_hint = QLabel("Apply to all listed files:")
        utilities_hint.setWordWrap(True)
        utilities_hint.setAlignment(Qt.AlignCenter)
        utilities_layout.addWidget(utilities_hint)

        self.renameAllButton = QPushButton("Rename 8.3")
        self.renameAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.renameAllButton.setMinimumHeight(36)
        self.renameAllButton.setToolTip(
            "Utility: rename every listed file to DOS 8.3 format (00.MID, 01.MID, ...)."
        )
        self.renameAllButton.clicked.connect(self.rename_all_for_disk)

        self.convertType0Button = QPushButton("SMF1 -> SMF0")
        self.convertType0Button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertType0Button.setMinimumHeight(36)
        self.convertType0Button.setToolTip(
            "Utility: convert every listed file to MIDI Type 0 (single-track)."
        )
        self.convertType0Button.clicked.connect(self.convert_all_to_type0)

        self.convertEseqToMidiButton = QPushButton("E-SEQ -> MIDI")
        self.convertEseqToMidiButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertEseqToMidiButton.setMinimumHeight(36)
        self.convertEseqToMidiButton.setToolTip(
            "Image/Floppy Mode utility: queue conversion of listed E-SEQ files to SMF MIDI."
        )
        self.convertEseqToMidiButton.clicked.connect(self.convert_all_eseq_to_midi)

        self.convertMidiToEseqButton = QPushButton("MIDI -> E-SEQ")
        self.convertMidiToEseqButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.convertMidiToEseqButton.setMinimumHeight(36)
        self.convertMidiToEseqButton.setToolTip(
            "Image/Floppy Mode utility: queue conversion of listed MIDI files to Yamaha E-SEQ."
        )
        self.convertMidiToEseqButton.clicked.connect(self.convert_all_midi_to_eseq)
        self._apply_compact_button_labels()

        utilities_buttons_layout = QGridLayout()
        utilities_buttons_layout.setContentsMargins(0, 0, 0, 0)
        utilities_buttons_layout.setHorizontalSpacing(6)
        utilities_buttons_layout.setVerticalSpacing(6)
        utilities_buttons_layout.addWidget(self.renameAllButton, 0, 0)
        utilities_buttons_layout.addWidget(self.convertType0Button, 0, 1)
        utilities_buttons_layout.addWidget(self.convertEseqToMidiButton, 1, 0)
        utilities_buttons_layout.addWidget(self.convertMidiToEseqButton, 1, 1)
        utilities_buttons_layout.setColumnStretch(0, 1)
        utilities_buttons_layout.setColumnStretch(1, 1)
        utilities_layout.addLayout(utilities_buttons_layout)
        utilities_layout.addStretch()

        actions_group = QGroupBox("File Actions")
        actions_group.setToolTip("Save files, create images, or clear the current list.")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(10, 14, 10, 10)
        actions_layout.setSpacing(6)

        # Clear button (styled to match Save button)
        self.clearButton = QToolButton()
        self.clearButton.setText("Clear")
        self.clearButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.clearButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clearButton.setMinimumHeight(36)
        self.clearButton.setToolTip("Remove all files from the current list.")
        self.clearButton.clicked.connect(self.clear_list)

        self.saveButton = QToolButton()
        self.saveButton.setText("Save")
        self.saveButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveButton.setMinimumHeight(36)
        self.saveButton.clicked.connect(self.save_pending_changes)

        self.saveAsButton = QToolButton()
        self.saveAsButton.setText("Save As")
        self.saveAsButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveAsButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveAsButton.setMinimumHeight(36)
        self.saveAsButton.setToolTip("Save copies with current titles to a selected destination folder.")
        self.saveAsButton.clicked.connect(self.save_as_changes)

        self.saveAsImageButton = QToolButton()
        self.saveAsImageButton.setText("Save As Image")
        self.saveAsImageButton.setFont(QFont("Helvetica", 18, QFont.Bold))
        self.saveAsImageButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.saveAsImageButton.setMinimumHeight(36)
        self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self.saveAsImageButton.clicked.connect(self.save_as_image)
        self._apply_compact_button_labels()

        actions_buttons_layout = QGridLayout()
        actions_buttons_layout.setContentsMargins(0, 0, 0, 0)
        actions_buttons_layout.setHorizontalSpacing(6)
        actions_buttons_layout.setVerticalSpacing(6)
        actions_buttons_layout.addWidget(self.clearButton, 0, 0)
        actions_buttons_layout.addWidget(self.saveAsButton, 1, 0)
        actions_buttons_layout.addWidget(self.saveAsImageButton, 1, 1)
        actions_buttons_layout.setColumnStretch(0, 1)
        actions_buttons_layout.setColumnStretch(1, 1)
        actions_layout.addLayout(actions_buttons_layout)

        save_with_toggle_widget = QWidget(actions_group)
        save_with_toggle_layout = QHBoxLayout(save_with_toggle_widget)
        save_with_toggle_layout.setContentsMargins(0, 0, 0, 0)
        save_with_toggle_layout.setSpacing(6)
        save_with_toggle_layout.addWidget(self.saveButton, stretch=1)
        self.writeProtectToggle = WriteProtectToggle(actions_group)
        self.writeProtectToggle.toggled.connect(self.toggle_original_write)
        self.writeProtectToggle.setVisible(False)
        save_with_toggle_layout.addWidget(self.writeProtectToggle, alignment=Qt.AlignCenter)
        actions_buttons_layout.addWidget(save_with_toggle_widget, 0, 1)

        actions_layout.addStretch()

        for section in (options_group, utilities_group, actions_group):
            section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        controls_layout.addWidget(options_group, stretch=1)
        controls_layout.addWidget(utilities_group, stretch=1)
        controls_layout.addWidget(actions_group, stretch=1)
        main_layout.addLayout(controls_layout)

        self.imagePianodirMetadataWidget = QWidget()
        self.imagePianodirMetadataWidget.setToolTip(
            "Album title and catalog number stored in the Yamaha E-SEQ directory file."
        )
        pianodir_meta_layout = QHBoxLayout(self.imagePianodirMetadataWidget)
        pianodir_meta_layout.setContentsMargins(0, 0, 0, 0)
        pianodir_meta_layout.setSpacing(8)

        album_title_label = QLabel("Album Title")
        album_title_label.setToolTip(
            "Album title stored in the Yamaha E-SEQ directory file when supported."
        )
        pianodir_meta_layout.addWidget(album_title_label)

        self.imagePianodirTitleEdit = QLineEdit()
        self.imagePianodirTitleEdit.setPlaceholderText("Album title")
        self.imagePianodirTitleEdit.setMaxLength(PIANODIR_DISK_METADATA_SIZE)
        self.imagePianodirTitleEdit.setToolTip(
            "Album title stored in the Yamaha E-SEQ directory file when supported."
        )
        pianodir_meta_layout.addWidget(self.imagePianodirTitleEdit, stretch=3)

        catalog_label = QLabel("Catalog Number")
        catalog_label.setToolTip(
            "Catalog number stored in the Yamaha E-SEQ directory file when supported."
        )
        pianodir_meta_layout.addWidget(catalog_label)

        self.imagePianodirCatalogEdit = QLineEdit()
        self.imagePianodirCatalogEdit.setPlaceholderText("Catalog number")
        self.imagePianodirCatalogEdit.setMaxLength(PIANODIR_DISK_METADATA_SIZE)
        self.imagePianodirCatalogEdit.setToolTip(
            "Catalog number stored in the Yamaha E-SEQ directory file when supported."
        )
        self.imagePianodirCatalogEdit.editingFinished.connect(self._normalize_pianodir_catalog_field)
        pianodir_meta_layout.addWidget(self.imagePianodirCatalogEdit, stretch=1)

        use_album_subfolder = self.settings.value(
            self.SETTING_ESEQ_EXPORT_ALBUM_SUBFOLDER, True, type=bool
        )
        self.album_subfolder_checkbox = QCheckBox("Create Album Subfolder")
        self.album_subfolder_checkbox.setChecked(use_album_subfolder)
        self.album_subfolder_checkbox.setToolTip(
            "When exporting E-SEQ files or converting them to MIDI, place the files in a folder named from the catalog number and album title."
        )
        self.album_subfolder_checkbox.toggled.connect(self.toggle_album_subfolder)
        self.album_subfolder_checkbox.setVisible(False)
        pianodir_meta_layout.addWidget(self.album_subfolder_checkbox)

        self.imagePianodirMetadataWidget.setVisible(False)
        main_layout.addWidget(self.imagePianodirMetadataWidget)
        main_layout.addWidget(self.modeBannerLabel)

        self.fileMenu = self.menuBar().addMenu("&File")
        self.fileNewImageAction = QAction("New Image...", self)
        self.fileNewImageAction.triggered.connect(self.new_image_dialog)
        self.fileMenu.addAction(self.fileNewImageAction)

        self.fileOpenFolderAction = QAction("Open MIDI Folder...", self)
        self.fileOpenFolderAction.triggered.connect(self.browse_directory)
        self.fileMenu.addAction(self.fileOpenFolderAction)

        self.fileOpenImageAction = QAction("Open Image...", self)
        self.fileOpenImageAction.triggered.connect(self.open_image_dialog)
        self.fileMenu.addAction(self.fileOpenImageAction)

        self.fileReadFloppyAction = QAction("Read Floppy...", self)
        self.fileReadFloppyAction.triggered.connect(self.load_floppy_drive)
        self.fileMenu.addAction(self.fileReadFloppyAction)

        self.fileMenu.addSeparator()

        self.fileSaveAction = QAction("Save", self)
        self.fileSaveAction.triggered.connect(self.save_pending_changes)
        self.fileMenu.addAction(self.fileSaveAction)

        self.fileSaveAsAction = QAction("Save As...", self)
        self.fileSaveAsAction.triggered.connect(self.save_as_changes)
        self.fileMenu.addAction(self.fileSaveAsAction)

        self.fileCreateTagSidecarsAction = QAction("Create Tag Sidecars When Saving", self)
        self.fileCreateTagSidecarsAction.setCheckable(True)
        self.fileCreateTagSidecarsAction.setChecked(self._tag_sidecars_enabled())
        self.fileCreateTagSidecarsAction.setToolTip(
            "When saving local MIDI or E-SEQ files to folders, create one .tags.txt ID3 tag sidecar next to each song. "
            "This option is not used for Image Mode or Floppy Mode saves."
        )
        self.fileCreateTagSidecarsAction.toggled.connect(self.toggle_tag_sidecar_writing)

        self.fileSaveAsImageAction = QAction("Save As Image...", self)
        self.fileSaveAsImageAction.triggered.connect(self.save_as_image)
        self.fileMenu.addAction(self.fileSaveAsImageAction)

        self.fileSaveToFloppyAction = QAction("Save To Floppy...", self)
        self.fileSaveToFloppyAction.setToolTip(
            "Save the current listed files directly to a formatted floppy drive without rewriting the whole disk image."
        )
        self.fileSaveToFloppyAction.triggered.connect(self.save_to_floppy)
        self.fileMenu.addAction(self.fileSaveToFloppyAction)

        self.fileWriteImageToFloppyAction = QAction("Write Current Image to Floppy...", self)
        self.fileWriteImageToFloppyAction.setToolTip(
            "Write the currently loaded image or floppy session to a physical floppy disk."
        )
        self.fileWriteImageToFloppyAction.triggered.connect(self.write_image_to_floppy)
        self.fileMenu.addAction(self.fileWriteImageToFloppyAction)

        self.fileMenu.addSeparator()
        self.fileAutoWriteProtectAction = QAction("Auto Write-Protect", self)
        self.fileAutoWriteProtectAction.setCheckable(True)
        self.fileAutoWriteProtectAction.setChecked(self._auto_write_protect_on_load())
        self.fileAutoWriteProtectAction.setToolTip(
            "When enabled, newly read floppies and newly opened images start with original writes protected."
        )
        self.fileAutoWriteProtectAction.toggled.connect(self.toggle_auto_write_protect_on_load)
        self.fileMenu.addAction(self.fileAutoWriteProtectAction)
        self.fileMenu.addAction(self.fileCreateTagSidecarsAction)

        self.utilitiesMenu = self.menuBar().addMenu("&Utilities")
        self.utilitiesSongListAction = QAction("Song List...", self)
        self.utilitiesSongListAction.triggered.connect(self.show_song_list_tool)
        self.utilitiesMenu.addAction(self.utilitiesSongListAction)

        self.utilitiesFileInspectionAction = QAction("File Inspection...", self)
        self.utilitiesFileInspectionAction.triggered.connect(lambda _checked=False: self.show_file_inspection_tool())
        self.utilitiesMenu.addAction(self.utilitiesFileInspectionAction)

        self.utilitiesMenu.addSeparator()

        self.utilitiesRecoverImageAction = QAction("Recover Damaged Image...", self)
        self.utilitiesRecoverImageAction.setToolTip(
            "Recover song data from a damaged floppy image and open the result as a new editable image copy."
        )
        self.utilitiesRecoverImageAction.triggered.connect(self.recover_damaged_image_dialog)

        self.utilitiesRenameAction = QAction("Rename All to DOS 8.3", self)
        self.utilitiesRenameAction.triggered.connect(self.rename_all_for_disk)
        self.utilitiesMenu.addAction(self.utilitiesRenameAction)

        self.utilitiesSmfAction = QAction("Convert All SMF1 to SMF0", self)
        self.utilitiesSmfAction.triggered.connect(self.convert_all_to_type0)
        self.utilitiesMenu.addAction(self.utilitiesSmfAction)

        self.utilitiesEseqToMidiAction = QAction("Convert All E-SEQ to MIDI", self)
        self.utilitiesEseqToMidiAction.triggered.connect(self.convert_all_eseq_to_midi)

        self.utilitiesMidiToEseqAction = QAction("Convert All MIDI to E-SEQ", self)
        self.utilitiesMidiToEseqAction.triggered.connect(self.convert_all_midi_to_eseq)
        self.utilitiesMenu.addAction(self.utilitiesMidiToEseqAction)
        self.utilitiesMenu.addAction(self.utilitiesEseqToMidiAction)

        self.utilitiesMenu.addSeparator()
        self.utilitiesMenu.addAction(self.utilitiesRecoverImageAction)

        self.utilitiesFormatFloppyAction = QAction("Format Floppy Disk...", self)
        self.utilitiesFormatFloppyAction.setToolTip(
            "Format a physical floppy disk for Yamaha Disklavier use."
        )
        self.utilitiesFormatFloppyAction.triggered.connect(self.format_disklavier_floppy)
        self.utilitiesMenu.addAction(self.utilitiesFormatFloppyAction)

        help_menu = self.menuBar().addMenu("&Help")
        self.helpCheckUpdatesAction = QAction("Check for Updates...", self)
        self.helpCheckUpdatesAction.triggered.connect(lambda: self.check_for_updates(manual=True))
        help_menu.addAction(self.helpCheckUpdatesAction)

        self.helpCheckUpdatesAtStartupAction = QAction("Check for Updates at Startup", self)
        self.helpCheckUpdatesAtStartupAction.setCheckable(True)
        self.helpCheckUpdatesAtStartupAction.setChecked(
            self.settings.value(self.SETTING_CHECK_UPDATES_AT_STARTUP, True, type=bool)
        )
        self.helpCheckUpdatesAtStartupAction.toggled.connect(self.toggle_update_checks_at_startup)
        help_menu.addAction(self.helpCheckUpdatesAtStartupAction)
        help_menu.addSeparator()

        welcome_action = QAction("Show Welcome Screen", self)
        welcome_action.triggered.connect(self.show_welcome_dialog)
        help_menu.addAction(welcome_action)

        disclaimer_action = QAction("Disclaimer", self)
        disclaimer_action.triggered.connect(self.show_disclaimer_dialog)
        help_menu.addAction(disclaimer_action)

        about_action = QAction("About APS MIDI Prep Tool", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
        self._update_compat_warning_ui()
        self.table.setColumnHidden(6, False)

        # Set mouse tracking and install an event filter on the table viewport.
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)
        self._update_floppy_save_option_ui()
        self._update_menu_actions()

    def eventFilter(self, obj, event):
        if obj is self.table.viewport():
            if event.type() == QEvent.Resize:
                self._resize_table_columns_to_fill()
            elif event.type() == QEvent.MouseMove:
                pos = event.position().toPoint()
                index = self.table.indexAt(pos)
                # When hovering over the Title cell, show a pointing hand.
                if index.isValid() and index.column() == 4:
                    self.table.viewport().setCursor(Qt.PointingHandCursor)
                else:
                    self.table.viewport().setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_apply_initial_column_sizing:
            self._resize_table_columns_to_fill()
            self._did_apply_initial_column_sizing = True

    def schedule_startup_update_check(self):
        if self.updateCheckStartupScheduled:
            return
        self.updateCheckStartupScheduled = True
        if self.settings.value(self.SETTING_CHECK_UPDATES_AT_STARTUP, True, type=bool):
            QTimer.singleShot(900, lambda: self.check_for_updates(manual=False))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_table_columns_to_fill()

    def _center_child_dialog(self, dialog):
        center_dialog_on_parent(dialog, self)

    def _exec_child_dialog(self, dialog):
        dialog.setWindowModality(Qt.WindowModal)
        self._center_child_dialog(dialog)
        return dialog.exec()

    def _progress_dialog_title(self, dialog):
        current_title = (dialog.windowTitle() or "").strip()
        generic_titles = {
            "",
            APP_NAME,
            APP_SETTINGS_APP,
            self.windowTitle(),
            (QApplication.applicationName() or "").strip(),
        }
        if current_title not in generic_titles:
            return current_title

        label_text = (dialog.labelText() or "").strip()
        label_text = re.sub(r"\s+", " ", label_text).strip()
        label_text = re.sub(r"\.{2,}$", "", label_text).rstrip(".").strip()
        return label_text or APP_NAME

    def _prepare_progress_dialog(self, dialog):
        dialog.setWindowTitle(self._progress_dialog_title(dialog))
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        self._center_child_dialog(dialog)
        return dialog

    def _clean_error_detail(self, detail):
        text = str(detail or "").strip()
        return text or "No detailed error message was provided."

    def _guidance_for_error_detail(self, detail):
        text = str(detail or "")
        lower = text.lower()
        if not text:
            return ""
        if "mtools" in lower or any(command in lower for command in ("mformat", "mcopy", "mdir", "mdel", "mren")):
            return (
                "Install mtools, or use an AppImage build that bundles mtools. "
                "Then try the operation again."
            )
        if "greaseweazle cli" in lower or "greaseweazle" in lower and "not found" in lower:
            return (
                "Install the Greaseweazle CLI, or use an AppImage build that bundles it. "
                "The command should be available as 'gw' or 'greaseweazle'."
            )
        if "permission denied" in lower or "access is denied" in lower:
            return (
                "Check that the file or floppy device is writable by your user. "
                "On Linux, unmount the disk before direct floppy writes and make sure your user has device access."
            )
        if "device or resource busy" in lower or "text file busy" in lower or "could not lock" in lower:
            return "Close file managers or other programs using the disk, unmount it if needed, and try again."
        if "disk full" in lower or "no directory slots" in lower or "too large to fit" in lower:
            return "Remove files, choose a larger disk format, or split the set across multiple images."
        if "already exists" in lower:
            return "Choose a different output name, or rename/remove the existing file and try again."
        if "unsupported image" in lower or "unsupported output image" in lower:
            return "Use a supported floppy image type such as IMG, BIN, IMA, or HFE."
        if "fat12" in lower or "boot sector" in lower:
            return "Make sure the source is an IBM/Yamaha FAT12 floppy or image. For protected Yamaha disks, try Read Floppy or a Greaseweazle capture."
        return ""

    def _show_operation_error(self, title, summary, detail=None, *, guidance=None):
        detail_text = self._clean_error_detail(detail)
        message = summary.rstrip(".") + "."
        if detail_text:
            message += f"\n\nDetails: {detail_text}"
        guidance_text = guidance if guidance is not None else self._guidance_for_error_detail(detail_text)
        if guidance_text:
            message += f"\n\n{guidance_text.rstrip('.')}."
        QMessageBox.critical(self, title, message)

    def _limited_message_list(self, messages, *, max_rows=10):
        cleaned = [str(message).strip() for message in messages if str(message).strip()]
        preview = "\n".join(cleaned[:max_rows])
        if len(cleaned) > max_rows:
            preview += f"\n...and {len(cleaned) - max_rows} more."
        return preview or "No detailed error message was provided."

    def _show_error_list(self, title, summary, errors, *, max_rows=10, warning=False, guidance=""):
        details = self._limited_message_list(errors, max_rows=max_rows)
        message = summary.rstrip(".") + f".\n\n{details}"
        if guidance:
            message += f"\n\n{guidance.rstrip('.')}."
        if warning:
            QMessageBox.warning(self, title, message)
        else:
            QMessageBox.critical(self, title, message)

    def _apply_stage_progress(self, dialog, step, total, message):
        if dialog is None:
            return
        if total and total > 0:
            if dialog.maximum() != total:
                dialog.setRange(0, total)
            dialog.setValue(max(0, min(step, total)))
        else:
            dialog.setRange(0, 0)
        dialog.setLabelText(message)
        QApplication.processEvents()

    def _set_disk_load_busy(self, busy):
        is_busy = bool(busy)
        self.choose_button.setEnabled(not is_busy)
        self.open_image_button.setEnabled(not is_busy)
        self.read_floppy_button.setEnabled(not is_busy)
        self._update_menu_actions()

    def _set_disk_write_busy(self, busy):
        is_busy = bool(busy)
        self._set_disk_load_busy(is_busy)
        for widget_name in (
            "clearButton",
            "saveButton",
            "saveAsButton",
            "saveAsImageButton",
            "renameAllButton",
            "convertType0Button",
            "convertEseqToMidiButton",
            "convertMidiToEseqButton",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(not is_busy)
        if hasattr(self, "table"):
            self.table.setEnabled(not is_busy)
        if not is_busy:
            self._update_floppy_save_option_ui()
            if self.is_image_mode():
                self._refresh_image_mode_action_state()
            else:
                self._refresh_regular_mode_action_state()
        else:
            self._update_menu_actions()

    def _disk_worker_busy(self):
        return (
            self.diskLoadWorker is not None
            or self.diskRecoveryWorker is not None
            or self.diskFormatWorker is not None
            or self.diskCommitWorker is not None
            or self.diskWriteTargetWorker is not None
        )

    def _message_indicates_cancelled(self, message):
        text = str(message or "").strip().lower()
        return "cancelled" in text or "canceled" in text

    def _start_disk_load_worker(
        self,
        *,
        load_kind,
        source,
        progress_title,
        progress_total,
        initial_message,
        final_message,
        failure_title,
        offer_greaseweazle_capture=False,
    ):
        if self._disk_worker_busy():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return False

        progress_dialog = QProgressDialog(progress_title, "Cancel", 0, progress_total, self)
        progress_dialog.setWindowTitle(progress_title)
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        self._apply_stage_progress(progress_dialog, 0, progress_total, initial_message)

        worker = DiskSessionLoadWorker(
            load_kind,
            source,
            final_total=progress_total,
            final_message=final_message,
            parent=self,
        )
        worker.progressChanged.connect(
            lambda step, total, message, dialog=progress_dialog: self._apply_stage_progress(
                dialog, step, total, message
            )
        )
        progress_dialog.canceled.connect(worker.cancel)
        progress_dialog.canceled.connect(
            lambda dialog=progress_dialog: dialog.setLabelText("Cancelling floppy operation...")
        )
        worker.sessionLoaded.connect(self._on_disk_load_success)
        worker.captureReady.connect(self._on_greaseweazle_capture_ready)
        worker.loadFailedWithDetails.connect(self._on_disk_load_failure_with_details)
        worker.loadFailed.connect(self._on_disk_load_failure)
        worker.operationCancelled.connect(self._on_disk_load_cancelled)
        worker.finished.connect(self._on_disk_load_finished)

        self.diskLoadWorker = worker
        self.diskLoadProgressDialog = progress_dialog
        self.diskLoadFailureTitle = failure_title
        self.diskLoadShouldOfferCapture = bool(offer_greaseweazle_capture)
        recovery_load_kind = load_kind
        recovery_source = source
        source_label = "floppy disk" if load_kind.startswith("floppy") else "floppy image"
        if load_kind == "floppy_gw_capture_only" and isinstance(source, dict):
            recovery_load_kind = "floppy_gw"
            recovery_source = source.get("gw_source")
            source_label = "Greaseweazle floppy"
        self.diskLoadContext = {
            "load_kind": recovery_load_kind,
            "source": recovery_source,
            "failure_title": failure_title,
            "source_label": source_label,
        }
        self.pendingGwConversionDetails = None
        self.pendingGwCapture = None
        self.pendingDiskRecoveryRequest = None
        self._set_disk_load_busy(True)
        worker.start()
        return True

    def _on_disk_load_success(self, session, listing):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None

        if self._offer_mpc_seq_conversion_for_loaded_session(session, listing):
            self.pendingFloppyReadConvertToMidi = False
            return

        try:
            self._activate_disk_session(session, listing)
        except Exception as exc:
            try:
                session.cleanup()
            except Exception:
                pass
            self._show_operation_error(
                self.diskLoadFailureTitle,
                "The disk or image was read, but the app could not open it for editing",
                exc,
            )
            return

        if self.diskLoadShouldOfferCapture:
            self._offer_save_greaseweazle_capture()
        if self.pendingFloppyReadConvertToMidi:
            QTimer.singleShot(0, self._convert_loaded_floppy_to_midi_after_read)
        else:
            QTimer.singleShot(0, self._offer_post_load_sequence_conversions)

    def _offer_post_load_sequence_conversions(self):
        if self._offer_v50_nseq_conversion_if_available():
            return
        self._offer_mpc_seq_conversion_if_available()

    def _v50_nseq_sequence_summary_for_image(self, image_path):
        try:
            data = Path(image_path).read_bytes()
        except OSError:
            return None
        if v50_nseq_to_midi.V50SEQ_SIGNATURE not in data:
            return None
        if not v50_nseq_to_midi.looks_like_v50_disk_image(data):
            return None

        fat = data[
            v50_nseq_to_midi.FAT_OFFSET:
            v50_nseq_to_midi.FAT_OFFSET
            + v50_nseq_to_midi.SECTORS_PER_FAT * v50_nseq_to_midi.BYTES_PER_SECTOR
        ]
        sequence_files = []
        song_names = []
        slot_count = 0
        for entry in v50_nseq_to_midi.parse_root_directory(data):
            if entry.deleted or entry.attr & 0x08 or entry.attr & 0x10:
                continue
            if entry.size <= 0 or entry.start_cluster < 2:
                continue
            try:
                filedata = v50_nseq_to_midi.extract_file_from_image(data, fat, entry)
            except Exception:
                continue
            if v50_nseq_to_midi.V50SEQ_SIGNATURE not in filedata:
                continue
            slots = [
                slot
                for slot in v50_nseq_to_midi.scan_v50_sequence_slots(filedata)
                if slot.tracks
            ]
            if not slots:
                continue
            sequence_files.append(entry.display_name)
            slot_count += len(slots)
            song_names.extend(slot.song_name.strip() for slot in slots if slot.song_name.strip())

        if slot_count <= 0:
            return None
        return {
            "file_count": len(sequence_files),
            "slot_count": slot_count,
            "sequence_files": sequence_files,
            "song_names": song_names,
        }

    def _offer_v50_nseq_conversion_if_available(self):
        if self.image_session is None:
            return False
        image_path = getattr(self.image_session, "working_img_path", "")
        if not image_path or image_path == self.v50NseqPromptedSessionPath:
            return False
        self.v50NseqPromptedSessionPath = image_path
        summary = self._v50_nseq_sequence_summary_for_image(image_path)
        if not summary:
            return False

        if not self._prompt_for_v50_nseq_conversion(summary):
            return True

        self._convert_current_v50_nseq_to_midi_mode(summary)
        return True

    def _prompt_for_v50_nseq_conversion(self, summary):
        slot_count = int((summary or {}).get("slot_count") or 0)
        file_count = int((summary or {}).get("file_count") or 0)
        preview_names = [name for name in (summary or {}).get("song_names", [])[:6] if name]
        detail = (
            f"Found {slot_count} sequence(s) in {file_count} V50/SY77 file(s).\n\n"
            "Convert these sequences to Standard MIDI files with routed channels and program changes, "
            "then open the MIDI files in the list?"
        )
        if preview_names:
            detail += "\n\nDetected songs:\n" + "\n".join(f"- {name}" for name in preview_names)
            if len((summary or {}).get("song_names", [])) > len(preview_names):
                detail += "\n..."

        prompt = QMessageBox(self)
        apply_window_icon(prompt)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle("V50/SY77 Sequences Detected")
        prompt.setText("This disk appears to contain Yamaha V50/SY77 NSEQ sequences.")
        prompt.setInformativeText(detail)
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.Yes)
        prompt.button(QMessageBox.Yes).setText("Convert to MIDI")
        prompt.button(QMessageBox.No).setText("Not Now")
        return self._exec_child_dialog(prompt) == QMessageBox.Yes

    def _convert_v50_nseq_image_to_midi_mode(self, source_image, source_name, summary, *, reset_current_image=False):
        if not source_image or not os.path.isfile(source_image):
            QMessageBox.warning(
                self,
                "Conversion Unavailable",
                "The source image is no longer available for V50/SY77 conversion.",
            )
            return

        output_root = os.path.join(self._ensure_midi_scratch_dir(), f"v50_nseq_{uuid.uuid4().hex}")
        progress_dialog = QProgressDialog("Converting V50/SY77 sequences to MIDI...", None, 0, 0, self)
        progress_dialog.setWindowTitle("Converting V50/SY77")
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setCancelButton(None)
        progress_dialog.show()
        QApplication.processEvents()

        try:
            args = argparse.Namespace(
                mode="image",
                ppq=96,
                zero_duration_ticks=1,
                include_events_json=False,
                no_midi=False,
                no_raw=True,
                no_embed_meta=False,
                program_mode="gm-fallback",
                initial_programs="",
                initial_program_overrides=v50_nseq_to_midi.parse_initial_program_overrides(""),
                fallback_tempo=120,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                v50_nseq_to_midi.process_input(Path(source_image), Path(output_root), args)
            midi_dir = Path(output_root) / "midi"
            midi_paths = sorted(str(path) for path in midi_dir.glob("*.mid"))
        except Exception as exc:
            progress_dialog.close()
            self._show_operation_error(
                "V50/SY77 Conversion Failed",
                "The app could not convert the detected V50/SY77 sequences to MIDI",
                exc,
                guidance="The source disk image is still open; try saving an image copy and converting again",
            )
            return

        progress_dialog.close()
        if not midi_paths:
            QMessageBox.information(
                self,
                "No MIDI Files Created",
                "The V50/SY77 converter did not create any MIDI files.",
            )
            return

        converted_count = len(midi_paths)
        slot_count = int((summary or {}).get("slot_count") or converted_count)
        if reset_current_image and self.image_session is not None:
            self._reset_image_state()
        status_text = (
            f"Converted {converted_count} MIDI file(s) from {slot_count} V50/SY77 sequence(s) in {source_name} "
            "with routed channels and program changes.\n"
            "The source disk/image was not modified. Use Save As to choose a permanent folder."
        )
        self._load_regular_files(midi_paths, status_text)

    def _convert_current_v50_nseq_to_midi_mode(self, summary):
        if self.image_session is None:
            return
        source_image = getattr(self.image_session, "working_img_path", "")
        source_name = getattr(self.image_session, "source_name", "V50/SY77 disk")
        self._convert_v50_nseq_image_to_midi_mode(
            source_image,
            source_name,
            summary,
            reset_current_image=True,
        )

    def _is_mpc_seq_path(self, file_path):
        return (
            bool(file_path)
            and os.path.isfile(file_path)
            and os.path.splitext(file_path)[1].lower() == ".seq"
        )

    def can_accept_mpc_seq_path(self, file_path):
        return self._is_mpc_seq_path(file_path)

    def _mpc_seq_file_paths_in_folder(self, directory):
        try:
            filenames = os.listdir(directory)
        except OSError:
            return []
        return sorted(
            (
                os.path.join(directory, filename)
                for filename in filenames
                if self._is_mpc_seq_path(os.path.join(directory, filename))
            ),
            key=lambda path: (os.path.basename(path).upper(), path.upper()),
        )

    def _mpc_seq_entries_from_listing(self, listing):
        return [
            entry
            for entry in getattr(listing, "entries", [])
            if os.path.splitext(entry.name)[1].lower() == ".seq"
        ]

    def _current_image_mpc_seq_entries(self):
        if self.image_session is None:
            return []
        try:
            listing = self.image_session.list_entries()
        except Exception:
            return []
        return self._mpc_seq_entries_from_listing(listing)

    def _unique_path_in_directory(self, directory, filename):
        directory = Path(directory)
        filename = os.path.basename(str(filename or "").strip()) or "sequence.SEQ"
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        stem = candidate.stem or "sequence"
        suffix = candidate.suffix
        counter = 2
        while True:
            candidate = directory / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _mpc_seq_input_specs(self, seq_paths):
        inputs = []
        for item in seq_paths or []:
            if isinstance(item, dict):
                path = item.get("path", "")
                output_stem = item.get("output_stem")
                label = item.get("label") or path
            else:
                path = item
                output_stem = None
                label = path
            if not self._is_mpc_seq_path(path):
                continue
            inputs.append(
                {
                    "path": os.path.abspath(path),
                    "output_stem": output_stem,
                    "label": label,
                }
            )
        return inputs

    def _extract_image_mpc_seq_entries(self, entries, session=None):
        session = session or self.image_session
        if session is None:
            return [], ["No disk or image is currently open."]
        seq_inputs = []
        failures = []
        for entry in entries:
            try:
                extracted_path = session.extract_file(entry.path)
                seq_inputs.append(
                    {
                        "path": extracted_path,
                        "output_stem": os.path.splitext(entry.name or "sequence")[0],
                        "label": entry.path,
                    }
                )
            except Exception as exc:
                failures.append(f"{entry.path}: {exc}")
        return seq_inputs, failures

    def _prompt_for_mpc_seq_conversion(self, seq_labels, source_label=""):
        labels = [str(label).strip() for label in (seq_labels or []) if str(label).strip()]
        count = len(labels)
        if count <= 0:
            return False
        detail = (
            f"Found {count} MPC .SEQ file(s)"
            + (f" in {source_label}" if source_label else "")
            + ".\n\nConvert them to Standard MIDI files and open the MIDI files in the list?"
        )
        preview = labels[:8]
        if preview:
            detail += "\n\nDetected files:\n" + "\n".join(f"- {os.path.basename(label)}" for label in preview)
            if len(labels) > len(preview):
                detail += "\n..."

        prompt = QMessageBox(self)
        apply_window_icon(prompt)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle("MPC .SEQ Files Detected")
        prompt.setText("These files appear to be Akai MPC sequence files.")
        prompt.setInformativeText(detail)
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.Yes)
        prompt.button(QMessageBox.Yes).setText("Convert to MIDI")
        prompt.button(QMessageBox.No).setText("Not Now")
        return self._exec_child_dialog(prompt) == QMessageBox.Yes

    def _convert_mpc_seq_paths_to_midi_paths(self, seq_paths):
        seq_inputs = self._mpc_seq_input_specs(seq_paths)
        if not seq_inputs:
            return [], [], [], False

        output_root = Path(self._ensure_midi_scratch_dir()) / f"mpc_seq_{uuid.uuid4().hex}"
        staged_output_dir = output_root / "midi"
        staged_output_dir.mkdir(parents=True, exist_ok=True)
        reports = []
        failures = []
        midi_paths = []
        cancelled = False

        progress_dialog = QProgressDialog(
            "Converting MPC .SEQ files to MIDI...",
            "Cancel",
            0,
            len(seq_inputs),
            self,
        )
        progress_dialog.setWindowTitle("Converting MPC .SEQ")
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.show()
        QApplication.processEvents()

        try:
            for index, seq_input in enumerate(seq_inputs, start=1):
                seq_path = seq_input["path"]
                label = seq_input.get("label") or seq_path
                progress_dialog.setValue(index - 1)
                progress_dialog.setLabelText(f"Converting {os.path.basename(label)}...")
                QApplication.processEvents()
                if progress_dialog.wasCanceled():
                    cancelled = True
                    break
                try:
                    per_file_dir = output_root / f"{index:03d}_{uuid.uuid4().hex}"
                    report = mpc_seq_to_midi.convert_one(
                        Path(seq_path),
                        per_file_dir,
                        output_stem=seq_input.get("output_stem"),
                    )
                    source_output = Path(report.output)
                    if not source_output.is_file():
                        raise RuntimeError("The converter did not create a MIDI file.")
                    target_output = self._unique_path_in_directory(staged_output_dir, source_output.name)
                    shutil.move(str(source_output), str(target_output))
                    report.input = label
                    report.output = str(target_output)
                    reports.append(report)
                    midi_paths.append(str(target_output))
                except Exception as exc:
                    failures.append(f"{os.path.basename(label)}: {exc}")
        finally:
            progress_dialog.setValue(len(seq_inputs))
            progress_dialog.close()

        return midi_paths, reports, failures, cancelled

    def _append_regular_files_from_paths(self, file_paths):
        file_paths = [path for path in (file_paths or []) if os.path.isfile(path)]
        if not file_paths:
            return
        self.prepare_regular_file_drop(file_paths)
        results = []
        for file_path in file_paths:
            result = self.add_regular_file_from_drop(file_path)
            results.append(result)
            if result and result.get("status") == "cancelled":
                break
        self.finish_regular_file_drop(results)

    def _convert_mpc_seq_files_to_midi_mode(
        self,
        seq_paths,
        source_name,
        *,
        reset_current_image=False,
        confirm_image_exit=True,
        append=False,
        extra_regular_paths=None,
        pre_conversion_warnings=None,
    ):
        seq_inputs = self._mpc_seq_input_specs(seq_paths)
        extra_regular_paths = [
            os.path.abspath(path)
            for path in (extra_regular_paths or [])
            if os.path.isfile(path) and self._regular_drop_file_kind(path) in {"midi", "eseq", "pianodir"}
        ]
        warnings = list(pre_conversion_warnings or [])
        if not seq_inputs:
            return False
        if (
            reset_current_image
            and confirm_image_exit
            and self.image_session is not None
            and not self._confirm_discard_image_changes()
        ):
            return False

        midi_paths, reports, failures, cancelled = self._convert_mpc_seq_paths_to_midi_paths(seq_inputs)
        warnings.extend(failures)

        if not midi_paths:
            if warnings:
                self._show_error_list(
                    "MPC .SEQ Conversion Failed",
                    "The app could not convert the selected MPC .SEQ files to MIDI",
                    warnings,
                    warning=True,
                    guidance="The source files were not modified",
                )
            elif cancelled:
                self.status_label.setText("MPC .SEQ conversion cancelled. No files were changed.")
            return False

        if reset_current_image and self.image_session is not None:
            self._reset_image_state()

        converted_count = len(midi_paths)
        source_count = len(seq_inputs)
        status_text = (
            f"Converted {converted_count} MIDI file(s) from {source_count} MPC .SEQ file(s)"
            + (f" in {source_name}" if source_name else "")
            + ".\nThe source files were not modified. Use Save As to choose a permanent folder."
        )
        if cancelled:
            status_text += "\nConversion was cancelled after the files listed above were created."

        files_to_load = extra_regular_paths + midi_paths
        if append:
            self._append_regular_files_from_paths(files_to_load)
            existing_status = self.status_label.text().strip()
            self.status_label.setText(
                status_text if not existing_status else status_text + "\n" + existing_status
            )
        else:
            self._load_regular_files(files_to_load, status_text)

        report_warnings = []
        for report in reports:
            for warning in report.warnings:
                report_warnings.append(f"{os.path.basename(report.input)}: {warning}")
        warnings.extend(report_warnings)
        if warnings:
            self._show_error_list(
                "Some MPC .SEQ Files Need Review",
                "Some MPC .SEQ files were converted with warnings",
                warnings,
                warning=True,
                guidance="Review the converted MIDI files before using them for preservation or playback",
            )
        return True

    def _offer_mpc_seq_conversion_for_loaded_session(self, session, listing):
        entries = self._mpc_seq_entries_from_listing(listing)
        if not entries:
            return False

        image_path = getattr(session, "working_img_path", "")
        if image_path:
            self.mpcSeqPromptedSessionPath = image_path
        source_name = getattr(session, "source_name", "disk or image")
        if not self._prompt_for_mpc_seq_conversion([entry.path for entry in entries], source_name):
            return False

        self._cleanup_midi_scratch_dir()
        seq_paths, extraction_failures = self._extract_image_mpc_seq_entries(entries, session=session)
        if not seq_paths:
            self._show_error_list(
                "MPC .SEQ Conversion Failed",
                "The app could not extract the detected MPC .SEQ files from the disk or image",
                extraction_failures or ["No .SEQ files could be extracted."],
                warning=True,
                guidance="The source disk or image was not modified",
            )
            return False

        if self.image_session is not None:
            self._reset_image_state()

        converted = self._convert_mpc_seq_files_to_midi_mode(
            seq_paths,
            source_name,
            pre_conversion_warnings=extraction_failures,
        )
        if not converted:
            return False

        try:
            session.cleanup()
        except Exception:
            pass
        return True

    def _offer_mpc_seq_conversion_if_available(self):
        if self.image_session is None:
            return False
        image_path = getattr(self.image_session, "working_img_path", "")
        if not image_path or image_path == self.mpcSeqPromptedSessionPath:
            return False
        self.mpcSeqPromptedSessionPath = image_path

        entries = self._current_image_mpc_seq_entries()
        if not entries:
            return False

        source_name = getattr(self.image_session, "source_name", "disk or image")
        if not self._prompt_for_mpc_seq_conversion([entry.path for entry in entries], source_name):
            return True

        seq_paths, extraction_failures = self._extract_image_mpc_seq_entries(entries)
        if not seq_paths:
            self._show_error_list(
                "MPC .SEQ Conversion Failed",
                "The app could not extract the detected MPC .SEQ files from the disk or image",
                extraction_failures or ["No .SEQ files could be extracted."],
                warning=True,
                guidance="The source disk or image was not modified",
            )
            return True
        self._convert_mpc_seq_files_to_midi_mode(
            seq_paths,
            source_name,
            reset_current_image=True,
            confirm_image_exit=False,
            pre_conversion_warnings=extraction_failures,
        )
        return True

    def handle_mpc_seq_file_drop(self, file_paths):
        seq_paths = [path for path in (file_paths or []) if self._is_mpc_seq_path(path)]
        if not seq_paths:
            return False

        extra_regular_paths = []
        if not self.is_image_mode():
            seq_path_set = {os.path.abspath(path) for path in seq_paths}
            extra_regular_paths = [
                path
                for path in (file_paths or [])
                if os.path.abspath(path) not in seq_path_set
                and self._regular_drop_file_kind(path) in {"midi", "eseq", "pianodir"}
            ]

        if not self._prompt_for_mpc_seq_conversion(
            [os.path.basename(path) for path in seq_paths],
            "the dropped files",
        ):
            return False

        return self._convert_mpc_seq_files_to_midi_mode(
            seq_paths,
            "the dropped files",
            reset_current_image=self.is_image_mode(),
            append=not self.is_image_mode(),
            extra_regular_paths=extra_regular_paths,
        )

    def _on_greaseweazle_capture_ready(self, payload):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None
        self.pendingGwCapture = dict(payload or {})
        QTimer.singleShot(25, self._handle_pending_greaseweazle_capture_if_ready)

    def _handle_pending_greaseweazle_capture_if_ready(self):
        if not self.pendingGwCapture:
            return
        if self.diskLoadWorker is not None:
            QTimer.singleShot(25, self._handle_pending_greaseweazle_capture_if_ready)
            return
        payload = self.pendingGwCapture
        self.pendingGwCapture = None
        self.diskLoadContext = {}
        self._handle_greaseweazle_capture_ready(payload)

    def _handle_greaseweazle_capture_ready(self, payload):
        payload = dict(payload or {})
        capture = payload.get("capture")
        gw_source = getattr(capture, "gw_source", None)
        capture_path = getattr(capture, "capture_path", "")
        if not isinstance(gw_source, GreaseweazleFloppySource) or not capture_path:
            self.pendingFloppyReadConvertToMidi = False
            self._show_operation_error(
                "Greaseweazle Capture Failed",
                "The Greaseweazle SCP capture could not be prepared for saving",
                "No completed capture was available.",
            )
            return

        drive_name = gw_source.drive.lower()
        default_path = os.path.join(
            os.path.expanduser("~"),
            f"gw_drive_{drive_name}_archival.scp",
        )
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Raw SCP Capture",
            default_path,
            "SCP flux capture (*.scp *.SCP)",
        )
        if not output_path:
            capture.cleanup()
            self.pendingFloppyReadConvertToMidi = False
            self.status_label.setText("Greaseweazle capture was not saved; opening cancelled.")
            return
        if image_extension(output_path) != "scp":
            output_path = f"{output_path}.scp"

        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            shutil.copy2(capture_path, output_path)
        except Exception as exc:
            capture.cleanup()
            self.pendingFloppyReadConvertToMidi = False
            self._show_operation_error(
                "SCP Save Failed",
                f"Could not save the raw Greaseweazle capture to {os.path.basename(output_path)}",
                exc,
            )
            return
        finally:
            if os.path.abspath(output_path) != os.path.abspath(capture_path):
                capture.cleanup()

        if payload.get("recover_after_capture"):
            self._start_disk_recovery_worker(
                {
                    "load_kind": "image",
                    "source": ImageRecoverySource(output_path, gw_source.disk_format),
                    "failure_title": "Floppy Recovery Failed",
                    "source_label": f"saved Greaseweazle capture ({gw_source.disk_format.label})",
                    "progress_title": "Recovering Saved Greaseweazle Capture",
                }
            )
            return

        self._start_disk_load_worker(
            load_kind="floppy_gw_capture",
            source={
                "gw_source": gw_source,
                "capture_path": output_path,
                "disk_format": gw_source.disk_format,
            },
            progress_title=f"Converting Greaseweazle Capture ({gw_source.disk_format.label})",
            progress_total=4,
            initial_message=f"Converting saved SCP capture as {gw_source.disk_format.label}...",
            final_message="Opening floppy contents...",
            failure_title="Greaseweazle Conversion Failed",
        )

    def _on_disk_load_failure(self, message):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None
        if self._message_indicates_cancelled(message):
            self._on_disk_load_cancelled(message)
            return
        recovery_request = dict(self.diskLoadContext)
        source = recovery_request.get("source")
        source_path = getattr(source, "path", source if isinstance(source, str) else "")
        if source_path and os.path.isfile(source_path):
            summary = self._v50_nseq_sequence_summary_for_image(source_path)
            if summary and self._prompt_for_v50_nseq_conversion(summary):
                self.pendingDiskRecoveryRequest = None
                self.diskLoadContext = {}
                self._convert_v50_nseq_image_to_midi_mode(
                    source_path,
                    os.path.basename(source_path) or "V50/SY77 image",
                    summary,
                    reset_current_image=False,
                )
                return
        recovery_request["message"] = message
        self.pendingDiskRecoveryRequest = recovery_request

    def _build_greaseweazle_sector_table(self, sector_map, parent):
        return GreaseweazleSectorGrid(sector_map, parent)

    def _choose_greaseweazle_retry_format(self, details):
        details = dict(details or {})
        sector_map = details.get("sector_map") or {}
        current_format = details.get("disk_format")
        suggested_format = details.get("suggested_format")
        reason = details.get("reason") or ""

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Greaseweazle Conversion Report")
        dialog.setModal(True)
        dialog.setMinimumWidth(720)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        found = sector_map.get("found")
        total = sector_map.get("total")
        bad = int(sector_map.get("bad") or 0)
        has_sector_failures = bool(sector_map.get("has_failures") or bad > 0)
        if reason == "format_mismatch" and not has_sector_failures:
            summary_text = "The SCP capture read successfully, but the selected disk format appears to be wrong."
            if current_format is not None:
                summary_text += f"\nSelected format: {current_format.label}."
            if suggested_format is not None:
                summary_text += f"\nDetected format: {suggested_format.label}."
        elif found is not None and total is not None:
            summary_text = (
                f"Greaseweazle found {found} of {total} expected sector(s). "
                f"{bad} sector position(s) need attention."
            )
            if current_format is not None:
                summary_text += f"\nSelected format: {current_format.label}."
        else:
            summary_text = "Greaseweazle could not convert the capture with the selected format."
            if current_format is not None:
                summary_text += f"\nSelected format: {current_format.label}."
        summary = QLabel(summary_text)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        detail_text = re.sub(r"\s+", " ", str(details.get("message", "") or "")).strip()
        if detail_text and len(detail_text) <= 400:
            detail_label = QLabel(detail_text)
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label)

        if has_sector_failures:
            legend = QLabel("Green dots read successfully. Red dots indicate missing or failed sector positions.")
            legend.setWordWrap(True)
            layout.addWidget(legend)
            layout.addWidget(self._build_greaseweazle_sector_table(sector_map, dialog))

        format_combo = QComboBox(dialog)
        failed_key = current_format.key if current_format is not None else ""
        selected_index = 0
        for index, disk_format in enumerate(DISK_FORMATS):
            label = f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})"
            if disk_format.key == failed_key:
                label += " - current"
            format_combo.addItem(label, disk_format)
            if suggested_format is not None and disk_format.key == suggested_format.key:
                selected_index = index
            elif suggested_format is None and disk_format.key != failed_key and selected_index == 0:
                selected_index = index
        format_combo.setCurrentIndex(selected_index)

        form_grid = self._make_dialog_form_grid()
        format_label = self._add_dialog_form_row(form_grid, 0, "Try format:", format_combo)
        self._align_dialog_form_labels([format_label])
        layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("Try Selected Format")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None
        return format_combo.currentData()

    def _on_disk_load_failure_with_details(self, details):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None
        details = dict(details or {})
        message = details.get("message", "")
        if self._message_indicates_cancelled(message):
            self._on_disk_load_cancelled(message)
            return
        if details.get("type") != "greaseweazle_conversion":
            self._on_disk_load_failure(message)
            return
        self.pendingGwConversionDetails = details

    def _handle_greaseweazle_conversion_failure(self, details):
        details = dict(details or {})
        message = details.get("message", "")

        capture_path = details.get("capture_path") or ""
        if not capture_path or not os.path.isfile(capture_path):
            self.pendingFloppyReadConvertToMidi = False
            self._show_operation_error(
                "Greaseweazle Conversion Failed",
                "The Greaseweazle capture could not be converted",
                message or "No saved SCP capture was available for retry.",
            )
            return

        retry_format = self._choose_greaseweazle_retry_format(details)
        if retry_format is None:
            self.pendingFloppyReadConvertToMidi = False
            self.status_label.setText(
                f"Greaseweazle conversion stopped. Raw capture saved at {capture_path}."
            )
            return

        original_source = details.get("source")
        if isinstance(original_source, dict):
            original_source = original_source.get("gw_source")
        if isinstance(original_source, GreaseweazleFloppySource):
            load_kind = "floppy_gw_capture"
            source = {
                "gw_source": original_source,
                "capture_path": capture_path,
                "disk_format": retry_format,
            }
        else:
            load_kind = "image"
            source = ImageLoadSource(capture_path, retry_format)

        self._start_disk_load_worker(
            load_kind=load_kind,
            source=source,
            progress_title=f"Converting Greaseweazle Capture ({retry_format.label})",
            progress_total=4,
            initial_message=f"Converting saved SCP capture as {retry_format.label}...",
            final_message="Opening floppy contents...",
            failure_title="Greaseweazle Conversion Failed",
        )

    def _on_disk_load_cancelled(self, _message):
        if self.diskLoadProgressDialog is not None:
            self.diskLoadProgressDialog.close()
            self.diskLoadProgressDialog = None
        self.status_label.setText("Disk operation cancelled.")
        self.pendingFloppyReadConvertToMidi = False
        self.pendingDiskRecoveryRequest = None
        self.pendingGwConversionDetails = None
        if self.pendingGwCapture:
            capture = self.pendingGwCapture.get("capture") if isinstance(self.pendingGwCapture, dict) else None
            if capture is not None:
                capture.cleanup()
        self.pendingGwCapture = None

    def _on_disk_load_finished(self):
        self._set_disk_load_busy(False)
        self.diskLoadShouldOfferCapture = False
        if self.diskLoadWorker is not None:
            self.diskLoadWorker.deleteLater()
            self.diskLoadWorker = None
        gw_conversion_details = self.pendingGwConversionDetails
        self.pendingGwConversionDetails = None
        gw_capture = self.pendingGwCapture
        self.pendingGwCapture = None
        recovery_request = self.pendingDiskRecoveryRequest
        self.pendingDiskRecoveryRequest = None
        if gw_conversion_details:
            self.diskLoadContext = {}
            QTimer.singleShot(
                0,
                lambda details=gw_conversion_details: self._handle_greaseweazle_conversion_failure(details),
            )
        elif gw_capture:
            self.diskLoadContext = {}
            QTimer.singleShot(
                0,
                lambda payload=gw_capture: self._handle_greaseweazle_capture_ready(payload),
            )
        elif recovery_request:
            QTimer.singleShot(0, lambda request=recovery_request: self._offer_disk_recovery(request))
        else:
            self.diskLoadContext = {}

    def _offer_disk_recovery(self, request):
        source_label = request.get("source_label", "disk or image")
        message = (
            f"The normal read failed for this {source_label}.\n\n"
            "APS MIDI Prep Tool can try recovery. It may take a long time. "
            "For a physical floppy, recovery must copy a full disk image first.\n\n"
            "Recovery will try Yamaha/FAT repairs, then scan the copied bytes for any MIDI, E-SEQ, "
            "or PIANODIR data it can salvage. Some filenames, order, titles, or parts of damaged songs may be missing.\n\n"
            "Try recovery now?"
        )
        reply = QMessageBox.question(
            self,
            "Try Disk Recovery?",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._show_operation_error(
                request.get("failure_title", self.diskLoadFailureTitle),
                "The disk or image could not be opened",
                request.get("message", ""),
            )
            self.pendingFloppyReadConvertToMidi = False
            self.diskLoadContext = {}
            return
        if request.get("load_kind") == "floppy_usb":
            source = self._wrap_floppy_recovery_source_with_format(request.get("source"))
            if source is None or not isinstance(source, FloppyRecoverySource):
                self.diskLoadContext = {}
                return
            request = dict(request)
            request["source"] = source
            request["source_label"] = f"floppy disk ({source.disk_format.label})"
            request["progress_title"] = "Recovering Floppy Data"
        self._start_disk_recovery_worker(request)

    def _start_disk_recovery_worker(self, request):
        if self._disk_worker_busy():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return False

        progress_text = request.get("progress_title", "Recovering Disk Data")
        progress_dialog = QProgressDialog(progress_text, "Cancel", 0, 100, self)
        progress_dialog.setWindowTitle(progress_text)
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        self._apply_stage_progress(progress_dialog, 0, 100, progress_text)

        worker = DiskSessionRecoveryWorker(
            request.get("load_kind"),
            request.get("source"),
            final_total=100,
            final_message="Opening recovered files...",
            parent=self,
        )
        worker.progressChanged.connect(
            lambda step, total, message, dialog=progress_dialog: self._apply_stage_progress(
                dialog, step, total, message
            )
        )
        progress_dialog.canceled.connect(worker.cancel)
        progress_dialog.canceled.connect(
            lambda dialog=progress_dialog: dialog.setLabelText("Cancelling disk recovery...")
        )
        worker.sessionRecovered.connect(self._on_disk_recovery_success)
        worker.recoveryFailed.connect(self._on_disk_recovery_failure)
        worker.operationCancelled.connect(self._on_disk_recovery_cancelled)
        worker.finished.connect(self._on_disk_recovery_finished)

        self.diskRecoveryWorker = worker
        self.diskRecoveryProgressDialog = progress_dialog
        self.diskRecoveryContext = dict(request)
        self._set_disk_load_busy(True)
        worker.start()
        return True

    def _on_disk_recovery_success(self, session, listing):
        if self.diskRecoveryProgressDialog is not None:
            self.diskRecoveryProgressDialog.close()
            self.diskRecoveryProgressDialog = None

        try:
            self._activate_disk_session(session, listing)
        except Exception as exc:
            try:
                session.cleanup()
            except Exception:
                pass
            self._show_operation_error(
                "Recovery Failed",
                "Recovery found data, but the app could not open it for editing",
                exc,
            )
            return

        song_count = sum(1 for entry in listing.entries if not is_pianodir_path(entry.path))
        QMessageBox.information(
            self,
            "Recovery Complete",
            (
                f"Recovered {len(listing.entries)} file(s), including {song_count} song file(s), into a new editable image copy.\n\n"
                "The original source was not modified. Review the recovered list, then use Save As Image or Write Current Image to Floppy to keep a clean copy."
            ),
        )
        if self.pendingFloppyReadConvertToMidi:
            QTimer.singleShot(0, self._convert_loaded_floppy_to_midi_after_read)

    def _on_disk_recovery_failure(self, message):
        if self.diskRecoveryProgressDialog is not None:
            self.diskRecoveryProgressDialog.close()
            self.diskRecoveryProgressDialog = None
        if self._message_indicates_cancelled(message):
            self._on_disk_recovery_cancelled(message)
            return
        original_message = self.diskRecoveryContext.get("message", "")
        if original_message:
            detail = f"Original read error: {original_message}\n\nRecovery error: {message}"
        else:
            detail = f"Recovery error: {message}"
        self._show_operation_error(
            "Recovery Failed",
            "The disk or image could not be opened or recovered",
            detail,
            guidance="If this is a physical floppy, try a different drive, a Greaseweazle capture with more retries, or a known-good disk image",
        )
        self.pendingFloppyReadConvertToMidi = False

    def _on_disk_recovery_cancelled(self, _message):
        if self.diskRecoveryProgressDialog is not None:
            self.diskRecoveryProgressDialog.close()
            self.diskRecoveryProgressDialog = None
        self.status_label.setText("Disk recovery cancelled.")
        self.pendingFloppyReadConvertToMidi = False

    def _on_disk_recovery_finished(self):
        self._set_disk_load_busy(False)
        self.diskLoadContext = {}
        self.diskRecoveryContext = {}
        if self.diskRecoveryWorker is not None:
            self.diskRecoveryWorker.deleteLater()
            self.diskRecoveryWorker = None

    def _confirm_with_optional_skip(self, *, setting_key, title, message, icon=QMessageBox.Warning):
        if self.settings.value(setting_key, False, type=bool):
            return True

        dialog = QMessageBox(self)
        apply_window_icon(dialog)
        dialog.setIcon(icon)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dialog.setDefaultButton(QMessageBox.No)
        skip_checkbox = QCheckBox("Do not remind me again for this action")
        dialog.setCheckBox(skip_checkbox)

        confirmed = self._exec_child_dialog(dialog) == QMessageBox.Yes
        if confirmed and skip_checkbox.isChecked():
            self.settings.setValue(setting_key, True)
        return confirmed

    def _file_modified_timestamp(self, path):
        try:
            return os.path.getmtime(path)
        except (TypeError, OSError):
            return None

    def _format_modified_timestamp(self, timestamp):
        if timestamp is None:
            return "Unknown"
        try:
            return datetime.datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError, OverflowError):
            return "Unknown"

    def _drop_conflict_display_name(self, value):
        text = str(value or "").strip()
        if ": " in text:
            text = text.rsplit(": ", 1)[-1]
        text = text.replace("\\", "/").rstrip("/")
        return os.path.basename(text) or text or "Unknown"

    def _prompt_drop_filename_conflict(
        self,
        *,
        filename,
        existing_label,
        existing_modified,
        incoming_path,
        incoming_modified,
        allow_do_all=True,
    ):
        dialog = QMessageBox(self)
        apply_window_icon(dialog)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("Replace Existing File?")
        display_filename = self._drop_conflict_display_name(filename)
        existing_display = self._drop_conflict_display_name(existing_label)
        incoming_display = self._drop_conflict_display_name(incoming_path)
        dialog.setText(f"A file named '{display_filename}' is already listed.")
        dialog.setInformativeText(
            "Listed file:\n"
            f"{existing_display}\n"
            f"Modified: {self._format_modified_timestamp(existing_modified)}\n\n"
            "Dropped file:\n"
            f"{incoming_display}\n"
            f"Modified: {self._format_modified_timestamp(incoming_modified)}"
        )
        replace_button = dialog.addButton("Use Dropped File", QMessageBox.AcceptRole)
        keep_button = dialog.addButton("Keep Listed File", QMessageBox.RejectRole)
        cancel_button = dialog.addButton("Cancel Drop", QMessageBox.DestructiveRole)
        dialog.setDefaultButton(keep_button)
        dialog.setEscapeButton(cancel_button)
        do_all_checkbox = None
        if allow_do_all:
            do_all_checkbox = QCheckBox("Do this for all filename conflicts")
            dialog.setCheckBox(do_all_checkbox)

        self._exec_child_dialog(dialog)
        clicked = dialog.clickedButton()
        do_all = bool(do_all_checkbox and do_all_checkbox.isChecked())
        if clicked == replace_button:
            return "replace", do_all
        if clicked == cancel_button:
            return "cancel", do_all
        return "keep", do_all

    def closeEvent(self, event):
        if self.is_image_mode() and not self._confirm_discard_image_changes():
            event.ignore()
            return
        self._reset_image_state()
        self._cleanup_midi_scratch_dir()
        super().closeEvent(event)

    def _handle_section_resized(self, logical_index, old_size, new_size):
        if self._is_adjusting_columns:
            return
        if logical_index in self.USER_RESIZABLE_EDGE_COLUMNS:
            self._manual_column_widths[logical_index] = new_size
            return
        if logical_index != 1:
            self._resize_table_columns_to_fill(preferred_column=logical_index)

    def _default_filename_column_width(self):
        metrics = QFontMetrics(self.table.font())
        sample = "M" * self.FILENAME_COLUMN_CHARS
        return max(
            self.table.horizontalHeader().minimumSectionSize(),
            metrics.horizontalAdvance(sample) + self.FILENAME_COLUMN_PADDING,
        )

    def _minimum_title_column_width(self):
        metrics = QFontMetrics(self.title_monospace_font)
        sample = "M" * self.TITLE_COLUMN_MIN_CHARS
        return max(
            self.table.horizontalHeader().minimumSectionSize(),
            metrics.horizontalAdvance(sample) + self.TITLE_COLUMN_PADDING,
        )

    def _preferred_type_column_width(self):
        if self.table.isColumnHidden(6):
            return self.TYPE_COLUMN_MIN_WIDTH

        header_item = self.table.horizontalHeaderItem(6)
        header_text = header_item.text() if header_item is not None else "Type"
        header_metrics = QFontMetrics(self.table.horizontalHeader().font())
        preferred = header_metrics.horizontalAdvance(header_text)
        has_eseq_detail = False

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 6)
            if item is None:
                continue
            text = item.text()
            if not text:
                continue
            metrics = QFontMetrics(item.font() if item.font() is not None else self.table.font())
            preferred = max(preferred, metrics.horizontalAdvance(text))
            if "(" in text and ")" in text:
                has_eseq_detail = True

        preferred += 30
        if has_eseq_detail:
            preferred = max(preferred, self.TYPE_COLUMN_ESEQ_DETAIL_MIN_WIDTH)
        return max(self.TYPE_COLUMN_MIN_WIDTH, min(preferred, self.TYPE_COLUMN_MAX_WIDTH))

    def _resize_table_columns_to_fill(self, preferred_column=None):
        if self._is_adjusting_columns:
            return

        available_width = self.table.viewport().width()
        if available_width <= 0:
            return

        min_section = self.table.horizontalHeader().minimumSectionSize()
        type_width = None
        fixed_columns = [0, 2]
        if not self.table.isColumnHidden(5):
            fixed_columns.append(5)
        if not self.table.isColumnHidden(6):
            type_width = self._preferred_type_column_width()
            if 6 in self._manual_column_widths:
                type_width = max(type_width, min_section, self._manual_column_widths[6])
            if preferred_column == 6:
                type_width = max(type_width, self.table.columnWidth(6))
        fixed_total = sum(self.table.columnWidth(column) for column in fixed_columns)
        if type_width is not None:
            fixed_total += type_width

        title_min_width = self._minimum_title_column_width()
        remaining = max((min_section + title_min_width), available_width - fixed_total)

        filename_width = self._manual_column_widths.get(3, self._default_filename_column_width())
        filename_width = max(min_section, min(filename_width, remaining - title_min_width))
        title_width = remaining - filename_width

        self._is_adjusting_columns = True
        try:
            if type_width is not None:
                self.table.setColumnWidth(6, type_width)
            self.table.setColumnWidth(3, filename_width)
            self.table.setColumnWidth(4, title_width)
        finally:
            self._is_adjusting_columns = False

    def toggle_compat_warnings(self, state):
        self.settings.setValue(self.SETTING_SHOW_COMPAT_WARNING, bool(state))
        self._update_compat_warning_ui()
        self._resize_table_columns_to_fill()
        if self._compat_warning_is_active():
            self.refresh_compat_indicators()

    def toggle_format_disklavier_screen(self, state):
        self.settings.setValue(self.SETTING_FORMAT_DISKLAVIER_SCREEN, bool(state))

    def _enable_disklavier_screen_format_option(self):
        checkbox = getattr(self, "format_disklavier_checkbox", None)
        if checkbox is None:
            return
        if checkbox.isChecked():
            self.settings.setValue(self.SETTING_FORMAT_DISKLAVIER_SCREEN, True)
            return
        checkbox.setChecked(True)

    def toggle_store_backups(self, state):
        self.settings.setValue(self.SETTING_STORE_BACKUPS, bool(state))

    def _original_write_setting_key(self):
        if self.is_floppy_mode():
            return self.SETTING_ALLOW_FLOPPY_SAVE
        if self.is_image_mode():
            return self.SETTING_CONFIRM_IMAGE_SAVE
        return None

    def _original_write_is_allowed(self):
        setting_key = self._original_write_setting_key()
        if setting_key is None:
            return True
        return self.settings.value(setting_key, False, type=bool)

    def _auto_write_protect_on_load(self):
        return self.settings.value(self.SETTING_AUTO_WRITE_PROTECT_ON_LOAD, True, type=bool)

    def toggle_auto_write_protect_on_load(self, enabled):
        self.settings.setValue(self.SETTING_AUTO_WRITE_PROTECT_ON_LOAD, bool(enabled))

    def _reset_original_write_permissions_for_new_media(self):
        if not self._auto_write_protect_on_load():
            return
        self.settings.setValue(self.SETTING_ALLOW_FLOPPY_SAVE, False)
        self.settings.setValue(self.SETTING_CONFIRM_IMAGE_SAVE, False)

    def toggle_original_write(self, state):
        setting_key = self._original_write_setting_key()
        if setting_key is None:
            return
        self.settings.setValue(setting_key, bool(state))
        self._update_floppy_save_option_ui()

    def toggle_album_subfolder(self, state):
        self.settings.setValue(self.SETTING_ESEQ_EXPORT_ALBUM_SUBFOLDER, bool(state))

    def is_image_mode(self):
        return self.image_session is not None

    def is_floppy_mode(self):
        return self.image_session is not None and self.image_session.source_kind.startswith("floppy")

    def _is_compat_warning_locked(self):
        return self.is_local_eseq_mode() or (self.is_image_mode() and self.imageEseqMode)

    def _compat_warning_is_active(self):
        return self.compat_warning_checkbox.isChecked() and not self._is_compat_warning_locked()

    def _update_compat_warning_ui(self):
        locked = self._is_compat_warning_locked()
        self.compat_warning_checkbox.setEnabled(not locked)
        if locked:
            self.compat_warning_checkbox.setToolTip(
                "Disabled while editing E-SEQ files because the 32-character limit is already enforced."
            )
            self.table.setColumnHidden(5, True)
            self.title_delegate.set_highlight_enabled(False)
        else:
            self.compat_warning_checkbox.setToolTip(
                "Highlight title characters beyond the 32-character legacy compatibility limit."
            )
            self.table.setColumnHidden(5, not self.compat_warning_checkbox.isChecked())
            self.title_delegate.set_highlight_enabled(self.compat_warning_checkbox.isChecked())
        self.table.viewport().update()

    def _update_floppy_save_option_ui(self):
        is_floppy = self.is_floppy_mode()
        is_image = self.is_image_mode() and not is_floppy
        show_original_write_toggle = is_floppy or is_image
        if hasattr(self, "writeProtectToggle"):
            self.writeProtectToggle.setVisible(show_original_write_toggle)
            self.writeProtectToggle.setEnabled(show_original_write_toggle)
            if show_original_write_toggle:
                target_label = "floppy" if is_floppy else "image"
                self.writeProtectToggle.set_target_label(target_label)
                self.writeProtectToggle.blockSignals(True)
                self.writeProtectToggle.setChecked(self._original_write_is_allowed())
                self.writeProtectToggle.blockSignals(False)
                self.writeProtectToggle._refresh_tooltip()
        if not hasattr(self, "saveButton"):
            return

        if is_floppy and not self._original_write_is_allowed():
            self.saveButton.setEnabled(False)
            self.saveButton.setToolTip(
                "Original floppy write is protected. Turn on Overwrite Original, or use Save As or Save As Image."
            )
            self.saveAsButton.setToolTip(
                "Save the current floppy session's listed files to a destination folder and leave Floppy Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current floppy session as a separate image file.")
        elif is_floppy:
            self.saveButton.setEnabled(True)
            if self.image_session is not None and self.image_session.source_kind == "floppy_usb":
                self.saveButton.setToolTip(
                    "Save pending file changes directly back to the floppy currently loaded in Floppy Mode."
                )
            else:
                self.saveButton.setToolTip("Write pending changes back to the floppy currently loaded in Floppy Mode.")
            self.saveAsButton.setToolTip(
                "Save the current floppy session's listed files to a destination folder and leave Floppy Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current floppy session as a separate image file.")
        elif is_image and not self._original_write_is_allowed():
            self.saveButton.setEnabled(False)
            self.saveButton.setToolTip(
                "Original image write is protected. Turn on Overwrite Original, or use Save As or Save As Image."
            )
            self.saveAsButton.setToolTip(
                "Save the current image session's listed files to a destination folder and leave Image Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current image session as a separate image file.")
        elif is_image:
            self.saveButton.setEnabled(True)
            self.saveButton.setToolTip("Write pending image changes back to the currently loaded image.")
            self.saveAsButton.setToolTip(
                "Save the current image session's listed files to a destination folder and leave Image Mode."
            )
            self.saveAsImageButton.setToolTip("Save the current image session as a separate image file.")
        else:
            self.saveButton.setEnabled(True)
            self.saveButton.setToolTip("Write pending file changes to the currently listed files.")
            self.saveAsButton.setToolTip("Save copies with current titles and filenames to a selected destination folder.")
            self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self._update_menu_actions()

    def _has_pending_image_changes(self):
        return bool(
            self.pendingImageRenames
            or self.pendingImageTitleEdits
            or self.pendingImageDeletes
            or self.pendingImageAdditions
            or self.pendingImageReplacements
            or self._eseq_order_changed()
            or self._image_pianodir_metadata_changed()
            or self._image_directory_filename_mismatch()
            or self.pendingGeneratePianodir
            or self.pendingDeletePianodir
            or (self.image_session and self.image_session.repair_changed)
        )

    def _confirm_discard_image_changes(self):
        if not self.is_image_mode() or not self._has_pending_image_changes():
            return True
        reply = QMessageBox.question(
            self,
            "Discard Image Changes",
            "Leave Image Mode and discard pending image changes?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _reset_image_state(self, cleanup=True):
        if cleanup and self.image_session is not None:
            self.image_session.cleanup()
        self.image_session = None
        self.pendingImageRenames.clear()
        self.pendingImageTitleEdits.clear()
        self.pendingImageDeletes.clear()
        self.pendingImageAdditions.clear()
        self.pendingImageReplacements.clear()
        self.imageEntriesByPath.clear()
        self.imageFileInfo.clear()
        self.imageEseqMode = False
        self.imageEseqVariant = ESEQ_VARIANT_DISKLAVIER
        self.imageTitlesLikelyCentered = False
        self.imageHasPianodir = False
        self.imagePianodirPopulated = False
        self.loadedImagePianodirMetadata = PianodirMetadata()
        self.pendingExportPianodirMetadata = PianodirMetadata()
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = False
        self.imagePianodirTitleEdit.clear()
        self.imagePianodirCatalogEdit.clear()
        self._update_image_pianodir_metadata_ui()
        self._refresh_disk_usage_bars()

    def _cleanup_midi_scratch_dir(self):
        scratch_dir = self.midiScratchDir
        self.midiScratchDir = None
        if not scratch_dir:
            return
        shutil.rmtree(scratch_dir, ignore_errors=True)

    def _ensure_midi_scratch_dir(self):
        if self.midiScratchDir and os.path.isdir(self.midiScratchDir):
            return self.midiScratchDir
        self.midiScratchDir = tempfile.mkdtemp(prefix="aps_midi_prep_")
        return self.midiScratchDir

    def _set_mode_banner(self, headline, detail=""):
        text = headline.strip().upper()
        if detail:
            text += f"\n{detail}"
        self.modeBannerLabel.setText(text)

    def _apply_compact_button_labels(self):
        if hasattr(self, "renameAllButton"):
            self.renameAllButton.setText("Rename 8.3")
        if hasattr(self, "convertType0Button"):
            self.convertType0Button.setText("SMF1 -> SMF0")
        if hasattr(self, "convertEseqToMidiButton"):
            self.convertEseqToMidiButton.setText("E-SEQ -> MIDI")
        if hasattr(self, "convertMidiToEseqButton"):
            self.convertMidiToEseqButton.setText("MIDI -> E-SEQ")
        if hasattr(self, "clearButton"):
            self.clearButton.setText("Clear")
        if hasattr(self, "saveButton"):
            self.saveButton.setText("Save")
        if hasattr(self, "saveAsButton"):
            self.saveAsButton.setText("Save As")
        if hasattr(self, "saveAsImageButton"):
            self.saveAsImageButton.setText("Save As Image")

    def _update_menu_actions(self):
        if not hasattr(self, "fileSaveAction"):
            return

        open_enabled = self.choose_button.isEnabled()
        if hasattr(self, "fileNewImageAction"):
            self.fileNewImageAction.setEnabled(open_enabled)
        if hasattr(self, "fileOpenFolderAction"):
            self.fileOpenFolderAction.setEnabled(open_enabled)
            self.fileOpenFolderAction.setToolTip(self.choose_button.toolTip())
            self.fileOpenFolderAction.setStatusTip(self.choose_button.toolTip())
        if hasattr(self, "fileOpenImageAction"):
            self.fileOpenImageAction.setEnabled(open_enabled)
            self.fileOpenImageAction.setToolTip(self.open_image_button.toolTip())
            self.fileOpenImageAction.setStatusTip(self.open_image_button.toolTip())
        if hasattr(self, "fileReadFloppyAction"):
            self.fileReadFloppyAction.setEnabled(open_enabled)
            self.fileReadFloppyAction.setToolTip(self.read_floppy_button.toolTip())
            self.fileReadFloppyAction.setStatusTip(self.read_floppy_button.toolTip())

        self.fileSaveAction.setText("Save")
        self.fileSaveAction.setEnabled(self.saveButton.isEnabled())
        self.fileSaveAction.setToolTip(self.saveButton.toolTip())
        self.fileSaveAction.setStatusTip(self.saveButton.toolTip())

        self.fileSaveAsAction.setText("Save As...")
        self.fileSaveAsAction.setEnabled(self.saveAsButton.isEnabled())
        self.fileSaveAsAction.setToolTip(self.saveAsButton.toolTip())
        self.fileSaveAsAction.setStatusTip(self.saveAsButton.toolTip())

        image_action_text = "Save As Image..."
        if self.is_image_mode():
            image_action_text = "Save As Image..."
        self.fileSaveAsImageAction.setText(image_action_text)
        self.fileSaveAsImageAction.setEnabled(self.saveAsImageButton.isEnabled())
        self.fileSaveAsImageAction.setToolTip(self.saveAsImageButton.toolTip())
        self.fileSaveAsImageAction.setStatusTip(self.saveAsImageButton.toolTip())

        if hasattr(self, "fileSaveToFloppyAction"):
            enabled = self.choose_button.isEnabled() and self.is_image_mode()
            self.fileSaveToFloppyAction.setEnabled(enabled)
            self.fileSaveToFloppyAction.setToolTip(
                "Save the current listed files directly to a formatted floppy drive."
                if enabled else
                "Open or create an image before saving files to a floppy drive."
            )
            self.fileSaveToFloppyAction.setStatusTip(self.fileSaveToFloppyAction.toolTip())

        if hasattr(self, "fileWriteImageToFloppyAction"):
            enabled = self.choose_button.isEnabled() and self.is_image_mode()
            self.fileWriteImageToFloppyAction.setEnabled(enabled)
            self.fileWriteImageToFloppyAction.setToolTip(
                "Write the current image to a physical floppy disk."
                if enabled else
                "Open or create an image before writing it to a physical floppy disk."
            )
            self.fileWriteImageToFloppyAction.setStatusTip(self.fileWriteImageToFloppyAction.toolTip())

        self.utilitiesRenameAction.setEnabled(self.renameAllButton.isEnabled())
        self.utilitiesRenameAction.setToolTip(self.renameAllButton.toolTip())
        self.utilitiesRenameAction.setStatusTip(self.renameAllButton.toolTip())

        self.utilitiesSmfAction.setEnabled(self.convertType0Button.isEnabled())
        self.utilitiesSmfAction.setToolTip(self.convertType0Button.toolTip())
        self.utilitiesSmfAction.setStatusTip(self.convertType0Button.toolTip())

        self.utilitiesEseqToMidiAction.setEnabled(self.convertEseqToMidiButton.isEnabled())
        self.utilitiesEseqToMidiAction.setToolTip(self.convertEseqToMidiButton.toolTip())
        self.utilitiesEseqToMidiAction.setStatusTip(self.convertEseqToMidiButton.toolTip())

        self.utilitiesMidiToEseqAction.setEnabled(self.convertMidiToEseqButton.isEnabled())
        self.utilitiesMidiToEseqAction.setToolTip(self.convertMidiToEseqButton.toolTip())
        self.utilitiesMidiToEseqAction.setStatusTip(self.convertMidiToEseqButton.toolTip())

        if hasattr(self, "utilitiesFormatFloppyAction"):
            enabled = self.choose_button.isEnabled()
            self.utilitiesFormatFloppyAction.setEnabled(enabled)
            self.utilitiesFormatFloppyAction.setStatusTip(
                "Format a physical floppy disk for Yamaha Disklavier use."
                if enabled else
                "Please wait for the current operation to finish before formatting a floppy disk."
            )
        if hasattr(self, "utilitiesRecoverImageAction"):
            enabled = self.choose_button.isEnabled()
            self.utilitiesRecoverImageAction.setEnabled(enabled)
            self.utilitiesRecoverImageAction.setStatusTip(
                "Recover song data from a damaged floppy image."
                if enabled else
                "Please wait for the current operation to finish before recovering a damaged image."
            )
        if hasattr(self, "fileCreateTagSidecarsAction"):
            enabled = self.choose_button.isEnabled()
            self.fileCreateTagSidecarsAction.setEnabled(enabled)
            self.fileCreateTagSidecarsAction.setStatusTip(
                "Create .tags.txt sidecar files next to saved local MIDI or E-SEQ files."
                if enabled else
                "Please wait for the current operation to finish before changing sidecar output."
            )
        if hasattr(self, "fileAutoWriteProtectAction"):
            enabled = self.choose_button.isEnabled()
            self.fileAutoWriteProtectAction.setEnabled(enabled)
            self.fileAutoWriteProtectAction.setStatusTip(
                "Automatically protect original disks and images when they are opened."
                if enabled else
                "Please wait for the current operation to finish before changing write-protect behavior."
            )

        has_listed_files = self.choose_button.isEnabled() and any(
            self.table.item(row, 1) is not None and not self._is_special_pianodir_row(row)
            for row in range(self.table.rowCount())
        )
        if hasattr(self, "utilitiesSongListAction"):
            self.utilitiesSongListAction.setEnabled(has_listed_files)
        if hasattr(self, "utilitiesFileInspectionAction"):
            self.utilitiesFileInspectionAction.setEnabled(has_listed_files)

    def _set_loaded_image_pianodir_metadata(self, metadata=None):
        metadata = metadata or PianodirMetadata()
        self.loadedImagePianodirMetadata = metadata
        self.imagePianodirTitleEdit.setText(metadata.disk_title)
        self.imagePianodirCatalogEdit.setText(metadata.catalog_number)

    def _set_loaded_regular_pianodir_metadata(self, metadata=None):
        metadata = metadata or PianodirMetadata()
        self.loadedRegularPianodirMetadata = metadata
        self.imagePianodirTitleEdit.setText(metadata.disk_title)
        self.imagePianodirCatalogEdit.setText(metadata.catalog_number)

    def _current_image_pianodir_metadata(self):
        return PianodirMetadata(
            catalog_number=normalize_pianodir_catalog_number(self.imagePianodirCatalogEdit.text()),
            disk_title=self.imagePianodirTitleEdit.text().strip(),
        )

    def _current_regular_pianodir_metadata(self):
        return PianodirMetadata(
            catalog_number=normalize_pianodir_catalog_number(self.imagePianodirCatalogEdit.text()),
            disk_title=self.imagePianodirTitleEdit.text().strip(),
        )

    def _normalize_pianodir_catalog_field(self):
        if not hasattr(self, "imagePianodirCatalogEdit"):
            return
        normalized = normalize_pianodir_catalog_number(self.imagePianodirCatalogEdit.text())
        if normalized != self.imagePianodirCatalogEdit.text():
            self.imagePianodirCatalogEdit.setText(normalized)

    def _current_visible_pianodir_metadata(self):
        if self._pianodir_metadata_fields_should_show() and self.is_image_mode():
            return self._current_image_pianodir_metadata()
        if self._pianodir_metadata_fields_should_show() and self.is_local_eseq_mode():
            return self._current_regular_pianodir_metadata()
        if self._metadata_has_text(self.pendingExportPianodirMetadata):
            return self.pendingExportPianodirMetadata
        return PianodirMetadata()

    def _metadata_has_text(self, metadata):
        return bool(
            metadata
            and (
                str(metadata.catalog_number or "").strip()
                or str(metadata.disk_title or "").strip()
            )
        )

    def _image_pianodir_metadata_changed(self):
        return (
            self.is_image_mode()
            and self.imageHasPianodir
            and not self.pendingDeletePianodir
            and self._current_image_pianodir_metadata() != self.loadedImagePianodirMetadata
        )

    def _regular_pianodir_metadata_changed(self):
        return (
            self.is_local_eseq_mode()
            and self.regularHasPianodir
            and self._current_regular_pianodir_metadata() != self.loadedRegularPianodirMetadata
        )

    def _image_pianodir_metadata_for_save(self):
        if not self.is_image_mode() or not self.imageHasPianodir or self.pendingDeletePianodir:
            return None
        metadata = self._current_image_pianodir_metadata()
        if metadata == self.loadedImagePianodirMetadata:
            return self.loadedImagePianodirMetadata
        return metadata

    def _regular_pianodir_metadata_for_save(self):
        if not self.is_local_eseq_mode() or not (self.regularHasPianodir or self.pendingGeneratePianodir):
            return None
        metadata = self._current_regular_pianodir_metadata()
        if metadata == self.loadedRegularPianodirMetadata:
            return self.loadedRegularPianodirMetadata
        return metadata

    def _pianodir_metadata_fields_should_show(self):
        if self._is_clavinova_eseq_variant():
            return False
        if self.is_image_mode():
            return (
                self.imageEseqMode
                and not self.pendingDeletePianodir
                and (self.imageHasPianodir or self.pendingGeneratePianodir)
            )
        return self.is_local_eseq_mode() and (self.regularHasPianodir or self.pendingGeneratePianodir)

    def _album_subfolder_option_should_show(self):
        return self._pianodir_metadata_fields_should_show() or (
            self._metadata_has_text(self.pendingExportPianodirMetadata)
            and (
                bool(self.pendingRegularConversions)
                or bool(self.pendingImageReplacements)
                or bool(self.pendingImageRenames)
            )
        )

    def _update_image_pianodir_metadata_ui(self):
        should_show = self._pianodir_metadata_fields_should_show()
        album_option_should_show = self._album_subfolder_option_should_show()
        self.imagePianodirMetadataWidget.setVisible(should_show or album_option_should_show)
        if hasattr(self, "album_subfolder_checkbox"):
            self.album_subfolder_checkbox.setVisible(album_option_should_show)
            self.album_subfolder_checkbox.setEnabled(album_option_should_show)

    def _set_regular_mode_context(self, *, preferred_path="", file_paths=None):
        context_path = ""
        if preferred_path:
            context_path = os.path.abspath(preferred_path)
        elif file_paths:
            abs_paths = [os.path.abspath(path) for path in file_paths if path]
            if abs_paths:
                try:
                    context_path = os.path.commonpath(abs_paths)
                except ValueError:
                    context_path = os.path.dirname(abs_paths[0])
                if not os.path.isdir(context_path):
                    context_path = os.path.dirname(abs_paths[0])
        self.regularModeContextPath = context_path

    def _abbreviated_context_path(self, path):
        clean_path = os.path.abspath(path) if path else ""
        if not clean_path:
            return "No folder selected"

        home = os.path.expanduser("~")
        if clean_path == home:
            clean_path = "~"
        elif clean_path.startswith(home + os.sep):
            clean_path = "~" + clean_path[len(home):]

        max_length = 44
        if len(clean_path) <= max_length:
            return clean_path

        normalized = clean_path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 2:
            shortened = f".../{parts[-2]}/{parts[-1]}"
            if len(shortened) <= max_length:
                return shortened

        return "..." + clean_path[-(max_length - 3):]

    def _regular_mode_context_label(self):
        return self._abbreviated_context_path(self.regularModeContextPath)

    def _sanitize_export_folder_name(self, folder_name):
        text = re.sub(r"\s+", " ", str(folder_name or "")).strip()
        cleaned = []
        for char in text:
            if ord(char) < 32:
                cleaned.append(" ")
            elif char in self.EXPORT_FOLDER_INVALID_CHARS:
                cleaned.append(" ")
            else:
                cleaned.append(char)
        text = re.sub(r"\s+", " ", "".join(cleaned)).strip(" .")
        if not text:
            text = "Yamaha E-SEQ Disk"
        reserved_names = {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        }
        if text.upper() in reserved_names:
            text = f"{text} Album"
        return text[:150].rstrip(" .") or "Yamaha E-SEQ Disk"

    def _album_subfolder_name(self):
        metadata = self._current_visible_pianodir_metadata()
        parts = [
            metadata.catalog_number.strip(),
            metadata.disk_title.strip(),
        ]
        raw_name = " ".join(part for part in parts if part)
        return self._sanitize_export_folder_name(raw_name or "Yamaha E-SEQ Disk")

    def _album_subfolder_option_applies(self):
        checkbox = getattr(self, "album_subfolder_checkbox", None)
        return bool(
            checkbox is not None
            and checkbox.isChecked()
            and self._album_subfolder_option_should_show()
        )

    def _destination_with_album_subfolder(self, dest_dir):
        if not self._album_subfolder_option_applies():
            return dest_dir
        return os.path.join(dest_dir, self._album_subfolder_name())

    def _active_eseq_variant(self):
        if self.is_image_mode():
            return self.imageEseqVariant
        if self.is_local_eseq_mode():
            return self.regularEseqVariant
        return ESEQ_VARIANT_DISKLAVIER

    def _is_clavinova_eseq_variant(self, variant=None):
        return (variant or self._active_eseq_variant()) == ESEQ_VARIANT_CLAVINOVA

    def _eseq_directory_filename(self, variant=None):
        return MUSICDIR_FILENAME if self._is_clavinova_eseq_variant(variant) else PIANODIR_FILENAME

    def _eseq_song_extension(self, variant=None):
        return "MDA" if self._is_clavinova_eseq_variant(variant) else "FIL"

    def _eseq_converter_container(self, variant=None):
        return ESEQ_CONTAINER_CLAVINOVA_MDA if self._is_clavinova_eseq_variant(variant) else ESEQ_CONTAINER_DISKLAVIER

    def _eseq_mode_label(self, variant=None):
        return "CLAVINOVA E-SEQ" if self._is_clavinova_eseq_variant(variant) else "E-SEQ"

    def _disk_content_label(self):
        return self._eseq_mode_label(self.imageEseqVariant) if self.imageEseqMode else "MIDI"

    def _disk_mode_banner_headline(self):
        if self.image_session is None:
            return "Image Mode"
        return f"{self.image_session.mode_name} ({self._disk_content_label()})"

    def _clear_regular_list_state(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.pendingEdits.clear()
        self.listedFileInfo.clear()
        self.regularModeContextPath = ""
        self.regularEseqMode = False
        self.regularEseqVariant = ESEQ_VARIANT_DISKLAVIER
        self.regularTitlesLikelyCentered = False
        self.regularHasPianodir = False
        self.regularPianodirPopulated = False
        self.regularPianodirSourcePath = ""
        self.loadedRegularPianodirMetadata = PianodirMetadata()
        self.loadedRegularEseqPaths = tuple()
        self.pendingGeneratePianodir = False
        self.pendingExportPianodirMetadata = PianodirMetadata()
        if hasattr(self, "imagePianodirTitleEdit"):
            self.imagePianodirTitleEdit.clear()
        if hasattr(self, "imagePianodirCatalogEdit"):
            self.imagePianodirCatalogEdit.clear()
        self.pendingRegularConversions.clear()
        self.pendingRegularRenames.clear()

    def _set_listed_file_info(self, full_path, *, title_mode="", midi_type="", is_midi=False, order_key=b""):
        self.listedFileInfo[full_path] = {
            "title_mode": title_mode or "",
            "midi_type": midi_type or "",
            "is_midi": bool(is_midi),
            "order_key": normalize_eseq_order_key(order_key),
        }

    def _listed_file_info(self, full_path):
        return self.listedFileInfo.get(full_path, {})

    def _listed_file_title_mode(self, full_path):
        return self._listed_file_info(full_path).get("title_mode", "")

    def _listed_file_order_key(self, full_path):
        return normalize_eseq_order_key(self._listed_file_info(full_path).get("order_key", b""))

    def is_local_eseq_mode(self):
        return self.image_session is None and self.regularEseqMode

    def _regular_file_rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            rows.append(row)
        return rows

    def _regular_file_count(self):
        return len(self.listedFileInfo)

    def _regular_midi_file_count(self):
        return sum(
            1
            for info in self.listedFileInfo.values()
            if info.get("title_mode") == "midi"
        )

    def _pending_regular_conversion(self, full_path):
        return self.pendingRegularConversions.get(full_path, {})

    def _regular_source_material_path(self, full_path):
        conversion = self._pending_regular_conversion(full_path)
        temp_path = conversion.get("temp_path")
        if temp_path:
            return temp_path
        return full_path

    def _type0_midi_source_for_eseq_conversion(self, source_path, scratch_dir, target_filename=""):
        os.makedirs(scratch_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(target_filename or source_path))[0] or "midi"
        type0_path = os.path.join(scratch_dir, f"{uuid.uuid4().hex}_{stem}_type0.mid")
        changed = convert_midi_file_to_type0_path(source_path, type0_path)
        if changed:
            return type0_path
        if os.path.exists(type0_path):
            os.remove(type0_path)
        return source_path

    def _regular_output_filename_for_path(self, full_path):
        pending_filename = self.pendingRegularRenames.get(full_path)
        if pending_filename:
            return pending_filename
        conversion = self._pending_regular_conversion(full_path)
        target_filename = conversion.get("target_filename")
        if target_filename:
            return target_filename
        return os.path.basename(full_path)

    def _regular_row_output_filename(self, row):
        filename_item = self.table.item(row, 3)
        if filename_item is not None and filename_item.text().strip():
            return filename_item.text().strip()
        full_path_item = self.table.item(row, 1)
        if full_path_item is None:
            return "Untitled"
        return self._regular_output_filename_for_path(full_path_item.text())

    def _current_regular_eseq_paths(self):
        paths = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) == "eseq":
                paths.append(full_path)
        return tuple(sorted(paths, key=str.upper))

    def _regular_eseq_rows(self):
        rows = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            if self._listed_file_title_mode(full_path_item.text()) == "eseq":
                rows.append(row)
        return rows

    def _image_eseq_rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            image_path = path_item.text()
            if self._is_eseq_candidate(self._final_image_path(image_path), is_midi=self._image_path_is_midi(image_path)):
                rows.append(row)
        return rows

    def _current_eseq_rows(self):
        if self.is_image_mode():
            return self._image_eseq_rows()
        return self._regular_eseq_rows()

    def _current_song_rows_for_listing(self):
        if self.is_image_mode():
            if self._supports_eseq_reordering():
                return self._image_eseq_rows()
            rows = []
            for row in range(self.table.rowCount()):
                if self._is_special_pianodir_row(row):
                    continue
                if self.table.item(row, 1) is not None:
                    rows.append(row)
            return rows
        if self.is_local_eseq_mode():
            return self._regular_eseq_rows()
        return self._regular_file_rows()

    def _song_title_for_row(self, row):
        title = (self._row_raw_title(row) or "").strip()
        if not title or title.lower().startswith("error") or title == "No title found.":
            return "Untitled"
        return title

    @staticmethod
    def _song_list_display_text(value, fallback=""):
        text = re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()
        return text or fallback

    def _build_song_list_text(self):
        rows = self._current_song_rows_for_listing()
        metadata = self._current_visible_pianodir_metadata()
        lines = []
        disk_title = self._song_list_display_text(metadata.disk_title)
        catalog_number = self._song_list_display_text(metadata.catalog_number)
        if disk_title:
            lines.append(f"Album: {disk_title}")
        if catalog_number:
            lines.append(f"Catalog: {catalog_number}")
        if lines:
            lines.append("")
        for index, row in enumerate(rows, start=1):
            title = self._song_list_display_text(self._song_title_for_row(row), "Untitled")
            lines.append(f"{index}. {title}")
        return "\n".join(lines).strip()

    def toggle_tag_sidecar_writing(self, enabled):
        self.settings.setValue(self.SETTING_WRITE_TAG_SIDECARS, bool(enabled))

    def _tag_sidecars_enabled(self):
        return self.settings.value(self.SETTING_WRITE_TAG_SIDECARS, False, type=bool)

    def _tag_sidecar_path_for_output(self, output_path):
        base_path, _ext = os.path.splitext(output_path)
        return f"{base_path}.tags.txt"

    def _tag_sidecar_lines_for_row(self, row):
        metadata = self._current_visible_pianodir_metadata()
        title = self._song_list_display_text(self._song_title_for_row(row), "Untitled")
        album = self._song_list_display_text(metadata.disk_title)
        catalog = self._song_list_display_text(metadata.catalog_number)

        lines = [f"TIT2={title}"]
        if catalog:
            lines.append(f"TIT3={catalog}")
        if album:
            lines.append(f"TALB={album}")
        lines.append("TCON=Player Piano")
        return lines

    def _write_tag_sidecar_file(self, output_path, row):
        if is_pianodir_path(output_path):
            return None
        tag_path = self._tag_sidecar_path_for_output(output_path)
        try:
            tag_dir = os.path.dirname(tag_path)
            if tag_dir:
                os.makedirs(tag_dir, exist_ok=True)
            payload = "\r\n".join(self._tag_sidecar_lines_for_row(row)) + "\r\n"
            with open(tag_path, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(payload)
            return None
        except Exception as exc:
            return f"Could not write tag file for {os.path.basename(output_path)}: {exc}"

    def _write_tag_sidecars_for_regular_rows(self, path_remap=None, only_paths=None):
        if not self._tag_sidecars_enabled() or self.is_image_mode():
            return []

        path_remap = path_remap or {}
        only_paths = set(only_paths) if only_paths is not None else None
        errors = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if only_paths is not None and full_path not in only_paths:
                continue
            output_path = path_remap.get(full_path, full_path)
            if not output_path or is_pianodir_path(output_path):
                continue
            error = self._write_tag_sidecar_file(output_path, row)
            if error:
                errors.append(error)
        return errors

    def show_song_list_tool(self):
        song_list_text = self._build_song_list_text()
        if not song_list_text:
            QMessageBox.information(self, "No Songs", "No loaded songs are available for a song list.")
            return

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Song List")
        dialog.setModal(False)
        dialog.resize(520, 460)
        layout = QVBoxLayout(dialog)

        text_box = QPlainTextEdit(dialog)
        text_box.setPlainText(song_list_text)
        text_box.setReadOnly(True)
        layout.addWidget(text_box, stretch=1)

        buttons_row = QHBoxLayout()
        copy_button = QPushButton("Copy to Clipboard", dialog)
        close_button = QPushButton("Close", dialog)
        buttons_row.addWidget(copy_button)
        buttons_row.addStretch()
        buttons_row.addWidget(close_button)
        layout.addLayout(buttons_row)

        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(text_box.toPlainText()))
        close_button.clicked.connect(dialog.close)
        self.songListDialog = dialog
        dialog.show()

    def _inspection_items(self):
        items = []
        rows = self._current_song_rows_for_listing()
        for row in rows:
            path_item = self.table.item(row, 1)
            filename_item = self.table.item(row, 3)
            if path_item is None:
                continue
            display_name = filename_item.text().strip() if filename_item and filename_item.text().strip() else os.path.basename(path_item.text())
            title = self._song_title_for_row(row)
            label = display_name if title == "Untitled" else f"{display_name} - {title}"
            try:
                if self.is_image_mode():
                    material_path = self._pending_or_extracted_image_path(path_item.text())
                else:
                    material_path = self._regular_source_material_path(path_item.text())
            except Exception:
                material_path = ""
            if material_path and os.path.isfile(material_path):
                items.append({"label": label, "path": material_path, "row": row})
        return items

    def show_file_inspection_tool(self, selected_row=None):
        if isinstance(selected_row, bool):
            selected_row = None
        items = self._inspection_items()
        if not items:
            QMessageBox.information(self, "No Files", "No loaded MIDI or E-SEQ files are available to inspect.")
            return
        initial_row = None
        if selected_row is not None:
            if not any(item.get("row") == selected_row for item in items):
                QMessageBox.information(
                    self,
                    "File Not Available",
                    "That row does not have a loaded MIDI or E-SEQ file available to inspect.",
                )
                return
            initial_row = selected_row
        dialog = FileInspectionDialog(items, parent=self, initial_row=initial_row)
        self.fileInspectionDialog = dialog
        dialog.show()

    def _supports_eseq_reordering(self):
        if self.is_local_eseq_mode():
            return True
        if not self.is_image_mode():
            return False
        return self.imageEseqMode or (self.imageHasPianodir and not self.pendingDeletePianodir)

    def _row_eseq_order_key(self, row):
        path_item = self.table.item(row, 1)
        if path_item is None:
            return b""
        path = path_item.text()
        if self.is_image_mode():
            order_key = self._image_path_order_key(path)
            fallback_path = self._final_image_path(path)
        else:
            order_key = self._listed_file_order_key(path)
            fallback_path = path
        return order_key or build_eseq_order_key_from_path(fallback_path, sort_last=True)

    def _current_eseq_order_keys(self):
        return [self._row_eseq_order_key(row) for row in self._current_eseq_rows()]

    def _eseq_order_changed(self):
        if not self._supports_eseq_reordering():
            return False
        current_keys = self._current_eseq_order_keys()
        return len(current_keys) >= 2 and current_keys != sorted(current_keys)

    def _regular_eseq_order_key_edits(self):
        rows = self._regular_eseq_rows()
        sorted_keys = sorted(self._row_eseq_order_key(row) for row in rows)
        edits = {}
        for row, assigned_key in zip(rows, sorted_keys):
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            full_path = path_item.text()
            current_key = self._listed_file_order_key(full_path)
            if current_key != assigned_key:
                edits[full_path] = assigned_key
        return edits

    def _image_eseq_order_key_edits(self):
        rows = self._image_eseq_rows()
        sorted_keys = sorted(self._row_eseq_order_key(row) for row in rows)
        edits = {}
        for row, assigned_key in zip(rows, sorted_keys):
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            image_path = path_item.text()
            current_key = self._image_path_order_key(image_path)
            if current_key != assigned_key:
                edits[image_path] = assigned_key
        return edits

    def _image_eseq_directory_order(self):
        order = {}
        for row in self._image_eseq_rows():
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            image_path = path_item.text()
            order[self._final_image_path(image_path)] = self._row_eseq_order_key(row)
        return order

    def _selected_table_row(self):
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selected_rows = selection_model.selectedRows()
            if selected_rows:
                return selected_rows[0].row()
        current_row = self.table.currentRow()
        if current_row >= 0:
            return current_row
        return -1

    def _neighbor_eseq_row(self, row, direction):
        rows = self._current_eseq_rows()
        if row not in rows:
            return -1
        index = rows.index(row) + direction
        if 0 <= index < len(rows):
            return rows[index]
        return -1

    def _refresh_eseq_reorder_buttons(self):
        if not hasattr(self, "moveEseqUpButton") or not hasattr(self, "eseqReorderWidget"):
            return
        should_show = self._supports_eseq_reordering()
        self.eseqReorderWidget.setVisible(should_show)
        if not should_show:
            self.moveEseqUpButton.setEnabled(False)
            self.moveEseqDownButton.setEnabled(False)
            return

        row = self._selected_table_row()
        self.moveEseqUpButton.setEnabled(self._neighbor_eseq_row(row, -1) >= 0)
        self.moveEseqDownButton.setEnabled(self._neighbor_eseq_row(row, 1) >= 0)

    def _move_table_row(self, source_row, target_row):
        if source_row == target_row or source_row < 0 or target_row < 0:
            return
        column_count = self.table.columnCount()
        saved_items = [self.table.takeItem(source_row, column) for column in range(column_count)]
        if source_row < target_row:
            for row in range(source_row, target_row):
                for column in range(column_count):
                    self.table.setItem(row, column, self.table.takeItem(row + 1, column))
        else:
            for row in range(source_row, target_row, -1):
                for column in range(column_count):
                    self.table.setItem(row, column, self.table.takeItem(row - 1, column))
        for column, item in enumerate(saved_items):
            self.table.setItem(target_row, column, item)

    def move_selected_eseq_row(self, direction):
        if direction not in {-1, 1} or not self._supports_eseq_reordering():
            return
        source_row = self._selected_table_row()
        target_row = self._neighbor_eseq_row(source_row, direction)
        if target_row < 0:
            return

        self.table.setSortingEnabled(False)
        self._move_table_row(source_row, target_row)
        moved_path_item = self.table.item(target_row, 1)
        moved_path = moved_path_item.text() if moved_path_item is not None else ""
        if self.is_local_eseq_mode():
            self._refresh_regular_pianodir_row()
        else:
            self._refresh_pianodir_row()
        if moved_path:
            for row in range(self.table.rowCount()):
                path_item = self.table.item(row, 1)
                if path_item is not None and path_item.text() == moved_path:
                    self.table.setCurrentCell(row, 4)
                    break
        self._refresh_eseq_reorder_buttons()
        direction_text = "earlier" if direction < 0 else "later"
        self.status_label.setText(f"Moved the selected E-SEQ file {direction_text} in the playback order.")

    def _image_song_file_count(self):
        count = 0
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is not None:
                count += 1
        return count

    def _current_eseq_file_count(self):
        if self.is_image_mode():
            return self._image_song_file_count()
        return self._regular_file_count()

    def _active_eseq_file_limit(self):
        return (
            CLAVINOVA_MUSICDIR_MAX_TRACKS
            if self._is_clavinova_eseq_variant()
            else self.ESEQ_FILE_LIMIT
        )

    def _generated_eseq_directory_size(self, song_count=None):
        if self._is_clavinova_eseq_variant():
            if song_count is None:
                song_count = self._current_eseq_file_count()
            song_count = max(0, min(int(song_count or 0), CLAVINOVA_MUSICDIR_MAX_TRACKS))
            return CLAVINOVA_MUSICDIR_HEADER_SIZE + song_count * CLAVINOVA_MUSICDIR_RECORD_SIZE
        return PIANODIR_TARGET_FILE_SIZE

    def _warn_eseq_file_limit(self, projected_count, *, action_text):
        limit = self._active_eseq_file_limit()
        QMessageBox.warning(
            self,
            "Too Many E-SEQ Files",
            (
                f"Yamaha E-SEQ supports at most {limit} files per disk or set.\n\n"
                f"{action_text} would leave {projected_count} files, which exceeds that limit."
            ),
        )

    def _ensure_eseq_file_limit(self, projected_count, *, action_text):
        if projected_count <= self._active_eseq_file_limit():
            return True
        self._warn_eseq_file_limit(projected_count, action_text=action_text)
        return False

    def _regular_pianodir_path(self, base_dir=None):
        target_dir = os.path.abspath(base_dir or self.regularModeContextPath or os.path.expanduser("~"))
        return os.path.join(target_dir, self._eseq_directory_filename(self.regularEseqVariant))

    def _existing_regular_pianodir_path(self):
        source_path = os.path.abspath(self.regularPianodirSourcePath) if self.regularPianodirSourcePath else ""
        if source_path and os.path.isfile(source_path):
            return source_path
        candidate = self._regular_pianodir_path()
        if os.path.isfile(candidate):
            return candidate
        return ""

    def _existing_image_pianodir_host_path(self):
        if self.image_session is None or not self.imageHasPianodir or self.pendingDeletePianodir:
            return ""
        for image_path in self.imageEntriesByPath:
            if is_eseq_directory_path(image_path):
                try:
                    return self.image_session.extract_file(image_path)
                except Exception:
                    return ""
        return ""

    def _active_image_directory_paths(self):
        if not self.is_image_mode() or self.pendingDeletePianodir:
            return set()
        paths = {
            path.upper()
            for path in self.imageEntriesByPath
            if is_eseq_directory_path(path) and path not in self.pendingImageDeletes
        }
        paths.update(
            path.upper()
            for path in self.pendingImageAdditions
            if is_eseq_directory_path(path)
        )
        paths.update(
            path.upper()
            for path in self.pendingImageReplacements
            if is_eseq_directory_path(path)
        )
        return paths

    def _image_directory_filename_mismatch(self):
        if not self.imageEseqMode or self.pendingDeletePianodir:
            return False
        active_paths = self._active_image_directory_paths()
        if not active_paths:
            return False
        target_name = self._eseq_directory_filename(self.imageEseqVariant).upper()
        return active_paths != {target_name}

    def _image_pianodir_needs_refresh(self):
        if not self.imageEseqMode or not self.imageHasPianodir or self.pendingDeletePianodir:
            return False
        song_deletes = any(not is_eseq_directory_path(path) for path in self.pendingImageDeletes)
        song_additions = any(not is_eseq_directory_path(path) for path in self.pendingImageAdditions)
        song_replacements = any(not is_eseq_directory_path(path) for path in self.pendingImageReplacements)
        return bool(
            self._image_directory_filename_mismatch()
            or self.pendingImageRenames
            or self.pendingImageTitleEdits
            or song_deletes
            or song_additions
            or song_replacements
            or self._eseq_order_changed()
            or self._image_pianodir_metadata_changed()
        )

    def _regular_pianodir_needs_refresh(self, *, for_export=False):
        if not self.regularEseqMode or not self.regularHasPianodir:
            return False
        if self._regular_pianodir_metadata_changed():
            return True
        if any(self._listed_file_title_mode(path) == "eseq" for path in self.pendingEdits):
            return True
        if self._eseq_order_changed():
            return True
        if self._current_regular_eseq_paths() != self.loadedRegularEseqPaths:
            return True
        return False

    def _refresh_regular_eseq_mode(self):
        has_eseq_rows = any(
            info.get("title_mode") == "eseq"
            for info in self.listedFileInfo.values()
        )
        self.regularEseqMode = self.regularHasPianodir or has_eseq_rows
        if not self.regularEseqMode:
            self.pendingGeneratePianodir = False
            self.regularEseqVariant = ESEQ_VARIANT_DISKLAVIER

    def _populate_regular_pianodir_row(self, row):
        directory_name = self._eseq_directory_filename(self.regularEseqVariant)
        row_items = []
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(row, column, item)
            row_items.append(item)

        is_present = self.regularHasPianodir and self.regularPianodirPopulated
        is_missing = self.regularEseqMode and not is_present
        refresh_on_save = self._should_generate_pianodir()
        title_text = "Present - will refresh on save" if (is_present and refresh_on_save) else ("Present" if is_present else "")
        if is_missing and self.pendingGeneratePianodir:
            title_text = "Missing - will generate on save"
        elif is_missing:
            title_text = "Missing - click to generate"

        row_items[0].setText("")
        row_items[0].setToolTip(f"{directory_name} is managed automatically.")
        row_items[1].setText(PIANODIR_ROW_PATH)
        row_items[2].setText("")
        row_items[2].setToolTip(f"{directory_name} is managed automatically.")
        row_items[3].setText(directory_name)
        row_items[3].setToolTip("Directory file for Yamaha E-SEQ folders.")
        row_items[4].setText(title_text)
        if is_missing:
            row_items[4].setToolTip(f"Click to offer {directory_name} generation.")
        elif refresh_on_save:
            row_items[4].setToolTip(f"{directory_name} will be refreshed on save because related E-SEQ metadata has changed.")
        else:
            row_items[4].setToolTip(f"{directory_name} is present and will be left unchanged unless E-SEQ metadata changes.")
        row_items[5].setText("")
        row_items[5].setToolTip("Not applicable.")
        row_items[6].setText("DIR")
        row_items[6].setTextAlignment(Qt.AlignCenter)
        row_items[6].setToolTip("Special Yamaha E-SEQ directory file.")

        bg_color, fg_color = self._pianodir_row_colors(is_present)
        for item in row_items:
            item.setBackground(bg_color)
            item.setForeground(fg_color)

    def _refresh_regular_pianodir_row(self):
        self._refresh_regular_eseq_mode()
        row = self._find_pianodir_row()

        if not self.regularEseqMode:
            if row >= 0:
                self.table.removeRow(row)
            if not self.is_image_mode():
                self._apply_midi_mode_ui()
            return

        if row < 0:
            self.table.insertRow(0)
            row = 0
        elif row != 0:
            self.table.removeRow(row)
            self.table.insertRow(0)
            row = 0

        self._populate_regular_pianodir_row(row)
        if not self.is_image_mode():
            self._apply_local_eseq_mode_ui()

    def _probe_regular_file(self, file_path):
        title = ""
        title_mode = ""
        is_midi = is_midi_file(file_path)
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        midi_type = ext.upper() if ext else "File"
        order_key = b""

        is_clavinova_mda = is_clavinova_mda_file(file_path)
        if is_clavinova_mda:
            title = os.path.splitext(os.path.basename(file_path))[0]
            title_mode = "eseq"
            order_key = build_eseq_order_key_from_path(file_path)
            midi_type = "MDA"
        elif is_eseq_file(file_path) and has_eseq_title_metadata(file_path):
            title = extract_eseq_title_from_file(file_path)
            title_mode = "eseq"
            if title.startswith("Error"):
                title = ""
            try:
                order_key = read_eseq_order_key_from_file(file_path)
            except Exception:
                order_key = build_eseq_order_key_from_path(file_path)
            eseq_kind = "ESQ" if ext == "esq" else "FIL"
            try:
                arrangement_type = read_eseq_arrangement_type_label_from_file(file_path)
            except Exception:
                arrangement_type = ""
            try:
                write_protected = read_eseq_write_protect_from_file(file_path)
            except Exception:
                write_protected = None
            midi_type = eseq_type_display_label(eseq_kind, arrangement_type, write_protected)

        if is_midi:
            if title_mode != "eseq":
                title = extract_first_title_from_midi(file_path)
                if title.startswith("Error"):
                    title = ""
                title_mode = "midi"
            if title_mode != "eseq":
                midi_type = extract_midi_type_label_from_midi(file_path)

        return title, midi_type, title_mode, is_midi, order_key

    def _regular_drop_file_kind(self, file_path):
        if not file_path or not os.path.isfile(file_path):
            return ""
        if os.path.basename(file_path).upper() in {PIANODIR_FILENAME, MUSICDIR_FILENAME}:
            return "pianodir"
        if is_midi_file(file_path):
            return "midi"
        if is_clavinova_mda_file(file_path):
            return "eseq"
        if is_eseq_file(file_path) and has_eseq_title_metadata(file_path):
            return "eseq"
        return ""

    def _regular_path_eseq_variant(self, file_path):
        basename = os.path.basename(file_path or "").upper()
        if basename == MUSICDIR_FILENAME:
            return ESEQ_VARIANT_CLAVINOVA
        if basename == PIANODIR_FILENAME:
            return ESEQ_VARIANT_DISKLAVIER
        if is_clavinova_mda_file(file_path):
            return ESEQ_VARIANT_CLAVINOVA
        return ESEQ_VARIANT_DISKLAVIER

    def can_accept_regular_drop_path(self, file_path):
        return not self.is_image_mode() and self._regular_drop_file_kind(file_path) in {"midi", "eseq", "pianodir"}

    def _regular_folder_file_paths(self, directory):
        file_paths = []
        try:
            file_names = os.listdir(directory)
        except OSError:
            return file_paths
        for file_name in file_names:
            full_path = os.path.join(directory, file_name)
            if not os.path.isfile(full_path):
                continue
            if file_name.upper() in {PIANODIR_FILENAME, MUSICDIR_FILENAME}:
                file_paths.append(full_path)
                continue
            if self._regular_drop_file_kind(full_path) in {"midi", "eseq"}:
                file_paths.append(full_path)
        return file_paths

    def _should_promote_regular_drop_to_eseq(self, file_kinds):
        if self.is_image_mode() or self.is_local_eseq_mode() or self._regular_midi_file_count() > 0:
            return False
        if "pianodir" in file_kinds:
            return True
        accepted_kinds = [kind for kind in file_kinds if kind in {"midi", "eseq"}]
        return bool(accepted_kinds) and all(kind == "eseq" for kind in accepted_kinds)

    def prepare_regular_file_drop(self, file_paths):
        file_paths = [os.path.abspath(path) for path in (file_paths or [])]
        file_kinds = [
            self._regular_drop_file_kind(path)
            for path in file_paths
        ]
        self.regularDropBatchPrepared = True
        self.regularDropBatchPromotesToEseq = self._should_promote_regular_drop_to_eseq(file_kinds)
        self.regularDropConflictChoice = ""
        self.regularDropCancelled = False
        if self.regularDropBatchPromotesToEseq:
            self.regularEseqMode = True
            variants = {
                self._regular_path_eseq_variant(path)
                for path, kind in zip(file_paths, file_kinds)
                if kind in {"pianodir", "eseq"}
            }
            self.regularEseqVariant = (
                ESEQ_VARIANT_CLAVINOVA
                if ESEQ_VARIANT_CLAVINOVA in variants and ESEQ_VARIANT_DISKLAVIER not in variants
                else ESEQ_VARIANT_DISKLAVIER
            )

    def _find_regular_row_for_path(self, full_path):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.text() == full_path:
                return row
        return -1

    def _regular_row_source_path(self, row):
        item = self.table.item(row, 1)
        return item.text() if item is not None else ""

    def _find_regular_row_for_filename(self, filename):
        target_name = os.path.basename(filename or "").upper()
        if not target_name:
            return -1
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            filename_item = self.table.item(row, 3)
            row_name = filename_item.text() if filename_item is not None else ""
            if not row_name:
                row_name = os.path.basename(self._regular_row_source_path(row))
            if row_name.upper() == target_name:
                return row
        return -1

    def _regular_drop_conflict_choice(self, incoming_path, existing_path, conflict_filename=None):
        if self.regularDropCancelled:
            return "cancel"
        if self.regularDropConflictChoice:
            return self.regularDropConflictChoice

        filename = conflict_filename or os.path.basename(incoming_path)
        choice, do_all = self._prompt_drop_filename_conflict(
            filename=filename,
            existing_label=existing_path,
            existing_modified=self._file_modified_timestamp(existing_path),
            incoming_path=incoming_path,
            incoming_modified=self._file_modified_timestamp(incoming_path),
            allow_do_all=self.regularDropBatchPrepared,
        )
        if do_all:
            self.regularDropConflictChoice = choice
        if choice == "cancel":
            self.regularDropCancelled = True
        return choice

    def _remove_regular_row_for_path(self, full_path):
        row = self._find_regular_row_for_path(full_path)
        if row >= 0:
            self.table.removeRow(row)
        self.pendingEdits.pop(full_path, None)
        self.pendingRegularConversions.pop(full_path, None)
        self.pendingRegularRenames.pop(full_path, None)
        self.listedFileInfo.pop(full_path, None)

    def _stage_regular_row_conversion(self, row, full_path, target_kind):
        target_filename = self._converted_regular_filename_for_kind(
            full_path,
            target_kind,
            used_filenames=self._regular_used_output_filenames_for_directory(
                os.path.dirname(full_path),
                exclude_row=row,
            ),
        )
        output_temp_path = os.path.join(
            self._ensure_midi_scratch_dir(),
            f"{uuid.uuid4().hex}_{target_filename}",
        )
        source_material_path = self._regular_source_material_path(full_path)
        title_override = self._row_raw_title(row) or None

        if target_kind == "midi":
            convert_eseq_file_to_midi_path(
                source_material_path,
                output_temp_path,
                title_override=title_override,
            )
        else:
            source_material_path = self._type0_midi_source_for_eseq_conversion(
                source_material_path,
                self._ensure_midi_scratch_dir(),
                target_filename,
            )
            convert_midi_file_to_eseq_path(
                source_material_path,
                output_temp_path,
                title_override=title_override,
                filename_hint=target_filename,
                container_variant=self._eseq_converter_container(self.regularEseqVariant),
            )

        self._apply_regular_row_pending_conversion(
            row,
            full_path,
            target_filename,
            output_temp_path,
            target_kind,
        )

    def _load_regular_pianodir_from_path(self, full_path):
        self.regularEseqVariant = self._regular_path_eseq_variant(full_path)
        self.regularHasPianodir = True
        self.regularEseqMode = True
        self.regularPianodirSourcePath = full_path
        try:
            size = os.path.getsize(full_path)
            self.regularPianodirPopulated = (
                musicdir_is_populated(size)
                if self.regularEseqVariant == ESEQ_VARIANT_CLAVINOVA
                else pianodir_is_populated(size)
            )
        except OSError:
            self.regularPianodirPopulated = False
        try:
            metadata = (
                PianodirMetadata()
                if self.regularEseqVariant == ESEQ_VARIANT_CLAVINOVA
                else read_pianodir_metadata_from_file(full_path)
            )
        except Exception:
            metadata = PianodirMetadata()
        self.loadedRegularPianodirMetadata = metadata
        self._set_loaded_regular_pianodir_metadata(metadata)
        self.pendingGeneratePianodir = False
        return self.regularPianodirPopulated

    def add_regular_file_from_drop(self, file_path):
        full_path = os.path.abspath(file_path)
        file_kind = self._regular_drop_file_kind(full_path)
        if file_kind == "pianodir":
            self._load_regular_pianodir_from_path(full_path)
            return {
                "status": "added",
                "kind": "pianodir",
                "path": full_path,
                "message": f"Loaded {self._eseq_directory_filename(self.regularEseqVariant)}.",
            }
        if file_kind not in {"midi", "eseq"}:
            return {"status": "error", "path": full_path, "message": "Unsupported file type."}
        if self.regularDropBatchPrepared:
            should_promote = self.regularDropBatchPromotesToEseq
        else:
            should_promote = self._should_promote_regular_drop_to_eseq([file_kind])
        if should_promote:
            self.regularEseqMode = True
            self.regularEseqVariant = self._regular_path_eseq_variant(full_path)

        title, midi_type, title_mode, _is_midi, order_key = self._probe_regular_file(full_path)
        if title_mode not in {"midi", "eseq"}:
            return {"status": "error", "path": full_path, "message": "Could not read file metadata."}

        target_kind = ""
        if self.is_local_eseq_mode() and title_mode == "midi":
            target_kind = "eseq"
        elif not self.is_local_eseq_mode() and title_mode == "eseq":
            target_kind = "midi"

        display_filename = (
            self._converted_regular_filename_for_kind(full_path, target_kind, used_filenames=set())
            if target_kind else
            os.path.basename(full_path)
        )
        existing_row = self._find_regular_row_for_filename(display_filename)
        if existing_row >= 0:
            existing_path = self._regular_row_source_path(existing_row)
            choice = self._regular_drop_conflict_choice(
                full_path,
                existing_path,
                conflict_filename=display_filename,
            )
            if choice == "cancel":
                return {"status": "cancelled", "path": full_path, "message": "Drop cancelled."}
            if choice != "replace":
                return {"status": "skipped", "path": full_path, "message": "Kept listed file."}
            self._remove_regular_row_for_path(existing_path)

        will_add_eseq = self.is_local_eseq_mode() and (title_mode == "eseq" or target_kind == "eseq")
        if will_add_eseq and not self._ensure_eseq_file_limit(
            self._current_eseq_file_count() + 1,
            action_text="Dropping this file",
        ):
            return {"status": "error", "path": full_path, "message": "E-SEQ file limit exceeded."}

        self.add_table_row(
            full_path,
            os.path.basename(full_path),
            title,
            midi_type,
            title_mode=title_mode,
            order_key=order_key,
        )

        if not target_kind:
            return {"status": "added", "path": full_path, "message": "Added."}

        row = self._find_regular_row_for_path(full_path)
        if row < 0:
            return {"status": "error", "path": full_path, "message": "Could not find added row."}

        try:
            self._stage_regular_row_conversion(row, full_path, target_kind)
            if target_kind == "eseq" and not self.regularHasPianodir:
                self.pendingGeneratePianodir = True
            return {
                "status": "converted",
                "path": full_path,
                "message": f"Staged {title_mode.upper()} -> {target_kind.upper()} conversion.",
            }
        except Exception as exc:
            self._remove_regular_row_for_path(full_path)
            return {
                "status": "error",
                "path": full_path,
                "message": f"Could not stage automatic {title_mode.upper()} -> {target_kind.upper()} conversion: {exc}",
            }

    def finish_regular_file_drop(self, results):
        self.regularDropBatchPrepared = False
        self.regularDropBatchPromotesToEseq = False
        self.regularDropConflictChoice = ""
        self.regularDropCancelled = False
        results = [result for result in (results or []) if result]
        accepted_paths = [
            result.get("path", "")
            for result in results
            if result.get("status") in {"added", "converted"}
        ]
        if accepted_paths and not self.regularModeContextPath:
            self._set_regular_mode_context(file_paths=accepted_paths)

        self._refresh_regular_eseq_mode()
        if self.regularEseqMode:
            self._refresh_regular_pianodir_row()
        else:
            self._apply_midi_mode_ui()

        self._reapply_regular_centered_title_assumption()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()

        added_count = sum(1 for result in results if result.get("status") == "added")
        converted_count = sum(1 for result in results if result.get("status") == "converted")
        skipped_count = sum(1 for result in results if result.get("status") == "skipped")
        cancelled_count = sum(1 for result in results if result.get("status") == "cancelled")
        errors = [result for result in results if result.get("status") == "error"]

        status_parts = []
        if added_count:
            status_parts.append(f"Added {added_count} file(s).")
        if converted_count:
            status_parts.append(f"Staged {converted_count} dropped file(s) for automatic conversion.")
            status_parts.append("Use Save, Save As, or Save As Image to write the converted files.")
        if skipped_count:
            status_parts.append(f"Skipped {skipped_count} file(s).")
        if cancelled_count:
            status_parts.append("Drop cancelled.")
        if errors:
            status_parts.append(f"{len(errors)} file(s) could not be added.")
        if status_parts:
            pianodir_count = sum(
                1
                for result in results
                if result.get("status") == "added" and result.get("kind") == "pianodir"
            )
            if pianodir_count:
                status_parts.append(f"Loaded {self._eseq_directory_filename(self.regularEseqVariant)}.")
            self.status_label.setText("\n".join(status_parts))

        if errors:
            details = [
                f"{os.path.basename(result.get('path', ''))}: {result.get('message', '')}"
                for result in errors
            ]
            self._show_error_list(
                "Some Files Were Not Added",
                "Some dropped files could not be added to the list",
                details,
                warning=True,
                guidance="Unsupported or unreadable files were skipped; the files already added remain staged",
            )

    def _load_regular_files(self, file_paths, status_text):
        self.table.setSortingEnabled(False)
        self._clear_regular_list_state()
        self._set_regular_mode_context(file_paths=file_paths)
        regular_specs = []
        loaded_pianodir_metadata = PianodirMetadata()
        music_dir_order_keys = {}
        if any(os.path.basename(path).upper() == MUSICDIR_FILENAME for path in file_paths):
            self.regularEseqVariant = ESEQ_VARIANT_CLAVINOVA
        elif any(is_clavinova_mda_file(path) for path in file_paths if os.path.isfile(path)):
            self.regularEseqVariant = ESEQ_VARIANT_CLAVINOVA
        for full_path in sorted(file_paths, key=lambda path: (os.path.basename(path).upper(), path.upper())):
            basename_upper = os.path.basename(full_path).upper()
            if basename_upper in {PIANODIR_FILENAME, MUSICDIR_FILENAME}:
                if basename_upper == MUSICDIR_FILENAME:
                    self.regularEseqVariant = ESEQ_VARIANT_CLAVINOVA
                    try:
                        music_dir_order_keys.update(read_music_dir_order_keys_from_file(full_path))
                    except Exception:
                        pass
                self.regularHasPianodir = True
                self.regularPianodirSourcePath = full_path
                try:
                    size = os.path.getsize(full_path)
                    populated = (
                        musicdir_is_populated(size)
                        if basename_upper == MUSICDIR_FILENAME
                        else pianodir_is_populated(size)
                    )
                    self.regularPianodirPopulated = self.regularPianodirPopulated or populated
                except OSError:
                    pass
                try:
                    loaded_pianodir_metadata = (
                        PianodirMetadata()
                        if basename_upper == MUSICDIR_FILENAME
                        else read_pianodir_metadata_from_file(full_path)
                    )
                except Exception:
                    loaded_pianodir_metadata = PianodirMetadata()
                continue
            title, midi_type, title_mode, _, order_key = self._probe_regular_file(full_path)
            if title_mode == "eseq" and os.path.basename(full_path).upper() in music_dir_order_keys:
                order_key = music_dir_order_keys[os.path.basename(full_path).upper()]
            regular_specs.append(
                (
                    full_path,
                    os.path.basename(full_path),
                    title,
                    midi_type,
                    title_mode,
                    order_key,
                )
            )

        self.loadedRegularEseqPaths = tuple(
            sorted(
                (
                    full_path
                    for full_path, _filename, _title, _midi_type, title_mode, _order_key in regular_specs
                    if title_mode == "eseq"
                ),
                key=str.upper,
            )
        )

        self.regularEseqMode = self.regularHasPianodir or any(spec[4] == "eseq" for spec in regular_specs)
        if self.regularEseqMode:
            self._enable_disklavier_screen_format_option()
            regular_specs.sort(
                key=lambda spec: (
                    0 if spec[4] == "eseq" else 1,
                    spec[5] if spec[4] == "eseq" else b"",
                    spec[1].upper(),
                    spec[0].upper(),
                )
            )
        if self.regularEseqMode:
            self._apply_local_eseq_mode_ui()
        else:
            self._apply_midi_mode_ui()

        self._update_regular_centered_title_assumption(
            candidate_titles=[
                spec[2]
                for spec in regular_specs
                if spec[2] and spec[4] in {"midi", "eseq"}
            ]
        )
        for full_path, filename, title, midi_type, title_mode, order_key in regular_specs:
            self.add_table_row(
                full_path,
                filename,
                title,
                midi_type,
                title_mode=title_mode,
                order_key=order_key,
            )

        if self.regularEseqMode:
            self._set_loaded_regular_pianodir_metadata(loaded_pianodir_metadata)
            self._refresh_regular_pianodir_row()
        else:
            self._set_loaded_regular_pianodir_metadata(PianodirMetadata())
            self.table.setSortingEnabled(True)
            self.table.sortItems(3, order=Qt.AscendingOrder)
        self._refresh_regular_title_display_items()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()
        self.status_label.setText(status_text)

    def _refresh_regular_mode_action_state(self):
        if self.is_image_mode():
            self._refresh_eseq_reorder_buttons()
            return

        row_count = self._regular_file_count()
        midi_count = 0
        eseq_count = 0
        unknown_count = 0
        for full_path, info in self.listedFileInfo.items():
            title_mode = info.get("title_mode", "")
            if title_mode == "midi":
                midi_count += 1
            elif title_mode == "eseq":
                eseq_count += 1
            else:
                unknown_count += 1

        has_midi = midi_count > 0
        has_eseq = eseq_count > 0
        has_only_midi = row_count > 0 and midi_count == row_count
        rename_needed = has_only_midi and self._regular_filenames_need_dos83_rename()
        type0_needed = has_only_midi and self._regular_midi_files_need_type0_conversion()
        if self.is_local_eseq_mode():
            self._set_rename_all_enabled(False, "Rename 8.3 is available for MIDI folders only.")
            self._set_type0_enabled(False, "SMF1 -> SMF0 is available for MIDI folders only.")
        else:
            self._set_rename_all_enabled(rename_needed)
            self._set_type0_enabled(type0_needed)
        self.convertMidiToEseqButton.setEnabled(has_midi)
        self.convertEseqToMidiButton.setEnabled(has_eseq)

        if row_count == 0:
            self._set_rename_all_enabled(False, "Add MIDI files before using Rename 8.3.")
            self._set_type0_enabled(False, "Add MIDI files before using SMF1 -> SMF0.")
            self.convertMidiToEseqButton.setEnabled(False)
            self.convertEseqToMidiButton.setEnabled(False)
        elif unknown_count:
            self._set_rename_all_enabled(False, "Rename 8.3 is available only when all listed files are MIDI files.")
            self._set_type0_enabled(False, "SMF1 -> SMF0 is available only when all listed files are MIDI files.")
        elif has_only_midi and not rename_needed:
            self._set_rename_all_enabled(False, "All listed filenames are already 8.3 length or shorter.")
            if not type0_needed:
                self._set_type0_enabled(False, "All listed MIDI files are already SMF0 / Type 0.")
        self._refresh_eseq_reorder_buttons()
        self._update_menu_actions()

    def _filename_needs_dos83_rename(self, filename):
        name = os.path.basename(filename or "")
        if not name:
            return False
        stem, ext = os.path.splitext(name)
        if not stem:
            return False
        if "." in stem:
            return True
        extension = ext[1:] if ext.startswith(".") else ext
        return len(stem) > 8 or len(extension) > 3

    def _regular_filenames_need_dos83_rename(self):
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            if self._listed_file_title_mode(full_path_item.text()) != "midi":
                continue
            if self._filename_needs_dos83_rename(self._regular_row_output_filename(row)):
                return True
        return False

    def _row_midi_type_label(self, row, full_path=""):
        type_item = self.table.item(row, 6)
        if type_item is not None:
            label = type_item.text().strip()
            if label:
                return label
        if full_path:
            return extract_midi_type_label_from_midi(full_path)
        return ""

    def _regular_midi_files_need_type0_conversion(self):
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) != "midi":
                continue
            if self._row_midi_type_label(row, full_path) != "Type 0":
                return True
        return False

    def _set_rename_all_enabled(self, enabled, disabled_tooltip=""):
        self.renameAllButton.setEnabled(bool(enabled))
        if enabled:
            tooltip = "Rename every listed MIDI file to DOS 8.3 format (00.MID, 01.MID, ...)."
        else:
            tooltip = disabled_tooltip or "Rename 8.3 is not needed for the current list."
        self.renameAllButton.setToolTip(tooltip)

    def _set_type0_enabled(self, enabled, disabled_tooltip=""):
        self.convertType0Button.setEnabled(bool(enabled))
        if enabled:
            tooltip = "Convert every listed MIDI file to SMF0 / MIDI Type 0."
        else:
            tooltip = disabled_tooltip or "SMF1 -> SMF0 is not needed for the current list."
        self.convertType0Button.setToolTip(tooltip)

    def _image_mode_file_counts(self):
        midi_count = 0
        eseq_count = 0
        unknown_count = 0
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            is_midi = self._image_path_is_midi(source_path)
            if self._is_eseq_candidate(final_path, is_midi=is_midi):
                eseq_count += 1
            elif is_midi:
                midi_count += 1
            else:
                unknown_count += 1
        return midi_count, eseq_count, unknown_count

    def _refresh_image_mode_action_state(self):
        if not self.is_image_mode():
            return
        midi_count, eseq_count, unknown_count = self._image_mode_file_counts()
        row_count = midi_count + eseq_count + unknown_count
        has_only_midi = row_count > 0 and midi_count == row_count
        has_only_eseq = row_count > 0 and eseq_count == row_count
        self.convertMidiToEseqButton.setEnabled(has_only_midi)
        self.convertEseqToMidiButton.setEnabled(has_only_eseq)
        self._update_menu_actions()

    def _write_listed_file_to_path(self, source_path, new_title, dest_path, *, order_key=None):
        source_material_path = self._regular_source_material_path(source_path)
        title_mode = self._listed_file_title_mode(source_path)
        if title_mode == "eseq":
            return self._write_eseq_file_to_path(source_material_path, dest_path, title=new_title, order_key=order_key)
        if title_mode == "midi":
            return update_midi_title_to_path(source_material_path, new_title, dest_path)
        try:
            shutil.copy2(source_material_path, dest_path)
            return None
        except Exception as exc:
            return f"Error copying {os.path.basename(source_path)}: {exc}"

    def _write_eseq_file_to_path(self, source_path, dest_path, *, title=None, order_key=None):
        source_abs = os.path.abspath(source_path)
        dest_abs = os.path.abspath(dest_path)
        temp_path = ""
        try:
            if source_abs == dest_abs:
                temp_path = os.path.join(
                    os.path.dirname(dest_abs),
                    f".{os.path.basename(dest_abs)}.aps_{uuid.uuid4().hex}",
                )
                if title is not None:
                    error_msg = update_eseq_title_to_path(source_path, title, temp_path)
                else:
                    shutil.copy2(source_path, temp_path)
                    error_msg = None
                if error_msg:
                    return error_msg
                if order_key is not None:
                    error_msg = update_eseq_order_key(temp_path, order_key)
                    if error_msg:
                        return error_msg
                os.replace(temp_path, dest_path)
                temp_path = ""
                return None

            if title is not None:
                error_msg = update_eseq_title_to_path(source_path, title, dest_path)
            else:
                shutil.copy2(source_path, dest_path)
                error_msg = None
            if error_msg:
                return error_msg
            if order_key is not None:
                return update_eseq_order_key(dest_path, order_key)
            return None
        except Exception as exc:
            return f"Could not write updated E-SEQ data for {os.path.basename(source_path)}: {exc}"
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _image_export_relative_parts(self, image_path):
        parts = [part for part in image_path.replace("\\", "/").split("/") if part]
        if parts:
            return parts
        fallback_name = os.path.basename(image_path) or "image-file"
        return [fallback_name]

    def _write_image_row_to_destination(self, source_path, dest_path, *, order_key=None):
        source_host_path = self._pending_or_extracted_image_path(source_path)
        final_name = os.path.basename(self._final_image_path(source_path)) or os.path.basename(dest_path)
        if not source_host_path or not os.path.isfile(source_host_path):
            raise FloppyImageError(f"Could not prepare '{final_name}' for export.")

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        pending_title = self.pendingImageTitleEdits.get(source_path)
        title_mode = self._image_path_title_mode(source_path)

        if title_mode == "eseq":
            error_msg = self._write_eseq_file_to_path(
                source_host_path,
                dest_path,
                title=pending_title if pending_title is not None else None,
                order_key=order_key,
            )
        elif pending_title and title_mode == "midi":
            error_msg = update_midi_title_to_path(source_host_path, pending_title, dest_path)
        else:
            try:
                shutil.copy2(source_host_path, dest_path)
                error_msg = None
            except Exception as exc:
                error_msg = f"Error copying {final_name}: {exc}"

        if error_msg:
            raise FloppyImageError(error_msg)

    def _build_regular_pianodir_entries(self, path_remap=None):
        entries = []
        path_remap = path_remap or {}

        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) != "eseq":
                continue

            local_path = path_remap.get(full_path, full_path)
            if not local_path or not os.path.isfile(local_path):
                continue

            display_title = self._row_raw_title(row)
            entries.append(
                PianodirTrackEntry(
                    image_path=os.path.basename(local_path),
                    local_path=local_path,
                    title=display_title,
                )
            )

        return entries

    def _write_regular_pianodir(self, *, base_dir=None, path_remap=None):
        entries = self._build_regular_pianodir_entries(path_remap=path_remap)
        if not entries:
            raise FloppyImageError(
                f"No E-SEQ files were available to build {self._eseq_directory_filename(self.regularEseqVariant)}."
            )

        output_path = self._regular_pianodir_path(base_dir=base_dir)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as handle:
            if self.regularEseqVariant == ESEQ_VARIANT_CLAVINOVA:
                handle.write(build_music_dir_bytes(entries))
            else:
                handle.write(
                    build_pianodir_bytes(
                        entries,
                        metadata=self._regular_pianodir_metadata_for_save(),
                    )
                )
        return output_path

    def _export_image_session_files_to_folder(self, dest_dir, progress_callback=None):
        export_rows = []
        order_key_edits = self._image_eseq_order_key_edits()
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            if self._is_special_pianodir_path(source_path) or source_path in self.pendingImageDeletes:
                continue
            export_rows.append((row, source_path))

        generate_pianodir = self._should_generate_pianodir(for_export=True)
        total_steps = len(export_rows) + (1 if generate_pianodir else 0)
        total_steps = max(1, total_steps)
        output_paths = []
        pianodir_entries = []

        for index, (row, source_path) in enumerate(export_rows, start=1):
            final_image_path = self._final_image_path(source_path)
            display_name = os.path.basename(final_image_path) or final_image_path
            if progress_callback is not None:
                progress_callback(index - 1, total_steps, f"Saving {display_name}...")

            dest_path = os.path.join(dest_dir, *self._image_export_relative_parts(final_image_path))
            self._write_image_row_to_destination(
                source_path,
                dest_path,
                order_key=order_key_edits.get(source_path),
            )
            output_paths.append(dest_path)

            if self._is_eseq_candidate(final_image_path, is_midi=self._image_path_is_midi(source_path)):
                display_title = self._row_raw_title(row)
                pianodir_entries.append(
                    PianodirTrackEntry(
                        image_path=final_image_path,
                        local_path=dest_path,
                        title=display_title,
                    )
                )

        if generate_pianodir:
            if progress_callback is not None:
                progress_callback(len(export_rows), total_steps, f"Generating {self._eseq_directory_filename(self.imageEseqVariant)}...")
            pianodir_path = os.path.join(dest_dir, self._eseq_directory_filename(self.imageEseqVariant))
            os.makedirs(os.path.dirname(pianodir_path), exist_ok=True)
            with open(pianodir_path, "wb") as handle:
                if self.imageEseqVariant == ESEQ_VARIANT_CLAVINOVA:
                    handle.write(build_music_dir_bytes(pianodir_entries))
                else:
                    handle.write(
                        build_pianodir_bytes(
                            pianodir_entries,
                            metadata=self._image_pianodir_metadata_for_save(),
                        )
                    )
            output_paths.append(pianodir_path)
        elif self.imageHasPianodir and not self.pendingDeletePianodir:
            existing_pianodir = self._existing_image_pianodir_host_path()
            if existing_pianodir and os.path.isfile(existing_pianodir):
                pianodir_path = os.path.join(dest_dir, self._eseq_directory_filename(self.imageEseqVariant))
                os.makedirs(os.path.dirname(pianodir_path), exist_ok=True)
                shutil.copy2(existing_pianodir, pianodir_path)
                output_paths.append(pianodir_path)

        if progress_callback is not None:
            progress_callback(total_steps, total_steps, "Finalizing exported files...")

        return output_paths

    def _apply_midi_mode_ui(self):
        self._apply_compact_button_labels()
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "Long", "Type"])
        self.choose_button.setText("Open MIDI Folder")
        self.choose_button.setToolTip("Select a folder to scan for .mid and .midi files.")
        self.open_image_button.setEnabled(True)
        self.open_image_button.setToolTip("Open a floppy image file for editing in Image Mode.")
        self.read_floppy_button.setEnabled(True)
        self.read_floppy_button.setToolTip(
            "Read a floppy from a floppy drive or from a Greaseweazle-connected drive."
        )
        self.table.setToolTip(
            "Drop MIDI files here, click a Title cell to edit, or click the clipboard icon to copy a filename."
        )
        self._set_rename_all_enabled(True)
        self._set_type0_enabled(True)
        self.convertEseqToMidiButton.setEnabled(False)
        self.convertMidiToEseqButton.setEnabled(False)
        self.table.setColumnHidden(6, False)
        self.saveButton.setVisible(True)
        self.saveAsButton.setVisible(True)
        self.saveAsImageButton.setVisible(True)
        self.saveButton.setToolTip("Write pending title edits to the currently listed files.")
        self.saveAsButton.setToolTip("Save copies with current titles to a selected destination folder.")
        self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self.clearButton.setToolTip("Remove all files from the current list.")
        self._set_mode_banner("MIDI Mode", self._regular_mode_context_label())
        self._update_compat_warning_ui()
        self._update_floppy_save_option_ui()
        self._update_image_pianodir_metadata_ui()
        self._refresh_regular_mode_action_state()
        self._refresh_eseq_reorder_buttons()
        self._refresh_disk_usage_bars()
        self._resize_table_columns_to_fill()

    def _apply_local_eseq_mode_ui(self):
        self._apply_compact_button_labels()
        mode_label = self._eseq_mode_label(self.regularEseqVariant)
        directory_name = self._eseq_directory_filename(self.regularEseqVariant)
        self.table.setHorizontalHeaderLabels(["X", "FullPath", "📋", "Filename", "Title", "Long", "Type"])
        self.choose_button.setText("Open MIDI Folder")
        self.choose_button.setToolTip(f"Leave {mode_label} Mode and select a folder to scan for .mid and .midi files.")
        self.open_image_button.setEnabled(True)
        self.open_image_button.setToolTip("Open a floppy image file for editing in Image Mode.")
        self.read_floppy_button.setEnabled(True)
        self.read_floppy_button.setToolTip(
            "Read a floppy from a floppy drive or from a Greaseweazle-connected drive."
        )
        self.table.setToolTip(
            f"{mode_label} Mode: edit local MIDI and E-SEQ titles, and manage the local {directory_name} row."
        )
        self._set_rename_all_enabled(False, "Rename 8.3 is available for MIDI folders only.")
        self._set_type0_enabled(False, "SMF1 -> SMF0 is available for MIDI folders only.")
        self.convertEseqToMidiButton.setEnabled(True)
        self.convertMidiToEseqButton.setEnabled(True)
        self.table.setColumnHidden(6, False)
        self.table.setSortingEnabled(False)
        self.saveButton.setVisible(True)
        self.saveAsButton.setVisible(True)
        self.saveAsImageButton.setVisible(True)
        self.saveButton.setToolTip(f"Write pending title edits to the currently listed local files and update {directory_name}.")
        self.saveAsButton.setToolTip(f"Save local E-SEQ files and {directory_name} to a selected destination folder.")
        self.saveAsImageButton.setToolTip("Create one or more floppy images from the currently listed files.")
        self.clearButton.setToolTip("Remove all files from the current E-SEQ list.")
        self._set_mode_banner(f"{mode_label} Mode", self._regular_mode_context_label())
        self._update_compat_warning_ui()
        self._update_floppy_save_option_ui()
        self._update_image_pianodir_metadata_ui()
        self._refresh_regular_mode_action_state()
        self._refresh_eseq_reorder_buttons()
        self._refresh_disk_usage_bars()
        self._resize_table_columns_to_fill()

    def _load_midi_paths_into_list(self, midi_specs, status_text):
        self.table.setSortingEnabled(False)
        self._clear_regular_list_state()
        self._set_regular_mode_context(file_paths=[spec[0] for spec in midi_specs])
        self._apply_midi_mode_ui()

        sorted_specs = sorted(
            midi_specs,
            key=lambda spec: (spec[1].upper(), spec[0].upper()),
        )
        self._update_regular_centered_title_assumption(
            candidate_titles=[spec[2] for spec in sorted_specs if spec[2]]
        )

        for full_path, filename, title, midi_type in sorted_specs:
            self.add_table_row(full_path, filename, title, midi_type, title_mode="midi")

        self.table.setSortingEnabled(True)
        self.table.sortItems(3, order=Qt.AscendingOrder)
        self._refresh_regular_title_display_items()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()
        self.status_label.setText(status_text)

    def _apply_image_mode_ui(self):
        self._apply_compact_button_labels()
        mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        mode_banner = self._disk_mode_banner_headline()
        self.table.setHorizontalHeaderLabels(["X", "ImagePath", "📋", "Filename", "Title", "Long", "Type"])
        self.choose_button.setText("Open MIDI Folder")
        self.choose_button.setToolTip(f"Leave {mode_name} and select a folder to scan for .mid and .midi files.")
        self.open_image_button.setEnabled(True)
        self.open_image_button.setToolTip("Open another floppy image file for editing in Image Mode.")
        self.read_floppy_button.setEnabled(True)
        self.read_floppy_button.setToolTip(
            "Read another floppy from a floppy drive or a Greaseweazle-connected drive."
        )
        self.table.setToolTip(
            f"{mode_banner}: edit titles, rename files, remove rows to delete files on Save, or drop files to add them."
        )
        self._set_rename_all_enabled(False, "Rename 8.3 is available for MIDI folders only.")
        self._set_type0_enabled(False, "SMF1 -> SMF0 is available for MIDI folders only.")
        self.convertEseqToMidiButton.setEnabled(True)
        self.convertMidiToEseqButton.setEnabled(True)
        self.table.setColumnHidden(6, False)
        if self.image_session is not None and self.image_session.source_kind.startswith("floppy"):
            self.clearButton.setToolTip("Leave Floppy Mode and clear the current floppy list.")
        else:
            self.saveButton.setToolTip(
                "Save pending title edits, filename edits, removals, and additions back into the image."
            )
            self.clearButton.setToolTip("Leave Image Mode and clear the current image list.")
        self.saveButton.setVisible(True)
        self.saveAsButton.setVisible(True)
        self.saveAsButton.setToolTip(
            f"Save the current {mode_name.lower()}'s listed files to a destination folder and leave {mode_name}."
        )
        self.saveAsImageButton.setVisible(True)
        self.saveAsImageButton.setText("Save As Image")
        self.saveAsImageButton.setToolTip(f"Save the current {mode_name.lower()} as a separate image file.")
        self._set_mode_banner(mode_banner, self.image_session.source_name if self.image_session is not None else "")
        self._update_compat_warning_ui()
        self._update_floppy_save_option_ui()
        self._update_image_pianodir_metadata_ui()
        self._refresh_eseq_reorder_buttons()
        self._refresh_image_mode_action_state()
        self._refresh_disk_usage_bars()
        self._resize_table_columns_to_fill()

    def _image_mode_summary(self):
        if self.image_session is None:
            return ""
        listing = self.image_session.list_entries()
        return (
            f"{self._disk_mode_banner_headline()}: {self.image_session.source_name} "
            f"({self.image_session.disk_format.label}, {display_bytes(self.image_session.disk_format.size_bytes)}). "
            f"{len(listing.entries)} file(s), {display_bytes(listing.free_space)} free."
        )

    def _image_info_for_path(self, image_path):
        return self.imageFileInfo.get(image_path, {})

    def _set_image_file_info(self, image_path, *, is_midi=False, title="", midi_type="", size=0, title_mode="", order_key=b""):
        self.imageFileInfo[image_path] = {
            "is_midi": bool(is_midi),
            "title": title or "",
            "midi_type": midi_type or "",
            "size": int(size or 0),
            "title_mode": title_mode or "",
            "order_key": normalize_eseq_order_key(order_key),
        }

    def _pending_or_extracted_image_path(self, image_path):
        if image_path in self.pendingImageAdditions:
            return self.pendingImageAdditions[image_path]
        if image_path in self.pendingImageReplacements:
            return self.pendingImageReplacements[image_path]
        if self.image_session is None:
            return ""
        return self.image_session.extract_file(image_path)

    def _is_special_pianodir_path(self, image_path):
        return image_path == PIANODIR_ROW_PATH

    def _is_special_pianodir_row(self, row):
        path_item = self.table.item(row, 1)
        return bool(path_item and self._is_special_pianodir_path(path_item.text()))

    def _final_image_path(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return image_path
        return self.pendingImageRenames.get(image_path, image_path)

    def _row_final_image_path(self, row):
        path_item = self.table.item(row, 1)
        if path_item is None:
            return ""
        return self._final_image_path(path_item.text())

    def _image_path_is_midi(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return False
        info = self._image_info_for_path(image_path)
        if info:
            return bool(info.get("is_midi"))
        return os.path.splitext(image_path)[1].lower() in {".mid", ".midi"}

    def _image_path_title_mode(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return ""
        return self._image_info_for_path(image_path).get("title_mode", "")

    def _image_path_order_key(self, image_path):
        if self._is_special_pianodir_path(image_path):
            return b""
        return normalize_eseq_order_key(self._image_info_for_path(image_path).get("order_key", b""))

    def _image_path_has_editable_title(self, image_path):
        return bool(self._image_path_title_mode(image_path))

    def _is_eseq_candidate(self, image_path, *, is_midi=None):
        if self._is_special_pianodir_path(image_path) or is_eseq_directory_path(image_path):
            return False
        info = self._image_info_for_path(image_path)
        if not info:
            normalized_target = image_path.replace("\\", "/").upper()
            for source_path, source_info in self.imageFileInfo.items():
                if self._final_image_path(source_path).replace("\\", "/").upper() == normalized_target:
                    info = source_info
                    break
        if info.get("title_mode") == "eseq":
            return True
        if is_midi is None:
            is_midi = self._image_path_is_midi(image_path)
        if not is_eseq_filename(image_path):
            return False
        return self.imageHasPianodir or bool(is_midi)

    def _find_pianodir_row(self):
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                return row
        return -1

    def _pianodir_row_colors(self, is_present):
        if is_dark_theme():
            if is_present:
                return QColor("#214D2E"), QColor("#E9F8EE")
            return QColor("#5A2326"), QColor("#FDEDEE")
        if is_present:
            return QColor("#D9F2D9"), QColor("#1C1C1C")
        return QColor("#FAD6D6"), QColor("#1C1C1C")

    def _update_image_eseq_mode(self):
        self.imageEseqMode = self.imageHasPianodir and not self.pendingDeletePianodir
        if self.imageEseqMode:
            return
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                self.imageEseqMode = True
                if os.path.splitext(final_path)[1].lower() == ".mda":
                    self.imageEseqVariant = ESEQ_VARIANT_CLAVINOVA
                return

    def _sync_pianodir_requirement(self):
        self._update_image_eseq_mode()
        if self.imageEseqMode:
            self.pendingDeletePianodir = False
            return
        self.imageEseqVariant = ESEQ_VARIANT_DISKLAVIER
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = self.imageHasPianodir

    def _populate_pianodir_row(self, row):
        directory_name = self._eseq_directory_filename(self.imageEseqVariant)
        row_items = []
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(row, column, item)
            row_items.append(item)

        is_present = self.imageHasPianodir and not self.pendingDeletePianodir and self.imagePianodirPopulated
        is_missing = self.imageEseqMode and not is_present
        delete_text = ""
        refresh_on_save = self._should_generate_pianodir()
        title_text = "Present - will refresh on save" if (is_present and refresh_on_save) else ("Present" if is_present else "")
        if is_missing and self.pendingGeneratePianodir:
            title_text = "Missing - will generate on save"
        elif is_missing:
            title_text = "Missing - click to generate"

        row_items[0].setText(delete_text)
        row_items[0].setToolTip(f"{directory_name} is managed automatically.")
        row_items[1].setText(PIANODIR_ROW_PATH)
        row_items[2].setText("")
        row_items[2].setToolTip(f"{directory_name} is managed automatically.")
        row_items[3].setText(directory_name)
        row_items[3].setToolTip("Directory file for Yamaha E-SEQ disks.")
        row_items[4].setText(title_text)
        if is_missing:
            row_items[4].setToolTip(f"Click to offer {directory_name} generation.")
        elif refresh_on_save:
            row_items[4].setToolTip(f"{directory_name} will be refreshed on save because related E-SEQ metadata has changed.")
        else:
            row_items[4].setToolTip(f"{directory_name} is present and will be left unchanged unless E-SEQ metadata changes.")
        row_items[5].setText("")
        row_items[5].setToolTip("Not applicable.")
        row_items[6].setText("DIR")
        row_items[6].setTextAlignment(Qt.AlignCenter)
        row_items[6].setToolTip("Special Yamaha E-SEQ directory file.")

        bg_color, fg_color = self._pianodir_row_colors(is_present)
        for item in row_items:
            item.setBackground(bg_color)
            item.setForeground(fg_color)

    def _refresh_pianodir_row(self):
        self._sync_pianodir_requirement()
        should_show = (self.imageHasPianodir and not self.pendingDeletePianodir) or self.imageEseqMode
        row = self._find_pianodir_row()

        if not should_show:
            if row >= 0:
                self.table.removeRow(row)
            self._apply_image_mode_ui()
            self._update_image_pianodir_metadata_ui()
            return

        if row < 0:
            self.table.insertRow(0)
            row = 0
        elif row != 0:
            self.table.removeRow(row)
            self.table.insertRow(0)
            row = 0

        self._populate_pianodir_row(row)
        self._apply_image_mode_ui()
        self._update_image_pianodir_metadata_ui()

    def _probe_image_file(self, image_path, size, extraction_path):
        is_midi = is_midi_file(extraction_path)
        title = ""
        midi_type = self._kind_for_image_file(image_path)
        title_mode = ""
        order_key = b""
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")

        is_clavinova_mda = is_clavinova_mda_file(extraction_path)
        if is_clavinova_mda:
            title = os.path.splitext(os.path.basename(image_path))[0]
            title_mode = "eseq"
            order_key = build_eseq_order_key_from_path(image_path)
            midi_type = "MDA"
        elif (self._is_eseq_candidate(image_path, is_midi=is_midi) or is_eseq_file(extraction_path)) and has_eseq_title_metadata(extraction_path):
            title = extract_eseq_title_from_file(extraction_path)
            title_mode = "eseq"
            if title.startswith("Error"):
                title = ""
            try:
                order_key = read_eseq_order_key_from_file(extraction_path)
            except Exception:
                order_key = build_eseq_order_key_from_path(image_path)
            eseq_kind = "ESQ" if ext == "esq" else "FIL"
            try:
                arrangement_type = read_eseq_arrangement_type_label_from_file(extraction_path)
            except Exception:
                arrangement_type = ""
            try:
                write_protected = read_eseq_write_protect_from_file(extraction_path)
            except Exception:
                write_protected = None
            midi_type = eseq_type_display_label(eseq_kind, arrangement_type, write_protected)

        if is_midi:
            if title_mode != "eseq":
                try:
                    title = extract_first_title_from_midi(extraction_path)
                    title_mode = "midi"
                except Exception as exc:
                    title = f"Error reading MIDI title: {exc}"
            if title_mode != "eseq":
                try:
                    midi_type = extract_midi_type_label_from_midi(extraction_path)
                except Exception:
                    midi_type = "Error"

        self._set_image_file_info(
            image_path,
            is_midi=is_midi,
            title=title,
            midi_type=midi_type,
            size=size,
            title_mode=title_mode,
            order_key=order_key,
        )
        return is_midi, title, midi_type, title_mode, order_key

    def _make_stage_progress_callback(self, dialog):
        def callback(step, total, message):
            self._apply_stage_progress(dialog, step, total, message)

        return callback

    def _make_dialog_button_box(self, buttons, parent):
        button_box = QDialogButtonBox(buttons, parent=parent)
        button_box.setContentsMargins(0, 8, 6, 4)
        if button_box.layout() is not None:
            button_box.layout().setSpacing(8)
        return button_box

    def _make_dialog_form_grid(self):
        form_grid = QGridLayout()
        form_grid.setContentsMargins(0, 0, 0, 0)
        form_grid.setHorizontalSpacing(12)
        form_grid.setVerticalSpacing(8)
        form_grid.setColumnStretch(1, 1)
        return form_grid

    def _make_dialog_form_label(self, text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def _add_dialog_form_row(self, form_grid, row, label_text, field):
        label = self._make_dialog_form_label(label_text)
        form_grid.addWidget(label, row, 0)
        if isinstance(field, QLayout):
            form_grid.addLayout(field, row, 1)
        else:
            form_grid.addWidget(field, row, 1)
        return label

    def _align_dialog_form_labels(self, labels):
        visible_labels = [label for label in labels if label is not None]
        if not visible_labels:
            return
        label_width = max(label.sizeHint().width() for label in visible_labels)
        for label in visible_labels:
            label.setMinimumWidth(label_width)

    def _prompt_for_new_image_options(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("New Image")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(18, 18, 18, 18)
        dialog_layout.setSpacing(8)

        summary = QLabel("Create a fresh editable floppy image.")
        summary.setWordWrap(True)
        dialog_layout.addWidget(summary)

        type_combo = QComboBox(dialog)
        list_all_types_checkbox = QCheckBox("List all image types")
        disk_combo = QComboBox(dialog)
        list_all_disks_checkbox = QCheckBox("List all disk sizes")
        eseq_checkbox = QCheckBox("E-SEQ disk with empty PIANODIR.FIL")
        eseq_checkbox.setChecked(True)

        form_grid = self._make_dialog_form_grid()
        type_label = self._add_dialog_form_row(form_grid, 0, "Image type:", type_combo)
        type_options_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(type_options_spacer, 1, 0)
        form_grid.addWidget(list_all_types_checkbox, 1, 1)
        disk_label = self._add_dialog_form_row(form_grid, 2, "Disk size:", disk_combo)
        disk_options_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(disk_options_spacer, 3, 0)
        form_grid.addWidget(list_all_disks_checkbox, 3, 1)
        eseq_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(eseq_spacer, 4, 0)
        form_grid.addWidget(eseq_checkbox, 4, 1)
        self._align_dialog_form_labels(
            [type_label, type_options_spacer, disk_label, disk_options_spacer, eseq_spacer]
        )
        dialog_layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        dialog_layout.addWidget(buttons)

        def refresh_type_combo():
            current_ext = type_combo.currentData() or "img"
            options = PREFERRED_OUTPUT_EXTENSIONS if list_all_types_checkbox.isChecked() else self._basic_image_export_types()
            type_combo.clear()
            selected_index = 0
            for index, (ext, label) in enumerate(options):
                type_combo.addItem(label, ext)
                if ext == current_ext:
                    selected_index = index
            type_combo.setCurrentIndex(selected_index)

        def refresh_disk_combo():
            current = disk_combo.currentData()
            current_key = current.key if current is not None else "ibm.720"
            options = DISK_FORMATS if list_all_disks_checkbox.isChecked() else self._basic_disk_export_formats()
            disk_combo.clear()
            selected_index = 0
            for index, disk_format in enumerate(options):
                disk_combo.addItem(f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})", disk_format)
                if disk_format.key == current_key:
                    selected_index = index
            disk_combo.setCurrentIndex(selected_index)

        list_all_types_checkbox.toggled.connect(refresh_type_combo)
        list_all_disks_checkbox.toggled.connect(refresh_disk_combo)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        refresh_type_combo()
        refresh_disk_combo()

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        return {
            "output_ext": type_combo.currentData() or "img",
            "disk_format": disk_combo.currentData(),
            "eseq_disk": eseq_checkbox.isChecked(),
        }

    def new_image_dialog(self):
        if not self._prepare_for_disk_load("a new blank image"):
            return
        options = self._prompt_for_new_image_options()
        if not options:
            return
        disk_format = options["disk_format"]
        if disk_format is None:
            return

        mode_label = "E-SEQ" if options["eseq_disk"] else "MIDI"
        progress_dialog = QProgressDialog("Creating new image...", None, 0, 4, self)
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        progress_dialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progress_dialog)
        progress_callback(0, 4, "Creating new image...")
        QApplication.processEvents()
        session = None
        try:
            session = FloppyImageSession.create_blank_session(
                disk_format,
                source_ext=options["output_ext"],
                eseq_disk=options["eseq_disk"],
                volume_label="ESEQ" if options["eseq_disk"] else "YAMAHA",
                progress_callback=progress_callback,
            )
            listing = session.list_entries()
            self._activate_disk_session(session, listing)
            session = None
            progress_dialog.close()
            self.status_label.setText(
                f"Created a new {disk_format.label} {mode_label} image. Use Save As Image or Write Current Image to Floppy when ready."
            )
        except Exception as exc:
            progress_dialog.close()
            if session is not None:
                session.cleanup()
            self._show_operation_error(
                "New Image Failed",
                "The new image could not be created",
                exc,
            )

    def _choose_format_floppy_options(self):
        floppy_drives = list_floppy_drives()
        greaseweazle_devices = list_greaseweazle_devices()

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Format Floppy Disk...")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        hint = QLabel(
            "Recommended: use a double-density disk and format it as IBM 720K DD for Yamaha Disklavier compatibility."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        target_combo = QComboBox(dialog)
        target_combo.addItem("Floppy Drive", "floppy_usb")
        target_combo.addItem("Greaseweazle", "floppy_gw")
        if not floppy_drives and greaseweazle_devices:
            target_combo.setCurrentIndex(1)

        format_combo = QComboBox(dialog)
        default_index = 0
        for index, disk_format in enumerate(DISK_FORMATS):
            format_combo.addItem(
                f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})",
                disk_format,
            )
            if disk_format.key == "ibm.720":
                default_index = index
        format_combo.setCurrentIndex(default_index)

        eseq_checkbox = QCheckBox("Create E-SEQ disk with empty PIANODIR.FIL")
        eseq_checkbox.setToolTip(
            "Adds an empty Yamaha PIANODIR.FIL so the formatted disk opens in E-SEQ mode."
        )

        common_grid = self._make_dialog_form_grid()
        target_label = self._add_dialog_form_row(common_grid, 0, "Format using:", target_combo)
        format_label = self._add_dialog_form_row(common_grid, 1, "Disk format:", format_combo)
        eseq_spacer = self._make_dialog_form_label("")
        common_grid.addWidget(eseq_spacer, 2, 0)
        common_grid.addWidget(eseq_checkbox, 2, 1)
        layout.addLayout(common_grid)

        drive_page = QWidget(dialog)
        drive_grid = self._make_dialog_form_grid()
        drive_page.setLayout(drive_grid)
        drive_combo = QComboBox(drive_page)
        if floppy_drives:
            for drive in floppy_drives:
                drive_combo.addItem(drive.display_name, drive)
        else:
            drive_combo.addItem("No supported floppy drive detected", None)
            drive_combo.setEnabled(False)
        drive_label = self._add_dialog_form_row(drive_grid, 0, "Floppy drive:", drive_combo)
        layout.addWidget(drive_page)

        gw_page = QWidget(dialog)
        gw_grid = self._make_dialog_form_grid()
        gw_page.setLayout(gw_grid)
        gw_device_combo = QComboBox(gw_page)
        if greaseweazle_devices:
            for device in greaseweazle_devices:
                gw_device_combo.addItem(device.display_name, device)
        else:
            gw_device_combo.addItem("No Greaseweazle device detected", None)
            gw_device_combo.setEnabled(False)
        gw_drive_combo = QComboBox(gw_page)
        drive_options = self._greaseweazle_drive_options()
        gw_drive_combo.addItems(drive_options)
        if greaseweazle_devices:
            self._restore_greaseweazle_dialog_selection(
                greaseweazle_devices,
                gw_device_combo,
                drive_options,
                gw_drive_combo,
            )
        gw_device_label = self._add_dialog_form_row(gw_grid, 0, "Greaseweazle device:", gw_device_combo)
        gw_drive_label = self._add_dialog_form_row(gw_grid, 1, "Drive:", gw_drive_combo)
        layout.addWidget(gw_page)

        form_labels = [
            target_label,
            format_label,
            eseq_spacer,
            drive_label,
            gw_device_label,
            gw_drive_label,
        ]
        self._align_dialog_form_labels(form_labels)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def resize_dialog_to_content():
            dialog.layout().activate()
            hint_size = dialog.sizeHint()
            dialog.resize(max(dialog.minimumWidth(), hint_size.width()), hint_size.height())

        def refresh_target_state():
            target_kind = target_combo.currentData()
            is_gw = target_kind == "floppy_gw"
            drive_page.setVisible(not is_gw)
            gw_page.setVisible(is_gw)
            ok_button = buttons.button(QDialogButtonBox.Ok)
            if ok_button is not None:
                ok_button.setEnabled(bool(greaseweazle_devices) if is_gw else bool(floppy_drives))
            QTimer.singleShot(0, resize_dialog_to_content)

        target_combo.currentIndexChanged.connect(refresh_target_state)
        refresh_target_state()
        resize_dialog_to_content()

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        target_kind = target_combo.currentData()
        disk_format = format_combo.currentData()
        if target_kind == "floppy_gw":
            selected_device = gw_device_combo.currentData()
            if selected_device is None:
                return None
            selected_drive = drive_options[gw_drive_combo.currentIndex()]
            self._remember_greaseweazle_dialog_selection(selected_device, selected_drive)
            selected_source = GreaseweazleFloppySource(
                device_path=selected_device.path,
                drive=selected_drive,
                disk_format=disk_format,
            )
            target_name = selected_source.display_name
            drive_size_bytes = 0
        else:
            selected_source = drive_combo.currentData()
            if not isinstance(selected_source, FloppyDriveInfo):
                return None
            target_name = selected_source.display_name
            drive_size_bytes = selected_source.size_bytes

        return {
            "source_kind": target_kind,
            "source": selected_source,
            "target_name": target_name,
            "drive_size_bytes": drive_size_bytes,
            "disk_format": disk_format,
            "eseq_disk": eseq_checkbox.isChecked(),
        }

    def _greaseweazle_drive_options(self):
        return ["A", "B", "0", "1", "2"]

    def _restore_greaseweazle_dialog_selection(self, devices, device_combo, drive_options, drive_combo):
        saved_device_path = str(
            self.settings.value(self.SETTING_GREASEWEAZLE_DEVICE_PATH, "") or ""
        ).strip()
        if saved_device_path:
            for index, device in enumerate(devices):
                if getattr(device, "path", "") == saved_device_path:
                    device_combo.setCurrentIndex(index)
                    break

        saved_drive = str(self.settings.value(self.SETTING_GREASEWEAZLE_DRIVE, "") or "").strip().upper()
        if saved_drive in drive_options:
            drive_combo.setCurrentIndex(drive_options.index(saved_drive))

    def _remember_greaseweazle_dialog_selection(self, device, drive):
        device_path = str(getattr(device, "path", "") or "").strip()
        if device_path:
            self.settings.setValue(self.SETTING_GREASEWEAZLE_DEVICE_PATH, device_path)
        drive_text = str(drive or "").strip().upper()
        if drive_text:
            self.settings.setValue(self.SETTING_GREASEWEAZLE_DRIVE, drive_text)
        self.settings.sync()

    def _remember_read_floppy_dialog_selection(
        self,
        source_kind,
        *,
        archival_quality=None,
        disk_format=None,
        revs=None,
        retries=None,
    ):
        if source_kind in {"floppy_usb", "floppy_gw"}:
            self.settings.setValue(self.SETTING_READ_FLOPPY_SOURCE_KIND, source_kind)
        if archival_quality is not None:
            self.settings.setValue(self.SETTING_READ_FLOPPY_GW_ARCHIVAL, bool(archival_quality))
        format_key = str(getattr(disk_format, "key", "") or "").strip()
        if format_key:
            self.settings.setValue(self.SETTING_READ_FLOPPY_GW_FORMAT, format_key)
        if revs is not None:
            self.settings.setValue(self.SETTING_READ_FLOPPY_GW_REVS, int(revs))
        if retries is not None:
            self.settings.setValue(self.SETTING_READ_FLOPPY_GW_RETRIES, int(retries))
        self.settings.sync()

    def _restore_read_floppy_source_selection(self, source_combo, *, has_floppy_drives, has_greaseweazle_devices):
        saved_source = str(
            self.settings.value(self.SETTING_READ_FLOPPY_SOURCE_KIND, "") or ""
        ).strip()
        preferred_source = ""
        if saved_source == "floppy_gw" and has_greaseweazle_devices:
            preferred_source = "floppy_gw"
        elif saved_source == "floppy_usb" and has_floppy_drives:
            preferred_source = "floppy_usb"
        elif not has_floppy_drives and has_greaseweazle_devices:
            preferred_source = "floppy_gw"
        elif has_floppy_drives:
            preferred_source = "floppy_usb"

        if preferred_source:
            index = source_combo.findData(preferred_source)
            if index >= 0:
                source_combo.setCurrentIndex(index)

    def _populate_disk_format_combo(self, combo, *, default_key="ibm.720"):
        selected_index = 0
        combo.clear()
        for index, disk_format in enumerate(DISK_FORMATS):
            combo.addItem(
                f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})",
                disk_format,
            )
            if disk_format.key == default_key:
                selected_index = index
        combo.setCurrentIndex(selected_index)

    def _choose_floppy_read_options(self, *, default_recovery=False):
        floppy_drives = list_floppy_drives()
        greaseweazle_devices = list_greaseweazle_devices()

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Read Floppy")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        source_combo = QComboBox(dialog)
        source_combo.addItem("Floppy Drive", "floppy_usb")
        source_combo.addItem("Greaseweazle", "floppy_gw")
        self._restore_read_floppy_source_selection(
            source_combo,
            has_floppy_drives=bool(floppy_drives),
            has_greaseweazle_devices=bool(greaseweazle_devices),
        )

        source_row = QGridLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setHorizontalSpacing(12)
        source_row.setColumnStretch(1, 1)
        source_label = QLabel("Read using:")
        source_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        source_row.addWidget(source_label, 0, 0)
        source_row.addWidget(source_combo, 0, 1)
        layout.addLayout(source_row)

        drive_page = QWidget(dialog)
        drive_layout = QGridLayout(drive_page)
        drive_layout.setContentsMargins(0, 0, 0, 0)
        drive_layout.setHorizontalSpacing(12)
        drive_layout.setVerticalSpacing(8)
        drive_layout.setColumnStretch(1, 1)

        drive_label = QLabel("Floppy drive:")
        drive_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        drive_combo = QComboBox(drive_page)
        if floppy_drives:
            for drive in floppy_drives:
                drive_combo.addItem(drive.display_name, drive)
        else:
            drive_combo.addItem("No supported floppy drive detected", None)
            drive_combo.setEnabled(False)
        drive_layout.addWidget(drive_label, 0, 0)
        drive_layout.addWidget(drive_combo, 0, 1)

        drive_recovery_label = QLabel("Recovery disk format:")
        drive_recovery_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        drive_recovery_format_combo = QComboBox(drive_page)
        self._populate_disk_format_combo(drive_recovery_format_combo)
        drive_layout.addWidget(drive_recovery_label, 1, 0)
        drive_layout.addWidget(drive_recovery_format_combo, 1, 1)
        layout.addWidget(drive_page)

        gw_page = QWidget(dialog)
        gw_layout = QGridLayout(gw_page)
        gw_layout.setContentsMargins(0, 0, 0, 0)
        gw_layout.setHorizontalSpacing(12)
        gw_layout.setVerticalSpacing(8)
        gw_layout.setColumnStretch(1, 1)

        gw_device_label = QLabel("Greaseweazle device:")
        gw_device_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        gw_device_combo = QComboBox(gw_page)
        if greaseweazle_devices:
            for device in greaseweazle_devices:
                gw_device_combo.addItem(device.display_name, device)
        else:
            gw_device_combo.addItem("No Greaseweazle device detected", None)
            gw_device_combo.setEnabled(False)
        gw_layout.addWidget(gw_device_label, 0, 0)
        gw_layout.addWidget(gw_device_combo, 0, 1)

        gw_drive_label = QLabel("Drive:")
        gw_drive_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        gw_drive_combo = QComboBox(gw_page)
        drive_options = self._greaseweazle_drive_options()
        gw_drive_combo.addItems(drive_options)
        gw_layout.addWidget(gw_drive_label, 1, 0)
        gw_layout.addWidget(gw_drive_combo, 1, 1)
        if greaseweazle_devices:
            self._restore_greaseweazle_dialog_selection(
                greaseweazle_devices,
                gw_device_combo,
                drive_options,
                gw_drive_combo,
            )

        gw_format_label = QLabel("Disk format:")
        gw_format_label.setToolTip(
            "Greaseweazle reads and SCP conversions need the expected floppy format."
        )
        gw_format_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        gw_format_combo = QComboBox(gw_page)
        saved_gw_format_key = str(
            self.settings.value(self.SETTING_READ_FLOPPY_GW_FORMAT, "ibm.720") or "ibm.720"
        ).strip()
        self._populate_disk_format_combo(gw_format_combo, default_key=saved_gw_format_key)
        gw_layout.addWidget(gw_format_label, 2, 0)
        gw_layout.addWidget(gw_format_combo, 2, 1)

        revs_label = QLabel("Read revs:")
        revs_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        revs_spin = QSpinBox(gw_page)
        revs_spin.setRange(0, 20)
        revs_spin.setSpecialValueText("CLI default")
        revs_spin.setValue(
            max(0, min(20, self.settings.value(self.SETTING_READ_FLOPPY_GW_REVS, 0, type=int)))
        )
        revs_spin.setToolTip("Number of revolutions to read per track. Use 0 for Greaseweazle's default.")
        gw_layout.addWidget(revs_label, 3, 0)
        gw_layout.addWidget(revs_spin, 3, 1)

        retries_label = QLabel("Read retries:")
        retries_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        retries_spin = QSpinBox(gw_page)
        retries_spin.setRange(0, 20)
        retries_spin.setValue(
            max(0, min(20, self.settings.value(self.SETTING_READ_FLOPPY_GW_RETRIES, 3, type=int)))
        )
        retries_spin.setSpecialValueText("CLI default")
        retries_spin.setToolTip("Number of retries per seek-retry. Use 0 for Greaseweazle's default.")
        gw_layout.addWidget(retries_label, 4, 0)
        gw_layout.addWidget(retries_spin, 4, 1)

        archival_checkbox = QCheckBox("Archival quality (raw SCP flux capture)")
        archival_checkbox.setChecked(
            self.settings.value(self.SETTING_READ_FLOPPY_GW_ARCHIVAL, False, type=bool)
        )
        archival_checkbox.setToolTip(
            "Reads a raw magnetic flux capture to SCP first, then converts it using the selected disk format."
        )
        gw_layout.addWidget(archival_checkbox, 5, 1)

        gw_hint = QLabel(
            "Use archival quality for difficult disks or when you want the option to save a raw SCP capture after opening."
        )
        gw_hint.setWordWrap(True)
        gw_layout.addWidget(gw_hint, 6, 1)
        layout.addWidget(gw_page)

        recovery_checkbox = QCheckBox("Start in recovery mode")
        recovery_checkbox.setChecked(bool(default_recovery))
        recovery_checkbox.setToolTip(
            "Copies a full disk image and tries Yamaha/FAT repair plus raw MIDI/E-SEQ/PIANODIR scanning. "
            "The source floppy is not modified."
        )

        recovery_hint = QLabel()
        recovery_hint.setWordWrap(True)

        recovery_layout = QGridLayout()
        recovery_layout.setContentsMargins(0, 0, 0, 0)
        recovery_layout.setHorizontalSpacing(12)
        recovery_layout.setVerticalSpacing(8)
        recovery_layout.setColumnStretch(1, 1)
        recovery_label_spacer = QLabel("")
        recovery_layout.addWidget(recovery_label_spacer, 0, 0)
        recovery_layout.addWidget(recovery_checkbox, 0, 1)
        recovery_layout.addWidget(recovery_hint, 1, 1)
        layout.addLayout(recovery_layout)

        convert_to_midi_checkbox = QCheckBox("Convert E-SEQ files to MIDI after reading")
        convert_to_midi_checkbox.setChecked(
            self.settings.value(self.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, False, type=bool)
        )
        convert_to_midi_checkbox.setToolTip(
            "After the floppy opens, queue detected Yamaha E-SEQ songs for Standard MIDI conversion."
        )

        convert_layout = QGridLayout()
        convert_layout.setContentsMargins(0, 0, 0, 0)
        convert_layout.setHorizontalSpacing(12)
        convert_layout.setColumnStretch(1, 1)
        convert_label_spacer = QLabel("")
        convert_layout.addWidget(convert_label_spacer, 0, 0)
        convert_layout.addWidget(convert_to_midi_checkbox, 0, 1)
        layout.addLayout(convert_layout)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        form_labels = [
            source_label,
            drive_label,
            drive_recovery_label,
            gw_device_label,
            gw_drive_label,
            gw_format_label,
            revs_label,
            retries_label,
            recovery_label_spacer,
            convert_label_spacer,
        ]
        form_label_width = max(label.sizeHint().width() for label in form_labels)
        for label in form_labels:
            label.setMinimumWidth(form_label_width)

        def resize_dialog_to_content():
            dialog.layout().activate()
            hint = dialog.sizeHint()
            dialog.resize(max(dialog.minimumWidth(), hint.width()), hint.height())

        def refresh_dialog_state():
            source_kind = source_combo.currentData()
            is_gw = source_kind == "floppy_gw"
            is_recovery = recovery_checkbox.isChecked()
            drive_page.setVisible(not is_gw)
            gw_page.setVisible(is_gw)
            drive_recovery_label.setVisible(is_recovery and not is_gw)
            drive_recovery_format_combo.setVisible(is_recovery and not is_gw)
            if is_recovery:
                if is_gw:
                    recovery_hint.setText(
                        "Recovery may take a long time and opens recovered data in a new editable image copy."
                    )
                else:
                    recovery_hint.setText(
                        "Recovery copies the selected full disk size first; most Yamaha Disklavier floppies are IBM 720K DD."
                    )
            else:
                recovery_hint.setText("Normal read uses fast file-level reading when possible.")
            ok_enabled = bool(greaseweazle_devices) if is_gw else bool(floppy_drives)
            ok_button = buttons.button(QDialogButtonBox.Ok)
            if ok_button is not None:
                ok_button.setEnabled(ok_enabled)
                ok_button.setText("Recover" if is_recovery else "Read")
            QTimer.singleShot(0, resize_dialog_to_content)

        source_combo.currentIndexChanged.connect(refresh_dialog_state)
        recovery_checkbox.toggled.connect(refresh_dialog_state)
        refresh_dialog_state()
        resize_dialog_to_content()

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        source_kind = source_combo.currentData()
        recover = recovery_checkbox.isChecked()
        convert_to_midi = convert_to_midi_checkbox.isChecked()
        self.settings.setValue(self.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, convert_to_midi)
        if source_kind == "floppy_usb":
            drive_info = drive_combo.currentData()
            if not isinstance(drive_info, FloppyDriveInfo):
                QMessageBox.information(
                    self,
                    "No Floppy Drive Found",
                    "No supported floppy drive was detected. Insert a disk and try again.",
                )
                return None
            self._remember_read_floppy_dialog_selection(source_kind)
            if recover:
                disk_format = drive_recovery_format_combo.currentData()
                return {
                    "load_kind": "floppy_usb",
                    "source": FloppyRecoverySource(drive_info, disk_format),
                    "recover": True,
                    "source_label": f"floppy disk ({disk_format.label})",
                    "progress_title": "Recovering Floppy Data",
                    "progress_total": 100,
                    "offer_greaseweazle_capture": False,
                    "convert_to_midi": convert_to_midi,
                }
            return {
                "load_kind": "floppy_usb",
                "source": drive_info,
                "recover": False,
                "source_label": "floppy disk",
                "progress_title": "Reading Floppy",
                "progress_total": 100,
                "offer_greaseweazle_capture": False,
                "convert_to_midi": convert_to_midi,
            }

        selected_device = gw_device_combo.currentData()
        if selected_device is None:
            QMessageBox.information(
                self,
                "No Greaseweazle Found",
                "No Greaseweazle device was detected. Connect one and try again.",
            )
            return None
        selected_drive = drive_options[gw_drive_combo.currentIndex()]
        self._remember_greaseweazle_dialog_selection(selected_device, selected_drive)
        disk_format = gw_format_combo.currentData()
        archival_quality = archival_checkbox.isChecked()
        read_revs = revs_spin.value()
        read_retries = retries_spin.value()
        self._remember_read_floppy_dialog_selection(
            source_kind,
            archival_quality=archival_quality,
            disk_format=disk_format,
            revs=read_revs,
            retries=read_retries,
        )
        source = GreaseweazleFloppySource(
            device_path=selected_device.path,
            drive=selected_drive,
            disk_format=disk_format,
            archival_quality=archival_quality,
            revs=read_revs,
            retries=read_retries,
        )
        if source.archival_quality:
            return {
                "load_kind": "floppy_gw_capture_only",
                "source": {
                    "gw_source": source,
                    "recover_after_capture": recover,
                },
                "recover": False,
                "source_label": f"Greaseweazle floppy ({disk_format.label})",
                "progress_title": "Reading Floppy via Greaseweazle (Archival SCP)",
                "progress_total": 2,
                "final_message": "Greaseweazle SCP capture ready.",
                "offer_greaseweazle_capture": False,
                "convert_to_midi": convert_to_midi,
            }
        if recover:
            return {
                "load_kind": "floppy_gw",
                "source": source,
                "recover": True,
                "source_label": f"Greaseweazle floppy ({disk_format.label})",
                "progress_title": "Recovering Greaseweazle Floppy Data",
                "progress_total": 100,
                "offer_greaseweazle_capture": False,
                "convert_to_midi": convert_to_midi,
            }
        progress_title = "Reading Floppy via Greaseweazle"
        progress_total = 5 if source.archival_quality else 4
        return {
            "load_kind": "floppy_gw",
            "source": source,
            "recover": False,
            "source_label": f"Greaseweazle floppy ({disk_format.label})",
            "progress_title": progress_title,
            "progress_total": progress_total,
            "offer_greaseweazle_capture": False,
            "convert_to_midi": convert_to_midi,
        }

    def _confirm_format_floppy(self, target_name, disk_format, *, eseq_disk=False, drive_size_bytes=0):
        mode_label = "E-SEQ" if eseq_disk else "MIDI"
        message = (
            f"Format {target_name} as a Yamaha Disklavier {mode_label} floppy?\n\n"
            f"Format: {disk_format.label} ({display_bytes(disk_format.size_bytes)})\n\n"
            "This will erase the disk in the selected drive."
        )
        if drive_size_bytes and drive_size_bytes != disk_format.size_bytes:
            message += (
                "\n\nThe selected floppy drive currently reports "
                f"{display_bytes(drive_size_bytes)}, which does not match the selected format."
            )
        return QMessageBox.question(
            self,
            "Format Floppy Disk...",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes

    def format_disklavier_floppy(self):
        if not self._prepare_for_disk_load("a newly formatted floppy disk"):
            return

        options = self._choose_format_floppy_options()
        if not options:
            return

        disk_format = options["disk_format"]
        eseq_disk = bool(options["eseq_disk"])
        selected_source = options["source"]
        source_kind = options["source_kind"]
        drive_size_bytes = options.get("drive_size_bytes", 0)
        target_name = options.get("target_name", "")

        if not self._confirm_format_floppy(
            target_name,
            disk_format,
            eseq_disk=eseq_disk,
            drive_size_bytes=drive_size_bytes,
        ):
            return

        self._start_floppy_format_worker(
            source_kind,
            selected_source,
            disk_format,
            eseq_disk=eseq_disk,
            target_name=target_name,
        )

    def _start_floppy_format_worker(self, source_kind, selected_source, disk_format, *, eseq_disk, target_name):
        if self._disk_worker_busy():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return

        mode_label = "E-SEQ" if eseq_disk else "MIDI"
        progress_text = f"Formatting Yamaha Disklavier {mode_label} floppy..."
        progress_dialog = QProgressDialog(progress_text, "Cancel", 0, 5, self)
        progress_dialog.setWindowTitle("Formatting Floppy")
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        self._apply_stage_progress(progress_dialog, 0, 5, progress_text)

        worker = DiskSessionFormatWorker(
            source_kind,
            selected_source,
            disk_format=disk_format,
            eseq_disk=eseq_disk,
            volume_label="ESEQ" if eseq_disk else "YAMAHA",
            parent=self,
        )
        worker.progressChanged.connect(
            lambda step, total, message, dialog=progress_dialog: self._apply_stage_progress(
                dialog, step, total, message
            )
        )
        progress_dialog.canceled.connect(worker.cancel)
        progress_dialog.canceled.connect(
            lambda dialog=progress_dialog: dialog.setLabelText("Cancelling floppy format...")
        )
        worker.sessionFormatted.connect(self._on_floppy_format_success)
        worker.formatFailed.connect(self._on_floppy_format_failure)
        worker.operationCancelled.connect(self._on_floppy_format_cancelled)
        worker.finished.connect(self._on_floppy_format_finished)

        self.diskFormatWorker = worker
        self.diskFormatProgressDialog = progress_dialog
        self.diskFormatContext = {
            "target_name": target_name,
            "disk_format": disk_format,
            "mode_label": mode_label,
        }
        self._set_disk_write_busy(True)
        worker.start()

    def _on_floppy_format_success(self, session, listing):
        if self.diskFormatProgressDialog is not None:
            self.diskFormatProgressDialog.close()
            self.diskFormatProgressDialog = None

        context = dict(self.diskFormatContext)
        try:
            self.settings.setValue(self.SETTING_ALLOW_FLOPPY_SAVE, True)
            self._activate_disk_session(session, listing, reset_original_write=False)
        except Exception as exc:
            try:
                session.cleanup()
            except Exception:
                pass
            self._show_operation_error(
                "Format Failed",
                "The floppy was formatted, but the app could not open it for editing",
                exc,
            )
            return

        target_name = context.get("target_name", "the selected drive")
        disk_format = context.get("disk_format")
        mode_label = context.get("mode_label", "MIDI")
        format_label = disk_format.label if disk_format is not None else "selected format"
        self.status_label.setText(
            f"Formatted {target_name} as {format_label} Yamaha Disklavier {mode_label} floppy."
        )
        QMessageBox.information(
            self,
            "Floppy Formatted",
            f"The disk was formatted and opened in Floppy Disk ({mode_label}) mode.",
        )

    def _on_floppy_format_failure(self, message):
        if self.diskFormatProgressDialog is not None:
            self.diskFormatProgressDialog.close()
            self.diskFormatProgressDialog = None
        target_name = self.diskFormatContext.get("target_name", "the selected drive")
        self._show_operation_error(
            "Format Failed",
            f"The floppy in {target_name} was not formatted",
            message,
            guidance="Check the selected drive, disk type, and write permissions before trying again",
        )

    def _on_floppy_format_cancelled(self, _message):
        if self.diskFormatProgressDialog is not None:
            self.diskFormatProgressDialog.close()
            self.diskFormatProgressDialog = None
        QMessageBox.warning(
            self,
            "Format Cancelled",
            "Formatting was cancelled. The floppy may be partially written; format it again before using it.",
        )
        self.status_label.setText("Floppy formatting cancelled.")

    def _on_floppy_format_finished(self):
        self._set_disk_write_busy(False)
        self.diskFormatContext = {}
        if self.diskFormatWorker is not None:
            self.diskFormatWorker.deleteLater()
            self.diskFormatWorker = None

    def _collect_current_image_write_operations(self):
        renames, deletes, additions, replacements, title_edits, delete_pianodir = self._collect_image_operations()
        return {
            "renames": renames,
            "deletes": deletes,
            "additions": additions,
            "replacements": replacements,
            "title_edits": title_edits,
            "order_key_edits": self._image_eseq_order_key_edits(),
            "pianodir_metadata": self._image_pianodir_metadata_for_save(),
            "generate_pianodir": self._should_generate_pianodir(),
            "eseq_variant": self.imageEseqVariant,
            "eseq_directory_order": self._image_eseq_directory_order(),
            "delete_pianodir": delete_pianodir,
        }

    def _confirm_write_image_to_floppy(self, target_name, *, drive_size_bytes=0):
        disk_format = self.image_session.disk_format if self.image_session is not None else None
        format_text = disk_format.label if disk_format is not None else "current image format"
        message = (
            f"Write the current {format_text} image to {target_name}?\n\n"
            "This will overwrite the floppy disk in the selected drive."
        )
        if self._has_pending_image_changes():
            message += "\n\nPending image changes will be included in the floppy write."
        if drive_size_bytes and disk_format is not None and drive_size_bytes != disk_format.size_bytes:
            message += (
                "\n\nThe selected floppy drive currently reports "
                f"{display_bytes(drive_size_bytes)}, which does not match the current image size "
                f"({display_bytes(disk_format.size_bytes)})."
            )
        return QMessageBox.question(
            self,
            "Write Current Image to Floppy",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes

    def _confirm_save_to_floppy_files(self, target_name, *, drive_size_bytes=0):
        disk_format = self.image_session.disk_format if self.image_session is not None else None
        format_text = disk_format.label if disk_format is not None else "current image format"
        message = (
            f"Save the current {format_text} file list to {target_name}?\n\n"
            "This will remove the existing files on the floppy and copy the listed files over. "
            "It will not rewrite the whole disk image."
        )
        if self._has_pending_image_changes():
            message += "\n\nPending image changes will be included."
        if drive_size_bytes and disk_format is not None and drive_size_bytes != disk_format.size_bytes:
            message += (
                "\n\nThe selected floppy drive currently reports "
                f"{display_bytes(drive_size_bytes)}, which does not match the current image size "
                f"({display_bytes(disk_format.size_bytes)})."
            )
        return QMessageBox.question(
            self,
            "Save To Floppy",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes

    def _choose_save_to_floppy_drive(self):
        floppy_drives = list_floppy_drives()
        disk_format = self.image_session.disk_format if self.image_session is not None else None

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Save To Floppy")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        format_text = disk_format.label if disk_format is not None else "current image format"
        hint = QLabel(
            f"Choose a formatted floppy drive to receive the current {format_text} file list."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        drive_combo = QComboBox(dialog)
        if floppy_drives:
            for drive in floppy_drives:
                drive_combo.addItem(drive.display_name, drive)
        else:
            drive_combo.addItem("No supported floppy drive detected", None)
            drive_combo.setEnabled(False)

        form_grid = self._make_dialog_form_grid()
        drive_label = self._add_dialog_form_row(form_grid, 0, "Floppy drive:", drive_combo)
        self._align_dialog_form_labels([drive_label])
        layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("Save")
            ok_button.setEnabled(bool(floppy_drives))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.layout().activate()
        hint_size = dialog.sizeHint()
        dialog.resize(max(dialog.minimumWidth(), hint_size.width()), hint_size.height())

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None
        target = drive_combo.currentData()
        if not isinstance(target, FloppyDriveInfo):
            return None
        return {
            "target_kind": "floppy_usb",
            "target": target,
            "target_name": target.display_name,
            "drive_size_bytes": target.size_bytes,
        }

    def _choose_write_image_floppy_target(self):
        floppy_drives = list_floppy_drives()
        greaseweazle_devices = list_greaseweazle_devices()
        disk_format = self.image_session.disk_format if self.image_session is not None else None

        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Write Current Image to Floppy")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        format_text = disk_format.label if disk_format is not None else "current image format"
        hint = QLabel(f"Choose where to write the current {format_text} image.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        target_combo = QComboBox(dialog)
        target_combo.addItem("Floppy Drive", "floppy_usb")
        target_combo.addItem("Greaseweazle", "floppy_gw")
        if not floppy_drives and greaseweazle_devices:
            target_combo.setCurrentIndex(1)

        common_grid = self._make_dialog_form_grid()
        target_label = self._add_dialog_form_row(common_grid, 0, "Write using:", target_combo)
        layout.addLayout(common_grid)

        drive_page = QWidget(dialog)
        drive_grid = self._make_dialog_form_grid()
        drive_page.setLayout(drive_grid)
        drive_combo = QComboBox(drive_page)
        if floppy_drives:
            for drive in floppy_drives:
                drive_combo.addItem(drive.display_name, drive)
        else:
            drive_combo.addItem("No supported floppy drive detected", None)
            drive_combo.setEnabled(False)
        drive_label = self._add_dialog_form_row(drive_grid, 0, "Floppy drive:", drive_combo)
        layout.addWidget(drive_page)

        gw_page = QWidget(dialog)
        gw_grid = self._make_dialog_form_grid()
        gw_page.setLayout(gw_grid)
        gw_device_combo = QComboBox(gw_page)
        if greaseweazle_devices:
            for device in greaseweazle_devices:
                gw_device_combo.addItem(device.display_name, device)
        else:
            gw_device_combo.addItem("No Greaseweazle device detected", None)
            gw_device_combo.setEnabled(False)
        gw_drive_combo = QComboBox(gw_page)
        drive_options = self._greaseweazle_drive_options()
        gw_drive_combo.addItems(drive_options)
        if greaseweazle_devices:
            self._restore_greaseweazle_dialog_selection(
                greaseweazle_devices,
                gw_device_combo,
                drive_options,
                gw_drive_combo,
            )
        gw_device_label = self._add_dialog_form_row(gw_grid, 0, "Greaseweazle device:", gw_device_combo)
        gw_drive_label = self._add_dialog_form_row(gw_grid, 1, "Drive:", gw_drive_combo)
        layout.addWidget(gw_page)

        self._align_dialog_form_labels([target_label, drive_label, gw_device_label, gw_drive_label])

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def resize_dialog_to_content():
            dialog.layout().activate()
            hint_size = dialog.sizeHint()
            dialog.resize(max(dialog.minimumWidth(), hint_size.width()), hint_size.height())

        def refresh_target_state():
            target_kind = target_combo.currentData()
            is_gw = target_kind == "floppy_gw"
            drive_page.setVisible(not is_gw)
            gw_page.setVisible(is_gw)
            ok_button = buttons.button(QDialogButtonBox.Ok)
            if ok_button is not None:
                ok_button.setEnabled(bool(greaseweazle_devices) if is_gw else bool(floppy_drives))
            QTimer.singleShot(0, resize_dialog_to_content)

        target_combo.currentIndexChanged.connect(refresh_target_state)
        refresh_target_state()
        resize_dialog_to_content()

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        target_kind = target_combo.currentData()
        if target_kind == "floppy_gw":
            selected_device = gw_device_combo.currentData()
            if selected_device is None or disk_format is None:
                return None
            selected_drive = drive_options[gw_drive_combo.currentIndex()]
            self._remember_greaseweazle_dialog_selection(selected_device, selected_drive)
            target = GreaseweazleFloppySource(
                device_path=selected_device.path,
                drive=selected_drive,
                disk_format=disk_format,
            )
            return {
                "target_kind": "floppy_gw",
                "target": target,
                "target_name": target.display_name,
                "drive_size_bytes": 0,
            }

        target = drive_combo.currentData()
        if not isinstance(target, FloppyDriveInfo):
            return None
        return {
            "target_kind": "floppy_usb",
            "target": target,
            "target_name": target.display_name,
            "drive_size_bytes": target.size_bytes,
        }

    def save_to_floppy(self):
        if self.image_session is None:
            QMessageBox.information(self, "No Image", "Open or create an image before saving to a floppy disk.")
            return
        if self._disk_worker_busy():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return
        if self.imageEseqMode and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Saving this E-SEQ set to floppy",
        ):
            return
        if not self._ensure_pianodir_generation_for_save():
            return
        if self._pending_image_space_remaining() < 0:
            QMessageBox.warning(
                self,
                "Image Is Full",
                "Pending additions do not fit in the current floppy image. Remove files or cancel additions before saving.",
            )
            return

        target_options = self._choose_save_to_floppy_drive()
        if not target_options:
            return

        target_kind = target_options["target_kind"]
        target = target_options["target"]
        target_name = target_options["target_name"]
        drive_size_bytes = target_options.get("drive_size_bytes", 0)

        if not self._confirm_save_to_floppy_files(target_name, drive_size_bytes=drive_size_bytes):
            return

        self._start_write_image_to_floppy_worker(
            target_kind,
            target,
            target_name,
            self._collect_current_image_write_operations(),
            file_level=True,
        )

    def write_image_to_floppy(self):
        if self.image_session is None:
            QMessageBox.information(self, "No Image", "Open or create an image before writing to a floppy disk.")
            return
        if self._disk_worker_busy():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return
        if self.imageEseqMode and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Writing this E-SEQ image to floppy",
        ):
            return
        if not self._ensure_pianodir_generation_for_save():
            return
        if self._pending_image_space_remaining() < 0:
            QMessageBox.warning(
                self,
                "Image Is Full",
                "Pending additions do not fit in the floppy image. Remove files or cancel additions before writing.",
            )
            return

        target_options = self._choose_write_image_floppy_target()
        if not target_options:
            return

        target_kind = target_options["target_kind"]
        target = target_options["target"]
        target_name = target_options["target_name"]
        drive_size_bytes = target_options.get("drive_size_bytes", 0)

        if not self._confirm_write_image_to_floppy(target_name, drive_size_bytes=drive_size_bytes):
            return

        self._start_write_image_to_floppy_worker(
            target_kind,
            target,
            target_name,
            self._collect_current_image_write_operations(),
        )

    def _start_write_image_to_floppy_worker(self, target_kind, target, target_name, operations, *, file_level=False):
        if file_level:
            progress_text = "Saving files to floppy..."
            progress_title = "Saving To Floppy"
        else:
            progress_text = "Writing floppy..." if target_kind == "floppy_usb" else "Writing floppy via Greaseweazle..."
            progress_title = "Writing Image to Floppy"
        progress_dialog = QProgressDialog(progress_text, "Cancel", 0, 5, self)
        progress_dialog.setWindowTitle(progress_title)
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        self._apply_stage_progress(progress_dialog, 0, 5, progress_text)

        worker = DiskSessionWriteTargetWorker(
            self.image_session,
            target_kind,
            target,
            operations,
            parent=self,
            file_level=file_level,
        )
        worker.progressChanged.connect(
            lambda step, total, message, dialog=progress_dialog: self._apply_stage_progress(
                dialog, step, total, message
            )
        )
        progress_dialog.canceled.connect(worker.cancel)
        progress_dialog.canceled.connect(
            lambda dialog=progress_dialog: dialog.setLabelText("Cancelling floppy write...")
        )
        worker.writeFinished.connect(
            lambda target_name=target_name, file_level=file_level: self._on_write_image_to_floppy_success(
                target_name,
                file_level=file_level,
            )
        )
        worker.writeFailed.connect(
            lambda message, file_level=file_level: self._on_write_image_to_floppy_failure(
                message,
                file_level=file_level,
            )
        )
        worker.operationCancelled.connect(
            lambda message, file_level=file_level: self._on_write_image_to_floppy_cancelled(
                message,
                file_level=file_level,
            )
        )
        worker.finished.connect(self._on_write_image_to_floppy_finished)

        self.diskWriteTargetWorker = worker
        self.diskWriteTargetProgressDialog = progress_dialog
        self._set_disk_write_busy(True)
        worker.start()

    def _on_write_image_to_floppy_success(self, target_name, *, file_level=False):
        if self.diskWriteTargetProgressDialog is not None:
            self.diskWriteTargetProgressDialog.close()
            self.diskWriteTargetProgressDialog = None
        if file_level:
            QMessageBox.information(self, "Floppy Saved", f"The current files were saved to {target_name}.")
            self.status_label.setText(f"Saved current files to {target_name}.")
        else:
            QMessageBox.information(self, "Image Written", f"The current image was written to {target_name}.")
            self.status_label.setText(f"Wrote current image to {target_name}.")

    def _on_write_image_to_floppy_failure(self, message, *, file_level=False):
        if self.diskWriteTargetProgressDialog is not None:
            self.diskWriteTargetProgressDialog.close()
            self.diskWriteTargetProgressDialog = None
        if file_level:
            self._show_operation_error(
                "Save To Floppy Failed",
                "The app could not finish saving files to the floppy disk",
                message,
                guidance="The current image is still open; check that the selected floppy is formatted and try again",
            )
            return
        self._show_operation_error(
            "Write Image Failed",
            "The app could not finish writing the image to the floppy disk",
            message,
            guidance="The current image is still open; check the selected drive and try again",
        )

    def _on_write_image_to_floppy_cancelled(self, _message, *, file_level=False):
        if self.diskWriteTargetProgressDialog is not None:
            self.diskWriteTargetProgressDialog.close()
            self.diskWriteTargetProgressDialog = None
        if file_level:
            QMessageBox.warning(
                self,
                "Save To Floppy Cancelled",
                "Saving was cancelled. The floppy may contain a partial file set; save again before using it.",
            )
            self.status_label.setText("Save to floppy cancelled. The current image is still open.")
            return
        QMessageBox.warning(
            self,
            "Floppy Write Cancelled",
            "Writing was cancelled. The floppy may be partially written; write it again or reformat before using it.",
        )
        self.status_label.setText("Floppy write cancelled. The current image is still open.")

    def _on_write_image_to_floppy_finished(self):
        self._set_disk_write_busy(False)
        if self.diskWriteTargetWorker is not None:
            self.diskWriteTargetWorker.deleteLater()
            self.diskWriteTargetWorker = None

    def _prepare_for_disk_load(self, source_label):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return False
        if self.is_image_mode() and not self._confirm_discard_image_changes():
            return False
        if (
            not self.is_image_mode()
            and (
                self.pendingEdits
                or self.pendingRegularConversions
                or self.pendingRegularRenames
            )
        ):
            reply = QMessageBox.question(
                self,
                "Discard Pending Changes",
                f"Load {source_label} and discard pending file changes?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        return True

    def _activate_disk_session(self, session, listing, *, reset_original_write=True):
        old_session = self.image_session

        if old_session is not None:
            old_session.cleanup()

        self._cleanup_midi_scratch_dir()
        self.table.setSortingEnabled(False)
        self._clear_regular_list_state()
        self._reset_image_state(cleanup=False)
        self.image_session = session
        self.imageEntriesByPath = {entry.path: entry for entry in listing.entries}
        if reset_original_write:
            self._reset_original_write_permissions_for_new_media()
        self._apply_image_mode_ui()
        self._load_image_rows(listing.entries, auto_enable_format=True)

        status = self._image_mode_summary()
        if session.repair_changed:
            status += "\n" + session.repair_note
        self.status_label.setText(status)

    def _offer_save_greaseweazle_capture(self):
        if not self.is_floppy_mode() or self.image_session.source_kind != "floppy_gw":
            return
        capture_path = getattr(self.image_session, "capture_path", None)
        capture_ext = (getattr(self.image_session, "capture_ext", "") or "").lower()
        is_archival_scp = (
            self.image_session.gw_source is not None
            and self.image_session.gw_source.archival_quality
            and capture_ext == "scp"
            and capture_path
            and os.path.isfile(capture_path)
        )
        prompt_text = "Save the imported Greaseweazle floppy as an image file now?"
        if is_archival_scp:
            prompt_text = "Save the raw archival Greaseweazle SCP flux capture now?"
        reply = QMessageBox.question(
            self,
            "Save Greaseweazle Capture",
            prompt_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        if is_archival_scp:
            drive_name = self.image_session.gw_source.drive.lower()
            catalog_number = normalize_pianodir_catalog_number(
                getattr(self.loadedImagePianodirMetadata, "catalog_number", "")
            )
            default_stem = self._sanitize_export_folder_name(catalog_number) if catalog_number else ""
            if not default_stem:
                default_stem = f"gw_drive_{drive_name}_archival"
            default_path = os.path.join(
                os.path.expanduser("~"),
                f"{default_stem}.scp",
            )
            output_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Raw SCP Capture",
                default_path,
                "SCP flux capture (*.scp *.SCP)",
            )
            if not output_path:
                return
            if image_extension(output_path) != "scp":
                output_path = f"{output_path}.scp"
            try:
                shutil.copy2(capture_path, output_path)
                QMessageBox.information(
                    self,
                    "SCP Capture Saved",
                    f"Raw SCP capture saved as {os.path.basename(output_path)}.",
                )
            except Exception as exc:
                self._show_operation_error(
                    "SCP Save Failed",
                    f"Could not save the raw Greaseweazle capture to {os.path.basename(output_path)}",
                    exc,
                )
            return
        self.save_image_as()

    def _image_open_filters(self):
        common_exts = ("img", "hfe", "bin")
        common_patterns = " ".join(
            pattern
            for ext in common_exts
            for pattern in (f"*.{ext}", f"*.{ext.upper()}")
        )
        all_exts = []
        seen_exts = set()
        for ext in common_exts:
            all_exts.append(ext)
            seen_exts.add(ext)
        for ext, _label in PREFERRED_OUTPUT_EXTENSIONS:
            if ext not in seen_exts:
                all_exts.append(ext)
                seen_exts.add(ext)
        all_patterns = " ".join(f"*.{ext}" for ext in all_exts)
        return (
            f"Common floppy images ({common_patterns});;"
            f"All supported images ({all_patterns});;"
            "All files (*)"
        )

    def open_image_dialog(self):
        filters = self._image_open_filters()
        default_path = os.path.expanduser("~")
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Floppy Image",
            default_path,
            filters,
        )
        if not image_path:
            return
        self.load_image_file(image_path)

    def recover_damaged_image_dialog(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Recover Damaged Image")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        image_row = QHBoxLayout()
        image_edit = QLineEdit(dialog)
        image_edit.setPlaceholderText("Choose a floppy image")
        image_row.addWidget(image_edit, stretch=1)

        browse_button = QPushButton("Browse...")
        image_row.addWidget(browse_button)

        format_combo = QComboBox(dialog)
        format_combo.addItem("Autodetect", None)
        for disk_format in DISK_FORMATS:
            format_combo.addItem(
                f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})",
                disk_format,
            )

        form_grid = self._make_dialog_form_grid()
        image_label = self._add_dialog_form_row(form_grid, 0, "Image:", image_row)
        format_label = self._add_dialog_form_row(form_grid, 1, "Disk format:", format_combo)
        self._align_dialog_form_labels([image_label, format_label])
        layout.addLayout(form_grid)

        def browse_image():
            selected_path, _ = QFileDialog.getOpenFileName(
                dialog,
                "Choose Damaged Floppy Image",
                image_edit.text().strip() or os.path.expanduser("~"),
                self._image_open_filters(),
            )
            if selected_path:
                image_edit.setText(selected_path)

        browse_button.clicked.connect(browse_image)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return

        image_path = image_edit.text().strip()
        if not image_path:
            QMessageBox.information(self, "No Image Selected", "Choose a floppy image to recover.")
            return
        if not os.path.isfile(image_path):
            QMessageBox.warning(self, "Image Not Found", f"The selected image file does not exist:\n\n{image_path}")
            return
        if not self._prepare_for_disk_load("a recovered image"):
            return

        disk_format = format_combo.currentData()
        source = ImageRecoverySource(path=image_path, disk_format=disk_format)
        self._start_disk_recovery_worker(
            {
                "load_kind": "image",
                "source": source,
                "failure_title": "Image Recovery Failed",
                "source_label": "floppy image",
                "progress_title": "Recovering Image Data",
            }
        )

    def _choose_floppy_recovery_disk_format(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Floppy Recovery Disk Size")
        dialog.setModal(True)
        dialog.setMinimumWidth(500)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        hint = QLabel(
            "Choose the format of the disk in the drive. Most Yamaha Disklavier floppies are IBM 720K DD; "
            "recovery will copy exactly the selected amount of data."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        format_combo = QComboBox(dialog)
        default_index = 0
        for index, disk_format in enumerate(DISK_FORMATS):
            format_combo.addItem(
                f"{disk_format.label} ({display_bytes(disk_format.size_bytes)})",
                disk_format,
            )
            if disk_format.key == "ibm.720":
                default_index = index
        format_combo.setCurrentIndex(default_index)

        form_grid = self._make_dialog_form_grid()
        format_label = self._add_dialog_form_row(form_grid, 0, "Disk format:", format_combo)
        self._align_dialog_form_labels([format_label])
        layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None
        return format_combo.currentData()

    def _wrap_floppy_recovery_source_with_format(self, source):
        if isinstance(source, FloppyRecoverySource):
            return source
        if not isinstance(source, FloppyDriveInfo):
            return source
        disk_format = self._choose_floppy_recovery_disk_format()
        if disk_format is None:
            return None
        return FloppyRecoverySource(source, disk_format)

    def recover_damaged_floppy_dialog(self):
        self.load_floppy_drive(default_recovery=True)

    def load_image_file(self, image_path, prevalidated=False):
        if not prevalidated and not self._prepare_for_disk_load("this floppy image"):
            return

        self.pendingFloppyReadConvertToMidi = False
        self._start_disk_load_worker(
            load_kind="image",
            source=image_path,
            progress_title="Preparing floppy image...",
            progress_total=4,
            initial_message="Preparing floppy image...",
            final_message="Loading floppy view...",
            failure_title="Image Load Failed",
        )

    def load_floppy_drive(self, _checked=False, *, default_recovery=False):
        if not self._prepare_for_disk_load("this floppy disk"):
            return

        options = self._choose_floppy_read_options(default_recovery=default_recovery)
        if not options:
            return

        self.pendingFloppyReadConvertToMidi = bool(options.get("convert_to_midi"))
        if options.get("recover"):
            self._start_disk_recovery_worker(
                {
                    "load_kind": options["load_kind"],
                    "source": options["source"],
                    "failure_title": "Floppy Recovery Failed",
                    "source_label": options.get("source_label", "floppy disk"),
                    "progress_title": options.get("progress_title", "Recovering Floppy Data"),
                }
            )
            return

        self._start_disk_load_worker(
            load_kind=options["load_kind"],
            source=options["source"],
            progress_title=options.get("progress_title", "Reading Floppy"),
            progress_total=options.get("progress_total", 100),
            initial_message=options.get("progress_title", "Reading Floppy"),
            final_message=options.get("final_message", "Opening floppy contents..."),
            failure_title="Floppy Load Failed",
            offer_greaseweazle_capture=bool(options.get("offer_greaseweazle_capture")),
        )

    def _load_image_rows(self, entries, *, auto_enable_format=False):
        self.imageFileInfo.clear()
        self.imageHasPianodir = False
        self.imagePianodirPopulated = False
        self.imageEseqMode = False
        self.imageEseqVariant = ESEQ_VARIANT_DISKLAVIER
        self.imageTitlesLikelyCentered = False
        loaded_pianodir_metadata = PianodirMetadata()
        image_order_overrides = {}

        musicdir_entries = [entry for entry in entries if is_musicdir_path(entry.path)]
        pianodir_entries = [entry for entry in entries if is_pianodir_path(entry.path)]
        if musicdir_entries:
            self.imageEseqVariant = ESEQ_VARIANT_CLAVINOVA
            self.imageHasPianodir = True
            self.imagePianodirPopulated = any(musicdir_is_populated(entry.size) for entry in musicdir_entries)
            for entry in musicdir_entries:
                try:
                    local_path = self.image_session.extract_file(entry.path)
                    image_order_overrides.update(read_music_dir_order_keys_from_file(local_path))
                    break
                except Exception:
                    pass
        if pianodir_entries:
            self.imageEseqVariant = ESEQ_VARIANT_DISKLAVIER
            self.imageHasPianodir = True
            self.imagePianodirPopulated = any(pianodir_is_populated(entry.size) for entry in pianodir_entries)
            for entry in pianodir_entries:
                try:
                    local_path = self.image_session.extract_file(entry.path)
                    loaded_pianodir_metadata = read_pianodir_metadata_from_file(local_path)
                    break
                except Exception:
                    loaded_pianodir_metadata = PianodirMetadata()

        row_specs = []
        for entry in entries:
            if is_eseq_directory_path(entry.path):
                continue

            midi_type = self._kind_for_image_file(entry.path)
            title = ""
            order_key = b""
            try:
                local_path = self.image_session.extract_file(entry.path)
                _, title, midi_type, _, order_key = self._probe_image_file(entry.path, entry.size, local_path)
                if entry.name.upper() in image_order_overrides:
                    order_key = image_order_overrides[entry.name.upper()]
            except Exception:
                self._set_image_file_info(
                    entry.path,
                    is_midi=False,
                    title="",
                    midi_type=midi_type,
                    size=entry.size,
                    title_mode="",
                    order_key=b"",
                )

            row_specs.append(
                {
                    "image_path": entry.path,
                    "filename": entry.name,
                    "size": entry.size,
                    "title": title,
                    "midi_type": midi_type,
                    "title_mode": self._image_path_title_mode(entry.path),
                    "order_key": order_key,
                }
            )

        self._update_image_centered_title_assumption(
            candidate_titles=[
                spec["title"]
                for spec in row_specs
                if spec.get("title") and spec.get("title_mode") in {"midi", "eseq"}
            ]
        )

        if any(
            spec.get("title_mode") == "eseq"
            and os.path.splitext(spec.get("filename", ""))[1].lower() == ".mda"
            for spec in row_specs
        ):
            self.imageEseqVariant = ESEQ_VARIANT_CLAVINOVA

        image_has_eseq_titles = self.imageHasPianodir or any(spec.get("title_mode") == "eseq" for spec in row_specs)
        if image_has_eseq_titles and auto_enable_format:
            self._enable_disklavier_screen_format_option()
        if image_has_eseq_titles:
            row_specs.sort(
                key=lambda spec: (
                    0 if spec.get("title_mode") == "eseq" else 1,
                    spec.get("order_key", b"") if spec.get("title_mode") == "eseq" else b"",
                    spec["filename"].upper(),
                )
            )
        else:
            row_specs.sort(key=lambda spec: spec["filename"].upper())
        for spec in row_specs:
            self.add_image_table_row(
                spec["image_path"],
                spec["filename"],
                spec["size"],
                title=spec["title"],
                midi_type=spec["midi_type"],
                order_key=spec.get("order_key", b""),
                is_pending_addition=False,
            )

        self._set_loaded_image_pianodir_metadata(loaded_pianodir_metadata)
        self._refresh_pianodir_row()
        self._resize_table_columns_to_fill()

    def _is_midi_image_path(self, image_path):
        return self._image_path_is_midi(image_path)

    def _kind_for_image_file(self, image_path):
        if self._is_special_pianodir_path(image_path) or is_eseq_directory_path(image_path):
            return "DIR"
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        if ext in {"mid", "midi"}:
            return "MIDI"
        if ext == "fil":
            return "FIL"
        if ext == "mda":
            return "MDA"
        if ext:
            return ext.upper()
        if self.is_image_mode() and self.imageHasPianodir and not self.pendingDeletePianodir:
            return "FIL"
        return "File"

    def add_image_table_row(self, image_path, filename, size, title="", midi_type="", order_key=b"", is_pending_addition=False):
        row = self.table.rowCount()
        self.table.insertRow(row)

        delete_item = QTableWidgetItem("X")
        delete_item.setTextAlignment(Qt.AlignCenter)
        delete_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        delete_item.setToolTip(
            "Cancel this pending addition."
            if is_pending_addition
            else "Remove this file from the image on Save."
        )
        self.table.setItem(row, 0, delete_item)

        path_item = QTableWidgetItem(image_path)
        path_item.setFlags(path_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 1, path_item)

        copy_item = QTableWidgetItem("📋")
        copy_item.setTextAlignment(Qt.AlignCenter)
        copy_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        copy_item.setToolTip("Copy filename to clipboard.")
        self.table.setItem(row, 2, copy_item)

        filename_item = QTableWidgetItem(filename)
        filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        filename_item.setToolTip("Double-click to rename this file inside the image.")
        self.table.setItem(row, 3, filename_item)

        title_mode = self._image_path_title_mode(image_path)
        is_midi = self._is_midi_image_path(image_path)
        raw_title = title if title != "" else (filename if title_mode == "midi" else "")
        title_item = self._make_title_item(raw_title, title_mode=title_mode, fallback_title=filename)
        self.table.setItem(row, 4, title_item)

        self._update_compat_indicator(row, raw_title)

        kind_item = QTableWidgetItem(midi_type or self._kind_for_image_file(filename))
        kind_item.setTextAlignment(Qt.AlignCenter)
        kind_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if title_mode == "eseq" or kind_item.text().startswith(("FIL", "ESQ")):
            kind_item.setToolTip("Yamaha E-SEQ type, arrangement, and write-protect information.")
        elif is_midi:
            kind_item.setToolTip("Detected MIDI file type from header bytes.")
        else:
            kind_item.setToolTip("File type from the image filename.")
        self.table.setItem(row, 6, kind_item)

    def _unique_backup_path(self, desired_path):
        if not os.path.exists(desired_path):
            return desired_path
        stem, ext = os.path.splitext(desired_path)
        counter = 2
        while True:
            candidate = f"{stem}_{counter}{ext}"
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def _get_backup_path(self, file_path):
        source_dir = os.path.dirname(os.path.abspath(file_path))
        backup_root = self.regularModeContextPath if not self.is_image_mode() else ""
        if not backup_root or not os.path.isdir(backup_root):
            backup_root = source_dir
        backup_dir = os.path.join(backup_root, "backup")
        os.makedirs(backup_dir, exist_ok=True)
        return self._unique_backup_path(os.path.join(backup_dir, os.path.basename(file_path)))

    def _get_image_backup_path(self, image_path):
        stem, ext = os.path.splitext(os.path.abspath(image_path))
        return self._unique_backup_path(f"{stem}_backup{ext}")

    def _centered_title_plain_text(self, title):
        if not title:
            return ""
        if not self._title_looks_centered(title):
            return title if len(title) > 32 else title.strip()
        padded_title = title[:32].ljust(32)
        first_half = padded_title[:16].strip()
        second_half = padded_title[16:32].strip()
        plain_text = " ".join(part for part in (first_half, second_half) if part)
        return plain_text or title.strip()

    def _centered_title_threshold(self, titled_count):
        if titled_count < 2:
            return 99
        return min(self.CENTERED_TITLE_DISK_THRESHOLD, titled_count)

    def _active_titles_likely_centered(self):
        if self.is_image_mode():
            return self.imageTitlesLikelyCentered
        return self.regularTitlesLikelyCentered

    def _update_image_centered_title_assumption(self, candidate_titles=None):
        titles = []
        if candidate_titles is not None:
            titles = [str(title) for title in candidate_titles if title]
        elif self.is_image_mode():
            for row in range(self.table.rowCount()):
                if self._is_special_pianodir_row(row):
                    continue
                raw_title = self._row_raw_title(row)
                if raw_title:
                    titles.append(raw_title)

        centered_count = sum(1 for title in titles if self._title_looks_centered(title))
        threshold = self._centered_title_threshold(len(titles))
        self.imageTitlesLikelyCentered = centered_count >= threshold
        return self.imageTitlesLikelyCentered

    def _update_regular_centered_title_assumption(self, candidate_titles=None):
        titles = []
        if candidate_titles is not None:
            titles = [str(title) for title in candidate_titles if title]
        elif not self.is_image_mode():
            for row in self._regular_file_rows():
                full_path_item = self.table.item(row, 1)
                if full_path_item is None:
                    continue
                title_mode = self._listed_file_title_mode(full_path_item.text())
                if title_mode not in {"midi", "eseq"}:
                    continue
                raw_title = self._row_raw_title(row)
                if raw_title:
                    titles.append(raw_title)

        centered_count = sum(1 for title in titles if self._title_looks_centered(title))
        threshold = self._centered_title_threshold(len(titles))
        self.regularTitlesLikelyCentered = centered_count >= threshold
        return self.regularTitlesLikelyCentered

    def _should_display_centered_title(self, raw_title, *, title_mode=""):
        if not raw_title:
            return False
        return self._title_looks_centered(raw_title)

    def _display_title_text(self, raw_title, *, title_mode="", fallback_title=""):
        if raw_title:
            return raw_title
        if title_mode == "midi":
            return fallback_title
        return ""

    def _title_item_tooltip(self, title_mode, raw_title=""):
        if title_mode == "eseq":
            tooltip = "Click to edit this E-SEQ title."
        elif title_mode == "midi":
            tooltip = "Click to edit this MIDI title."
        else:
            tooltip = "Only MIDI and E-SEQ files have editable title metadata."
        return tooltip

    def _make_title_item(self, raw_title, *, title_mode="", fallback_title=""):
        display_title = self._display_title_text(
            raw_title,
            title_mode=title_mode,
            fallback_title=fallback_title,
        )
        title_item = QTableWidgetItem(display_title)
        title_item.setFont(self.title_monospace_font)
        title_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        title_item.setData(self.TITLE_RAW_ROLE, raw_title)
        title_item.setToolTip(self._title_item_tooltip(title_mode, raw_title))
        return title_item

    def _row_raw_title(self, row):
        title_item = self.table.item(row, 4)
        if title_item is None:
            return ""
        raw_title = title_item.data(self.TITLE_RAW_ROLE)
        if raw_title is None:
            return title_item.text()
        return str(raw_title)

    def _refresh_image_title_display_items(self):
        if not self.is_image_mode():
            return
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            filename_item = self.table.item(row, 3)
            title_item = self.table.item(row, 4)
            if path_item is None or title_item is None:
                continue
            image_path = path_item.text()
            raw_title = self._row_raw_title(row)
            title_mode = self._image_path_title_mode(image_path)
            fallback_title = filename_item.text() if filename_item is not None else os.path.basename(image_path)
            title_item.setText(
                self._display_title_text(
                    raw_title,
                    title_mode=title_mode,
                    fallback_title=fallback_title,
                )
            )
            title_item.setToolTip(self._title_item_tooltip(title_mode, raw_title))

    def _refresh_regular_title_display_items(self):
        if self.is_image_mode():
            return
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            filename_item = self.table.item(row, 3)
            title_item = self.table.item(row, 4)
            if full_path_item is None or title_item is None:
                continue
            full_path = full_path_item.text()
            raw_title = self._row_raw_title(row)
            title_mode = self._listed_file_title_mode(full_path)
            fallback_title = filename_item.text() if filename_item is not None else os.path.basename(full_path)
            title_item.setText(
                self._display_title_text(
                    raw_title,
                    title_mode=title_mode,
                    fallback_title=fallback_title,
                )
            )
            title_item.setToolTip(self._title_item_tooltip(title_mode, raw_title))

    def _reapply_image_centered_title_assumption(self):
        if not self.is_image_mode():
            return
        self._update_image_centered_title_assumption()
        self._refresh_image_title_display_items()

    def _reapply_regular_centered_title_assumption(self):
        if self.is_image_mode():
            return
        self._update_regular_centered_title_assumption()
        self._refresh_regular_title_display_items()

    def _create_backup_if_enabled(self, file_path):
        if not self.backup_checkbox.isChecked():
            return None
        backup_path = self._get_backup_path(file_path)
        try:
            shutil.copy2(file_path, backup_path)
            return None
        except Exception as e:
            return (
                f"Could not create backup for {os.path.basename(file_path)} at "
                f"{os.path.basename(backup_path)}: {e}"
            )

    def _create_image_backup_if_enabled(self, image_path):
        if not self.backup_checkbox.isChecked():
            return None
        backup_path = self._get_image_backup_path(image_path)
        try:
            shutil.copy2(image_path, backup_path)
            return None
        except Exception as e:
            return (
                f"Could not create backup image for {os.path.basename(image_path)} at "
                f"{os.path.basename(backup_path)}: {e}"
            )

    def _is_title_too_long(self, title):
        return len(title) > self.TITLE_COMPAT_LIMIT

    def _update_compat_indicator(self, row, title):
        indicator = QTableWidgetItem("LONG" if self._is_title_too_long(title) else "")
        indicator.setTextAlignment(Qt.AlignCenter)
        indicator.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if self._is_title_too_long(title):
            indicator.setToolTip(
                f"Title is longer than {self.TITLE_COMPAT_LIMIT} characters; "
                "older systems may truncate or reject it."
            )
        else:
            indicator.setToolTip(
                f"Title length is within the {self.TITLE_COMPAT_LIMIT}-character compatibility limit."
            )
        self.table.setItem(row, 5, indicator)

    def refresh_compat_indicators(self):
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            self._update_compat_indicator(row, self._row_raw_title(row))

    def _update_midi_type_indicator(self, row, midi_type):
        indicator = QTableWidgetItem(midi_type if midi_type else "Unknown")
        indicator.setTextAlignment(Qt.AlignCenter)
        indicator.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if midi_type and midi_type.startswith(("FIL", "ESQ")):
            tooltip = "Yamaha E-SEQ type, arrangement, and write-protect information."
        elif midi_type:
            tooltip = "Detected MIDI file type from header bytes."
        else:
            tooltip = "MIDI type could not be determined for this file."
        indicator.setToolTip(f"{tooltip} Double-click to inspect this song.")
        self.table.setItem(row, 6, indicator)

    def refresh_midi_type_indicators(self):
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                self._update_midi_type_indicator(row, "Unknown")
                continue
            info = self._listed_file_info(full_path_item.text())
            if info.get("title_mode") == "eseq":
                midi_type = info.get("midi_type") or "FIL"
            else:
                midi_type = info.get("midi_type") or extract_midi_type_label_from_midi(
                    self._regular_source_material_path(full_path_item.text())
                )
            self._update_midi_type_indicator(row, midi_type)
        self._resize_table_columns_to_fill()

    def browse_directory(self):
        leaving_image_mode = False
        if self.is_image_mode():
            if not self._confirm_discard_image_changes():
                return
            leaving_image_mode = True

        directory = QFileDialog.getExistingDirectory(self, "Open MIDI Folder")
        if directory:
            if leaving_image_mode:
                self._reset_image_state()
                self._apply_midi_mode_ui()
            self._cleanup_midi_scratch_dir()
            file_paths = self._regular_folder_file_paths(directory)
            mpc_seq_paths = self._mpc_seq_file_paths_in_folder(directory)
            if mpc_seq_paths and self._prompt_for_mpc_seq_conversion(
                [os.path.basename(path) for path in mpc_seq_paths],
                os.path.basename(directory) or directory,
            ):
                self._convert_mpc_seq_files_to_midi_mode(
                    mpc_seq_paths,
                    os.path.basename(directory) or directory,
                    extra_regular_paths=file_paths,
                )
            else:
                self._load_regular_files(file_paths, f"Selected Folder: \"{directory}\"")

    def clear_list(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return
        if self.is_image_mode():
            if not self._confirm_discard_image_changes():
                return
            self._clear_regular_list_state()
            self._reset_image_state()
            self._apply_midi_mode_ui()
            self.status_label.setText("Image Mode closed.")
            return
        if self.table.rowCount() == 0:
            self._clear_regular_list_state()
            self._refresh_regular_mode_action_state()
            self._cleanup_midi_scratch_dir()
            self._apply_midi_mode_ui()
            self.status_label.setText("List is already empty.")
            return

        reply = QMessageBox.question(
            self,
            "Clear List",
            "Remove all files from the current list?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._clear_regular_list_state()
        self._refresh_regular_mode_action_state()
        self._cleanup_midi_scratch_dir()
        self._apply_midi_mode_ui()
        self.status_label.setText("List cleared.")

    def _apply_path_remap(self, old_to_new):
        if not old_to_new:
            return
        self.pendingEdits = {
            old_to_new.get(path, path): title
            for path, title in self.pendingEdits.items()
        }
        self.pendingRegularConversions = {
            old_to_new.get(path, path): conversion
            for path, conversion in self.pendingRegularConversions.items()
        }
        self.pendingRegularRenames = {
            old_to_new.get(path, path): filename
            for path, filename in self.pendingRegularRenames.items()
        }
        self.listedFileInfo = {
            old_to_new.get(path, path): info
            for path, info in self.listedFileInfo.items()
        }

    def _update_table_paths(self, old_to_new):
        if not old_to_new:
            return

        sorting_enabled = self.table.isSortingEnabled()
        if sorting_enabled:
            self.table.setSortingEnabled(False)

        try:
            for row in range(self.table.rowCount()):
                full_path_item = self.table.item(row, 1)
                if not full_path_item:
                    continue
                old_path = full_path_item.text()
                new_path = old_to_new.get(old_path)
                if not new_path:
                    continue

                full_path_item.setText(new_path)
                filename_item = self.table.item(row, 3)
                if filename_item:
                    filename_item.setText(os.path.basename(new_path))
                else:
                    filename_item = QTableWidgetItem(os.path.basename(new_path))
                    filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    self.table.setItem(row, 3, filename_item)
        finally:
            if sorting_enabled:
                self.table.setSortingEnabled(True)
                self.table.sortItems(3, order=Qt.AscendingOrder)

    def _stage_regular_row_pending_rename(self, row, source_path, target_filename):
        current_name = os.path.basename(source_path)
        if target_filename == current_name:
            self.pendingRegularRenames.pop(source_path, None)
            return

        self.pendingRegularRenames[source_path] = target_filename
        filename_item = self.table.item(row, 3)
        if filename_item is None:
            filename_item = QTableWidgetItem(target_filename)
            filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, 3, filename_item)
        else:
            filename_item.setText(target_filename)
        filename_item.setToolTip(
            "Pending DOS 8.3 filename. Use Save to rename the original file, or Save As to write a renamed copy."
        )

    def rename_all_for_disk(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return

        if self.is_image_mode() or self.is_local_eseq_mode():
            QMessageBox.information(self, "MIDI Mode Required", "Rename 8.3 is available for MIDI folders only.")
            return

        row_count = self._regular_file_count()
        if row_count == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return

        all_paths = []
        rows_by_path = {}
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if not full_path_item:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) != "midi":
                QMessageBox.information(
                    self,
                    "MIDI Files Required",
                    "Rename 8.3 is available only when all listed files are MIDI files.",
                )
                return
            all_paths.append(full_path)
            rows_by_path[full_path] = row

        if not all_paths:
            QMessageBox.information(self, "No Valid Files", "No valid files are currently listed.")
            return
        if not self._regular_filenames_need_dos83_rename():
            QMessageBox.information(
                self,
                "Rename Not Needed",
                "All listed filenames are already 8.3 length or shorter.",
            )
            self._refresh_regular_mode_action_state()
            return

        message = (
            f"Stage DOS 8.3 filenames for {len(all_paths)} listed file(s)?\n"
            "This applies 00/01/... prefixes and a .MID extension.\n\n"
            "Nothing will be renamed until you use Save. Save As writes renamed copies and leaves the originals alone."
        )
        if self.backup_checkbox.isChecked():
            message += "\n\nWhen you Save, copies with the old filenames will be kept in the backup folder."
        reply = QMessageBox.question(
            self,
            "Stage DOS 8.3 Filenames",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            plan = build_midi_dos83_plan(all_paths)
            validate_midi_dos83_plan(plan)
        except Exception as e:
            self._show_operation_error(
                "Rename Could Not Be Staged",
                "The listed files could not be staged for DOS 8.3 filenames",
                e,
                guidance="Check that the source files still exist and that the generated names do not conflict",
            )
            return

        staged_count = 0
        unchanged_count = 0
        for source, target in plan:
            row = rows_by_path.get(source)
            if row is None:
                continue
            if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
                self.pendingRegularRenames.pop(source, None)
                unchanged_count += 1
                continue
            self._stage_regular_row_pending_rename(row, source, os.path.basename(target))
            staged_count += 1

        status_parts = [f"Staged {staged_count} DOS 8.3 filename change(s)."]
        if unchanged_count:
            status_parts.append(f"{unchanged_count} already matched and were left unchanged.")
        status_parts.append("Use Save to rename originals, or Save As to write renamed copies elsewhere.")
        if self.backup_checkbox.isChecked() and staged_count:
            status_parts.append("Backup is enabled; Save will keep copies with the old filenames.")
        self.status_label.setText("\n".join(status_parts))
        self._refresh_regular_mode_action_state()

    def _confirm_type0_conversion(self, file_count):
        skip_warning = self.settings.value(self.SETTING_SKIP_TYPE0_WARNING, False, type=bool)
        if skip_warning:
            return True

        warning_box = QMessageBox(self)
        apply_window_icon(warning_box)
        warning_box.setIcon(QMessageBox.Warning)
        warning_box.setWindowTitle("Convert All to MIDI Type 0")
        warning_box.setText(
            f"This will stage {file_count} listed file(s) for MIDI Type 0 (single track) conversion.\n\n"
            "Nothing will be written to disk until you use Save or Save As.\n\n"
            "This conversion is not compatible with Yamaha XG files."
        )

        backup_hint = (
            "Backup recommendation: backups are currently enabled and will be created when you save."
            if self.backup_checkbox.isChecked()
            else (
                "Backup recommendation: enable \"Back up before saving\" before saving the staged conversion."
            )
        )
        warning_box.setInformativeText(
            f"{backup_hint}\n\nDo you want to continue?"
        )
        dont_show_checkbox = QCheckBox("Do not show this warning again")
        warning_box.setCheckBox(dont_show_checkbox)
        warning_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        warning_box.setDefaultButton(QMessageBox.No)
        result = self._exec_child_dialog(warning_box)
        confirmed = result == QMessageBox.Yes
        if confirmed and dont_show_checkbox.isChecked():
            self.settings.setValue(self.SETTING_SKIP_TYPE0_WARNING, True)
        return confirmed

    def convert_all_to_type0(self):
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return

        row_count = self._regular_file_count()
        if row_count == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return

        midi_rows = []
        rows_to_convert = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if not full_path_item:
                continue
            full_path = full_path_item.text()
            if self._listed_file_title_mode(full_path) != "midi":
                continue
            midi_rows.append((row, full_path))
            if self._row_midi_type_label(row, full_path) != "Type 0":
                rows_to_convert.append((row, full_path))

        if not midi_rows:
            QMessageBox.information(self, "No Valid Files", "No valid files are currently listed.")
            return
        if not rows_to_convert:
            QMessageBox.information(
                self,
                "Conversion Not Needed",
                "All listed MIDI files are already SMF0 / Type 0.",
            )
            self._refresh_regular_mode_action_state()
            return

        if not self._confirm_type0_conversion(len(rows_to_convert)):
            return

        progressDialog = QProgressDialog(
            "Staging MIDI Type 0 conversions...",
            "Cancel",
            0,
            len(rows_to_convert),
            self,
        )
        self._prepare_progress_dialog(progressDialog)

        converted_count = 0
        unchanged_count = 0
        errors = []
        scratch_dir = self._ensure_midi_scratch_dir()
        for index, (_initial_row, full_path) in enumerate(rows_to_convert, start=1):
            if progressDialog.wasCanceled():
                break

            row = None
            for candidate_row in range(self.table.rowCount()):
                item = self.table.item(candidate_row, 1)
                if item is not None and item.text() == full_path:
                    row = candidate_row
                    break
            if row is None:
                continue

            target_filename = self._regular_row_output_filename(row)
            output_temp_path = os.path.join(scratch_dir, f"{uuid.uuid4().hex}_{target_filename}")
            source_material_path = self._regular_source_material_path(full_path)
            try:
                changed = convert_midi_file_to_type0_path(source_material_path, output_temp_path)
                if not changed:
                    unchanged_count += 1
                    continue
                self._apply_regular_row_pending_conversion(
                    row,
                    full_path,
                    target_filename,
                    output_temp_path,
                    "midi_type0",
                    overwrite_original=True,
                )
                converted_count += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(full_path)}: {exc}")
            finally:
                progressDialog.setValue(index)
                QApplication.processEvents()
        progressDialog.close()

        status_parts = [f"Staged {converted_count} file(s) for MIDI Type 0 conversion."]
        if unchanged_count:
            status_parts.append(f"{unchanged_count} already Type 0 and were left unchanged.")
        if converted_count:
            status_parts.append("Use Save to overwrite the originals, or Save As to write copies.")
        if errors:
            status_parts.append(f"{len(errors)} file(s) failed conversion.")
        self.status_label.setText("\n".join(status_parts))
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()

        if errors:
            self._show_error_list(
                "Type 0 Conversion Issues",
                "Some MIDI files could not be staged for Type 0 conversion",
                errors,
                warning=True,
                guidance="The original files were not changed; remove or replace the listed files and try again",
            )

    def _dos_eseq_filename(self, filename, *, variant=None, used_filenames=None):
        stem = os.path.splitext(os.path.basename(filename))[0] or "FILE"
        extension = self._eseq_song_extension(variant)
        return self._build_dos_image_filename(
            f"{stem}.{extension}",
            {str(name).upper() for name in (used_filenames or set())},
        )

    def _image_used_filenames_for_directory(self, directory, *, exclude_row=None):
        directory_key = directory.replace("\\", "/").strip("/").upper()
        used = set()
        for row in range(self.table.rowCount()):
            if exclude_row is not None and row == exclude_row:
                continue
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            final_path = self._final_image_path(path_item.text()).replace("\\", "/").strip("/")
            if os.path.dirname(final_path).replace("\\", "/").strip("/").upper() == directory_key:
                used.add(os.path.basename(final_path).upper())
        return used

    def _converted_image_path_for_kind(self, image_path, target_kind, *, exclude_row=None):
        directory = os.path.dirname(image_path).replace("\\", "/")
        stem = os.path.splitext(os.path.basename(image_path))[0] or os.path.basename(image_path) or "FILE"
        extension = ".MID" if target_kind == "midi" else f".{self._eseq_song_extension(self.imageEseqVariant)}"
        if target_kind == "eseq":
            filename = self._dos_eseq_filename(
                f"{stem}{extension}",
                variant=self.imageEseqVariant,
                used_filenames=self._image_used_filenames_for_directory(directory, exclude_row=exclude_row),
            )
        else:
            filename = f"{stem.upper()}{extension}"
        return self._join_image_path(directory, filename)

    def _image_row_current_title(self, row):
        return self._row_raw_title(row)

    def _apply_image_row_conversion(
        self,
        row,
        source_path,
        target_path,
        replacement_host_path,
        *,
        title,
        midi_type,
        is_midi,
        title_mode,
        size,
        order_key,
    ):
        path_item = self.table.item(row, 1)
        filename_item = self.table.item(row, 3)
        if path_item is None or filename_item is None:
            return

        info_key = source_path
        if source_path in self.pendingImageAdditions:
            self.pendingImageAdditions.pop(source_path, None)
            self.pendingImageAdditions[target_path] = replacement_host_path
            self.pendingImageReplacements.pop(source_path, None)
            self.pendingImageTitleEdits.pop(source_path, None)
            if source_path in self.imageFileInfo:
                self.imageFileInfo[target_path] = self.imageFileInfo.pop(source_path)
            path_item.setText(target_path)
            info_key = target_path
        else:
            self.pendingImageReplacements[source_path] = replacement_host_path
            self.pendingImageTitleEdits.pop(source_path, None)
            if target_path.upper() == source_path.upper():
                self.pendingImageRenames.pop(source_path, None)
            else:
                self.pendingImageRenames[source_path] = target_path

        display_filename = os.path.basename(target_path)
        filename_item.setText(display_filename)
        raw_title = title if title != "" else (display_filename if title_mode == "midi" else "")
        title_item = self._make_title_item(raw_title, title_mode=title_mode, fallback_title=display_filename)
        self.table.setItem(row, 4, title_item)

        self._set_image_file_info(
            info_key,
            is_midi=is_midi,
            title=title,
            midi_type=midi_type,
            size=size,
            title_mode=title_mode,
            order_key=order_key,
        )
        self._reapply_image_centered_title_assumption()
        kind_item = self.table.item(row, 6)
        if kind_item is None:
            kind_item = QTableWidgetItem("")
            kind_item.setTextAlignment(Qt.AlignCenter)
            kind_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, 6, kind_item)
        kind_item.setText(midi_type or self._kind_for_image_file(target_path))
        kind_item.setToolTip(
            "Detected MIDI file type from header bytes."
            if is_midi
            else "File type from the image filename."
        )
        self._update_compat_indicator(row, raw_title)

    def _queue_image_format_conversion(self, row, target_kind):
        if self.image_session is None:
            raise EseqConversionError("No image or floppy is currently loaded.")
        if self._is_special_pianodir_row(row):
            raise EseqConversionError(f"{self._eseq_directory_filename(self.imageEseqVariant)} is managed automatically.")

        path_item = self.table.item(row, 1)
        if path_item is None:
            raise EseqConversionError("Could not locate the selected image file.")

        source_path = path_item.text()
        current_path = self._row_final_image_path(row)
        current_title = self._image_row_current_title(row)
        target_path = self._converted_image_path_for_kind(current_path, target_kind, exclude_row=row)
        if target_path.upper() in self._active_image_paths(exclude_row=row):
            raise EseqConversionError(f"'{os.path.basename(target_path)}' already exists in this image folder.")

        source_host_path = self._pending_or_extracted_image_path(source_path)
        if not source_host_path or not os.path.isfile(source_host_path):
            raise EseqConversionError(f"Could not prepare '{os.path.basename(current_path)}' for conversion.")

        output_host_path = os.path.join(
            self.image_session.patched_dir,
            f"{uuid.uuid4().hex}_{os.path.basename(target_path)}",
        )
        title_override = current_title or None
        if target_kind == "midi":
            convert_eseq_file_to_midi_path(source_host_path, output_host_path, title_override=title_override)
        else:
            source_host_path = self._type0_midi_source_for_eseq_conversion(
                source_host_path,
                self.image_session.patched_dir,
                os.path.basename(target_path),
            )
            convert_midi_file_to_eseq_path(
                source_host_path,
                output_host_path,
                title_override=title_override,
                filename_hint=os.path.basename(target_path),
                container_variant=self._eseq_converter_container(self.imageEseqVariant),
            )

        size = os.path.getsize(output_host_path)
        is_midi, title, midi_type, title_mode, order_key = self._probe_image_file(target_path, size, output_host_path)
        self._apply_image_row_conversion(
            row,
            source_path,
            target_path,
            output_host_path,
            title=title,
            midi_type=midi_type,
            is_midi=is_midi,
            title_mode=title_mode,
            size=size,
            order_key=order_key,
        )
        return os.path.basename(current_path), os.path.basename(target_path)

    def _prompt_for_eseq_to_midi_mode_switch(self, converted_count):
        saved_choice = str(
            self.settings.value(self.SETTING_ESEQ_TO_MIDI_SWITCH_MODE, "ask")
        ).strip().lower()
        if saved_choice in {"switch", "export"}:
            return True

        mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        prompt_box = QMessageBox(self)
        apply_window_icon(prompt_box)
        prompt_box.setIcon(QMessageBox.Question)
        prompt_box.setWindowTitle("Convert and Exit")
        prompt_box.setText(
            f"Convert {converted_count} E-SEQ file(s) to MIDI and leave {mode_name}?"
        )
        prompt_box.setInformativeText(
            "You will choose a destination folder next.\n"
            "Converted MIDI files will be written there and then opened in regular MIDI Mode.\n"
            "Only MIDI files are carried over."
        )
        remember_checkbox = QCheckBox("Remember my choice and do not ask again")
        prompt_box.setCheckBox(remember_checkbox)
        prompt_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt_box.setDefaultButton(QMessageBox.Yes)
        prompt_box.button(QMessageBox.Yes).setText("Convert and Exit")
        prompt_box.button(QMessageBox.No).setText("Cancel")

        should_switch = self._exec_child_dialog(prompt_box) == QMessageBox.Yes
        if remember_checkbox.isChecked():
            self.settings.setValue(
                self.SETTING_ESEQ_TO_MIDI_SWITCH_MODE,
                "export" if should_switch else "ask",
            )
        return should_switch

    def _choose_eseq_to_midi_export_directory(self):
        mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        default_dir = os.path.expanduser("~")
        if self.image_session is not None and not self.image_session.source_kind.startswith("floppy"):
            default_dir = os.path.dirname(self.image_session.source_path) or default_dir
        return QFileDialog.getExistingDirectory(self, f"Choose {mode_name} MIDI Export Folder", default_dir)

    def _build_switched_midi_mode_files(self, conversion_rows, dest_dir):
        if self.image_session is None:
            raise EseqConversionError("No floppy image or floppy session is currently loaded.")

        conversion_rows = set(conversion_rows or [])
        midi_specs = []
        used_targets = set()
        visible_file_count = 0

        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue

            path_item = self.table.item(row, 1)
            if path_item is None:
                continue

            source_path = path_item.text()
            if source_path in self.pendingImageDeletes:
                continue
            visible_file_count += 1

            final_image_path = self._final_image_path(source_path)
            should_convert = row in conversion_rows
            should_export = should_convert or self._image_path_is_midi(source_path)
            if not should_export:
                continue

            if should_convert:
                export_image_path = self._converted_image_path_for_kind(final_image_path, "midi")
            else:
                export_image_path = final_image_path

            relative_parts = self._image_export_relative_parts(export_image_path)
            dest_path = os.path.join(dest_dir, *relative_parts)
            dest_key = os.path.normcase(dest_path)
            if dest_key in used_targets:
                raise EseqConversionError(f"'{os.path.basename(export_image_path)}' would be written more than once.")
            used_targets.add(dest_key)

            source_host_path = self._pending_or_extracted_image_path(source_path)
            if not source_host_path or not os.path.isfile(source_host_path):
                raise EseqConversionError(
                    f"Could not prepare '{os.path.basename(final_image_path)}' for MIDI export."
                )

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            current_title = self._image_row_current_title(row)
            title_override = current_title or None

            if should_convert:
                convert_eseq_file_to_midi_path(source_host_path, dest_path, title_override=title_override)
            else:
                self._write_image_row_to_destination(source_path, dest_path)

            title = extract_first_title_from_midi(dest_path)
            if title.startswith("Error"):
                title = ""
            midi_type = extract_midi_type_label_from_midi(dest_path)
            midi_specs.append((dest_path, os.path.basename(dest_path), title, midi_type))

        omitted_count = max(0, visible_file_count - len(midi_specs))
        return midi_specs, omitted_count

    def _image_eseq_conversion_rows(self):
        rows = []
        if self.image_session is None:
            return rows
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                rows.append(row)
        return rows

    def _convert_loaded_floppy_to_midi_after_read(self):
        if not self.pendingFloppyReadConvertToMidi:
            return
        self.pendingFloppyReadConvertToMidi = False
        if self.image_session is None:
            return

        conversion_rows = self._image_eseq_conversion_rows()
        if not conversion_rows:
            QMessageBox.information(
                self,
                "No E-SEQ Files Found",
                "The floppy was read, but no Yamaha E-SEQ files were found to convert to MIDI.",
            )
            QTimer.singleShot(0, self._offer_post_load_sequence_conversions)
            return

        self.pendingExportPianodirMetadata = self._current_visible_pianodir_metadata()

        progressDialog = QProgressDialog(
            "Converting E-SEQ files...",
            "Cancel",
            0,
            len(conversion_rows),
            self,
        )
        self._prepare_progress_dialog(progressDialog)

        converted = []
        errors = []
        for index, row in enumerate(conversion_rows):
            if progressDialog.wasCanceled():
                break
            try:
                converted.append(self._queue_image_format_conversion(row, "midi"))
            except Exception as exc:
                filename_item = self.table.item(row, 3)
                label = filename_item.text() if filename_item is not None else "Unknown file"
                errors.append(f"{label}: {exc}")
            progressDialog.setValue(index + 1)
            QApplication.processEvents()

        progressDialog.close()
        has_eseq_remaining = False
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                has_eseq_remaining = True
                break
        if not has_eseq_remaining:
            self.pendingDeletePianodir = self.imageHasPianodir
        self._refresh_pianodir_row()

        status_parts = [f"Read the floppy and queued {len(converted)} file(s) for E-SEQ -> MIDI conversion."]
        remaining = self._pending_image_space_remaining()
        status_parts.append(f"Estimated free space after pending changes: {display_bytes(max(0, remaining))}.")
        if converted:
            status_parts.append("Use Save, Save As, or Save As Image to write the converted files.")
        if errors:
            status_parts.append(f"{len(errors)} file(s) could not be converted.")
        self.status_label.setText("\n".join(status_parts))

        if errors:
            self._show_error_list(
                "Floppy MIDI Conversion Issues",
                "Some E-SEQ files could not be staged for MIDI conversion",
                errors,
                warning=True,
                guidance="Nothing has been written yet; remove or replace the listed files and try again",
            )

    def _switch_to_midi_mode_after_eseq_conversion(self, conversion_rows):
        converted_count = len(conversion_rows)
        if converted_count <= 0 or not self._prompt_for_eseq_to_midi_mode_switch(converted_count):
            return

        dest_dir = self._choose_eseq_to_midi_export_directory()
        if not dest_dir:
            return
        export_dir = self._destination_with_album_subfolder(dest_dir)

        try:
            midi_specs, omitted_count = self._build_switched_midi_mode_files(conversion_rows, export_dir)
        except Exception as exc:
            self._show_error_list(
                "Convert and Exit Failed",
                "The converted MIDI files could not be written before leaving Floppy/Image mode",
                [exc],
                warning=True,
                guidance="The current floppy/image session is still open; choose a writable destination folder and try again",
            )
            return

        if not midi_specs:
            QMessageBox.information(
                self,
                "No MIDI Files",
                "No MIDI files were available to export into regular MIDI Mode.",
            )
            return

        source_mode_name = self.image_session.mode_name if self.image_session is not None else "Image Mode"
        self._reset_image_state()

        status_text = (
            f"Converted {converted_count} E-SEQ file(s) to MIDI and left {source_mode_name}.\n"
            f"Current context moved to: \"{export_dir}\""
        )
        if omitted_count:
            status_text += f"\n{omitted_count} non-MIDI file(s) were not exported into MIDI Mode."
        self._load_midi_paths_into_list(midi_specs, status_text)

    def _regular_used_output_filenames_for_directory(self, directory, *, exclude_row=None):
        directory_key = os.path.normcase(os.path.abspath(directory or ""))
        used = set()
        for row in self._regular_file_rows():
            if exclude_row is not None and row == exclude_row:
                continue
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            row_directory = os.path.normcase(os.path.abspath(os.path.dirname(full_path_item.text())))
            if row_directory == directory_key:
                used.add(self._regular_row_output_filename(row).upper())
        return used

    def _converted_regular_filename_for_kind(self, full_path, target_kind, *, used_filenames=None):
        stem = os.path.splitext(os.path.basename(full_path))[0] or os.path.basename(full_path) or "FILE"
        if target_kind == "eseq":
            return self._dos_eseq_filename(
                f"{stem}.{self._eseq_song_extension(self.regularEseqVariant)}",
                variant=self.regularEseqVariant,
                used_filenames=used_filenames,
            )
        return stem + ".mid"

    def _converted_regular_path_for_kind(self, full_path, target_kind, *, output_dir=None):
        directory = output_dir or os.path.dirname(full_path)
        return os.path.join(directory, self._converted_regular_filename_for_kind(full_path, target_kind))

    def _apply_regular_row_pending_conversion(
        self,
        row,
        source_path,
        target_filename,
        temp_path,
        target_kind,
        *,
        overwrite_original=False,
    ):
        title, midi_type, title_mode, is_midi, order_key = self._probe_regular_file(temp_path)
        self.pendingRegularConversions[source_path] = {
            "temp_path": temp_path,
            "target_kind": target_kind,
            "target_filename": target_filename,
            "overwrite_original": bool(overwrite_original),
        }
        self.pendingEdits.pop(source_path, None)
        self._set_listed_file_info(
            source_path,
            title_mode=title_mode,
            midi_type=midi_type,
            is_midi=is_midi,
            order_key=order_key,
        )

        filename_item = self.table.item(row, 3)
        if filename_item is None:
            filename_item = QTableWidgetItem(target_filename)
            filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, 3, filename_item)
        else:
            filename_item.setText(target_filename)
        filename_item.setToolTip("Pending converted filename. Use Save, Save As, or Save As Image to write it.")

        raw_title = title if title != "" else (target_filename if title_mode == "midi" else "")
        self.table.setItem(
            row,
            4,
            self._make_title_item(raw_title, title_mode=title_mode, fallback_title=target_filename),
        )
        self._update_compat_indicator(row, raw_title)
        self._update_midi_type_indicator(row, midi_type)

    def _convert_all_regular_rows(self, source_kind, target_kind):
        if self.is_image_mode():
            return False
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return True

        applicable_paths = []
        for row in range(self.table.rowCount()):
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            title_mode = self._listed_file_title_mode(full_path)
            if title_mode == source_kind:
                applicable_paths.append(full_path)

        if not applicable_paths:
            kind_label = "E-SEQ" if source_kind == "eseq" else "MIDI"
            QMessageBox.information(self, "Nothing To Convert", f"No {kind_label} files are currently listed.")
            return True

        if target_kind == "eseq":
            existing_eseq_count = sum(
                1
                for info in self.listedFileInfo.values()
                if info.get("title_mode") == "eseq"
            )
            if not self._ensure_eseq_file_limit(
                existing_eseq_count + len(applicable_paths),
                action_text="Converting these files to E-SEQ",
            ):
                return True

        reply = QMessageBox.question(
            self,
            f"Convert All {source_kind.upper()} to {target_kind.upper()}",
            (
                f"Convert {len(applicable_paths)} listed {source_kind.upper()} file(s) to {target_kind.upper()}?\n\n"
                "The converted files will be staged in the list only. Nothing will be written to disk until you use "
                "Save, Save As, or Save As Image."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return True

        if source_kind == "eseq" and target_kind == "midi":
            self.pendingExportPianodirMetadata = self._current_visible_pianodir_metadata()
        else:
            self.pendingExportPianodirMetadata = PianodirMetadata()

        progressDialog = QProgressDialog(
            f"Converting {source_kind.upper()} files...",
            "Cancel",
            0,
            len(applicable_paths),
            self,
        )
        self._prepare_progress_dialog(progressDialog)

        converted_count = 0
        errors = []
        scratch_dir = self._ensure_midi_scratch_dir()
        for index, full_path in enumerate(applicable_paths):
            if progressDialog.wasCanceled():
                break

            row = None
            for candidate_row in range(self.table.rowCount()):
                item = self.table.item(candidate_row, 1)
                if item is not None and item.text() == full_path:
                    row = candidate_row
                    break
            if row is None:
                continue

            target_filename = self._converted_regular_filename_for_kind(
                full_path,
                target_kind,
                used_filenames=self._regular_used_output_filenames_for_directory(
                    os.path.dirname(full_path),
                    exclude_row=row,
                ),
            )
            output_temp_path = os.path.join(scratch_dir, f"{uuid.uuid4().hex}_{target_filename}")
            source_material_path = self._regular_source_material_path(full_path)
            current_title = self._row_raw_title(row)
            title_override = current_title or None
            try:
                if target_kind == "midi":
                    convert_eseq_file_to_midi_path(
                        source_material_path,
                        output_temp_path,
                        title_override=title_override,
                    )
                else:
                    source_material_path = self._type0_midi_source_for_eseq_conversion(
                        source_material_path,
                        scratch_dir,
                        target_filename,
                    )
                    convert_midi_file_to_eseq_path(
                        source_material_path,
                        output_temp_path,
                        title_override=title_override,
                        filename_hint=target_filename,
                        container_variant=self._eseq_converter_container(self.regularEseqVariant),
                    )
                self._apply_regular_row_pending_conversion(
                    row,
                    full_path,
                    target_filename,
                    output_temp_path,
                    target_kind,
                )
                converted_count += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(full_path)}: {exc}")

            progressDialog.setValue(index + 1)
            QApplication.processEvents()

        progressDialog.close()

        if target_kind == "eseq" and converted_count and not self.regularHasPianodir:
            self.pendingGeneratePianodir = True
        if converted_count and not any(
            info.get("title_mode") == "eseq"
            for info in self.listedFileInfo.values()
        ):
            self.regularHasPianodir = False
            self.regularPianodirPopulated = False
            self.regularPianodirSourcePath = ""
            self.loadedRegularPianodirMetadata = PianodirMetadata()
            self.pendingGeneratePianodir = False
        self._refresh_regular_pianodir_row()
        self._reapply_regular_centered_title_assumption()
        self.refresh_compat_indicators()
        self.refresh_midi_type_indicators()
        self._refresh_regular_mode_action_state()

        if converted_count:
            self.status_label.setText(
                f"Staged {converted_count} file(s) for {source_kind.upper()} -> {target_kind.upper()} conversion.\n"
                "Use Save, Save As, or Save As Image to write the converted files."
            )

        if errors:
            self._show_error_list(
                "Conversion Issues",
                f"Some {source_kind.upper()} files could not be staged for {target_kind.upper()} conversion",
                errors,
                warning=True,
                guidance="Nothing has been written yet; remove or replace the listed files and try again",
            )
        return True

    def _convert_all_image_rows(self, source_kind, target_kind):
        if not self.is_image_mode():
            QMessageBox.information(
                self,
                "Image Mode Only",
                "This conversion utility is available while editing a floppy image or floppy session.",
            )
            return
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return

        applicable_rows = []
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            final_path = self._final_image_path(source_path)
            if source_kind == "eseq":
                if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                    applicable_rows.append(row)
            elif self._image_path_is_midi(source_path):
                applicable_rows.append(row)

        if not applicable_rows:
            kind_label = "E-SEQ" if source_kind == "eseq" else "MIDI"
            QMessageBox.information(self, "Nothing To Convert", f"No {kind_label} files are currently listed.")
            return

        if target_kind == "eseq" and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Converting this floppy set to E-SEQ",
        ):
            return

        summary = (
            f"Queue conversion of {len(applicable_rows)} {source_kind.upper()} file(s) "
            f"to {target_kind.upper()} in the current {self.image_session.mode_name.lower()}?\n\n"
            "The converted files will stay pending until you Save."
        )
        if target_kind == "eseq":
            summary += "\n\nE-SEQ titles are limited to 32 characters. Longer titles will be truncated."
        if source_kind == "eseq":
            summary += f"\n\nIf no E-SEQ files remain, {self._eseq_directory_filename(self.imageEseqVariant)} will be removed on save."
        else:
            summary += f"\n\n{self._eseq_directory_filename(self.imageEseqVariant)} will be generated or refreshed when needed on save."
        reply = QMessageBox.question(
            self,
            f"Convert All {source_kind.upper()} to {target_kind.upper()}",
            summary,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        if source_kind == "eseq" and target_kind == "midi":
            self.pendingExportPianodirMetadata = self._current_visible_pianodir_metadata()
        else:
            self.pendingExportPianodirMetadata = PianodirMetadata()

        progressDialog = QProgressDialog(
            f"Converting {source_kind.upper()} files...",
            "Cancel",
            0,
            len(applicable_rows),
            self,
        )
        self._prepare_progress_dialog(progressDialog)

        converted = []
        errors = []
        for index, row in enumerate(applicable_rows):
            if progressDialog.wasCanceled():
                break
            try:
                converted.append(self._queue_image_format_conversion(row, target_kind))
            except Exception as exc:
                filename_item = self.table.item(row, 3)
                label = filename_item.text() if filename_item is not None else "Unknown file"
                errors.append(f"{label}: {exc}")
            progressDialog.setValue(index + 1)
            QApplication.processEvents()

        progressDialog.close()
        if source_kind == "eseq" and target_kind == "midi":
            has_eseq_remaining = False
            for row in range(self.table.rowCount()):
                if self._is_special_pianodir_row(row):
                    continue
                path_item = self.table.item(row, 1)
                if path_item is None:
                    continue
                source_path = path_item.text()
                final_path = self._final_image_path(source_path)
                if self._is_eseq_candidate(final_path, is_midi=self._image_path_is_midi(source_path)):
                    has_eseq_remaining = True
                    break
            if not has_eseq_remaining:
                self.pendingDeletePianodir = self.imageHasPianodir
        self._refresh_pianodir_row()

        status_parts = [f"Queued {len(converted)} file(s) for {source_kind.upper()} -> {target_kind.upper()} conversion."]
        remaining = self._pending_image_space_remaining()
        status_parts.append(f"Estimated free space after pending changes: {display_bytes(max(0, remaining))}.")
        if errors:
            status_parts.append(f"{len(errors)} file(s) could not be converted.")
        self.status_label.setText("\n".join(status_parts))

        if errors:
            self._show_error_list(
                "Conversion Issues",
                f"Some {source_kind.upper()} files could not be staged for {target_kind.upper()} conversion",
                errors,
                warning=True,
                guidance="Nothing has been written yet; remove or replace the listed files and try again",
            )

    def convert_all_eseq_to_midi(self):
        if self._convert_all_regular_rows("eseq", "midi"):
            return
        self._convert_all_image_rows("eseq", "midi")

    def convert_all_midi_to_eseq(self):
        if self._convert_all_regular_rows("midi", "eseq"):
            return
        self._convert_all_image_rows("midi", "eseq")

    def add_table_row(self, full_path, filename, title, midi_type="", title_mode="midi", order_key=b""):
        sorting_enabled = self.table.isSortingEnabled()
        header = self.table.horizontalHeader()
        sort_section = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        if sorting_enabled:
            self.table.setSortingEnabled(False)

        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._set_listed_file_info(
                full_path,
                title_mode=title_mode,
                midi_type=midi_type,
                is_midi=(title_mode == "midi"),
                order_key=order_key,
            )

            # Column 0: Delete cell with "X"
            delete_item = QTableWidgetItem("X")
            delete_item.setTextAlignment(Qt.AlignCenter)
            delete_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            delete_item.setToolTip("Remove this file from the list.")
            self.table.setItem(row, 0, delete_item)

            # Column 1: FullPath (hidden)
            fullpath_item = QTableWidgetItem(full_path)
            fullpath_item.setFlags(fullpath_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, fullpath_item)

            # Column 2: Clipboard emoji
            copy_item = QTableWidgetItem("📋")
            copy_item.setTextAlignment(Qt.AlignCenter)
            copy_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            copy_item.setToolTip("Copy filename to clipboard.")
            self.table.setItem(row, 2, copy_item)

            # Column 3: Filename
            filename_item = QTableWidgetItem(filename)
            filename_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            filename_item.setToolTip("Double-click to copy filename.")
            self.table.setItem(row, 3, filename_item)

            # Column 4: Title (fallback to filename only when no title is present)
            stored_title = title if title != "" else (filename if title_mode == "midi" else "")
            title_item = self._make_title_item(stored_title, title_mode=title_mode, fallback_title=filename)
            self.table.setItem(row, 4, title_item)

            # Column 5: Compatibility indicator for titles > 32 characters
            self._update_compat_indicator(row, stored_title)

            # Column 6: MIDI type from file header bytes
            self._update_midi_type_indicator(row, midi_type)
        finally:
            if sorting_enabled:
                self.table.setSortingEnabled(True)
                if 0 <= sort_section < self.table.columnCount():
                    self.table.sortItems(sort_section, sort_order)

        self._refresh_regular_mode_action_state()

    def handle_cell_clicked(self, row, column):
        if self.is_image_mode():
            self.handle_image_cell_clicked(row, column)
            return
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return

        # Column 0: remove from list
        if column == 0:
            full_path_item = self.table.item(row, 1)
            if full_path_item:
                full_path = full_path_item.text()
                self.pendingEdits.pop(full_path, None)
                self.pendingRegularConversions.pop(full_path, None)
                self.pendingRegularRenames.pop(full_path, None)
                self.listedFileInfo.pop(full_path, None)
            self.table.removeRow(row)
            self._reapply_regular_centered_title_assumption()
            self._refresh_regular_mode_action_state()
            self._refresh_regular_pianodir_row()
            self.status_label.setText("File removed from the list.")
            return

        # Column 2: Clipboard copy (copies filename from col 3)
        elif column == 2:
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename = filename_item.text()
                QApplication.clipboard().setText(filename)
                self.status_label.setText(f"'{filename}' copied to clipboard.")
        # Column 4: Title edit via dialog.
        elif column == 4:
            self.edit_via_dialog(row)

    def handle_cell_double_clicked(self, row, column):
        if self.is_image_mode():
            self.handle_image_cell_double_clicked(row, column)
            return
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return

        # Double-clicking Filename (col 3) copies it.
        if column == 3:
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename = filename_item.text()
                QApplication.clipboard().setText(filename)
                self.status_label.setText(f"'{filename}' copied to clipboard.")
        # For Title (col 4): edit via dialog.
        elif column == 4:
            self.edit_via_dialog(row)
        # For Type (col 6): open File Inspection with this song selected.
        elif column == 6:
            self.show_file_inspection_tool(selected_row=row)

    def handle_image_cell_clicked(self, row, column):
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return
        if column == 0:
            self.remove_image_row(row)
            return
        if column == 2:
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename = filename_item.text()
                QApplication.clipboard().setText(filename)
                self.status_label.setText(f"'{filename}' copied to clipboard.")
            return
        if column == 4:
            self.edit_image_title(row)

    def handle_image_cell_double_clicked(self, row, column):
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return
        if column == 3:
            self.edit_image_filename(row)
            return
        if column == 4:
            self.edit_image_title(row)
            return
        if column == 6:
            self.show_file_inspection_tool(selected_row=row)

    def _normalize_image_filename(self, filename):
        return filename.strip().upper()

    def _center_title_segment(self, text, *, enforce_limit=True):
        trimmed = text.strip()
        if enforce_limit:
            trimmed = trimmed[:16]
        field_width = max(16, len(trimmed)) if not enforce_limit else 16
        padding = field_width - len(trimmed)
        left_padding = padding // 2
        right_padding = padding - left_padding
        return (" " * left_padding) + trimmed + (" " * right_padding)

    def _compose_centered_title(self, first_text, second_text, *, enforce_limit=True):
        return self._center_title_segment(first_text, enforce_limit=enforce_limit) + self._center_title_segment(
            second_text,
            enforce_limit=enforce_limit,
        )

    def _split_title_for_center_fields(self, title, *, enforce_limit=True):
        if self._title_looks_centered(title):
            padded_title = title[:32].ljust(32)
            return padded_title[:16], padded_title[16:32]

        cleaned = title.strip()
        if not cleaned:
            return "", ""

        if enforce_limit:
            cleaned = cleaned[:32]
            if len(cleaned) <= 16:
                return self._center_title_segment(cleaned), ""

            midpoint = len(cleaned) / 2.0
            candidates = []
            for match in re.finditer(r"\s+", cleaned):
                left = cleaned[:match.start()].rstrip()
                right = cleaned[match.end():].lstrip()
                if not left or not right:
                    continue
                if len(left) > 16 or len(right) > 16:
                    continue
                candidates.append((abs(len(left) - midpoint), abs(len(left) - len(right)), left, right))
            if candidates:
                _, _, left, right = min(candidates)
                return self._center_title_segment(left), self._center_title_segment(right)

            return self._center_title_segment(cleaned[:16].strip()), self._center_title_segment(
                cleaned[16:32].strip(),
            )

        if len(cleaned) <= 16:
            return cleaned, ""

        midpoint = len(cleaned) / 2.0
        candidates = []
        for match in re.finditer(r"\s+", cleaned):
            left = cleaned[:match.start()].rstrip()
            right = cleaned[match.end():].lstrip()
            if not left or not right:
                continue
            candidates.append((abs(len(left) - midpoint), abs(len(left) - len(right)), left, right))
        if candidates:
            _, _, left, right = min(candidates)
            return left, right

        split_at = max(1, min(len(cleaned) - 1, len(cleaned) // 2))
        return cleaned[:split_at].strip(), cleaned[split_at:].strip()

    def _title_looks_centered(self, title):
        if not title or not title.strip():
            return False

        candidate = title.rstrip(" ")
        return len(candidate) < self.TITLE_COMPAT_LIMIT and candidate.startswith(" ")

    def _validate_image_filename(self, filename):
        if not filename:
            return "Filename cannot be empty."
        if filename.upper() in {PIANODIR_FILENAME, MUSICDIR_FILENAME}:
            return f"{filename.upper()} is managed automatically."
        if filename in {".", ".."}:
            return "Filename cannot be '.' or '..'."
        if filename.endswith("."):
            return "Filename cannot end with '.'."
        if any(ch in self.IMAGE_FILENAME_INVALID_CHARS for ch in filename):
            return "Filename contains characters that are not valid in DOS/FAT names."
        if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in filename):
            return "Use printable ASCII characters only."

        stem, ext = os.path.splitext(filename)
        if not stem or stem.startswith("."):
            return "Filename must have a name before the extension."
        if "." in stem:
            return "Filename can only contain one extension separator."
        if len(stem) > 8:
            return "DOS/FAT filename base must be 8 characters or fewer."
        if ext:
            if len(ext) > 4:
                return "DOS/FAT extension must be 3 characters or fewer."
            if "." in ext[1:]:
                return "Filename can only contain one extension separator."
        if len(ext.lstrip(".")) > 3:
            return "DOS/FAT extension must be 3 characters or fewer."
        return None

    def _join_image_path(self, directory, filename):
        if directory:
            return f"{directory.rstrip('/')}/{filename}"
        return filename

    def _active_image_paths(self, exclude_row=None):
        paths = set()
        for row in range(self.table.rowCount()):
            if exclude_row is not None and row == exclude_row:
                continue
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if not path_item:
                continue
            source_path = path_item.text()
            active_path = self.pendingImageRenames.get(source_path, source_path)
            paths.add(active_path.upper())
        return paths

    def _find_image_row_for_active_path(self, active_path):
        target_path = str(active_path or "").replace("\\", "/").strip().strip("/").upper()
        if not target_path:
            return -1
        for row in range(self.table.rowCount()):
            if self._is_special_pianodir_row(row):
                continue
            path_item = self.table.item(row, 1)
            if path_item is None:
                continue
            source_path = path_item.text()
            if self._final_image_path(source_path).upper() == target_path:
                return row
        return -1

    def _image_existing_modified_timestamp(self, source_path):
        pending_path = self.pendingImageAdditions.get(source_path) or self.pendingImageReplacements.get(source_path)
        if pending_path:
            return self._file_modified_timestamp(pending_path)
        entry = self._image_entry_for_path(source_path)
        return getattr(entry, "modified_time", None) if entry is not None else None

    def _image_existing_conflict_label(self, source_path):
        return os.path.basename(self._final_image_path(source_path)) or source_path

    def _image_entry_for_path(self, image_path):
        return self.imageEntriesByPath.get(image_path)

    def _prompt_for_image_filename(self, current_filename):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Rename Image File")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(18, 18, 18, 18)
        dialog_layout.setSpacing(8)

        editor = QLineEdit(current_filename)
        editor.setMinimumWidth(480)

        warning_label = QLabel("")
        warning_label.setStyleSheet("color: #C62828;")
        warning_label.setVisible(False)

        form_grid = self._make_dialog_form_grid()
        prompt = self._add_dialog_form_row(form_grid, 0, "DOS filename:", editor)
        warning_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(warning_spacer, 1, 0)
        form_grid.addWidget(warning_label, 1, 1)
        self._align_dialog_form_labels([prompt, warning_spacer])
        dialog_layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        dialog_layout.addWidget(buttons)

        def update_state(text):
            normalized = self._normalize_image_filename(text)
            validation_error = self._validate_image_filename(normalized)
            unchanged = normalized == current_filename.upper()
            ok_button.setEnabled((validation_error is None and bool(normalized)) or unchanged)
            warning_label.setVisible(bool(validation_error and not unchanged))
            warning_label.setText(validation_error or "")

        editor.textChanged.connect(update_state)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        update_state(current_filename)
        editor.selectAll()
        editor.setFocus()

        if self._exec_child_dialog(dialog) == QDialog.Accepted:
            return self._normalize_image_filename(editor.text()), True
        return "", False

    def _handle_pianodir_row_clicked(self):
        if self.is_image_mode():
            eseq_mode = self.imageEseqMode
            has_pianodir = self.imageHasPianodir
            pianodir_populated = self.imagePianodirPopulated
            refresh_callback = self._refresh_pianodir_row
            directory_name = self._eseq_directory_filename(self.imageEseqVariant)
        elif self.is_local_eseq_mode():
            eseq_mode = self.regularEseqMode
            has_pianodir = self.regularHasPianodir
            pianodir_populated = self.regularPianodirPopulated
            refresh_callback = self._refresh_regular_pianodir_row
            directory_name = self._eseq_directory_filename(self.regularEseqVariant)
        else:
            return

        if not eseq_mode:
            return
        if has_pianodir and pianodir_populated:
            if self._should_generate_pianodir():
                message = f"{directory_name} is present and will be refreshed on save."
            else:
                message = f"{directory_name} is present and will be left unchanged unless related E-SEQ data changes."
            QMessageBox.information(
                self,
                directory_name,
                message,
            )
            return
        if self.pendingGeneratePianodir:
            QMessageBox.information(
                self,
                directory_name,
                f"{directory_name} is missing and will be generated on save.",
            )
            return

        reply = QMessageBox.question(
            self,
            f"Generate {directory_name}",
            f"Generate {directory_name} for these Yamaha E-SEQ files on save?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        self.pendingGeneratePianodir = True
        refresh_callback()
        self.status_label.setText(f"{directory_name} will be generated on save.")

    def _ensure_pianodir_generation_for_save(self):
        if self.is_image_mode():
            eseq_mode = self.imageEseqMode
            has_pianodir = self.imageHasPianodir
            refresh_callback = self._refresh_pianodir_row
            directory_name = self._eseq_directory_filename(self.imageEseqVariant)
        elif self.is_local_eseq_mode():
            eseq_mode = self.regularEseqMode
            has_pianodir = self.regularHasPianodir
            refresh_callback = self._refresh_regular_pianodir_row
            directory_name = self._eseq_directory_filename(self.regularEseqVariant)
        else:
            return True

        if not eseq_mode or has_pianodir or self.pendingGeneratePianodir:
            return True

        reply = QMessageBox.question(
            self,
            f"Generate {directory_name}",
            f"These files look like Yamaha E-SEQ files, but {directory_name} is missing.\n\n"
            f"Generate {directory_name} while saving?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self.pendingGeneratePianodir = True
            refresh_callback()
        return True

    def _should_generate_pianodir(self, *, for_export=False):
        if self.is_local_eseq_mode():
            if not self.regularEseqMode:
                return False
            if self.pendingGeneratePianodir:
                return True
            return self._regular_pianodir_needs_refresh(for_export=for_export)
        return self.imageEseqMode and (
            self._image_pianodir_needs_refresh()
            or self.pendingGeneratePianodir
        )

    def _confirm_floppy_write(self):
        if self.image_session is None or not self.image_session.source_kind.startswith("floppy"):
            return True
        if self.image_session.source_kind == "floppy_usb":
            title = "Save To Floppy"
            message = (
                f"Save pending changes directly back to {self.image_session.source_name}?\n\n"
                "This will update files on the floppy without rewriting the whole disk image. "
                "Files removed from the list will be removed from the floppy."
            )
        else:
            title = "Write Greaseweazle Floppy"
            message = (
                f"Save pending changes directly back to {self.image_session.source_name}?\n\n"
                "This will overwrite the floppy disk in the drive."
            )
        return self._confirm_with_optional_skip(
            setting_key=self.SETTING_SKIP_FLOPPY_WRITE_WARNING,
            title=title,
            message=message,
        )

    def edit_image_title(self, row):
        if self._is_special_pianodir_row(row):
            return
        path_item = self.table.item(row, 1)
        filename_item = self.table.item(row, 3)
        if path_item is None:
            return

        image_path = path_item.text()
        filename = filename_item.text() if filename_item else os.path.basename(image_path)
        title_mode = self._image_path_title_mode(image_path)
        if not title_mode:
            QMessageBox.information(self, "No Editable Title", "Only MIDI and E-SEQ files have editable title metadata.")
            return

        current_title = self._row_raw_title(row)
        new_title, ok = self._prompt_for_title(current_title, title_mode=title_mode)
        if not ok or not new_title.strip():
            return

        if new_title == current_title:
            return

        validation_error = validate_legacy_title_input(new_title)
        if validation_error:
            QMessageBox.warning(self, "Invalid Title", validation_error)
            return
        if title_mode == "eseq" and len(new_title.encode("latin1")) > 32:
            QMessageBox.warning(self, "Title Too Long", "E-SEQ titles must be 32 characters or fewer.")
            return

        self.pendingImageTitleEdits[image_path] = new_title
        new_title_item = self._make_title_item(new_title, title_mode=title_mode, fallback_title=filename)
        self.table.setItem(row, 4, new_title_item)
        self._update_compat_indicator(row, new_title)
        self._reapply_image_centered_title_assumption()

        warning = ""
        if self._compat_warning_is_active() and self._is_title_too_long(new_title):
            warning = f"\nCompatibility warning: over {self.TITLE_COMPAT_LIMIT} characters."
        title_kind = "E-SEQ title" if title_mode == "eseq" else "MIDI title"
        shown_title = self._display_title_text(new_title, title_mode=title_mode, fallback_title=filename)
        self.status_label.setText(
            f"Pending image change:\n{title_kind} for '{filename}' will be updated to '{shown_title}' on save.{warning}"
        )

        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)

    def edit_image_filename(self, row):
        if self._is_special_pianodir_row(row):
            return
        path_item = self.table.item(row, 1)
        current_item = self.table.item(row, 3)
        if path_item is None or current_item is None:
            return

        source_path = path_item.text()
        current_name = current_item.text()
        new_name, ok = self._prompt_for_image_filename(current_name)
        if not ok:
            return

        validation_error = self._validate_image_filename(new_name)
        if validation_error:
            QMessageBox.warning(self, "Invalid Filename", validation_error)
            return

        directory = os.path.dirname(self.pendingImageRenames.get(source_path, source_path)).replace("\\", "/")
        target_path = self._join_image_path(directory, new_name)
        current_target = self.pendingImageRenames.get(source_path, source_path)
        if target_path.upper() == current_target.upper():
            return

        if target_path.upper() in self._active_image_paths(exclude_row=row):
            QMessageBox.warning(self, "Name Already Exists", f"'{new_name}' already exists in this image folder.")
            return

        if source_path in self.pendingImageAdditions:
            host_path = self.pendingImageAdditions.pop(source_path)
            self.pendingImageAdditions[target_path] = host_path
            if source_path in self.pendingImageTitleEdits:
                self.pendingImageTitleEdits[target_path] = self.pendingImageTitleEdits.pop(source_path)
            if source_path in self.imageFileInfo:
                self.imageFileInfo[target_path] = self.imageFileInfo.pop(source_path)
            path_item.setText(target_path)
            filename_item = self.table.item(row, 3)
            if filename_item:
                filename_item.setText(new_name)
            self.status_label.setText(f"Pending addition renamed to '{new_name}'.")
        else:
            if target_path.upper() == source_path.upper():
                self.pendingImageRenames.pop(source_path, None)
            else:
                self.pendingImageRenames[source_path] = target_path
            self.status_label.setText(
                f"Pending image rename:\n'{os.path.basename(source_path)}' will become '{new_name}' on save."
            )

        new_name_item = QTableWidgetItem(new_name)
        new_name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        new_name_item.setToolTip("Double-click to rename this file inside the image.")
        self.table.setItem(row, 3, new_name_item)
        kind_item = self.table.item(row, 6)
        if kind_item:
            info = self._image_info_for_path(path_item.text())
            if info.get("is_midi"):
                kind_item.setText(info.get("midi_type") or "MIDI")
            else:
                kind_item.setText(self._kind_for_image_file(new_name))

        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)
        self._refresh_pianodir_row()

    def remove_image_row(self, row):
        if self._is_special_pianodir_row(row):
            QMessageBox.information(
                self,
                "Managed File",
                f"{self._eseq_directory_filename(self.imageEseqVariant)} is managed automatically.",
            )
            return
        path_item = self.table.item(row, 1)
        if path_item is None:
            return
        image_path = path_item.text()
        filename = os.path.basename(image_path)

        if image_path in self.pendingImageAdditions:
            self.pendingImageAdditions.pop(image_path, None)
            self.pendingImageTitleEdits.pop(image_path, None)
            self.imageFileInfo.pop(image_path, None)
            self.table.removeRow(row)
            self.status_label.setText(f"Pending addition '{filename}' canceled.")
            self._refresh_pianodir_row()
            self._reapply_image_centered_title_assumption()
            return

        container_label = "floppy disk" if self.is_floppy_mode() else "image"
        confirmed = self._confirm_with_optional_skip(
            setting_key=self.SETTING_SKIP_IMAGE_REMOVE_WARNING,
            title=f"Remove File From {container_label.title()}",
            message=(
                f"Remove '{filename}' from the listed files?\n\n"
                f"If you click Save, this will actually delete the file from the {container_label}.\n"
                f"If you click Save As, the file will simply be omitted from the exported folder and the {container_label} will not be changed."
            ),
        )
        if not confirmed:
            return

        self.pendingImageDeletes.add(image_path)
        self.pendingImageRenames.pop(image_path, None)
        self.pendingImageTitleEdits.pop(image_path, None)
        self.pendingImageReplacements.pop(image_path, None)
        self.table.removeRow(row)
        self.status_label.setText(
            f"Pending removal: '{filename}' will be deleted from the {container_label} on Save, "
            "or omitted from exported files on Save As."
        )
        self._refresh_pianodir_row()
        self._reapply_image_centered_title_assumption()

    def _build_default_image_filename(self, host_path, used_paths):
        return self._build_dos_image_filename(os.path.basename(host_path), used_paths)

    def _image_drop_conversion_kind(self, host_path):
        if self.imageEseqMode and is_midi_file(host_path):
            return "eseq"
        if (
            not self.imageEseqMode
            and is_eseq_file(host_path)
            and has_eseq_title_metadata(host_path)
        ):
            return "midi"
        return ""

    def _build_image_addition_filename(self, host_path, used_paths, conversion_kind=""):
        if conversion_kind == "eseq":
            stem = os.path.splitext(os.path.basename(host_path))[0] or "FILE"
            return self._build_dos_image_filename(f"{stem}.{self._eseq_song_extension(self.imageEseqVariant)}", used_paths)
        if conversion_kind == "midi":
            stem = os.path.splitext(os.path.basename(host_path))[0] or "FILE"
            return self._build_dos_image_filename(f"{stem}.MID", used_paths)
        return self._build_default_image_filename(host_path, used_paths)

    def _stage_image_addition_host_file(self, host_path, target_name="", conversion_kind=""):
        if self.image_session is None:
            raise FloppyImageError("No image or floppy is currently loaded.")
        if not os.path.isfile(host_path):
            raise FloppyImageError(f"File to add no longer exists: {host_path}")

        if conversion_kind:
            output_name = target_name or os.path.basename(host_path)
            staged_path = os.path.join(
                self.image_session.patched_dir,
                f"{uuid.uuid4().hex}_{output_name}",
            )
            if conversion_kind == "eseq":
                source_path = self._type0_midi_source_for_eseq_conversion(
                    host_path,
                    self.image_session.patched_dir,
                    output_name,
                )
                convert_midi_file_to_eseq_path(
                    source_path,
                    staged_path,
                    filename_hint=os.path.basename(output_name),
                    container_variant=self._eseq_converter_container(self.imageEseqVariant),
                )
            elif conversion_kind == "midi":
                convert_eseq_file_to_midi_path(host_path, staged_path)
            else:
                raise FloppyImageError(f"Unsupported automatic conversion kind: {conversion_kind}")
            return staged_path

        staged_path = os.path.join(
            self.image_session.patched_dir,
            f"{uuid.uuid4().hex}_{os.path.basename(host_path)}",
        )
        shutil.copy2(host_path, staged_path)
        return staged_path

    def _replace_image_row_from_drop(self, row, host_path, conversion_kind=""):
        path_item = self.table.item(row, 1)
        if path_item is None:
            raise FloppyImageError("Could not find the image row to replace.")

        source_path = path_item.text()
        target_path = self._final_image_path(source_path)
        target_name = os.path.basename(target_path)
        staged_host_path = self._stage_image_addition_host_file(
            host_path,
            target_name=target_name,
            conversion_kind=conversion_kind,
        )

        old_addition_marker = object()
        old_replacement_marker = object()
        old_addition = self.pendingImageAdditions.get(source_path, old_addition_marker)
        old_replacement = self.pendingImageReplacements.get(source_path, old_replacement_marker)
        old_deleted = source_path in self.pendingImageDeletes
        old_title_edit = self.pendingImageTitleEdits.get(source_path)
        had_title_edit = source_path in self.pendingImageTitleEdits
        old_info_marker = object()
        old_info = self.imageFileInfo.get(source_path, old_info_marker)

        try:
            if source_path in self.pendingImageAdditions:
                self.pendingImageAdditions[source_path] = staged_host_path
            else:
                self.pendingImageReplacements[source_path] = staged_host_path
                self.pendingImageDeletes.discard(source_path)
            self.pendingImageTitleEdits.pop(source_path, None)

            if self._pending_image_space_remaining() < 0:
                raise FloppyImageError("not enough free space in image")

            size = os.path.getsize(staged_host_path)
            is_midi, title, midi_type, title_mode, order_key = self._probe_image_file(
                source_path,
                size,
                staged_host_path,
            )
            if title_mode == "eseq" and os.path.splitext(target_path)[1].lower() == ".mda":
                self.imageEseqVariant = ESEQ_VARIANT_CLAVINOVA

            raw_title = title if title != "" else (target_name if title_mode == "midi" else "")
            title_item = self._make_title_item(raw_title, title_mode=title_mode, fallback_title=target_name)
            self.table.setItem(row, 4, title_item)
            self._update_compat_indicator(row, raw_title)

            kind_item = self.table.item(row, 6)
            if kind_item is None:
                kind_item = QTableWidgetItem()
                kind_item.setTextAlignment(Qt.AlignCenter)
                kind_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.table.setItem(row, 6, kind_item)
            kind_item.setText(midi_type or self._kind_for_image_file(target_name))
            if title_mode == "eseq" or kind_item.text().startswith(("FIL", "ESQ", "MDA")):
                kind_item.setToolTip("Yamaha E-SEQ type, arrangement, and write-protect information.")
            elif is_midi:
                kind_item.setToolTip("Detected MIDI file type from header bytes.")
            else:
                kind_item.setToolTip("File type from the image filename.")

            for old_path in (old_addition, old_replacement):
                if old_path not in (old_addition_marker, old_replacement_marker, staged_host_path) and old_path:
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
            return target_path
        except Exception:
            if old_addition is old_addition_marker:
                self.pendingImageAdditions.pop(source_path, None)
            else:
                self.pendingImageAdditions[source_path] = old_addition
            if old_replacement is old_replacement_marker:
                self.pendingImageReplacements.pop(source_path, None)
            else:
                self.pendingImageReplacements[source_path] = old_replacement
            if old_deleted:
                self.pendingImageDeletes.add(source_path)
            else:
                self.pendingImageDeletes.discard(source_path)
            if had_title_edit:
                self.pendingImageTitleEdits[source_path] = old_title_edit
            if old_info is old_info_marker:
                self.imageFileInfo.pop(source_path, None)
            else:
                self.imageFileInfo[source_path] = old_info
            try:
                os.remove(staged_host_path)
            except OSError:
                pass
            raise

    def _stage_dropped_image_pianodir(self, host_path, pending_extra):
        directory_name = self._eseq_directory_filename(self.imageEseqVariant)
        target_path = directory_name
        staged_host_path = self._stage_image_addition_host_file(
            host_path,
            target_name=directory_name,
        )
        existing_directory_paths = [
            path for path in self.imageEntriesByPath if is_eseq_directory_path(path)
        ]
        target_existing_path = next(
            (path for path in existing_directory_paths if path.upper() == target_path.upper()),
            "",
        )
        try:
            size = os.path.getsize(staged_host_path)
        except OSError:
            size = 0
        try:
            metadata = (
                PianodirMetadata()
                if self.imageEseqVariant == ESEQ_VARIANT_CLAVINOVA
                else read_pianodir_metadata_from_file(staged_host_path)
            )
        except Exception:
            metadata = PianodirMetadata()

        for pending_path, pending_host in list(self.pendingImageAdditions.items()):
            if not is_eseq_directory_path(pending_path) or pending_path.upper() == target_path.upper():
                continue
            self.pendingImageAdditions.pop(pending_path, None)
            pending_extra.pop(pending_path, None)
            if pending_host and pending_host != staged_host_path:
                try:
                    os.remove(pending_host)
                except OSError:
                    pass

        for stale_path in existing_directory_paths:
            if stale_path.upper() == target_path.upper():
                continue
            previous_replacement = self.pendingImageReplacements.pop(stale_path, None)
            if previous_replacement and previous_replacement != staged_host_path:
                try:
                    os.remove(previous_replacement)
                except OSError:
                    pass
            self.pendingImageDeletes.add(stale_path)

        if target_existing_path:
            self.pendingImageDeletes.discard(target_existing_path)
            previous_replacement = self.pendingImageReplacements.get(target_existing_path)
            if previous_replacement and previous_replacement != staged_host_path:
                try:
                    os.remove(previous_replacement)
                except OSError:
                    pass
            self.pendingImageReplacements[target_existing_path] = staged_host_path
        else:
            previous_addition = self.pendingImageAdditions.get(target_path)
            pending_extra[target_path] = staged_host_path
            if self._pending_image_space_remaining(pending_extra) < 0:
                pending_extra.pop(target_path, None)
                try:
                    os.remove(staged_host_path)
                except OSError:
                    pass
                raise FloppyImageError("not enough free space in image")
            pending_extra.pop(target_path, None)
            if previous_addition and previous_addition != staged_host_path:
                try:
                    os.remove(previous_addition)
                except OSError:
                    pass
            self.pendingImageAdditions[target_path] = staged_host_path

        self.imageHasPianodir = True
        self.imagePianodirPopulated = (
            musicdir_is_populated(size)
            if self.imageEseqVariant == ESEQ_VARIANT_CLAVINOVA
            else pianodir_is_populated(size)
        )
        self.pendingDeletePianodir = False
        self.pendingGeneratePianodir = False
        self.loadedImagePianodirMetadata = metadata
        self._set_loaded_image_pianodir_metadata(metadata)
        return directory_name

    def _build_dos_image_filename(self, filename, used_paths):
        stem, ext = os.path.splitext(filename)
        ext = ext.lstrip(".")
        if ext.lower() == "midi":
            ext = "MID"

        clean_stem = "".join(
            ch.upper() if ch.isalnum() else "_"
            for ch in stem
            if ord(ch) < 128
        ).strip("_")
        clean_ext = "".join(
            ch.upper() if ch.isalnum() else "_"
            for ch in ext
            if ord(ch) < 128
        ).strip("_")
        if not clean_stem:
            clean_stem = "FILE"
        clean_ext = clean_ext[:3]

        for counter in range(0, 1000):
            suffix = "" if counter == 0 else str(counter)
            base_len = max(1, 8 - len(suffix))
            candidate_stem = (clean_stem[:base_len] + suffix)[:8]
            candidate = candidate_stem
            if clean_ext:
                candidate += f".{clean_ext}"
            validation_error = self._validate_image_filename(candidate)
            if validation_error:
                continue
            if candidate.upper() not in used_paths:
                return candidate

        raise ValueError(f"Could not create a unique DOS filename for {filename}.")

    def _pending_image_space_remaining(self, extra_additions=None):
        if self.image_session is None:
            return 0
        listing = self.image_session.list_entries()
        entries_by_path = {entry.path: entry for entry in listing.entries}
        cluster_size = listing.cluster_size
        free_space = listing.free_space

        freed = 0
        for image_path in self.pendingImageDeletes:
            entry = entries_by_path.get(image_path) or self._image_entry_for_path(image_path)
            if entry:
                freed += entry.packed_size or allocated_size(entry.size, cluster_size)
        if self.pendingDeletePianodir:
            for entry in listing.entries:
                if is_eseq_directory_path(entry.path):
                    freed += entry.packed_size or allocated_size(entry.size, cluster_size)

        additions = dict(self.pendingImageAdditions)
        if extra_additions:
            additions.update(extra_additions)

        used = 0
        for host_path in additions.values():
            if os.path.isfile(host_path):
                used += allocated_size(os.path.getsize(host_path), cluster_size)

        replacement_delta = 0
        for image_path, host_path in self.pendingImageReplacements.items():
            if image_path in self.pendingImageDeletes or not os.path.isfile(host_path):
                continue
            entry = entries_by_path.get(image_path) or self._image_entry_for_path(image_path)
            if entry is None:
                continue
            old_size = entry.packed_size or allocated_size(entry.size, cluster_size)
            new_size = allocated_size(os.path.getsize(host_path), cluster_size)
            replacement_delta += new_size - old_size

        if self.imageEseqMode and not self.imageHasPianodir and self.pendingGeneratePianodir:
            used += allocated_size(self._generated_eseq_directory_size(), cluster_size)

        return free_space + freed - used - replacement_delta

    def _pending_image_used_bytes(self):
        if self.image_session is None:
            return 0

        listing = self.image_session.list_entries()
        cluster_size = listing.cluster_size
        used = 0

        for entry in listing.entries:
            if entry.path in self.pendingImageDeletes:
                continue
            if self.pendingDeletePianodir and is_eseq_directory_path(entry.path):
                continue
            if entry.path in self.pendingImageReplacements:
                host_path = self.pendingImageReplacements[entry.path]
                if os.path.isfile(host_path):
                    used += allocated_size(os.path.getsize(host_path), cluster_size)
                    continue
            used += entry.packed_size or allocated_size(entry.size, cluster_size)

        for host_path in self.pendingImageAdditions.values():
            if os.path.isfile(host_path):
                used += allocated_size(os.path.getsize(host_path), cluster_size)

        if self.imageEseqMode and not self.imageHasPianodir and self.pendingGeneratePianodir:
            used += allocated_size(self._generated_eseq_directory_size(), cluster_size)

        return max(0, used)

    def _refresh_disk_usage_bars(self):
        if not hasattr(self, "diskUsageBarsWidget"):
            return
        show_bars = self.is_image_mode()
        self.diskUsageBarsWidget.setVisible(show_bars)
        if not show_bars or self.image_session is None:
            self.diskUsageBar.set_fraction(0.0)
            self.eseqCountBar.set_count(0)
            self.eseqCountBar.setVisible(False)
            return

        total_size = max(1, int(self.image_session.disk_format.size_bytes or 1))
        self.diskUsageBar.set_fraction(self._pending_image_used_bytes() / total_size)
        self.eseqCountBar.setVisible(bool(self.imageEseqMode))
        self.eseqCountBar.set_segment_limit(self._active_eseq_file_limit())
        self.eseqCountBar.set_count(self._image_song_file_count() if self.imageEseqMode else 0)

    def queue_image_additions(self, file_paths):
        if not self.is_image_mode():
            return

        valid_files = [path for path in file_paths if os.path.isfile(path)]
        if not valid_files:
            self.status_label.setText("No files were added to the image.")
            return

        added = []
        skipped = []
        shortened = []
        converted_count = 0
        replaced = []
        drop_cancelled = False
        conflict_choice_for_all = ""
        pianodir_loaded = False
        used_paths = self._active_image_paths()
        pending_extra = {}
        for host_path in valid_files:
            original_name = os.path.basename(host_path)
            if original_name.upper() in {PIANODIR_FILENAME, MUSICDIR_FILENAME}:
                if original_name.upper() == MUSICDIR_FILENAME:
                    self.imageEseqVariant = ESEQ_VARIANT_CLAVINOVA
                directory_name = self._eseq_directory_filename(self.imageEseqVariant)
                existing_directory_path = next(
                    (path for path in self.imageEntriesByPath if is_eseq_directory_path(path)),
                    "",
                )
                if not existing_directory_path and directory_name in self.pendingImageAdditions:
                    existing_directory_path = directory_name
                if existing_directory_path:
                    if conflict_choice_for_all:
                        choice = conflict_choice_for_all
                    else:
                        choice, do_all = self._prompt_drop_filename_conflict(
                            filename=directory_name,
                            existing_label=self._image_existing_conflict_label(existing_directory_path),
                            existing_modified=self._image_existing_modified_timestamp(existing_directory_path),
                            incoming_path=host_path,
                            incoming_modified=self._file_modified_timestamp(host_path),
                            allow_do_all=len(valid_files) > 1,
                        )
                        if do_all:
                            conflict_choice_for_all = choice
                    if choice == "cancel":
                        drop_cancelled = True
                        break
                    if choice != "replace":
                        skipped.append(f"{original_name}: kept listed file")
                        continue
                try:
                    target_path = self._stage_dropped_image_pianodir(host_path, pending_extra)
                except Exception as exc:
                    skipped.append(f"{original_name}: {exc}")
                    continue
                pianodir_loaded = True
                if existing_directory_path:
                    replaced.append(target_path)
                else:
                    added.append(target_path)
                continue

            conversion_kind = self._image_drop_conversion_kind(host_path)
            try:
                conflict_candidate = self._build_image_addition_filename(host_path, set(), conversion_kind)
            except ValueError as exc:
                skipped.append(f"{original_name}: {exc}")
                continue

            conflict_row = self._find_image_row_for_active_path(conflict_candidate)
            if conflict_row >= 0:
                path_item = self.table.item(conflict_row, 1)
                existing_path = path_item.text() if path_item is not None else conflict_candidate
                if conflict_choice_for_all:
                    choice = conflict_choice_for_all
                else:
                    choice, do_all = self._prompt_drop_filename_conflict(
                        filename=conflict_candidate,
                        existing_label=self._image_existing_conflict_label(existing_path),
                        existing_modified=self._image_existing_modified_timestamp(existing_path),
                        incoming_path=host_path,
                        incoming_modified=self._file_modified_timestamp(host_path),
                        allow_do_all=len(valid_files) > 1,
                    )
                    if do_all:
                        conflict_choice_for_all = choice
                if choice == "cancel":
                    drop_cancelled = True
                    break
                if choice != "replace":
                    skipped.append(f"{original_name}: kept listed file")
                    continue
                try:
                    target_path = self._replace_image_row_from_drop(conflict_row, host_path, conversion_kind)
                except Exception as exc:
                    skipped.append(f"{original_name}: {exc}")
                    continue
                used_paths.add(target_path.upper())
                replaced.append(target_path)
                if conversion_kind:
                    converted_count += 1
                continue

            try:
                target_name = self._build_image_addition_filename(host_path, used_paths, conversion_kind)
            except ValueError as exc:
                skipped.append(f"{original_name}: {exc}")
                continue

            target_path = target_name
            try:
                staged_host_path = self._stage_image_addition_host_file(
                    host_path,
                    target_name=target_name,
                    conversion_kind=conversion_kind,
                )
            except Exception as exc:
                skipped.append(f"{original_name}: {exc}")
                continue

            pending_extra[target_path] = staged_host_path
            if self._pending_image_space_remaining(pending_extra) < 0:
                pending_extra.pop(target_path, None)
                try:
                    os.remove(staged_host_path)
                except OSError:
                    pass
                skipped.append(f"{original_name}: not enough free space in image")
                continue

            size = os.path.getsize(staged_host_path)
            is_midi, title, midi_type, title_mode, order_key = self._probe_image_file(
                target_path,
                size,
                staged_host_path,
            )
            if title_mode == "eseq" and os.path.splitext(target_path)[1].lower() == ".mda":
                self.imageEseqVariant = ESEQ_VARIANT_CLAVINOVA
            would_be_eseq_mode = self.imageEseqMode or title_mode == "eseq" or self._is_eseq_candidate(
                target_path,
                is_midi=is_midi,
            )
            eseq_limit = self._active_eseq_file_limit()
            if would_be_eseq_mode and (self._image_song_file_count() + 1) > eseq_limit:
                pending_extra.pop(target_path, None)
                self.imageFileInfo.pop(target_path, None)
                try:
                    os.remove(staged_host_path)
                except OSError:
                    pass
                skipped.append(
                    f"{original_name}: Yamaha E-SEQ supports at most {eseq_limit} files"
                )
                continue
            used_paths.add(target_path.upper())
            self.pendingImageAdditions[target_path] = staged_host_path
            if not conversion_kind and is_eseq_file(staged_host_path) and target_name.upper() != original_name.upper():
                shortened.append(f"{original_name} -> {target_name}")
            if conversion_kind:
                converted_count += 1
            if not title_mode:
                title = ""
            self.add_image_table_row(
                target_path,
                target_name,
                size,
                title=title,
                midi_type=midi_type,
                order_key=order_key,
                is_pending_addition=True,
            )
            added.append(target_path)

        self._refresh_pianodir_row()
        self._reapply_image_centered_title_assumption()
        self._resize_table_columns_to_fill()
        self._refresh_image_mode_action_state()

        status_parts = []
        if added:
            status_parts.append(f"Queued {len(added)} file(s) to add to the image.")
        if replaced:
            status_parts.append(f"Queued {len(replaced)} file(s) to replace matching filenames.")
        if pianodir_loaded:
            status_parts.append(f"Loaded {self._eseq_directory_filename(self.imageEseqVariant)}.")
        if converted_count:
            status_parts.append(f"Staged {converted_count} dropped file(s) for automatic conversion.")
        if shortened:
            status_parts.append(f"Shortened {len(shortened)} E-SEQ filename(s) to DOS 8.3.")
        if skipped:
            status_parts.append(f"Skipped {len(skipped)} file(s).")
        if drop_cancelled:
            status_parts.append("Drop cancelled.")
        remaining = self._pending_image_space_remaining()
        status_parts.append(f"Estimated free space after pending additions: {display_bytes(max(0, remaining))}.")
        self.status_label.setText("\n".join(status_parts))

        if skipped:
            self._show_error_list(
                "Some Files Were Not Added",
                "Some files could not be added to the image",
                skipped,
                warning=True,
                guidance="The other additions remain staged; remove or replace the listed files before saving",
            )
        elif shortened:
            QMessageBox.information(
                self,
                "Filename Shortened",
                "Some E-SEQ filenames were shortened to DOS 8.3 names for floppy compatibility.\n\n"
                + self._limited_message_list(shortened),
            )

    def _prompt_for_title(self, current_title, title_mode="midi"):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Edit Song Title")
        dialog.setModal(True)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(12, 10, 12, 10)
        dialog_layout.setSpacing(8)
        dialog_layout.setSizeConstraint(QLayout.SetFixedSize)
        enforce_eseq_limit = title_mode == "eseq"
        use_screen_format = bool(
            getattr(self, "format_disklavier_checkbox", None)
            and self.format_disklavier_checkbox.isChecked()
        )

        prompt = self._make_dialog_form_label("Song title:")

        title_field_font = QFont("Courier New")
        title_field_font.setStyleHint(QFont.Monospace)
        title_font_metrics = QFontMetrics(title_field_font)
        title_field_width = title_font_metrics.horizontalAdvance("M" * 32) + 28
        centered_field_width = title_font_metrics.horizontalAdvance("M" * 16) + 28
        active_field_width = centered_field_width if use_screen_format else title_field_width

        editor = QLineEdit(current_title)
        editor.setFont(title_field_font)
        editor.setLayoutDirection(Qt.LeftToRight)
        editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        editor.setFixedWidth(title_field_width)
        if enforce_eseq_limit:
            editor.setMaxLength(self.TITLE_COMPAT_LIMIT)

        editor_page = QWidget(dialog)
        editor_page_layout = QVBoxLayout(editor_page)
        editor_page_layout.setContentsMargins(0, 0, 0, 0)
        editor_page_layout.setSpacing(0)
        editor_page_layout.addWidget(editor, alignment=Qt.AlignLeft)
        editor_page_layout.addStretch(1)

        centered_fields_widget = QWidget(dialog)
        centered_fields_layout = QVBoxLayout(centered_fields_widget)
        centered_fields_layout.setContentsMargins(0, 0, 0, 0)
        centered_fields_layout.setSpacing(6)

        first_field = QLineEdit()
        first_field.setPlaceholderText("Field 1")
        first_field.setFont(title_field_font)
        first_field.setLayoutDirection(Qt.LeftToRight)
        first_field.setMaxLength(16)
        first_field.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        first_field.setFixedWidth(centered_field_width)
        centered_fields_layout.addWidget(first_field, alignment=Qt.AlignLeft)

        second_field = QLineEdit()
        second_field.setPlaceholderText("Field 2")
        second_field.setFont(title_field_font)
        second_field.setLayoutDirection(Qt.LeftToRight)
        second_field.setMaxLength(16)
        second_field.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        second_field.setFixedWidth(centered_field_width)
        centered_fields_layout.addWidget(second_field, alignment=Qt.AlignLeft)

        field_stack = QStackedWidget(dialog)
        field_stack.addWidget(editor_page)
        field_stack.addWidget(centered_fields_widget)
        field_stack.setFixedWidth(active_field_width)
        field_stack_height = editor.sizeHint().height()
        if use_screen_format:
            field_stack_height = (
                first_field.sizeHint().height()
                + second_field.sizeHint().height()
                + centered_fields_layout.spacing()
            )
        field_stack.setFixedHeight(field_stack_height)
        warning_label = QLabel("")
        warning_label.setWordWrap(True)
        warning_label.setFixedWidth(active_field_width)
        warning_label.setStyleSheet("color: #C62828;")
        warning_label.setVisible(False)

        form_grid = self._make_dialog_form_grid()
        form_grid.addWidget(prompt, 0, 0)
        form_grid.addWidget(field_stack, 0, 1)
        warning_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(warning_spacer, 1, 0)
        form_grid.addWidget(warning_label, 1, 1)
        self._align_dialog_form_labels([prompt, warning_spacer])
        dialog_layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        dialog_layout.addWidget(buttons)

        def composed_title():
            if use_screen_format:
                return first_field.text()[:16].ljust(16) + second_field.text()[:16].ljust(16)
            return editor.text()

        def update_state():
            title_text = composed_title()
            validation_error = validate_legacy_title_input(title_text)
            unchanged = title_text == current_title
            has_text = bool(first_field.text().strip() or second_field.text().strip()) if use_screen_format else bool(editor.text().strip())
            is_valid = validation_error is None or unchanged
            ok_button.setEnabled(has_text and is_valid)

            if has_text and validation_error and not unchanged:
                warning_label.setVisible(True)
                warning_label.setText(validation_error)
                return

            show_warning = self._compat_warning_is_active() and self._is_title_too_long(title_text)
            warning_label.setVisible(show_warning)
            if show_warning:
                warning_label.setText(
                    f"Compatibility warning: title is over {self.TITLE_COMPAT_LIMIT} characters."
                )
            else:
                warning_label.setText("")

        editor.textChanged.connect(lambda _text: update_state())
        first_field.textChanged.connect(lambda _text: update_state())
        second_field.textChanged.connect(lambda _text: update_state())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if use_screen_format:
            field_one, field_two = self._split_title_for_center_fields(
                current_title,
                enforce_limit=True,
            )
            first_field.setText(field_one)
            second_field.setText(field_two)
            field_stack.setCurrentWidget(centered_fields_widget)
        else:
            field_stack.setCurrentWidget(editor_page)
        update_state()
        if use_screen_format:
            first_field.selectAll()
            first_field.setFocus()
        else:
            editor.selectAll()
            editor.setFocus()

        if self._exec_child_dialog(dialog) == QDialog.Accepted:
            return composed_title(), True
        return "", False

    def edit_via_dialog(self, row):
        if self._is_special_pianodir_row(row):
            self._handle_pianodir_row_clicked()
            return
        full_path_item = self.table.item(row, 1)
        if full_path_item is None:
            return
        full_path = full_path_item.text()
        title_mode = self._listed_file_title_mode(full_path)
        if not title_mode:
            QMessageBox.information(self, "No Editable Title", "Only MIDI and E-SEQ files have editable title metadata.")
            return

        current_title = self._row_raw_title(row)
        if current_title == "No title found.":
            current_title = ""
        new_title, ok = self._prompt_for_title(current_title, title_mode=title_mode)
        if ok and new_title.strip():
            if new_title == current_title:
                return

            validation_error = validate_legacy_title_input(new_title)
            if validation_error:
                QMessageBox.warning(self, "Invalid Title", validation_error)
                return
            if title_mode == "eseq" and len(new_title.encode("latin1")) > 32:
                QMessageBox.warning(self, "Title Too Long", "E-SEQ titles must be 32 characters or fewer.")
                return
            self.pendingEdits[full_path] = new_title
            filename = self.table.item(row, 3).text() if self.table.item(row, 3) else "this file"
            new_title_item = self._make_title_item(new_title, title_mode=title_mode, fallback_title=filename)
            self.table.setItem(row, 4, new_title_item)
            self._update_compat_indicator(row, new_title)
            self._reapply_regular_centered_title_assumption()
            warning = ""
            if self._compat_warning_is_active() and self._is_title_too_long(new_title):
                warning = f"\nCompatibility warning: over {self.TITLE_COMPAT_LIMIT} characters."
            title_kind = "E-SEQ title" if title_mode == "eseq" else "MIDI title"
            shown_title = self._display_title_text(new_title, title_mode=title_mode, fallback_title=filename)
            self.status_label.setText(
                f"Pending change:\n{title_kind} for '{filename}' will be updated to '{shown_title}' on save.{warning}"
            )
        if self.table.selectionModel() is not None:
            self.table.selectionModel().clearSelection()
            self.table.setCurrentItem(None)

    def _collect_image_operations(self):
        return (
            dict(self.pendingImageRenames),
            set(self.pendingImageDeletes),
            dict(self.pendingImageAdditions),
            dict(self.pendingImageReplacements),
            dict(self.pendingImageTitleEdits),
            bool(self.pendingDeletePianodir),
        )

    def _reload_image_table_after_commit(self):
        if self.image_session is None:
            return
        listing = self.image_session.list_entries()
        self.imageEntriesByPath = {entry.path: entry for entry in listing.entries}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._load_image_rows(listing.entries)

    def _confirm_image_save_deletions(self):
        delete_count = len(self.pendingImageDeletes) + (1 if self.pendingDeletePianodir else 0)
        if delete_count == 0:
            return True
        container_label = "floppy disk" if self.is_floppy_mode() else "image"
        return self._confirm_with_optional_skip(
            setting_key=self.SETTING_SKIP_IMAGE_DELETE_ON_SAVE_WARNING,
            title=f"Delete Files From {container_label.title()}",
            message=(
                f"Saving will permanently remove {delete_count} file(s) from the {container_label}.\n\n"
                "Continue?"
            ),
        )

    def save_image_changes(self):
        if self.image_session is None:
            return
        if not self._has_pending_image_changes():
            QMessageBox.information(self, "No Changes", "There are no pending image changes to save.")
            return
        if not self.is_floppy_mode() and not self._original_write_is_allowed():
            QMessageBox.information(
                self,
                "Save To Image Is Off",
                "Use Save As to export files, use Save As Image to create a separate image, or enable Overwrite Original in File Actions.",
            )
            return
        if self.is_floppy_mode() and not self._original_write_is_allowed():
            QMessageBox.information(
                self,
                "Save To Floppy Is Off",
                "Use Save As Image to save an image file, or enable Overwrite Original in File Actions.",
            )
            return
        if not self._confirm_image_save_deletions():
            return
        if not self._confirm_floppy_write():
            return
        if self.imageEseqMode and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Saving this E-SEQ floppy set",
        ):
            return
        if not self._ensure_pianodir_generation_for_save():
            return
        if self._pending_image_space_remaining() < 0:
            QMessageBox.warning(
                self,
                "Image Is Full",
                "Pending additions do not fit in the floppy image. Remove files or cancel additions before saving.",
            )
            return

        if not self.image_session.source_kind.startswith("floppy"):
            backup_error = self._create_image_backup_if_enabled(self.image_session.source_path)
            if backup_error:
                self._show_operation_error(
                    "Backup Failed",
                    "The image was not saved because the backup could not be created",
                    backup_error,
                    guidance="Check that the image folder is writable, or turn off backups before saving",
                )
                return

        renames, deletes, additions, replacements, title_edits, delete_pianodir = self._collect_image_operations()
        order_key_edits = self._image_eseq_order_key_edits()
        if self.image_session.source_kind == "floppy_usb":
            progress_text = "Saving files to floppy..."
        elif self.image_session.source_kind == "floppy_gw":
            progress_text = "Writing floppy via Greaseweazle..."
        else:
            progress_text = "Saving floppy image..."
        operations = {
            "renames": renames,
            "deletes": deletes,
            "additions": additions,
            "replacements": replacements,
            "title_edits": title_edits,
            "order_key_edits": order_key_edits,
            "pianodir_metadata": self._image_pianodir_metadata_for_save(),
            "generate_pianodir": self._should_generate_pianodir(),
            "eseq_variant": self.imageEseqVariant,
            "eseq_directory_order": self._image_eseq_directory_order(),
            "delete_pianodir": delete_pianodir,
        }
        if self.image_session.source_kind.startswith("floppy"):
            self._start_floppy_commit_worker(operations, progress_text)
            return

        progressDialog = QProgressDialog(progress_text, None, 0, 5, self)
        self._prepare_progress_dialog(progressDialog)
        progressDialog.setAutoClose(False)
        progressDialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progressDialog)
        progress_callback(0, 5, progress_text)
        QApplication.processEvents()
        try:
            self.image_session.commit_to_source(
                renames=renames,
                deletes=deletes,
                additions=additions,
                replacements=replacements,
                title_edits=title_edits,
                order_key_edits=order_key_edits,
                pianodir_metadata=operations["pianodir_metadata"],
                generate_pianodir=operations["generate_pianodir"],
                eseq_variant=operations["eseq_variant"],
                eseq_directory_order=operations["eseq_directory_order"],
                delete_pianodir=delete_pianodir,
                progress_callback=progress_callback,
            )
            self.pendingImageRenames.clear()
            self.pendingImageTitleEdits.clear()
            self.pendingImageDeletes.clear()
            self.pendingImageAdditions.clear()
            self.pendingImageReplacements.clear()
            self.pendingGeneratePianodir = False
            self.pendingDeletePianodir = False
            progress_callback(5, 5, "Reloading floppy view...")
            self._reload_image_table_after_commit()
            progressDialog.close()
            if self.image_session.source_kind.startswith("floppy"):
                QMessageBox.information(self, "Floppy Saved", "Floppy changes have been saved back to the disk.")
            else:
                QMessageBox.information(self, "Image Saved", "Floppy image changes have been saved.")
            self.status_label.setText(self._image_mode_summary())
        except Exception as exc:
            progressDialog.close()
            if self.image_session.source_kind.startswith("floppy"):
                self._show_operation_error(
                    "Floppy Save Failed",
                    "The app could not finish writing changes back to the floppy disk",
                    exc,
                    guidance="Keep this session open and try Save As Image if you need a recoverable copy",
                )
            else:
                self._show_operation_error(
                    "Image Save Failed",
                    "The app could not finish writing changes back to the image file",
                    exc,
                    guidance="The pending changes are still listed; check the file location and try again",
                )

    def _start_floppy_commit_worker(self, operations, progress_text):
        if self.image_session is None:
            return
        if self._disk_worker_busy():
            QMessageBox.information(self, "Busy", "Please wait for floppy processing to finish.")
            return

        progress_dialog = QProgressDialog(progress_text, "Cancel", 0, 5, self)
        progress_dialog.setWindowTitle("Saving Floppy")
        self._prepare_progress_dialog(progress_dialog)
        progress_dialog.setAutoClose(False)
        self._apply_stage_progress(progress_dialog, 0, 5, progress_text)

        worker = DiskSessionCommitWorker(self.image_session, operations, parent=self)
        worker.progressChanged.connect(
            lambda step, total, message, dialog=progress_dialog: self._apply_stage_progress(
                dialog, step, total, message
            )
        )
        progress_dialog.canceled.connect(worker.cancel)
        progress_dialog.canceled.connect(
            lambda dialog=progress_dialog: dialog.setLabelText("Cancelling floppy save...")
        )
        worker.commitFinished.connect(self._on_floppy_commit_success)
        worker.commitFailed.connect(self._on_floppy_commit_failure)
        worker.operationCancelled.connect(self._on_floppy_commit_cancelled)
        worker.finished.connect(self._on_floppy_commit_finished)

        self.diskCommitWorker = worker
        self.diskCommitProgressDialog = progress_dialog
        self._set_disk_write_busy(True)
        worker.start()

    def _clear_pending_image_changes_after_commit(self):
        self.pendingImageRenames.clear()
        self.pendingImageTitleEdits.clear()
        self.pendingImageDeletes.clear()
        self.pendingImageAdditions.clear()
        self.pendingImageReplacements.clear()
        self.pendingGeneratePianodir = False
        self.pendingDeletePianodir = False

    def _on_floppy_commit_success(self, listing):
        if self.diskCommitProgressDialog is not None:
            self.diskCommitProgressDialog.close()
            self.diskCommitProgressDialog = None
        self._clear_pending_image_changes_after_commit()
        self.imageEntriesByPath = {entry.path: entry for entry in listing.entries}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._load_image_rows(listing.entries)
        QMessageBox.information(self, "Floppy Saved", "Floppy changes have been saved back to the disk.")
        self.status_label.setText(self._image_mode_summary())

    def _on_floppy_commit_failure(self, message):
        if self.diskCommitProgressDialog is not None:
            self.diskCommitProgressDialog.close()
            self.diskCommitProgressDialog = None
        self._show_operation_error(
            "Floppy Save Failed",
            "The app could not finish writing changes back to the floppy disk",
            message,
            guidance="Keep this session open and try Save As Image if you need a recoverable copy",
        )

    def _on_floppy_commit_cancelled(self, _message):
        if self.diskCommitProgressDialog is not None:
            self.diskCommitProgressDialog.close()
            self.diskCommitProgressDialog = None
        QMessageBox.warning(
            self,
            "Floppy Write Cancelled",
            "Writing was cancelled. The floppy may be partially written; save again or reformat before using it.",
        )
        self.status_label.setText("Floppy write cancelled. Pending changes are still staged.")

    def _on_floppy_commit_finished(self):
        self._set_disk_write_busy(False)
        if self.diskCommitWorker is not None:
            self.diskCommitWorker.deleteLater()
            self.diskCommitWorker = None

    def show_disclaimer_dialog(self):
        QMessageBox.information(
            self,
            "Disclaimer",
            (
                "APS MIDI Prep Tool is provided for lawful preservation, repair, and compatibility work.\n\n"
                "Use copies whenever possible, keep backups, and test outputs before relying on them. "
                "You are responsible for any data loss, disk damage, instrument behavior, or other results "
                "from using the software.\n\n"
                "Use the tool only with disks and files you own or are authorized to preserve, convert, or modify. "
                "Do not use it to distribute copyrighted music, commercial player-piano libraries, proprietary "
                "software, or other material you do not have the right to share.\n\n"
                "This software is independent and is not affiliated with Yamaha, PianoDisc, Nalbantov, "
                "Greaseweazle, or other companies mentioned. Trademarks belong to their respective owners."
            ),
        )

    def show_about_dialog(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle(f"About {APP_TITLE_WITH_VERSION}")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        logo_label = QLabel(dialog)
        logo_label.setAlignment(Qt.AlignCenter)
        pixmap = pixmap_from_base64(embedded_logo_dt if is_dark_theme() else embedded_logo_lt)
        if not pixmap.isNull():
            logo_label.setPixmap(pixmap.scaled(220, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            logo_label.setText("APS MIDI Prep Tool")
        layout.addWidget(logo_label)

        title_label = QLabel(APP_TITLE_WITH_VERSION, dialog)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Helvetica", 13, QFont.Bold))
        layout.addWidget(title_label)

        website_label = QLabel(f'<a href="{APP_WEBSITE}">{APP_WEBSITE}</a>', dialog)
        website_label.setAlignment(Qt.AlignCenter)
        website_label.setOpenExternalLinks(True)
        website_label.setToolTip("Project website.")
        layout.addWidget(website_label)

        info_label = QLabel(
            (
                f"{APP_COPYRIGHT_NOTICE}<br>"
                f"Author: {APP_AUTHOR}<br>"
                f"{APP_COMPANY}<br>"
                f"{APP_COMPANY_ADDRESS}<br>"
                f"License: {APP_LICENSE}"
            ),
            dialog,
        )
        info_label.setTextFormat(Qt.RichText)
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)

        self._exec_child_dialog(dialog)

    def show_welcome_dialog(self):
        show_first_time_dialog(self.windowIcon(), parent=self, force_show=True)

    def toggle_update_checks_at_startup(self, enabled):
        enabled = bool(enabled)
        self.settings.setValue(self.SETTING_CHECK_UPDATES_AT_STARTUP, enabled)
        self.settings.setValue(self.SETTING_SKIP_UPDATE_REMINDERS, not enabled)

    def check_for_updates(self, manual=False):
        if self.updateCheckWorker is not None:
            if manual:
                QMessageBox.information(self, "Update Check Running", "An update check is already in progress.")
            return
        self.updateCheckManual = bool(manual)
        if hasattr(self, "helpCheckUpdatesAction"):
            self.helpCheckUpdatesAction.setEnabled(False)
        worker = UpdateCheckWorker(UPDATE_CHECK_URL, parent=self)
        worker.updateChecked.connect(self._on_update_check_success)
        worker.updateCheckFailed.connect(self._on_update_check_failure)
        worker.finished.connect(self._on_update_check_finished)
        self.updateCheckWorker = worker
        worker.start()

    def _on_update_check_finished(self):
        worker = self.updateCheckWorker
        self.updateCheckWorker = None
        if hasattr(self, "helpCheckUpdatesAction"):
            self.helpCheckUpdatesAction.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _on_update_check_failure(self, message):
        if not self.updateCheckManual:
            return
        self._show_operation_error(
            "Update Check Failed",
            "The app could not check for updates",
            message,
            guidance=f"Make sure {UPDATE_CHECK_URL} is reachable and contains valid update JSON",
        )

    def _on_update_check_success(self, data):
        latest_version = str(data.get("latest_version") or data.get("version") or "").strip()
        if not _is_newer_version(latest_version, APP_VERSION):
            if self.updateCheckManual:
                QMessageBox.information(
                    self,
                    "No Update Available",
                    f"{APP_NAME} v{APP_VERSION} is up to date.",
                )
            return
        if self.updateCheckManual:
            self._show_update_available_dialog(data, startup_notice=False)
            return
        if self.settings.value(self.SETTING_SKIP_UPDATE_REMINDERS, False, type=bool):
            return
        self._show_update_available_dialog(data, startup_notice=True)

    def _update_download_url(self, data):
        return str(
            data.get("download_url")
            or data.get("release_url")
            or data.get("release_notes_url")
            or data.get("url")
            or APP_WEBSITE
        ).strip()

    def _update_message_text(self, data, *, startup_notice):
        latest_version = str(data.get("latest_version") or data.get("version") or "").strip()
        message = str(data.get("message") or "").strip()
        release_notes = str(data.get("release_notes") or "").strip()
        release_notes_url = str(data.get("release_notes_url") or "").strip()

        lines = [
            f"{APP_NAME} v{latest_version} is available.",
            f"You are running v{APP_VERSION}.",
        ]
        if message:
            lines.extend(["", message])
        if release_notes:
            lines.extend(["", release_notes])
        if release_notes_url:
            lines.extend(["", f"Release notes: {release_notes_url}"])
        if startup_notice:
            lines.extend(["", "You can turn off startup update reminders from this notice or the Help menu."])
        return "\n".join(lines)

    def _show_update_available_dialog(self, data, *, startup_notice):
        dialog = QMessageBox(self)
        apply_window_icon(dialog)
        dialog.setIcon(QMessageBox.Information)
        dialog.setWindowTitle("Update Available")
        dialog.setText(self._update_message_text(data, startup_notice=startup_notice))

        download_button = dialog.addButton("Open Download Page", QMessageBox.AcceptRole)
        later_button = dialog.addButton("Remind Me Later", QMessageBox.RejectRole)
        disable_button = None
        if startup_notice:
            disable_button = dialog.addButton("Turn Off Reminders", QMessageBox.DestructiveRole)
        dialog.setDefaultButton(download_button)
        self._exec_child_dialog(dialog)

        clicked = dialog.clickedButton()
        if clicked is download_button:
            url = self._update_download_url(data)
            if url:
                QDesktopServices.openUrl(QUrl(url))
        elif disable_button is not None and clicked is disable_button:
            self.settings.setValue(self.SETTING_CHECK_UPDATES_AT_STARTUP, False)
            self.settings.setValue(self.SETTING_SKIP_UPDATE_REMINDERS, True)
            if hasattr(self, "helpCheckUpdatesAtStartupAction"):
                self.helpCheckUpdatesAtStartupAction.blockSignals(True)
                self.helpCheckUpdatesAtStartupAction.setChecked(False)
                self.helpCheckUpdatesAtStartupAction.blockSignals(False)
        else:
            del later_button

    def _extension_from_filter(self, selected_filter):
        if "*." not in selected_filter:
            return ""
        return selected_filter.split("*.", 1)[1].split(")", 1)[0].strip().lower()

    def save_image_as(self):
        if self.image_session is None:
            return
        if self.imageEseqMode and not self._ensure_eseq_file_limit(
            self._image_song_file_count(),
            action_text="Saving this E-SEQ floppy set as a separate image",
        ):
            return
        if not self._ensure_pianodir_generation_for_save():
            return
        if self._pending_image_space_remaining() < 0:
            QMessageBox.warning(
                self,
                "Image Is Full",
                "Pending additions do not fit in the floppy image. Remove files or cancel additions before exporting.",
            )
            return

        default_ext = self.image_session.source_ext or "img"
        filters, fallback_ext = output_filters(default_ext)
        if self.image_session.source_kind.startswith("floppy"):
            source_dir = os.path.expanduser("~")
            source_stem = "floppy_capture"
            if self.image_session.source_kind == "floppy_gw":
                source_stem = f"gw_drive_{self.image_session.gw_source.drive.lower()}"
        else:
            source_dir = os.path.dirname(self.image_session.source_path)
            source_stem = os.path.splitext(os.path.basename(self.image_session.source_path))[0]
        default_path = os.path.join(source_dir, f"{source_stem}_edited.{default_ext or fallback_ext}")
        output_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save As Image",
            default_path,
            filters,
        )
        if not output_path:
            return

        selected_ext = image_extension(output_path) or self._extension_from_filter(selected_filter) or fallback_ext
        if not image_extension(output_path):
            output_path = f"{output_path}.{selected_ext}"

        renames, deletes, additions, replacements, title_edits, delete_pianodir = self._collect_image_operations()
        order_key_edits = self._image_eseq_order_key_edits()
        progressDialog = QProgressDialog("Exporting floppy image...", None, 0, 5, self)
        self._prepare_progress_dialog(progressDialog)
        progressDialog.setAutoClose(False)
        progressDialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progressDialog)
        progress_callback(0, 5, "Preparing floppy export...")
        QApplication.processEvents()
        try:
            self.image_session.export_to(
                output_path,
                selected_ext,
                renames=renames,
                deletes=deletes,
                additions=additions,
                replacements=replacements,
                title_edits=title_edits,
                order_key_edits=order_key_edits,
                pianodir_metadata=self._image_pianodir_metadata_for_save(),
                generate_pianodir=self._should_generate_pianodir(),
                eseq_variant=self.imageEseqVariant,
                eseq_directory_order=self._image_eseq_directory_order(),
                delete_pianodir=delete_pianodir,
                progress_callback=progress_callback,
            )
            progress_callback(5, 5, "Finalizing floppy export...")
            progressDialog.close()
            session = FloppyImageSession.load(output_path)
            listing = session.list_entries()
            self._activate_disk_session(session, listing)
            QMessageBox.information(self, "Save As Image Complete", f"Image saved as {os.path.basename(output_path)}.")
            self.status_label.setText(self._image_mode_summary())
        except Exception as exc:
            progressDialog.close()
            self._show_operation_error(
                "Image Export Failed",
                f"Could not create {os.path.basename(output_path)}",
                exc,
                guidance="Check that the destination folder is writable and that enough disk space is available",
            )

    def _basic_image_export_types(self):
        preferred = {ext: (ext, label) for ext, label in PREFERRED_OUTPUT_EXTENSIONS}
        return [
            preferred[ext]
            for ext in ("hfe", "img", "bin")
            if ext in preferred
        ]

    def _basic_disk_export_formats(self):
        return [
            disk_format
            for disk_format in DISK_FORMATS
            if disk_format.key in {"ibm.720", "ibm.1440"}
        ]

    def _prompt_for_save_image_options(self):
        dialog = QDialog(self)
        apply_window_icon(dialog)
        dialog.setWindowTitle("Save As Image")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(18, 18, 18, 18)
        dialog_layout.setSpacing(8)

        summary = QLabel(
            "Choose the output image type and floppy size.\n"
            "If the selected disk is too small, files will spill into numbered images in sequence."
        )
        summary.setWordWrap(True)
        dialog_layout.addWidget(summary)

        type_combo = QComboBox(dialog)
        list_all_types_checkbox = QCheckBox("List all image formats")
        disk_combo = QComboBox(dialog)
        list_all_disks_checkbox = QCheckBox("List all disk sizes")

        form_grid = self._make_dialog_form_grid()
        type_label = self._add_dialog_form_row(form_grid, 0, "Image format:", type_combo)
        type_options_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(type_options_spacer, 1, 0)
        form_grid.addWidget(list_all_types_checkbox, 1, 1)
        disk_label = self._add_dialog_form_row(form_grid, 2, "Disk size:", disk_combo)
        disk_options_spacer = self._make_dialog_form_label("")
        form_grid.addWidget(disk_options_spacer, 3, 0)
        form_grid.addWidget(list_all_disks_checkbox, 3, 1)
        self._align_dialog_form_labels([type_label, type_options_spacer, disk_label, disk_options_spacer])
        dialog_layout.addLayout(form_grid)

        buttons = self._make_dialog_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        dialog_layout.addWidget(buttons)

        def refresh_type_combo():
            current_ext = type_combo.currentData()
            options = PREFERRED_OUTPUT_EXTENSIONS if list_all_types_checkbox.isChecked() else self._basic_image_export_types()
            type_combo.clear()
            selected_index = 0
            for index, (ext, label) in enumerate(options):
                type_combo.addItem(label, ext)
                if ext == current_ext:
                    selected_index = index
            type_combo.setCurrentIndex(selected_index)

        def refresh_disk_combo():
            current_key = disk_combo.currentData().key if disk_combo.currentData() is not None else "ibm.720"
            options = DISK_FORMATS if list_all_disks_checkbox.isChecked() else self._basic_disk_export_formats()
            disk_combo.clear()
            selected_index = 0
            for index, disk_format in enumerate(options):
                disk_combo.addItem(disk_format.label, disk_format)
                if disk_format.key == current_key:
                    selected_index = index
            disk_combo.setCurrentIndex(selected_index)

        list_all_types_checkbox.toggled.connect(refresh_type_combo)
        list_all_disks_checkbox.toggled.connect(refresh_disk_combo)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        refresh_type_combo()
        refresh_disk_combo()

        if self._exec_child_dialog(dialog) != QDialog.Accepted:
            return None

        output_ext = type_combo.currentData()
        disk_format = disk_combo.currentData()
        if not output_ext or disk_format is None:
            return None

        output_label = type_combo.currentText() or f"{output_ext.upper()} image"
        default_path = os.path.join(os.path.expanduser("~"), f"midi_floppy.{output_ext}")
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save As Image",
            default_path,
            f"{output_label} (*.{output_ext})",
        )
        if not output_path:
            return None

        base_path = os.path.splitext(output_path)[0]
        return f"{base_path}.{output_ext}", output_ext, disk_format

    def _stage_files_for_image_export(self, temp_dir, progress_callback=None):
        row_count = self._regular_file_count()
        file_specs = []
        used_names = set()
        regular_order_key_edits = self._regular_eseq_order_key_edits() if self.is_local_eseq_mode() else {}

        for index, row in enumerate(self._regular_file_rows(), start=1):
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue

            full_path = full_path_item.text()
            filename_item = self.table.item(row, 3)
            display_name = filename_item.text() if filename_item is not None else os.path.basename(full_path)
            display_title = self._row_raw_title(row) or display_name

            _notify = progress_callback
            if _notify is not None:
                _notify(index - 1, max(1, row_count), f"Preparing {display_name} for image export...")

            staged_path = os.path.join(
                temp_dir,
                f"{index:04d}_{os.path.basename(full_path)}",
            )
            error_msg = self._write_listed_file_to_path(
                full_path,
                display_title,
                staged_path,
                order_key=regular_order_key_edits.get(full_path),
            )
            if error_msg:
                raise FloppyImageError(error_msg)

            image_name = self._build_dos_image_filename(display_name, used_names)
            used_names.add(image_name.upper())
            file_specs.append(
                {
                    "host_path": staged_path,
                    "image_path": image_name,
                    "display_name": display_name,
                    "title": display_title,
                    "title_mode": self._listed_file_title_mode(full_path),
                }
            )

        if self.is_local_eseq_mode() and self._should_generate_pianodir(for_export=True):
            track_entries = [
                PianodirTrackEntry(
                    image_path=spec["image_path"],
                    local_path=spec["host_path"],
                    title=spec.get("title", ""),
                )
                for spec in file_specs
                if spec.get("title_mode") == "eseq"
            ]
            if track_entries:
                directory_name = self._eseq_directory_filename(self.regularEseqVariant)
                generated_path = os.path.join(temp_dir, directory_name)
                with open(generated_path, "wb") as handle:
                    if self.regularEseqVariant == ESEQ_VARIANT_CLAVINOVA:
                        handle.write(build_music_dir_bytes(track_entries))
                    else:
                        handle.write(build_pianodir_bytes(track_entries))
                file_specs.append(
                    {
                        "host_path": generated_path,
                        "image_path": directory_name,
                        "display_name": directory_name,
                        "title": "",
                        "title_mode": "",
                    }
                )
        elif self.is_local_eseq_mode() and self.regularHasPianodir:
            existing_pianodir = self._existing_regular_pianodir_path()
            if existing_pianodir and os.path.isfile(existing_pianodir):
                directory_name = self._eseq_directory_filename(self.regularEseqVariant)
                staged_pianodir = os.path.join(temp_dir, directory_name)
                shutil.copy2(existing_pianodir, staged_pianodir)
                file_specs.append(
                    {
                        "host_path": staged_pianodir,
                        "image_path": directory_name,
                        "display_name": directory_name,
                        "title": "",
                        "title_mode": "",
                    }
                )

        if progress_callback is not None:
            progress_callback(len(file_specs), max(1, len(file_specs)), "Preparing floppy image export...")
        return file_specs

    def _materialize_export_context_files(self, file_specs, output_path):
        base_path = os.path.splitext(output_path)[0]
        context_dir = f"{base_path}_files"
        if os.path.isdir(context_dir):
            shutil.rmtree(context_dir, ignore_errors=True)
        os.makedirs(context_dir, exist_ok=True)

        context_paths = []
        for spec in file_specs:
            dest_path = os.path.join(context_dir, os.path.basename(spec["image_path"]))
            shutil.copy2(spec["host_path"], dest_path)
            context_paths.append(dest_path)
        return context_dir, context_paths

    def save_as_image(self):
        if self.is_image_mode():
            self.save_image_as()
            return
        if not self.choose_button.isEnabled():
            QMessageBox.information(self, "Busy", "Please wait for MIDI processing to finish.")
            return
        if self._regular_file_count() == 0:
            QMessageBox.information(self, "No Files", "Add one or more files first.")
            return
        if self.is_local_eseq_mode() and not self._ensure_eseq_file_limit(
            self._regular_file_count(),
            action_text="Saving this E-SEQ set as an image",
        ):
            return
        if self.is_local_eseq_mode() and not self._ensure_pianodir_generation_for_save():
            return

        options = self._prompt_for_save_image_options()
        if options is None:
            return

        output_path, output_ext, disk_format = options
        staging_dir = tempfile.mkdtemp(prefix="aps_save_image_")
        progressDialog = QProgressDialog("Preparing files for image export...", None, 0, max(1, self._regular_file_count()), self)
        self._prepare_progress_dialog(progressDialog)
        progressDialog.setAutoClose(False)
        progressDialog.setCancelButton(None)
        progress_callback = self._make_stage_progress_callback(progressDialog)

        try:
            file_specs = self._stage_files_for_image_export(staging_dir, progress_callback=progress_callback)
            if not file_specs:
                progressDialog.close()
                QMessageBox.information(self, "No Files", "No valid files were available to export.")
                return

            output_paths = create_floppy_images_from_files(
                file_specs,
                output_path,
                output_ext,
                disk_format,
                progress_callback=progress_callback,
            )
            progressDialog.close()

            if len(output_paths) == 1:
                session = FloppyImageSession.load(output_paths[0])
                listing = session.list_entries()
                self._activate_disk_session(session, listing)
                QMessageBox.information(
                    self,
                    "Save As Image Complete",
                    f"Created {os.path.basename(output_paths[0])}.",
                )
                self.status_label.setText(self._image_mode_summary())
                return

            _, context_paths = self._materialize_export_context_files(file_specs, output_path)
            self._load_regular_files(
                context_paths,
                (
                    f"Created {len(output_paths)} sequential {disk_format.label} {output_ext.upper()} images.\n"
                    "Current context moved to the new exported source files."
                ),
            )
            preview = "\n".join(os.path.basename(path) for path in output_paths[:10])
            if len(output_paths) > 10:
                preview += f"\n...and {len(output_paths) - 10} more."
            QMessageBox.information(
                self,
                "Save As Image Complete",
                f"Created {len(output_paths)} image files:\n\n{preview}",
            )
        except Exception as exc:
            progressDialog.close()
            self._show_operation_error(
                "Save As Image Failed",
                f"Could not create {os.path.basename(output_path)}",
                exc,
                guidance="Check that the destination folder is writable, then try again",
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _pending_regular_rename_plan(self, *, exclude_paths=()):
        excluded = {
            os.path.normcase(os.path.abspath(path))
            for path in exclude_paths
        }
        plan = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            if os.path.normcase(os.path.abspath(full_path)) in excluded:
                continue
            target_filename = self.pendingRegularRenames.get(full_path)
            if not target_filename:
                continue
            target_path = os.path.join(os.path.dirname(full_path), target_filename)
            plan.append((full_path, target_path))
        return plan

    def _apply_regular_rename_plan(self, plan, *, skip_backup_paths=None):
        if not plan:
            return [], {}, 0

        try:
            validate_midi_dos83_plan(plan)
        except Exception as exc:
            return [str(exc)], {}, 0

        skip_backup_keys = {
            os.path.normcase(os.path.abspath(path))
            for path in (skip_backup_paths or set())
        }
        errors = []
        backup_count = 0
        if self.backup_checkbox.isChecked():
            for source, target in plan:
                if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
                    continue
                if os.path.normcase(os.path.abspath(source)) in skip_backup_keys:
                    continue
                backup_error = self._create_backup_if_enabled(source)
                if backup_error:
                    errors.append(backup_error)
                else:
                    backup_count += 1
        if errors:
            return errors, {}, backup_count

        try:
            result = apply_midi_dos83_plan(plan, create_backups=False)
        except Exception as exc:
            return [str(exc)], {}, backup_count

        old_to_new = {source: target for source, target in result.renamed}
        self._apply_path_remap(old_to_new)
        self._update_table_paths(old_to_new)
        return [], old_to_new, backup_count

    def _save_pending_regular_renames(self, *, skip_backup_paths=None):
        plan = self._pending_regular_rename_plan()
        errors, old_to_new, backup_count = self._apply_regular_rename_plan(
            plan,
            skip_backup_paths=skip_backup_paths,
        )
        if errors:
            return errors, 0, backup_count
        self.pendingRegularRenames.clear()
        self._refresh_regular_mode_action_state()
        return [], len(old_to_new), backup_count

    def _save_pending_regular_conversions(self, regular_order_key_edits):
        converted_items = []
        all_source_paths = []
        for row in self._regular_file_rows():
            full_path_item = self.table.item(row, 1)
            if full_path_item is None:
                continue
            full_path = full_path_item.text()
            all_source_paths.append(full_path)
            conversion = self.pendingRegularConversions.get(full_path)
            if not conversion:
                continue
            dest_path = os.path.join(
                os.path.dirname(full_path),
                self._regular_row_output_filename(row),
            )
            converted_items.append((row, full_path, dest_path, conversion))

        if not converted_items:
            self.pendingRegularConversions.clear()
            return []

        errors = []
        output_paths = []
        output_path_map = {}
        converted_sources = {full_path for _row, full_path, _dest_path, _conversion in converted_items}
        rename_plan = self._pending_regular_rename_plan(exclude_paths=converted_sources)
        try:
            validate_midi_dos83_plan(rename_plan)
        except Exception as exc:
            return [str(exc)]
        for _row, full_path, dest_path, conversion in converted_items:
            is_original_path = (
                os.path.normcase(os.path.abspath(dest_path))
                == os.path.normcase(os.path.abspath(full_path))
            )
            if is_original_path and not conversion.get("overwrite_original"):
                errors.append(f"{os.path.basename(full_path)}: target path matches source path")
            elif not is_original_path and os.path.exists(dest_path):
                errors.append(f"{os.path.basename(dest_path)} already exists")
        if errors:
            return errors

        progressDialog = QProgressDialog("Saving converted files...", "Cancel", 0, len(converted_items), self)
        self._prepare_progress_dialog(progressDialog)
        for index, (row, full_path, dest_path, _conversion) in enumerate(converted_items, start=1):
            if progressDialog.wasCanceled():
                break

            backup_error = self._create_backup_if_enabled(full_path)
            if backup_error:
                errors.append(backup_error)
                progressDialog.setValue(index)
                QApplication.processEvents()
                continue

            title = self._row_raw_title(row)
            error_msg = self._write_listed_file_to_path(
                full_path,
                title,
                dest_path,
                order_key=regular_order_key_edits.get(full_path),
            )
            if error_msg:
                errors.append(error_msg)
            else:
                output_paths.append(dest_path)
                output_path_map[full_path] = dest_path
            progressDialog.setValue(index)
            QApplication.processEvents()
        progressDialog.close()

        if errors:
            return errors

        rename_errors, renamed_output_map, _rename_backup_count = self._apply_regular_rename_plan(rename_plan)
        if rename_errors:
            return rename_errors

        combined_path_map = {}
        combined_path_map.update(renamed_output_map)
        combined_path_map.update(output_path_map)
        output_paths = []
        seen_output_paths = set()
        for source_path in all_source_paths:
            output_path = combined_path_map.get(source_path, source_path)
            output_key = os.path.normcase(os.path.abspath(output_path))
            if output_key in seen_output_paths:
                continue
            seen_output_paths.add(output_key)
            output_paths.append(output_path)

        if self.is_local_eseq_mode() and self._should_generate_pianodir(for_export=True):
            try:
                target_dirs = [os.path.dirname(path) for path in output_paths]
                base_dir = os.path.commonpath(target_dirs) if target_dirs else self.regularModeContextPath
                if not os.path.isdir(base_dir):
                    base_dir = os.path.dirname(base_dir)
                output_paths.append(
                    self._write_regular_pianodir(
                        base_dir=base_dir,
                        path_remap=combined_path_map,
                    )
                )
            except Exception as exc:
                return [f"Could not write {self._eseq_directory_filename(self.regularEseqVariant)}: {exc}"]

        tag_errors = self._write_tag_sidecars_for_regular_rows(
            path_remap=combined_path_map,
            only_paths=combined_path_map.keys(),
        )
        if tag_errors:
            return tag_errors

        status_text = f"Saved {len(output_path_map)} converted file(s)."
        if renamed_output_map:
            status_text += f"\nRenamed {len(renamed_output_map)} file(s) to DOS 8.3."
        if self._tag_sidecars_enabled() and combined_path_map:
            status_text += "\nWrote .tags.txt sidecar file(s)."
        if self.backup_checkbox.isChecked():
            status_text += "\nCreated backup file(s) for the original source files."
        self._cleanup_midi_scratch_dir()
        self._load_regular_files(output_paths, status_text)
        return []

    def save_pending_changes(self):
        if self.is_image_mode():
            self.save_image_changes()
            return

        if self.is_local_eseq_mode() and not self._ensure_pianodir_generation_for_save():
            return

        should_write_local_pianodir = self.is_local_eseq_mode() and self._should_generate_pianodir()
        regular_order_key_edits = self._regular_eseq_order_key_edits() if self.is_local_eseq_mode() else {}
        has_pending_conversions = bool(self.pendingRegularConversions)
        has_pending_renames = bool(self.pendingRegularRenames)
        should_write_tag_sidecars = self._tag_sidecars_enabled() and self._regular_file_count() > 0

        if (
            not self.pendingEdits
            and not should_write_local_pianodir
            and not regular_order_key_edits
            and not has_pending_conversions
            and not has_pending_renames
            and not should_write_tag_sidecars
        ):
            QMessageBox.information(self, "No Changes", "There are no pending changes to save.")
            return
        if self.is_local_eseq_mode() and not self._ensure_eseq_file_limit(
            self._regular_file_count(),
            action_text="Saving this E-SEQ set",
        ):
            return

        if has_pending_conversions:
            errors = self._save_pending_regular_conversions(regular_order_key_edits)
            if errors:
                self._show_error_list(
                    "Save Failed",
                    "Some converted files could not be saved",
                    errors,
                    guidance="No completed conversion rows were cleared; fix the listed files and try Save or Save As again",
                )
            else:
                QMessageBox.information(self, "Save Complete", "Converted files have been saved.")
            return

        errors = []
        file_updates = {}
        backup_created_for = set()
        for full_path, new_title in self.pendingEdits.items():
            file_updates.setdefault(full_path, {})["title"] = new_title
        for full_path, order_key in regular_order_key_edits.items():
            file_updates.setdefault(full_path, {})["order_key"] = order_key

        if file_updates:
            progressDialog = QProgressDialog("Saving title and order changes...", "Cancel", 0, len(file_updates), self)
            self._prepare_progress_dialog(progressDialog)
            current = 0
            for full_path, update_spec in file_updates.items():
                new_title = update_spec.get("title")
                if new_title is not None:
                    validation_error = validate_legacy_title_input(new_title)
                    if validation_error:
                        errors.append(f"Invalid title for {os.path.basename(full_path)}: {validation_error}")
                        current += 1
                        progressDialog.setValue(current)
                        QApplication.processEvents()
                        if progressDialog.wasCanceled():
                            break
                        continue
                backup_error = self._create_backup_if_enabled(full_path)
                if backup_error:
                    errors.append(backup_error)
                    current += 1
                    progressDialog.setValue(current)
                    QApplication.processEvents()
                    if progressDialog.wasCanceled():
                        break
                    continue
                if self.backup_checkbox.isChecked():
                    backup_created_for.add(os.path.normcase(os.path.abspath(full_path)))

                title_mode = self._listed_file_title_mode(full_path)
                if title_mode == "eseq":
                    error_msg = self._write_eseq_file_to_path(
                        full_path,
                        full_path,
                        title=new_title,
                        order_key=update_spec.get("order_key"),
                    )
                else:
                    error_msg = update_midi_title(full_path, new_title)
                if error_msg:
                    errors.append(error_msg)
                current += 1
                progressDialog.setValue(current)
                QApplication.processEvents()
                if progressDialog.wasCanceled():
                        break
            progressDialog.close()
            if not errors:
                for full_path, order_key in regular_order_key_edits.items():
                    if full_path in self.listedFileInfo:
                        self.listedFileInfo[full_path]["order_key"] = normalize_eseq_order_key(order_key)
            self.pendingEdits.clear()

        if not errors and should_write_local_pianodir:
            try:
                output_path = self._write_regular_pianodir()
                self.regularPianodirSourcePath = output_path
                self.regularHasPianodir = True
                self.regularPianodirPopulated = True
                self.loadedRegularPianodirMetadata = self._current_regular_pianodir_metadata()
                self.pendingGeneratePianodir = False
                self._refresh_regular_pianodir_row()
            except Exception as exc:
                errors.append(f"Could not write {self._eseq_directory_filename(self.regularEseqVariant)}: {exc}")

        renamed_count = 0
        _rename_backup_count = 0
        if not errors and has_pending_renames:
            rename_errors, renamed_count, _rename_backup_count = self._save_pending_regular_renames(
                skip_backup_paths=backup_created_for,
            )
            errors.extend(rename_errors)

        if not errors and should_write_tag_sidecars:
            errors.extend(self._write_tag_sidecars_for_regular_rows())

        if errors:
            self._show_error_list(
                "Save Failed",
                "Some pending changes could not be saved",
                errors,
                guidance="Fix the listed files, then try Save again",
            )
        else:
            message = "All pending changes have been saved."
            if renamed_count:
                message += f"\n\nRenamed {renamed_count} file(s) to DOS 8.3."
                if self.backup_checkbox.isChecked():
                    message += " Copies with the old filenames were kept in the backup folder."
            if should_write_tag_sidecars:
                message += "\n\n.tags.txt sidecar file(s) were written next to the saved files."
            QMessageBox.information(self, "Save Complete", message)

    def save_as_changes(self):
        if self.is_image_mode():
            if self.imageEseqMode and not self._ensure_eseq_file_limit(
                self._image_song_file_count(),
                action_text="Exporting this E-SEQ floppy set to a folder",
            ):
                return
            if not self._ensure_pianodir_generation_for_save():
                return

            dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if not dest_dir:
                return
            export_dir = self._destination_with_album_subfolder(dest_dir)

            progressDialog = QProgressDialog("Saving files to new folder...", None, 0, max(1, self.table.rowCount()), self)
            self._prepare_progress_dialog(progressDialog)
            progressDialog.setAutoClose(False)
            progressDialog.setCancelButton(None)
            progress_callback = self._make_stage_progress_callback(progressDialog)
            progress_callback(0, max(1, self.table.rowCount()), "Preparing exported files...")
            QApplication.processEvents()

            try:
                output_paths = self._export_image_session_files_to_folder(export_dir, progress_callback=progress_callback)
                progressDialog.close()
                self._cleanup_midi_scratch_dir()
                self._reset_image_state()
                self._load_regular_files(
                    output_paths,
                    f"Current context moved to: \"{export_dir}\"",
                )
                QMessageBox.information(self, "Save As Complete", "Files have been saved to the new folder.")
            except Exception as exc:
                progressDialog.close()
                self._show_operation_error(
                    "Save As Failed",
                    f"Could not save files to {export_dir}",
                    exc,
                    guidance="Check that the destination folder is writable and try again",
                )
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if not dest_dir:
            return
        if self.is_local_eseq_mode() and not self._ensure_eseq_file_limit(
            self._regular_file_count(),
            action_text="Exporting this E-SEQ set to a folder",
        ):
            return
        if self.is_local_eseq_mode() and not self._ensure_pianodir_generation_for_save():
            return
        export_dir = self._destination_with_album_subfolder(dest_dir)
        os.makedirs(export_dir, exist_ok=True)

        progressDialog = QProgressDialog("Saving files to new folder...", "Cancel", 0, max(1, self._regular_file_count()), self)
        self._prepare_progress_dialog(progressDialog)
        row_count = self._regular_file_count()
        regular_order_key_edits = self._regular_eseq_order_key_edits() if self.is_local_eseq_mode() else {}
        errors = []
        output_paths = []
        output_path_map = {}
        for i, row in enumerate(self._regular_file_rows()):
            full_path = self.table.item(row, 1).text()
            title = self._row_raw_title(row)
            dest_path = os.path.join(export_dir, self._regular_row_output_filename(row))
            error_msg = self._write_listed_file_to_path(
                full_path,
                title,
                dest_path,
                order_key=regular_order_key_edits.get(full_path),
            )
            if error_msg:
                errors.append(error_msg)
            else:
                output_paths.append(dest_path)
                output_path_map[full_path] = dest_path
            progressDialog.setValue(i + 1)
            QApplication.processEvents()
            if progressDialog.wasCanceled():
                break
        progressDialog.close()
        if not errors and self.is_local_eseq_mode() and self._should_generate_pianodir(for_export=True):
            try:
                output_paths.append(self._write_regular_pianodir(base_dir=export_dir, path_remap=output_path_map))
            except Exception as exc:
                errors.append(f"Could not write {self._eseq_directory_filename(self.regularEseqVariant)}: {exc}")
        elif not errors and self.is_local_eseq_mode() and self.regularHasPianodir:
            existing_pianodir = self._existing_regular_pianodir_path()
            if existing_pianodir and os.path.isfile(existing_pianodir):
                copied_pianodir = os.path.join(export_dir, self._eseq_directory_filename(self.regularEseqVariant))
                shutil.copy2(existing_pianodir, copied_pianodir)
                output_paths.append(copied_pianodir)
        if not errors:
            errors.extend(
                self._write_tag_sidecars_for_regular_rows(
                    path_remap=output_path_map,
                    only_paths=output_path_map.keys(),
                )
            )
        if errors:
            self._show_error_list(
                "Save As Failed",
                f"Some files could not be saved to {export_dir}",
                errors,
                guidance="The original files were not modified; fix the listed files and try Save As again",
            )
        else:
            self._load_regular_files(
                output_paths,
                f"Current context moved to: \"{export_dir}\"",
            )
            message = "Files have been saved to the new folder."
            if self._tag_sidecars_enabled():
                message += "\n\n.tags.txt sidecar file(s) were written next to the exported files."
            QMessageBox.information(self, "Save As Complete", message)
