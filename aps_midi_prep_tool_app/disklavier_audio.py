"""Experimental Yamaha Y-modulation audio encoder.

The wire format is independently implemented from Yamaha US20030061931A1,
figures 5, 7 and 14--16 (6300 Hz carrier, 3150 symbols/s, differential phases
and quasi-MIDI escaping). No Yamaha or third-party executable code is used.

Output is a CD-quality stereo WAV: accompaniment on the left, control signal
on the right. It is intended for CD-DA or a configured analog MIDI input.
Ordinary WAV import on a piano is not a verified playback route. Neither a
software round trip nor patent conformance establishes hardware compatibility.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile
import wave

import numpy as np

from .helpers.atomic_file import publish_new_file


SAMPLE_RATE = 44100
SYMBOL_RATE = 3150
SAMPLES_PER_SYMBOL = 14
CARRIER_HZ = 6300
CONTROL_AMPLITUDE = 8192
# Independent reference-encoder measurements: 92 silent symbols precede the
# carrier. Matching its level and acquisition leader also matters to Yamaha's
# decoder timestamp estimation, not merely to successful symbol recovery.
CONTROL_LEADER_FRAMES = 92 * SAMPLES_PER_SYMBOL
# Figure 14, indexed by the transmitted nibble; units are pi / 8 radians.
PHASE_STEPS = (13, 12, 4, 5, 14, 11, 3, 6, 0, 9, 1, 8, 15, 10, 2, 7)
PIANO_LATENCY_SECONDS = 0.5
AUDIO_PREROLL_SECONDS = 0.5
MAX_QUEUE_SECONDS = 0.05
_BLOCK_FRAMES = 14 * 4096


class DisklavierEncodingCancelled(RuntimeError):
    """The caller canceled an encoding before publication."""


def _check_cancel(cancel):
    if cancel is not None:
        stopped = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
        if stopped:
            raise DisklavierEncodingCancelled("Disklavier audio encoding canceled.")


def read_midi_events(path):
    """Read (absolute seconds, complete wire message) pairs using the tempo map."""
    import mido

    midi = mido.MidiFile(path)
    if midi.type == 2:
        raise ValueError("Independent MIDI sequences cannot share one audio timeline.")
    elapsed = 0.0
    events = []
    for message in midi:
        elapsed += message.time
        if not message.is_meta:
            events.append((elapsed, bytes(message.bytes())))
    return events


def is_pianosmart_message(message):
    """Remove source CD synchronization/control headers, retaining musical SysEx.

    A reference encoder removes both 7B and 7E from Smart PianoSoft sources;
    7E includes the source playback header, not just human-readable titles.
    """
    return (
        len(message) >= 5
        and message[:3] == b"\xf0\x43\x71"
        and message[3] in (0x05, 0x7B, 0x7E)
    )


def midi_message_nibbles(message):
    """Encode one complete MIDI wire message into Yamaha's quasi-MIDI symbols.

    Idle F symbols occur only BETWEEN complete messages, never between data
    bytes, where an F nibble is ordinary data. Running status is not required.
    """
    message = bytes(message)
    if not message or message[0] < 0x80:
        raise ValueError("Each event must contain a complete MIDI wire message.")
    status = message[0]
    if status == 0xF0:
        if len(message) < 2 or message[-1] != 0xF7:
            raise ValueError("MIDI system-exclusive message is unterminated.")
        if any(value >= 0x80 for value in message[1:-1]):
            raise ValueError("MIDI system-exclusive data must be seven-bit bytes.")
    else:
        if status < 0xF0:
            expected = 2 if status >> 4 in (0xC, 0xD) else 3
        else:
            expected = {0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1,
                        0xF8: 1, 0xFA: 1, 0xFB: 1, 0xFC: 1,
                        0xFE: 1, 0xFF: 1}.get(status)
        if expected is None or len(message) != expected:
            raise ValueError("MIDI event has an unsupported status or invalid length.")
        if any(value >= 0x80 for value in message[1:]):
            raise ValueError("MIDI event contains an unexpected status byte.")

    symbols = []
    for value in message:
        high, low = value >> 4, value & 15
        if high == 0xC:
            symbols.extend((0xC, 4, low))
        elif high == 0xF:
            symbols.extend((0xC, low))
        else:
            symbols.extend((high, low))
    return bytes(symbols)


def _schedule(events, offset, scale, cancel):
    prepared = []
    skipped = 0
    previous = -math.inf
    for index, (seconds, message) in enumerate(events):
        if index % 256 == 0:
            _check_cancel(cancel)
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0 or seconds < previous:
            raise ValueError("MIDI events must have finite, increasing absolute times.")
        previous = seconds
        message = bytes(message)
        symbols = midi_message_nibbles(message)
        if is_pianosmart_message(message):
            skipped += 1
            continue
        prepared.append((seconds * scale + offset, symbols))
    if not prepared:
        raise ValueError("There are no playable MIDI events to encode.")

    # Plus Audio delays accompaniment and piano together during playback.
    # Unlike an external MIDI-IN stream, its encoded MIDI must therefore share
    # the accompaniment timeline. A reference encoder places both at +500 ms.
    preroll_symbols = math.ceil(
        (AUDIO_PREROLL_SECONDS + max(0.0, -prepared[0][0])) * SYMBOL_RATE
    )
    audio_start_frame = preroll_symbols * SAMPLES_PER_SYMBOL
    schedule = []
    next_symbol = 0
    max_lateness = 0.0
    for seconds, symbols in prepared:
        completion = round(seconds * SYMBOL_RATE + preroll_symbols)
        first = max(completion - len(symbols), next_symbol)
        end = first + len(symbols)
        lateness = max(0.0, (end - completion) / SYMBOL_RATE)
        if lateness > MAX_QUEUE_SECONDS:
            raise ValueError(
                "MIDI data exceeds the Disklavier audio channel capacity "
                "(more than 50 ms of timing displacement)."
            )
        max_lateness = max(max_lateness, lateness)
        schedule.append((first, symbols))
        next_symbol = end
    return schedule, audio_start_frame, max_lateness, skipped


def modulate_nibbles(nibbles, initial_phase=0):
    """Produce PCM16 Y-modulation plus final differential phase.

    Each symbol is exactly two carrier periods. The patent's quadrature
    summer is I*cos(carrier) + Q*sin(carrier), i.e. cos(carrier - phase).
    Keeping the carrier clock exact avoids cumulative song-length drift.
    """
    values = np.asarray(list(nibbles) if not isinstance(nibbles, np.ndarray)
                        else nibbles, dtype=np.int64)
    if values.ndim != 1 or np.any(values < 0) or np.any(values > 15):
        raise ValueError("Yamaha symbols must be nibbles in the range 0 to 15.")
    if not len(values):
        return np.empty(0, dtype="<i2"), int(initial_phase) % 16
    phases = (np.cumsum(np.asarray(PHASE_STEPS)[values]) + initial_phase) % 16
    sample_phase = np.arange(SAMPLES_PER_SYMBOL) * (2 * np.pi / 7)
    table = np.rint(CONTROL_AMPLITUDE * np.cos(
        sample_phase[None, :] - np.arange(16)[:, None] * np.pi / 8
    )).astype("<i2")
    return table[phases].reshape(-1), int(phases[-1])


def encode_disklavier_wav(
    events, audio_path, output_path, *, midi_offset_seconds=0.0,
    midi_time_scale=1.0, cancel=None, progress=None,
):
    """Stream paired PCM16 audio and MIDI to an experimental stereo WAV.

    ``events`` contains ordered (seconds, full MIDI wire bytes) pairs. The
    alignment maps MIDI time to CD time as ``seconds * scale + offset``.
    ``cancel`` is a zero-argument callable or threading.Event. ``progress``
    receives fractions from 0 to 1. Inputs and existing output are preserved;
    canceled/failed output is removed. Publication is atomic where the
    destination filesystem supports it, and otherwise uses exclusive creation.
    """
    offset, scale = float(midi_offset_seconds), float(midi_time_scale)
    if not math.isfinite(offset) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Audio alignment must use a finite offset and positive scale.")
    destination = Path(output_path)
    if os.path.lexists(destination) or os.path.abspath(audio_path) == os.path.abspath(output_path):
        raise FileExistsError("The encoded audio destination already exists.")
    _check_cancel(cancel)
    schedule, audio_start, lateness, skipped = _schedule(events, offset, scale, cancel)
    staging = None
    try:
        with wave.open(os.fspath(audio_path), "rb") as source:
            channels = source.getnchannels()
            if (source.getframerate() != SAMPLE_RATE or source.getsampwidth() != 2
                    or channels not in (1, 2) or source.getcomptype() != "NONE"):
                raise ValueError("Accompaniment must be 44.1 kHz, 16-bit mono or stereo PCM WAV.")
            if source.getnframes() == 0:
                raise ValueError("Accompaniment WAV contains no audio frames.")
            audio_end = audio_start + source.getnframes()
            midi_end = (schedule[-1][0] + len(schedule[-1][1])) * SAMPLES_PER_SYMBOL
            frame_count = max(audio_end, midi_end + SAMPLE_RATE)
            frame_count = math.ceil(frame_count / SAMPLES_PER_SYMBOL) * SAMPLES_PER_SYMBOL
            if frame_count * 4 > 0xFFFFFFFF - 36:
                raise ValueError("The encoded song exceeds the WAV format's 4 GiB limit.")
            descriptor, staging = tempfile.mkstemp(
                prefix=".disklavier-", suffix=".wav", dir=destination.parent,
            )
            os.close(descriptor)
            phase, event_index = 0, 0
            if progress:
                progress(0.0)
            with wave.open(staging, "wb") as output:
                output.setparams((2, 2, SAMPLE_RATE, frame_count, "NONE", "not compressed"))
                for frame in range(0, frame_count, _BLOCK_FRAMES):
                    _check_cancel(cancel)
                    count = min(_BLOCK_FRAMES, frame_count - frame)
                    first_symbol = frame // SAMPLES_PER_SYMBOL
                    symbol_count = count // SAMPLES_PER_SYMBOL
                    symbols = np.full(symbol_count, 15, dtype=np.uint8)
                    while event_index < len(schedule):
                        at, values = schedule[event_index]
                        if at >= first_symbol + symbol_count:
                            break
                        left = max(at, first_symbol)
                        right = min(at + len(values), first_symbol + symbol_count)
                        if right > left:
                            symbols[left - first_symbol:right - first_symbol] = np.frombuffer(
                                values, dtype=np.uint8,
                            )[left - at:right - at]
                        if at + len(values) > first_symbol + symbol_count:
                            break
                        event_index += 1
                    control, phase = modulate_nibbles(symbols, phase)
                    stereo = np.zeros((count, 2), dtype="<i2")
                    stereo[:, 1] = control
                    if frame < CONTROL_LEADER_FRAMES:
                        stereo[:min(count, CONTROL_LEADER_FRAMES - frame), 1] = 0
                    left, right = max(frame, audio_start), min(frame + count, audio_end)
                    if right > left:
                        raw = source.readframes(right - left)
                        if len(raw) != (right - left) * channels * 2:
                            raise ValueError("Accompaniment WAV is truncated.")
                        samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
                        # Widen first: mixing two loud channels must never overflow.
                        mono = samples[:, 0] if channels == 1 else (
                            samples[:, 0].astype(np.int32) + samples[:, 1].astype(np.int32)
                        ) // 2
                        stereo[left - frame:right - frame, 0] = mono
                    output.writeframesraw(stereo.tobytes())
                    if progress:
                        progress((frame + count) / frame_count)
            _check_cancel(cancel)
        publish_new_file(staging, destination, check_cancel=lambda: _check_cancel(cancel))
        return {
            "format": "Yamaha Y-modulation (experimental)",
            "sample_rate": SAMPLE_RATE, "sample_width": 2, "channels": 2,
            "music_channel": "left", "control_channel": "right",
            "duration_seconds": frame_count / SAMPLE_RATE,
            "audio_preroll_seconds": audio_start / SAMPLE_RATE,
            "piano_latency_seconds": PIANO_LATENCY_SECONDS,
            "midi_advance_seconds": 0.0,
            "control_leader_seconds": CONTROL_LEADER_FRAMES / SAMPLE_RATE,
            "control_amplitude": CONTROL_AMPLITUDE,
            "midi_offset_seconds": offset, "midi_time_scale": scale,
            "midi_events": len(schedule), "pianosmart_messages_removed": skipped,
            "max_serialization_delay_seconds": lateness,
            "hardware_verified": False,
        }
    finally:
        if staging is not None:
            try:
                os.unlink(staging)
            except OSError:
                pass
