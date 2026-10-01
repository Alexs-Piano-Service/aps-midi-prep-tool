"""Read Smart PianoSoft reference fingerprints and align them to CD PCM.

The signal path follows the Mark IV sequencer's Start_Envelope (0x08076d42)
and its 256-frame decimation, rather than identifying recordings by duration.
No proprietary executable, music, or fingerprint is included here.
"""

from dataclasses import dataclass
import math
from pathlib import Path
import wave

import mido
import numpy as np


SAMPLE_RATE = 44100
DECIMATION = 256
FINGERPRINT_SIZE = 256
_STEP = DECIMATION / SAMPLE_RATE


class SynchronizationError(ValueError):
    """The MIDI and audio cannot be paired confidently."""


class SynchronizationCancelled(InterruptedError):
    pass


@dataclass(frozen=True)
class Fingerprint:
    reference_seconds: float
    samples: tuple[int, ...]
    flags: int = 0
    precision: int = 0


@dataclass(frozen=True)
class TimedMidiEvent:
    time_seconds: float
    data: bytes


@dataclass(frozen=True)
class SPSMetadata:
    cd_track: int | None
    head: Fingerprint
    tail: Fingerprint
    events: tuple[TimedMidiEvent, ...]
    duration_seconds: float


@dataclass(frozen=True)
class Alignment:
    offset_seconds: float
    time_scale: float
    head_correlation: float
    tail_correlation: float
    confidence: float

    def map_time(self, midi_seconds):
        return self.offset_seconds + self.time_scale * midi_seconds


def _cancelled(cancel):
    if cancel is not None:
        is_cancelled = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
        if is_cancelled:
            raise SynchronizationCancelled("Synchronization was cancelled.")


def _timecode(data):
    minute, second, frame, subframe = data
    if not (0 <= minute < 128 and 0 <= second < 60
            and 0 <= frame < 75 and 0 <= subframe < 128):
        raise SynchronizationError("Invalid Smart PianoSoft reference time.")
    # Mark IV seqSyncIsSysEx subtracts raw times >= 100 minutes from
    # 6,000 seconds. These are negative references, not a clock wrapping to
    # zero: a fingerprint can begin in the silence before a CD track starts.
    if minute >= 100:
        return -((minute - 100) * 60 + second + frame / 75 + subframe / 9600)
    return minute * 60 + second + frame / 75 + subframe / 9600


def parse_sps_midi(path):
    """Parse an SMF, preserving its complete tempo map in event timestamps.

    Yamaha's 43 71 05 01 message is CD control, not a track number. Track
    selection must be verified by audio fingerprints, so cd_track is None.
    """
    try:
        midi = mido.MidiFile(filename=str(path), clip=False)
        if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
            raise SynchronizationError("Smart PianoSoft requires a synchronous, PPQN MIDI file.")
        fingerprints = {}
        events = []
        elapsed = 0.0
        for message in midi:
            elapsed += message.time
            if not math.isfinite(elapsed) or elapsed < 0:
                raise SynchronizationError("Invalid MIDI event timing.")
            if message.is_meta:
                continue
            if message.type == "sysex":
                data = tuple(message.data)
                if data[:3] == (0x43, 0x71, 0x7B):
                    if len(data) < 4:
                        raise SynchronizationError("Truncated Smart PianoSoft synchronization message.")
                    kind = data[3]
                    if kind in (0, 2):
                        if len(data) != 266 or kind in fingerprints:
                            raise SynchronizationError("Missing, duplicate, or malformed Smart PianoSoft fingerprints.")
                        if data[8] >> 4 or data[9] > 15:
                            raise SynchronizationError("Unsupported Smart PianoSoft fingerprint format.")
                        samples = tuple(value if value < 64 else value - 128 for value in data[10:])
                        fingerprints[kind] = Fingerprint(_timecode(data[4:8]), samples, data[8], data[9])
                    elif kind == 1:
                        if len(data) != 8:
                            raise SynchronizationError("Malformed Smart PianoSoft time marker.")
                        _timecode(data[4:8])
                    else:
                        raise SynchronizationError("Unsupported Smart PianoSoft synchronization message.")
                    continue
                # CD control and the source's Smart PianoSoft header do not
                # belong in the outgoing piano stream. Preserve unrelated
                # Yamaha musical SysEx.
                if data[:3] in {(0x43, 0x71, 0x05), (0x43, 0x71, 0x7E)}:
                    continue
            events.append(TimedMidiEvent(elapsed, bytes(message.bytes())))
    except (OSError, EOFError, ValueError, KeyError, IndexError) as exc:
        if isinstance(exc, SynchronizationError):
            raise
        raise SynchronizationError(f"Could not read Smart PianoSoft MIDI: {exc}") from exc
    if set(fingerprints) != {0, 2}:
        raise SynchronizationError("This MIDI lacks the two Smart PianoSoft CD fingerprints.")
    head, tail = fingerprints[0], fingerprints[2]
    if tail.reference_seconds - head.reference_seconds < 1:
        raise SynchronizationError("Smart PianoSoft reference times are too close or out of order.")
    if not events:
        raise SynchronizationError("The MIDI contains no musical events.")
    return SPSMetadata(None, head, tail, tuple(events), elapsed)


