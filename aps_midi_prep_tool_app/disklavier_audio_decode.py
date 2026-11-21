"""Conservative receiver for Yamaha's phase-encoded accompaniment audio.

This is the inverse of the public Y-modulation format described in Yamaha
US20030061931A1, not an audio-to-MIDI transcription. Both channels and all
fourteen possible symbol boundaries are examined. A complete, unambiguous
control stream is required before a clean companion waveform is published.

Lossy audio has no recoverable checksum. In particular, MP3 support means
that a file can be *examined*, not that damaged control data can be repaired.
Weak, inconsistent, interrupted or incomplete streams are rejected, leaving
the caller's original backup available. No hardware compatibility is claimed.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import wave

import numpy as np

from .audio_tools import decode_mp3
from .helpers.atomic_file import publish_new_file
from .smart_pianosoft_sync import TimedMidiEvent


SAMPLE_RATE = 44100
SAMPLES_PER_SYMBOL = 14
_ACQUIRE_FRAMES = 8 * SAMPLE_RATE
_BLOCK_SYMBOLS = 8192
# Independently read from patent Figure 14, indexed by differential phase.
_PHASE_TO_DATA = np.asarray((8, 10, 14, 6, 2, 3, 7, 15, 11, 9, 13, 5, 1, 0, 4, 12))
_PHASE_QUANTUM = np.pi / 8
_CARRIER = np.exp(2j * np.pi * np.arange(SAMPLES_PER_SYMBOL) / 7)
_INNER_SAMPLES = np.arange(1, 13)
_INNER_BASIS = np.column_stack((np.cos(2 * np.pi * _INNER_SAMPLES / 7),
                                np.sin(2 * np.pi * _INNER_SAMPLES / 7)))
_INNER_INVERSE = np.linalg.pinv(_INNER_BASIS).T
_MAX_SYSEX_BYTES = 65536


class DisklavierDecodeError(ValueError):
    """Recognized control audio could not be decoded safely."""


class NotEncodedAudioError(DisklavierDecodeError):
    """No convincing Yamaha control carrier was found on either channel."""


class DisklavierDecodingCancelled(InterruptedError):
    """The caller cancelled before publication of the extracted waveform."""


@dataclass(frozen=True)
class DecodedDisklavierAudio:
    events: tuple[TimedMidiEvent, ...]
    music_path: Path
    control_channel: str
    source_duration_seconds: float
    report: dict


def _check_cancel(cancel):
    if cancel is not None:
        stopped = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
        if stopped:
            raise DisklavierDecodingCancelled("Disklavier audio decoding cancelled.")


def _identity(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _measure(blocks):
    """Return phase, amplitude and explained carrier energy for PCM blocks."""
    blocks = blocks.astype(np.float64)
    quadrature = blocks @ _CARRIER
    amplitude = np.abs(quadrature) * (2 / SAMPLES_PER_SYMBOL)
    power = np.sum(blocks * blocks, axis=1)
    fit = 2 * np.abs(quadrature) ** 2 / (SAMPLES_PER_SYMBOL * power + 1e-20)
    return np.angle(quadrature), amplitude, fit


def _phase_symbols(phases):
    differences = np.diff(phases) / _PHASE_QUANTUM
    nearest = np.rint(differences)
    return (_PHASE_TO_DATA[nearest.astype(np.int64) % 16].astype(np.uint8),
            np.abs(differences - nearest))


def _acquire(samples, cancel):
    candidates = []
    for channel in range(2):
        for offset in range(SAMPLES_PER_SYMBOL):
            _check_cancel(cancel)
            count = (len(samples) - offset) // SAMPLES_PER_SYMBOL
            if count < 64:
                continue
            blocks = samples[offset:offset + count * SAMPLES_PER_SYMBOL,
                             channel].reshape(-1, SAMPLES_PER_SYMBOL)
            phases, amplitude, fit = _measure(blocks)
            # Quiet input and low-level leakage are not a control channel.
            level = float(np.percentile(amplitude, 95))
            if level < 100:
                continue
            threshold = max(32.0, level * 0.15)
            active = amplitude > threshold
            adjacent = active[1:] & active[:-1]
            if np.count_nonzero(adjacent) < 64:
                continue
            symbols, residual = _phase_symbols(phases)
            carrier_fit = float(np.median(fit[active]))
            phase_error = float(np.mean(residual[adjacent]))
            idle_fraction = float(np.mean(symbols[adjacent] == 15))
            if carrier_fit < 0.85 or phase_error > 0.12 or idle_fraction < 0.1:
                continue
            score = carrier_fit - phase_error
            candidates.append((score, channel, offset, threshold))
    if not candidates:
        raise NotEncodedAudioError("No reliable Yamaha control channel was found in this audio.")
    candidates.sort(reverse=True)
    best = candidates[0]
    if any(candidate[1] != best[1] for candidate in candidates[1:]):
        raise DisklavierDecodeError("Both audio channels contain possible control data; decoding is ambiguous.")
    if len(candidates) > 1 and best[0] - candidates[1][0] < 0.05:
        raise DisklavierDecodeError("The control signal's symbol boundary is ambiguous.")
    return best[1:]


def _read_symbols(reader, channel, offset, threshold, cancel, report):
    reader.setpos(offset)
    symbols, flags = [], []
    previous_phase = previous_inner = None
    previous_active = False
    total = (reader.getnframes() - offset) // SAMPLES_PER_SYMBOL
    completed = 0
    minimum_fit = 1.0
    maximum_error = 0.0
    while completed < total:
        _check_cancel(cancel)
        count = min(_BLOCK_SYMBOLS, total - completed)
        raw = reader.readframes(count * SAMPLES_PER_SYMBOL)
        if len(raw) != count * SAMPLES_PER_SYMBOL * 4:
            raise DisklavierDecodeError("The encoded audio file is truncated.")
        blocks = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)[:, channel].reshape(
            -1, SAMPLES_PER_SYMBOL)
        phases, amplitude, fit = _measure(blocks)
        inner = blocks[:, 1:13].astype(np.float64) @ _INNER_INVERSE
        inner_phases = np.arctan2(inner[:, 1], inner[:, 0])
        active = amplitude > threshold
        values, error = _phase_symbols(np.r_[phases[0] if previous_phase is None else previous_phase,
                                              phases])
        inner_values, _ = _phase_symbols(np.r_[inner_phases[0] if previous_inner is None else previous_inner,
                                                  inner_phases])
        adjacent = active & np.r_[previous_active, active[:-1]]
        # Two integration windows must agree. Do not repair a questionable
        # symbol by guessing which of two MIDI messages looks more plausible.
        reliable = adjacent & (fit >= 0.82) & (error <= 0.45) & (values == inner_values)
        symbols.append(values)
        flags.append(active.astype(np.uint8) | (reliable.astype(np.uint8) << 1))
        if np.any(adjacent):
            minimum_fit = min(minimum_fit, float(np.min(fit[adjacent])))
            maximum_error = max(maximum_error, float(np.max(error[adjacent])))
        previous_phase, previous_inner = phases[-1], inner_phases[-1]
        previous_active = bool(active[-1])
        completed += count
        report(0.15 + 0.55 * completed / total)
    values, quality = np.concatenate(symbols), np.concatenate(flags)
    active_indices = np.flatnonzero(quality & 1)
    if len(active_indices) < 32:
        raise DisklavierDecodeError("The control signal is too short to validate.")
    # Discard only acquisition/termination transients, then require intact
    # idle symbols on both ends. No musical message may be cut by trimming.
    first, last = int(active_indices[0]) + 2, int(active_indices[-1]) - 1
    if last - first < 16 or np.any(values[first:first + 8] != 15) or np.any(values[last - 8:last] != 15):
        raise DisklavierDecodeError("The control signal has no intact acquisition or ending idle interval.")
    if np.any(quality[first:last] != 3):
        raise DisklavierDecodeError("The control signal is damaged or uncertain; no MIDI was extracted.")
    return values[first:last], first, minimum_fit, maximum_error


def _parse_messages(symbols, first_symbol, frame_offset, cancel):
    """Decode quasi-MIDI framing, including running status and realtime bytes."""
    events = []
    index = 0
    running_status = None
    channel_sizes = {8: 2, 9: 2, 10: 2, 11: 2, 12: 1, 13: 1, 14: 2}
    system_sizes = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0}
    realtime = {0xF8, 0xFA, 0xFB, 0xFC, 0xFE, 0xFF}

    def byte():
        nonlocal index
        if index + 1 >= len(symbols):
            raise DisklavierDecodeError("The control stream ends inside a MIDI byte.")
        high, low = int(symbols[index]), int(symbols[index + 1])
        index += 2
        if high == 15:
            raise DisklavierDecodeError("Unexpected idle symbol inside a MIDI message.")
        if high == 12:
            if low != 4:
                return 0xF0 | low
            if index >= len(symbols):
                raise DisklavierDecodeError("The control stream ends inside a MIDI escape.")
            low = int(symbols[index])
            index += 1
        return (high << 4) | low

    def emit(message):
        seconds = (frame_offset + (first_symbol + index) * SAMPLES_PER_SYMBOL) / SAMPLE_RATE
        events.append(TimedMidiEvent(seconds, bytes(message)))

    while index < len(symbols):
        if index % 1024 == 0:
            _check_cancel(cancel)
        if symbols[index] == 15:
            index += 1
            continue
        status = byte()
        if status in realtime:
            emit((status,))
            continue
        if status == 0xF0:
            running_status = None
            message = [status]
            while True:
                _check_cancel(cancel)
                value = byte()
                if value in realtime:
                    emit((value,))
                    continue
                if value == 0xF7:
                    message.append(value)
                    emit(message)
                    break
                if value >= 0x80 or len(message) >= _MAX_SYSEX_BYTES:
                    raise DisklavierDecodeError("The control stream contains invalid or oversized MIDI SysEx.")
                message.append(value)
            continue
        if status < 0x80:
            if running_status is None:
                raise DisklavierDecodeError("The control stream contains MIDI data without a status byte.")
            message = [running_status, status]
            needed = channel_sizes[running_status >> 4] - 1
        elif status < 0xF0:
            running_status = status
            message = [status]
            needed = channel_sizes[status >> 4]
        elif status in system_sizes:
            running_status = None
            message = [status]
            needed = system_sizes[status]
        else:
            raise DisklavierDecodeError("The control stream contains an unsupported MIDI status byte.")
        while needed:
            value = byte()
            if value in realtime:
                emit((value,))
                continue
            if value >= 0x80:
                raise DisklavierDecodeError("The control stream contains an interrupted MIDI message.")
            message.append(value)
            needed -= 1
        emit(message)
    _check_cancel(cancel)
    active_notes = {}
    note_ons = note_offs = 0
    for event in events:
        data = event.data
        kind, channel = data[0] >> 4, data[0] & 15
        if kind == 9 and data[2] > 0:
            key = (channel, data[1])
            active_notes[key] = active_notes.get(key, 0) + 1
            note_ons += 1
        elif kind == 8 or (kind == 9 and data[2] == 0):
            key = (channel, data[1])
            if active_notes.get(key, 0) > 1:
                active_notes[key] -= 1
            else:
                active_notes.pop(key, None)
            note_offs += 1
        elif kind == 11 and data[1] in (120, 123, 124, 125, 126, 127):
            active_notes = {key: count for key, count in active_notes.items() if key[0] != channel}
            note_offs += 1
    if not note_ons or not note_offs:
        raise DisklavierDecodeError("The control stream does not contain a complete piano performance.")
    if active_notes:
        raise DisklavierDecodeError("Decoded MIDI ends with active notes; the control stream may be incomplete.")
    return tuple(events)


def decode_disklavier_audio(source_path, music_wav_path, *, cancel=None, progress=None):
    """Extract complete timed MIDI plus the other channel's clean mono music.

    Input is stereo PCM16/44.1-kHz WAV or MP3 decoded by the bundled LAME.
    The music retains its original timeline, including any leading silence;
    event timestamps mark completion of their encoded MIDI message on that
    same timeline. No guessed piano/mechanical delay is added or removed.
    ``progress`` receives fractions from zero to one. An existing destination
    is never replaced, and errors/cancellation leave no partial waveform.
    """
    source, destination = Path(source_path), Path(music_wav_path)
    if os.path.lexists(destination):
        raise FileExistsError(f"Output already exists: {destination}")
    _check_cancel(cancel)
    source_identity = _identity(source)

    def report(fraction):
        _check_cancel(cancel)
        if progress is not None:
            progress(fraction)
        _check_cancel(cancel)

    report(0.0)
    with tempfile.TemporaryDirectory(prefix=".aps-decode-audio-", dir=destination.parent) as temporary:
        folder = Path(temporary)
        pcm = source
        if source.suffix.lower() == ".mp3":
            pcm = folder / "decoded.wav"
            decode_mp3(source, pcm, cancel=cancel)
        elif source.suffix.lower() != ".wav":
            raise DisklavierDecodeError("Only WAV and MP3 encoded audio can be split.")
        report(0.05)
        try:
            reader = wave.open(str(pcm), "rb")
        except (wave.Error, EOFError) as exc:
            raise DisklavierDecodeError("The audio is not a readable PCM WAV file.") from exc
        with reader:
            if reader.getnchannels() != 2:
                raise NotEncodedAudioError("Separate music and control require a stereo audio file.")
            if (reader.getsampwidth() != 2 or reader.getframerate() != SAMPLE_RATE
                    or reader.getcomptype() != "NONE"):
                raise NotEncodedAudioError(
                    "Unsupported audio format for control decoding: "
                    "44.1 kHz, 16-bit stereo PCM is required.")
            frame_count = reader.getnframes()
            if frame_count < 64 * SAMPLES_PER_SYMBOL:
                raise NotEncodedAudioError("The audio is too short to contain a reliable control stream.")
            # Some transferred files have a long quiet introduction before
            # their control carrier begins. Search sequentially in bounded
            # windows instead of classifying the entire file from its intro.
            for first_frame in range(0, frame_count, _ACQUIRE_FRAMES):
                _check_cancel(cancel)
                requested = min(frame_count - first_frame, _ACQUIRE_FRAMES)
                raw = reader.readframes(requested)
                if len(raw) != requested * 4:
                    raise DisklavierDecodeError("The encoded audio file is truncated.")
                samples = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
                report(0.05 + 0.1 * (first_frame + requested) / frame_count)
                try:
                    channel, local_offset, threshold = _acquire(samples, cancel)
                except NotEncodedAudioError:
                    continue
                # _read_symbols starts from the source's beginning, so the
                # symbol boundary must be relative to the complete timeline.
                offset = (first_frame + local_offset) % SAMPLES_PER_SYMBOL
                break
            else:
                raise NotEncodedAudioError(
                    "No reliable Yamaha control channel was found in this audio.")
            values, first, minimum_fit, maximum_error = _read_symbols(
                reader, channel, offset, threshold, cancel, report)
            events = _parse_messages(values, first, offset, cancel)
            report(0.75)
            staged = folder / "music.wav"
            reader.rewind()
            with wave.open(str(staged), "wb") as writer:
                writer.setparams((1, 2, SAMPLE_RATE, frame_count, "NONE", "not compressed"))
                copied = 0
                while copied < frame_count:
                    _check_cancel(cancel)
                    count = min(65536, frame_count - copied)
                    raw = reader.readframes(count)
                    if len(raw) != count * 4:
                        raise DisklavierDecodeError("The encoded audio file is truncated.")
                    stereo = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
                    writer.writeframesraw(stereo[:, 1 - channel].tobytes())
                    copied += count
                    report(0.75 + 0.2 * copied / frame_count)
        if _identity(source) != source_identity:
            raise DisklavierDecodeError("The encoded audio source changed during decoding.")
        report(1.0)
        publish_new_file(staged, destination, check_cancel=lambda: _check_cancel(cancel))
    control_channel = "left" if channel == 0 else "right"
    metadata = {
        "format": "Yamaha Y-modulation decoded to MIDI and mono music",
        "control_channel": control_channel,
        "music_channel": "right" if channel == 0 else "left",
        "sample_rate": SAMPLE_RATE, "midi_events": len(events),
        "duration_seconds": frame_count / SAMPLE_RATE,
        "symbol_boundary_frames": offset,
        "minimum_carrier_fit": minimum_fit,
        "maximum_phase_error_symbols": maximum_error,
        "midi_advance_seconds": 0.0,
        "hardware_verified": False,
    }
    return DecodedDisklavierAudio(events, destination, control_channel,
                                 frame_count / SAMPLE_RATE, metadata)
