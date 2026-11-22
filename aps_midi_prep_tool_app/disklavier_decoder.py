"""Read APS Disklavier Y-modulation WAVs without playing their control channel.

This digital receiver decodes the right-hand PCM itself. It does not consult
the original MIDI, an export manifest, or a filename. The supported input is
the sample-aligned 44.1 kHz PCM16 stereo format produced by our encoder, not
an analog recording, MP3, resampled WAV, or every historical piano CD format.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import tempfile
import wave

import numpy as np

from .helpers.atomic_file import publish_new_file


SAMPLE_RATE = 44100
SAMPLES_PER_SYMBOL = 14
_BLOCK_FRAMES = 14 * 4096
_MAX_SYSEX_BYTES = 1024 * 1024
_MAX_DELTA = 0x0FFFFFFF
# Yamaha US20030061931A1 Figure 14, read in ascending differential phase order.
_PHASE_TO_NIBBLE = np.array((8, 10, 14, 6, 2, 3, 7, 15, 11, 9, 13, 5, 1, 0, 4, 12))
_CARRIER = np.exp(2j * np.pi * np.arange(SAMPLES_PER_SYMBOL) / 7)


class DisklavierDecodingCancelled(RuntimeError):
    """The caller canceled decoding before the result was published."""


def _check_cancel(cancel):
    if cancel is not None:
        stopped = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
        if stopped:
            raise DisklavierDecodingCancelled("Disklavier WAV decoding canceled.")


def _variable_length(value):
    result = bytearray((value & 0x7F,))
    while value > 0x7F:
        value >>= 7
        result.insert(0, 0x80 | (value & 0x7F))
    return result


class _MidiWriter:
    """A streaming type-0 SMF, with exactly one tick per source audio frame."""

    def __init__(self, handle):
        self.handle = handle
        self.last_frame = 0
        self.events = 0
        self.notes = 0
        self.system_events = 0
        # 22,050 ticks/beat at 500,000 us/beat is exactly 44,100 ticks/s.
        handle.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, SAMPLE_RATE // 2))
        handle.write(b"MTrk\0\0\0\0\x00\xff\x51\x03\x07\xa1\x20")

    def _delta(self, frame):
        delta = frame - self.last_frame
        while delta > _MAX_DELTA:
            # A harmless empty text meta event bridges very long silent gaps.
            self.handle.write(_variable_length(_MAX_DELTA) + b"\xff\x01\x00")
            delta -= _MAX_DELTA
        self.handle.write(_variable_length(delta))
        self.last_frame = frame

    def message(self, frame, message):
        status = message[0]
        if status >= 0xF0 and status != 0xF0:
            # Clock, transport and system-common wire messages are not musical
            # SMF events; in particular 0xFF would be mistaken for a meta event.
            self.system_events += 1
            return
        self._delta(frame)
        if status == 0xF0:
            self.handle.write(b"\xf0" + _variable_length(len(message) - 1))
            self.handle.write(message[1:])
        else:
            self.handle.write(message)
        self.events += 1
        self.notes += status >> 4 in (8, 9)

    def finish(self, frames):
        self._delta(frames)
        self.handle.write(b"\xff\x2f\x00")
        length = self.handle.tell() - 22
        if length > 0xFFFFFFFF:
            raise ValueError("The decoded MIDI exceeds the MIDI file size limit.")
        self.handle.seek(18)
        self.handle.write(struct.pack(">I", length))
        self.handle.flush()


class _WireReader:
    """Decode escaped nibbles and complete, non-running-status MIDI messages."""

    def __init__(self, writer):
        self.writer = writer
        self.byte_prefix = None
        self.message = bytearray()
        self.expected = None
        self.idle_run = 0
        self.acquired = False

    def nibble(self, value, frame):
        if self.byte_prefix is None:
            if not self.message and value == 15:
                self.idle_run += 1
                return
            if not self.acquired:
                if self.idle_run < 16:
                    raise ValueError("The right channel has no recognizable Disklavier idle leader.")
                self.acquired = True
            self.idle_run = 0
            self.byte_prefix = value
            return
        high = self.byte_prefix
        if high == 12 and value == 4:
            self.byte_prefix = 16  # Escaped Cx; the following nibble is its low half.
            return
        self.byte_prefix = None
        byte = (0xC0 | value) if high == 16 else ((0xF0 | value) if high == 12
                                                  else (high << 4) | value)
        if not self.message:
            if byte < 0x80:
                raise ValueError("The control channel contains MIDI data without a status byte.")
            if byte < 0xF0:
                self.expected = 2 if byte >> 4 in (12, 13) else 3
            elif byte == 0xF0:
                self.expected = None
            else:
                self.expected = {0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1,
                                 0xF8: 1, 0xFA: 1, 0xFB: 1, 0xFC: 1,
                                 0xFE: 1, 0xFF: 1}.get(byte)
                if self.expected is None:
                    raise ValueError("The control channel contains an unsupported MIDI status.")
            self.message.append(byte)
        elif self.message[0] == 0xF0:
            if byte >= 0x80 and byte != 0xF7:
                raise ValueError("The control channel contains malformed MIDI system-exclusive data.")
            self.message.append(byte)
            if len(self.message) > _MAX_SYSEX_BYTES:
                raise ValueError("A decoded system-exclusive message exceeds the 1 MiB limit.")
            if byte == 0xF7:
                self.expected = len(self.message)
        else:
            if byte >= 0x80:
                raise ValueError("The control channel contains an interrupted MIDI message.")
            self.message.append(byte)
        if self.expected == len(self.message):
            self.writer.message(frame, self.message)
            self.message.clear()
            self.expected = None

    def finish(self):
        if self.message or self.byte_prefix is not None:
            raise ValueError("The Disklavier control channel ends inside a MIDI message.")
        if not self.writer.events:
            raise ValueError("No playable MIDI messages were found in the Disklavier control channel.")


class _Receiver:
    def __init__(self, reader):
        self.reader = reader
        self.previous_phase = None
        self.started = False
        self.ended = False
        self.symbols = 0
        self.silent_symbols = 0

    def block(self, samples, first_frame):
        symbols = samples.reshape(-1, SAMPLES_PER_SYMBOL).astype(np.float64)
        power = np.einsum("ij,ij->i", symbols, symbols)
        quadrature = symbols @ _CARRIER
        carrier_power = 2 * np.abs(quadrature) ** 2 / SAMPLES_PER_SYMBOL
        active = power > SAMPLES_PER_SYMBOL * 64 ** 2
        # Exact digital Y-modulation is almost a pure sinusoid within a symbol.
        # Reject music/noise instead of interpreting arbitrary audio as piano.
        bad = active & (carrier_power < power * 0.995)
        if np.any(bad) or np.any((~active) & (power > 0)):
            raise ValueError("The right channel is not a supported digital Disklavier control signal.")
        phases = np.angle(quadrature)
        for index, (is_active, phase) in enumerate(zip(active, phases)):
            frame = first_frame + (index + 1) * SAMPLES_PER_SYMBOL
            if not is_active:
                if self.started:
                    self.ended = True
                else:
                    self.silent_symbols += 1
                continue
            if self.ended:
                raise ValueError("The Disklavier control signal drops out before the end of the file.")
            self.symbols += 1
            first_active = not self.started
            if first_active:
                self.started = True
                # APS replaces its first 92 idle symbols with silence while
                # the differential oscillator continues advancing. Recover
                # that known phase so a long initial SysEx can begin at the
                # very first audible symbol without losing its status byte.
                self.previous_phase = (self.silent_symbols * 7 % 16) * math.pi / 8
                if self.silent_symbols == 92:
                    self.reader.acquired = True
            steps = (phase - self.previous_phase) / (math.pi / 8)
            rounded = round(steps)
            if abs(steps - rounded) > 0.15:
                raise ValueError("The Disklavier control channel has invalid differential phases.")
            self.previous_phase = phase
            nibble = int(_PHASE_TO_NIBBLE[rounded % 16])
            if first_active and self.silent_symbols == 92 and nibble < 8:
                # A reference encoder uses a different absolute carrier phase.
                # This cannot begin an APS message: discard only this unknown
                # differential and require a normal idle acquisition run.
                self.reader.acquired = False
                continue
            self.reader.nibble(nibble, frame)


def _new_stage(destination, suffix):
    descriptor, name = tempfile.mkstemp(prefix=".disklavier-decode-", suffix=suffix,
                                      dir=Path(destination).parent)
    os.close(descriptor)
    return name


def decode_disklavier_wav(source, midi_output, accompaniment_output, *, cancel=None, progress=None):
    """Recover MIDI and safe left-channel audio on the original WAV timeline.

    ``cancel`` is a callable or threading.Event; ``progress`` receives fractions
    from zero to one. PCM and MIDI are streamed with bounded memory. MIDI event
    times use completion of the transmitted message, preserving encoder preroll
    and serialization timing. No additional acoustic-piano delay is applied.

    Both destinations must be new files. Each is published exclusively after
    full validation. A failure during publication withdraws our first output;
    recovery copies are retained if publication had already occurred. Source
    files and outputs created by another writer are never replaced.
    """
    paths = [Path(path) for path in (source, midi_output, accompaniment_output)]
    if len({os.path.normcase(os.path.realpath(path)) for path in paths}) != 3:
        raise ValueError("The encoded WAV, decoded MIDI and accompaniment need different paths.")
    for destination in paths[1:]:
        if os.path.lexists(destination):
            raise FileExistsError(f"The decoding destination already exists: {destination}")
    _check_cancel(cancel)
    staged = []
    published = []
    try:
        if progress:
            progress(0.0)
        try:
            wav = wave.open(os.fspath(paths[0]), "rb")
        except (wave.Error, EOFError) as exc:
            raise ValueError("Open a 44.1 kHz, 16-bit stereo PCM Disklavier WAV file.") from exc
        with wav:
            if (wav.getframerate() != SAMPLE_RATE or wav.getsampwidth() != 2
                    or wav.getnchannels() != 2 or wav.getcomptype() != "NONE"):
                raise ValueError("Disklavier decoding requires 44.1 kHz, 16-bit stereo PCM WAV.")
            frames = wav.getnframes()
            if not frames or frames % SAMPLES_PER_SYMBOL:
                raise ValueError("The WAV is empty or lacks complete Disklavier control symbols.")
            staged.append(_new_stage(paths[1], ".mid"))
            staged.append(_new_stage(paths[2], ".wav"))
            with open(staged[0], "w+b") as midi_file, wave.open(staged[1], "wb") as audio:
                writer = _MidiWriter(midi_file)
                wire = _WireReader(writer)
                receiver = _Receiver(wire)
                audio.setparams((1, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
                for first in range(0, frames, _BLOCK_FRAMES):
                    _check_cancel(cancel)
                    count = min(_BLOCK_FRAMES, frames - first)
                    raw = wav.readframes(count)
                    if len(raw) != count * 4:
                        raise ValueError("The encoded WAV is truncated.")
                    stereo = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
                    receiver.block(stereo[:, 1], first)
                    audio.writeframesraw(stereo[:, 0].tobytes())
                    if progress:
                        progress(0.98 * (first + count) / frames)
                wire.finish()
                writer.finish(frames)
            _check_cancel(cancel)
        if progress:
            progress(1.0)
        _check_cancel(cancel)
        for stage, destination in zip(staged, paths[1:]):
            publish_new_file(stage, destination, check_cancel=lambda: _check_cancel(cancel),
                             published_callback=published.append)
        return {
            "format": "Yamaha Y-modulation",
            "midi_path": os.fspath(paths[1]), "accompaniment_path": os.fspath(paths[2]),
            "duration_seconds": frames / SAMPLE_RATE, "sample_rate": SAMPLE_RATE,
            "channels": 1, "midi_events": writer.events, "note_events": writer.notes,
            "system_events_omitted": writer.system_events,
            "control_symbols": receiver.symbols, "timeline": "original WAV",
        }
    except BaseException as exc:
        recovery = []
        for receipt in reversed(published):
            try:
                recovery.extend(receipt.remove_if_unchanged())
            except OSError as cleanup_error:
                recovery.extend(getattr(cleanup_error, "retained_paths", (receipt.destination,)))
        if recovery:
            message = f"{exc}\nDecoding recovery files were kept at: {', '.join(recovery)}"
            if isinstance(exc, DisklavierDecodingCancelled):
                raise DisklavierDecodingCancelled(message) from exc
            raise OSError(message) from exc
        raise
    finally:
        for stage in staged:
            try:
                os.unlink(stage)
            except FileNotFoundError:
                pass