def _one_pole(signal, coefficient):
    """Causal first-order filter, using convolution with a bounded error tail."""
    count = len(signal)
    if not count:
        return np.empty(0, dtype=np.float64)
    taps = min(count, math.ceil(math.log(1e-12) / math.log(coefficient)))
    size = 1 << (count + taps - 2).bit_length()
    impulse = np.power(coefficient, np.arange(taps, dtype=np.float64))
    return np.fft.irfft(np.fft.rfft(signal, size) * np.fft.rfft(impulse, size), size)[:count]


def _envelope(pcm, cancel=None):
    """Mark IV envelope, sampled at 44100/256 Hz with zero initial state.

    Original code stores intermediate float32 states. Float64 convolution
    avoids slow sample loops and the rounding difference is below the 7-bit
    fingerprint quantization. Correlation intentionally tolerates that error.
    """
    _cancelled(cancel)
    mono = np.asarray(pcm, dtype=np.float64).mean(axis=1)
    highpass = _one_pole(np.r_[mono[:1], np.diff(mono)], 0.996444)
    rectified = np.abs(highpass)
    difference = rectified.copy()
    difference[4410:] -= rectified[:-4410]
    _cancelled(cancel)
    average = _one_pole(difference, 0.999858) * 0.000226757
    lowpass = _one_pole(average, 0.996444) * 0.00355556
    _cancelled(cancel)
    return np.r_[0.0, lowpass[:-1]][::DECIMATION]


