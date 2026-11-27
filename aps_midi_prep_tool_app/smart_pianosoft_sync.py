"""Read Smart PianoSoft reference fingerprints and align them to CD PCM.

The signal path follows the Mark IV sequencer's Start_Envelope (0x08076d42)
and its 256-frame decimation, rather than identifying recordings by duration.
No proprietary executable, music, or fingerprint is included here.
"""

from dataclasses import dataclass
import math
from pathlib import Path
import wave

import numpy as np


# Re-export the metadata API for existing audio callers. Recognition imports
# smart_pianosoft_midi directly so missing native audio libraries cannot block it.
from .smart_pianosoft_midi import (
    Fingerprint, SPSMetadata, TimedMidiEvent, SynchronizationError,
    SynchronizationCancelled, parse_sps_midi, _timecode,
)

SAMPLE_RATE = 44100
DECIMATION = 256
FINGERPRINT_SIZE = 256
_STEP = DECIMATION / SAMPLE_RATE


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


def _refine_peak(scores, index):
    """Estimate fractional-sample position and score gain at a local peak."""
    if 0 < index < len(scores) - 1:
        curvature = scores[index - 1] - 2 * scores[index] + scores[index + 1]
        if curvature < -1e-12:
            slope = (scores[index + 1] - scores[index - 1]) / 2
            fraction = float(np.clip(-slope / curvature, -0.5, 0.5))
            gain = float(slope * fraction + 0.5 * curvature * fraction * fraction)
            return fraction, max(0.0, min(gain, 1.0 - scores[index]))
    return 0.0, 0.0


def _match(audio, fingerprint, radius, cancel):
    reference = fingerprint.reference_seconds
    # Earlier candidates would contain only pre-track silence and cannot
    # identify music. This also bounds padding independently of the timecode.
    search_start = max(reference - radius, -FINGERPRINT_SIZE * _STEP)
    # Warm the filters before the first candidate, retaining absolute phase.
    start = math.floor((search_start - 1.0) * SAMPLE_RATE / DECIMATION) * DECIMATION
    # Keep one neighbour beyond the last allowed candidate for score refinement.
    # Without it, a repeated phrase on the search boundary can look artificially
    # weaker merely because its fractional-sample peak cannot be estimated.
    end = min(audio.getnframes(), math.ceil((reference + radius + FINGERPRINT_SIZE * _STEP) * SAMPLE_RATE)
              + DECIMATION)
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
    if not np.any(valid):
        raise SynchronizationError("The CD reference falls outside this audio track.")
    search_scores = np.where(valid, scores, -1.0)
    peak = int(np.argmax(search_scores))
    correlation = float(np.clip(scores[peak], -1, 1))
    if correlation < 0.95:
        raise SynchronizationError(f"The audio does not match the Smart PianoSoft CD reference (correlation {correlation:.3f}).")
    # Yamaha's testFile selects the strongest correlation; a similar later
    # phrase is not by itself a reason to reject the winning reference. The
    # former fixed 0.025 runner-up margin rejected valid repeating intros.
    refinement, gain = _refine_peak(scores, peak)
    local_peaks = valid & (search_scores >= np.r_[-np.inf, search_scores[:-1]]) & (
        search_scores >= np.r_[search_scores[1:], -np.inf])
    exclusion = math.ceil(0.25 / _STEP)
    local_peaks[max(0, peak - exclusion):peak + exclusion + 1] = False
    for rival in np.flatnonzero(local_peaks):
        _cancelled(cancel)
        if rival in (0, len(scores) - 1) and scores[rival] >= 0.95:
            # At physical EOF the extra neighbour may not exist. A second
            # credible match without enough data to refine it remains uncertain.
            raise SynchronizationError("The audio fingerprint has ambiguous matches; synchronization was not accepted.")
        _, rival_gain = _refine_peak(scores, rival)
        separation = (correlation + gain) - (scores[rival] + rival_gain)
        # APS keeps an additional conservative ambiguity check: do not trust
        # a lead that sub-sample score corrections alone can account for.
        # Identical repeated phrases sampled at different phases otherwise
        # produce slightly different scores. This is an interpolation-based
        # safeguard, not a Yamaha quantization rule or a formal error bound.
        if separation <= gain + rival_gain + np.finfo(np.float32).eps:
            raise SynchronizationError("The audio fingerprint has ambiguous matches; synchronization was not accepted.")
    position = float(np.clip(times[peak] + refinement * _STEP, search_start, reference + radius))
    return position, correlation


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
