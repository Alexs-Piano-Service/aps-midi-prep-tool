"""Prepare encoded audio or existing MIDI/audio pairs on their original timeline."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import wave

import mido
import numpy as np

from . import disklavier_decoder as decoder
from . import disklavier_audio_decode as audio_decoder
from . import audio_tools


def _companion(source, suffixes):
    candidates = [path for path in source.parent.iterdir()
                  if path.is_file() and path.suffix.casefold() in suffixes
                  and path.stem.casefold() == source.stem.casefold()]
    if suffixes == {".wav", ".mp3"}:
        # Keep the existing WAV choice when a folder also contains its MP3
        # export. Direct audio selection always uses the explicitly chosen file.
        candidates = [path for path in candidates if path.suffix.casefold() == ".wav"] or candidates
    exact = [path for path in candidates if path.stem == source.stem]
    candidates = exact or candidates
    if len(candidates) > 1:
        names = ", ".join(sorted(path.name for path in candidates))
        raise ValueError(f"More than one matching companion for {source.name}: {names}")
    if not candidates:
        kind = "WAV or MP3" if suffixes == {".wav", ".mp3"} else "MIDI"
        raise ValueError(f"No matching {kind} file was found beside {source.name}. "
                         "Keep the MIDI and its WAV or MP3 in the same folder with the same filename stem.")
    return candidates[0]


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _copy(source, destination, report):
    before = _identity(source)
    size, copied = before[2], 0
    report(0)
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            writer.write(chunk)
            copied += len(chunk)
            report(copied / max(1, size))
    if _identity(source) != before:
        raise ValueError(f"The source changed while it was being read: {source.name}")
    report(1)


def _inspect_midi(path, cancel):
    decoder._check_cancel(cancel)
    try:
        midi = mido.MidiFile(path)
        if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
            raise ValueError("MIDI/audio playback requires a type 0 or 1 MIDI file with beat-based timing.")
        duration = 0.0
        events = notes = 0
        for message in midi:
            decoder._check_cancel(cancel)
            duration += message.time
            events += not message.is_meta
            notes += message.type in {"note_on", "note_off"}
        return duration, events, notes
    except decoder.DisklavierDecodingCancelled:
        raise
    except (EOFError, OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"The MIDI companion cannot be played: {exc}") from exc


def _inspect_audio(path):
    try:
        with wave.open(str(path), "rb") as reader:
            if (reader.getframerate() != 44100 or reader.getsampwidth() != 2
                    or reader.getnchannels() not in (1, 2) or reader.getcomptype() != "NONE"):
                raise ValueError("Playback requires a 44.1 kHz, 16-bit mono or stereo PCM WAV file.")
            if not reader.getnframes():
                raise ValueError("The WAV file is empty.")
            return reader.getnframes(), reader.getnchannels()
    except (wave.Error, EOFError) as exc:
        raise ValueError("The audio is not a readable PCM WAV file.") from exc


def _check_plain_audio(path, cancel, progress=None):
    """Validate all PCM, rejecting carrier fragments even without MIDI framing.

    The typed decoder's non-encoded result excludes complete recognizable
    streams. A brief/damaged fragment can fail acquisition; do not expose such
    a carrier through an existing MIDI companion. This conservative check is
    intentionally independent of whether the framing can be decoded. Progress
    receives a fraction after each complete block, with cancellation checked
    before and after notifying the caller.
    """
    def report(value):
        decoder._check_cancel(cancel)
        if progress is not None:
            progress(value)
        decoder._check_cancel(cancel)

    report(0)
    with wave.open(str(path), "rb") as reader:
        channels = reader.getnchannels()
        total = reader.getnframes()
        block_frames = 14 * 4096
        previous = np.empty((0, channels), dtype="<i2")
        for first in range(0, total, block_frames):
            decoder._check_cancel(cancel)
            count = min(block_frames, total - first)
            raw = reader.readframes(count)
            if len(raw) != count * channels * 2:
                raise ValueError("The WAV file is truncated.")
            samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
            samples = np.concatenate((previous, samples))
            previous = samples[-14 * 10:].copy()
            # All boundaries are considered because a copied WAV may have
            # leading frames that are not aligned to a fourteen-frame symbol.
            for channel in range(channels):
                for offset in range(14):
                    decoder._check_cancel(cancel)
                    symbols = (len(samples) - offset) // 14
                    if symbols < 9:
                        continue
                    blocks = samples[offset:offset + symbols * 14, channel].reshape(-1, 14)
                    phases, amplitude, fit = audio_decoder._measure(blocks)
                    possible = (amplitude >= 100) & (fit >= 0.85)
                    values, error = audio_decoder._phase_symbols(phases)
                    idle = possible[1:] & possible[:-1] & (values == 15) & (error < 0.12)
                    # Require Yamaha's specific differential idle pattern.
                    # A strong high-frequency musical tone is not encoding.
                    if np.any(np.convolve(idle.astype(np.uint8), np.ones(8, dtype=np.uint8),
                                          mode="valid") == 8):
                        raise ValueError("Possible Disklavier control audio could not be decoded safely. "
                                         "The WAV will not be played as ordinary accompaniment.")
            report((first + count) / total)


def _broad_decode(source, midi_path, audio_path, cancel, report):
    recovered = audio_decoder.decode_disklavier_audio(
        source, audio_path, cancel=cancel, progress=report)
    frames = round(recovered.source_duration_seconds * 44100)
    with midi_path.open("xb+") as handle:
        writer = decoder._MidiWriter(handle)
        for event in recovered.events:
            decoder._check_cancel(cancel)
            writer.message(round(event.time_seconds * 44100), event.data)
        writer.finish(frames)
    return {**recovered.report, "midi_path": str(midi_path),
            "accompaniment_path": str(audio_path), "channels": 1,
            "midi_events": writer.events, "note_events": writer.notes,
            "system_events_omitted": writer.system_events, "timeline": "original WAV"}


def prepare_playback_source(source, directory, *, cancel=None, progress=None):
    """Return the preview schema for encoded audio or a MIDI/WAV or MIDI/MP3 pair.

    Selecting MIDI uses its same-stem WAV, or MP3 when no WAV is present.
    Selecting audio prefers its encoded piano, and only uses an adjacent MIDI
    when the audio has no control data. Ordinary pairs preserve MIDI bytes,
    stereo audio and leading silence. LAME decodes MP3 delay/padding metadata;
    this function does not guess or change synchronization.

    All prepared files are held in a private child of ``directory``. On any
    failure or cancellation that child is removed, leaving caller files and
    originals untouched. Successful files live until the caller removes its
    temporary playback directory. Progress receives ``(phase, fraction)``.
    """
    source, directory = Path(source).absolute(), Path(directory)

    def report(phase, fraction):
        decoder._check_cancel(cancel)
        if progress is not None:
            progress(phase, fraction)
        decoder._check_cancel(cancel)

    def strict_progress(value):
        # A successfully scanned block may still be ordinary leading silence;
        # only a complete result establishes that this receiver decoded piano.
        report("check_audio", value * 0.3)

    def broad_progress(value):
        # The acquisition endpoint is computed as 0.05 + 0.1 in the receiver,
        # which can be one floating-point step greater than literal 0.15.
        if value <= 0.15 + 1e-12:
            report("check_audio", 0.3 + min(value / 0.15, 1) * 0.3)
        else:
            report("decode", 0.6 + (value - 0.15) / 0.85 * 0.2)

    report("inspect", 0)
    suffix = source.suffix.casefold()
    if suffix not in {".mid", ".midi", ".wav", ".mp3"}:
        raise ValueError("Open MIDI with a matching WAV or MP3, or select a WAV or MP3 file.")
    if not source.is_file():
        raise ValueError(f"The selected file is not available: {source}")
    selected_midi = source if suffix in {".mid", ".midi"} else None
    audio_source = _companion(source, {".wav", ".mp3"}) if selected_midi else source
    compressed = audio_source.suffix.casefold() == ".mp3"
    if compressed and not audio_tools.find_lame_command():
        raise RuntimeError("LAME is required to play MP3 audio. Install or restore the bundled LAME tool.")
    workspace = Path(tempfile.mkdtemp(prefix="playback-source-", dir=directory))
    try:
        snapshot = workspace / "source.wav"
        if compressed:
            mp3_snapshot = workspace / "source.mp3"
            _copy(audio_source, mp3_snapshot, lambda value: report("inspect", value * 0.5))
            report("mp3", -1)
            try:
                audio_tools.decode_mp3(mp3_snapshot, snapshot, cancel=cancel)
            except InterruptedError as exc:
                raise decoder.DisklavierDecodingCancelled(str(exc)) from exc
            report("mp3", 1)
            mp3_snapshot.unlink()
        else:
            _copy(audio_source, snapshot, lambda value: report("inspect", value))
        frames, channels = _inspect_audio(snapshot)
        midi_path, audio_path = workspace / "decoded.mid", workspace / "accompaniment.wav"
        decoded = None
        if channels == 2:
            try:
                decoded = decoder.decode_disklavier_wav(
                    snapshot, midi_path, audio_path, cancel=cancel,
                    progress=strict_progress)
            except ValueError:
                # A failed strict decode alone never means that it is safe to
                # play both channels. The broader receiver distinguishes lack
                # of encoding from recognizable but damaged control data.
                try:
                    decoded = _broad_decode(snapshot, midi_path, audio_path, cancel,
                                            broad_progress)
                except audio_decoder.NotEncodedAudioError:
                    _check_plain_audio(snapshot, cancel,
                                       lambda value: report("check_audio", 0.6 + value * 0.4))
            if decoded is not None:
                # The strict receiver only inspects the expected right-hand
                # control. Do not expose a second control channel as music.
                _check_plain_audio(audio_path, cancel,
                                   lambda value: report("check_audio", 0.8 + value * 0.2))
        else:
            _check_plain_audio(snapshot, cancel, lambda value: report("check_audio", value))
        if decoded is not None and selected_midi is None:
            snapshot.unlink()
            return {**decoded, "source_kind": "encoded", "source_midi_path": None,
                    "source_audio_path": str(audio_source),
                    "timeline": "original MP3" if compressed else "original WAV"}

        original_midi = selected_midi or _companion(source, {".mid", ".midi"})
        paired_midi = workspace / "paired.mid"
        _copy(original_midi, paired_midi,
              lambda _value: report("audio", 1))
        midi_duration, event_count, note_count = _inspect_midi(paired_midi, cancel)
        if decoded is None:
            # Keep the complete stereo field and the original time zero. WAV
            # snapshots are byte-identical; MP3 PCM uses LAME's gapless decode.
            audio_path = snapshot
        else:
            snapshot.unlink()
            midi_path.unlink()
        report("audio", 1)
        audio_kind = "MP3" if compressed else "WAV"
        return {"format": f"MIDI/{audio_kind} pair", "midi_path": str(paired_midi),
                "accompaniment_path": str(audio_path),
                "duration_seconds": max(frames / 44100, midi_duration), "sample_rate": 44100,
                "channels": decoded["channels"] if decoded else channels,
                "midi_events": event_count, "note_events": note_count,
                "system_events_omitted": 0, "timeline": f"original MIDI/{audio_kind} pair",
                "source_kind": "pair", "source_midi_path": str(original_midi),
                "source_audio_path": str(audio_source),
                "control_channel_removed": decoded is not None}
    except BaseException as exc:
        shutil.rmtree(workspace)
        if isinstance(exc, audio_decoder.DisklavierDecodingCancelled):
            raise decoder.DisklavierDecodingCancelled(str(exc)) from exc
        raise