def _match(audio, fingerprint, radius, cancel):
    reference = fingerprint.reference_seconds
    # Earlier candidates would contain only pre-track silence and cannot
    # identify music. This also bounds padding independently of the timecode.
    search_start = max(reference - radius, -FINGERPRINT_SIZE * _STEP)
    # Warm the filters before the first candidate, retaining absolute phase.
    start = math.floor((search_start - 1.0) * SAMPLE_RATE / DECIMATION) * DECIMATION
    end = min(audio.getnframes(), math.ceil((reference + radius + FINGERPRINT_SIZE * _STEP) * SAMPLE_RATE))
    # No reference wholly outside the available track can establish a match.
    # Check before allocating pre-track padding, including malformed times
    # representing many minutes before the beginning of the disc.
    if end <= 0 or search_start >= audio.getnframes() / SAMPLE_RATE:
        raise SynchronizationError("The CD reference falls outside this audio track.")
    if end - start < FINGERPRINT_SIZE * DECIMATION:
        raise SynchronizationError("The audio is too short for this Smart PianoSoft reference.")
    _cancelled(cancel)
    read_start = max(0, start)
    audio.setpos(read_start)
    raw = audio.readframes(end - read_start)
    if len(raw) != (end - read_start) * 4:
        raise SynchronizationError("The audio file is truncated.")
    pcm = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
    if start < 0:
        # The native player feeds zero samples before the WAV data begins.
        # Preserve those samples in the candidate clock instead of clamping
        # a negative fingerprint to audio time zero.
        pcm = np.concatenate((np.zeros((-start, 2), dtype=np.int16), pcm))
    envelope = _envelope(pcm, cancel)
    target = np.asarray(fingerprint.samples, dtype=np.float64)
    if target.shape != (FINGERPRINT_SIZE,) or not np.all(np.isfinite(target)) or np.linalg.norm(target) < 1:
        raise SynchronizationError("The Smart PianoSoft fingerprint is empty or invalid.")
    numerator = np.correlate(envelope, target, mode="valid")
    energy = np.convolve(envelope * envelope, np.ones(FINGERPRINT_SIZE), mode="valid")
    denominator = np.sqrt(np.maximum(energy, 0) * np.dot(target, target))
    scores = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1e-9)
    times = start / SAMPLE_RATE + np.arange(len(scores)) * _STEP
    valid = (times >= search_start) & (times <= reference + radius)
    scores[~valid] = -1
    if not np.any(valid):
        raise SynchronizationError("The CD reference falls outside this audio track.")
    peak = int(np.argmax(scores))
    correlation = float(np.clip(scores[peak], -1, 1))
    if correlation < 0.95:
        raise SynchronizationError(f"The audio does not match the Smart PianoSoft CD reference (correlation {correlation:.3f}).")
    rivals = scores.copy()
    exclusion = math.ceil(0.25 / _STEP)
    rivals[max(0, peak - exclusion):peak + exclusion + 1] = -1
    if float(np.max(rivals)) >= correlation - 0.025:
        raise SynchronizationError("The audio fingerprint has ambiguous matches; synchronization was not accepted.")
    # Refine within one decimated sample; never extrapolate beyond the search.
    refinement = 0.0
    if 0 < peak < len(scores) - 1 and valid[peak - 1] and valid[peak + 1]:
        curvature = scores[peak - 1] - 2 * scores[peak] + scores[peak + 1]
        if curvature < -1e-12:
            refinement = float(np.clip(0.5 * (scores[peak - 1] - scores[peak + 1]) / curvature, -0.5, 0.5))
    return float(times[peak] + refinement * _STEP), correlation


def synchronize(metadata, audio_path, cancel=None, progress=None):
    """Match both fingerprints, then map MIDI seconds to CD audio seconds.

    Accept only uncompressed stereo 16-bit 44.1 kHz WAV. Callers decode other
    formats to this canonical PCM first. Cancellation accepts threading.Event
    or a predicate; progress receives a float from zero through one.
    """
    _cancelled(cancel)
    if progress:
        progress(0.0)
    if not math.isfinite(metadata.head.reference_seconds) or not math.isfinite(metadata.tail.reference_seconds):
        raise SynchronizationError("Invalid Smart PianoSoft reference times.")
    span = metadata.tail.reference_seconds - metadata.head.reference_seconds
    if span < 1:
        raise SynchronizationError("Smart PianoSoft reference times are invalid.")
    try:
        with wave.open(str(Path(audio_path)), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (2, 2, SAMPLE_RATE, "NONE"):
                raise SynchronizationError("Synchronization needs stereo, 16-bit, 44.1 kHz PCM WAV audio.")
            head_time, head_score = _match(audio, metadata.head, 4.0, cancel)
            if progress:
                progress(0.5)
            radius = min(6.0, metadata.tail.reference_seconds * (0.025 if metadata.tail.flags & 15 else 0.007))
            tail_time, tail_score = _match(audio, metadata.tail, max(radius, 0.25), cancel)
    except (OSError, EOFError, wave.Error) as exc:
        raise SynchronizationError(f"Could not read CD audio: {exc}") from exc
    scale = (tail_time - head_time) / span
    if not 0.975 <= scale <= 1.025:
        raise SynchronizationError("The two CD references disagree on playback speed.")
    result = Alignment(head_time - scale * metadata.head.reference_seconds, scale, head_score, tail_score, min(head_score, tail_score))
    _cancelled(cancel)
    if progress:
        progress(1.0)
    return result
