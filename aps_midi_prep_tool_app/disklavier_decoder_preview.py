"""Software-only piano preview for decoded Disklavier audio.

The source control channel never enters an audio playback path. All previews
are made from decoded MIDI and the decoder's separate left-channel WAV.
"""

from pathlib import Path
import math
import os
import subprocess
import time
import wave

import mido
import numpy as np


_WAVEFORM_MAX_BINS = 2048
_WAVEFORM_BLOCK_FRAMES = 65536


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        from .disklavier_decoder import DisklavierDecodingCancelled
        raise DisklavierDecodingCancelled("Disklavier playback preparation canceled.")


def _accompaniment_waveform(source, *, max_bins=_WAVEFORM_MAX_BINS, cancel=None,
                           progress=None):
    """Summarize only the decoder's safe mono WAV on its original timeline.

    Each fixed-width bin contains the actual minimum and maximum PCM sample,
    normalized to full scale. Bins start at frame zero, including silent
    lead-in; the last bin can be shorter. Exact frame metadata lets the view
    align the envelope with MIDI even when a rendered piano has a release tail.
    Both read buffers and the resulting envelope have bounded sizes.
    """
    if not isinstance(max_bins, int) or max_bins < 1:
        raise ValueError("The waveform needs a positive number of bins.")
    _check_cancel(cancel)
    if progress:
        progress(0.0)
    with wave.open(str(source), "rb") as audio:
        # A stereo input may contain the encoded right-channel control signal.
        # Accept only the separate, validated accompaniment output.
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getframerate() != 44100 or audio.getcomptype() != "NONE"):
            raise ValueError("The accompaniment waveform requires decoded mono 16-bit PCM at 44.1 kHz.")
        frames = audio.getnframes()
        rate = audio.getframerate()
        frames_per_bin = max(1, (frames + max_bins - 1) // max_bins)
        bin_count = (frames + frames_per_bin - 1) // frames_per_bin
        minimums = np.full(bin_count, 32767, dtype=np.int16)
        maximums = np.full(bin_count, -32768, dtype=np.int16)
        for offset in range(0, frames, _WAVEFORM_BLOCK_FRAMES):
            _check_cancel(cancel)
            count = min(_WAVEFORM_BLOCK_FRAMES, frames - offset)
            raw = audio.readframes(count)
            if len(raw) != count * 2:
                raise ValueError("The decoded accompaniment WAV is truncated.")
            samples = np.frombuffer(raw, dtype="<i2")
            first_bin = offset // frames_per_bin
            first_boundary = (first_bin + 1) * frames_per_bin - offset
            starts = np.concatenate((np.array([0], dtype=np.intp),
                                     np.arange(first_boundary, count, frames_per_bin, dtype=np.intp)))
            last_bin = first_bin + len(starts)
            minimums[first_bin:last_bin] = np.minimum(
                minimums[first_bin:last_bin], np.minimum.reduceat(samples, starts))
            maximums[first_bin:last_bin] = np.maximum(
                maximums[first_bin:last_bin], np.maximum.reduceat(samples, starts))
            if progress:
                progress((offset + count) / frames)
        _check_cancel(cancel)
        return {
            "minimums": (minimums.astype(np.float64) / 32768).tolist(),
            "maximums": (maximums.astype(np.float64) / 32768).tolist(),
            "frame_count": frames, "sample_rate": rate,
            "duration_seconds": frames / rate, "frames_per_bin": frames_per_bin,
        }


def _piano_midi(source, destination):
    """Force melodic piano voices, retaining the recovered event timeline."""
    original = mido.MidiFile(str(source))
    channels = {message.channel for track in original.tracks for message in track
                if hasattr(message, "channel")}
    # GM channel 10 is percussion. Move it to an unused melodic channel when
    # present; the built-in piano can still handle all sixteen channels.
    replacement = next((channel for channel in range(16)
                        if channel != 9 and channel not in channels), None)
    if 9 in channels and replacement is None:
        return False
    mapping = {channel: replacement if channel == 9 else channel for channel in channels}
    result = mido.MidiFile(type=0, ticks_per_beat=1000)
    track = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=1000000)])
    result.tracks.append(track)
    for channel in sorted(mapping.values()):
        track.extend([mido.Message("control_change", channel=channel, control=0, value=0),
                      mido.Message("control_change", channel=channel, control=32, value=0),
                      mido.Message("program_change", channel=channel, program=0)])
    elapsed = 0.0
    previous_tick = 0
    for message in original:
        elapsed += message.time
        if not hasattr(message, "channel") or message.type == "program_change":
            continue
        if message.type == "control_change" and message.control in (0, 32):
            continue
        tick = round(elapsed * 1000)
        track.append(message.copy(channel=mapping[message.channel], time=tick - previous_tick))
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, round(elapsed * 1000) - previous_tick)))
    result.save(str(destination))
    return True


def _render_soundfont(midi_path, output_path, cancel):
    # Keep the main window's discovery and platform conventions without an
    # import cycle when this utility is opened from its Utilities menu.
    from .main_window import (
        _deprioritize_preview_process, _find_fluidsynth_command,
        _find_preview_soundfont, windows_subprocess_kwargs,
    )
    command = _find_fluidsynth_command()
    soundfont = _find_preview_soundfont()
    if not command or not soundfont:
        return False
    process = subprocess.Popen(
        [command, "-ni", "-q", "-g", "0.8", "-F", str(output_path), "-T", "wav", "-O", "s16",
         "-r", "44100", str(soundfont), str(midi_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # Fast rendering uses files only. SDL can otherwise probe the speaker
        # server during library startup even though no audio driver is needed.
        env={**os.environ, "SDL_AUDIODRIVER": "dummy"},
        **windows_subprocess_kwargs(),
    )
    _deprioritize_preview_process(process)
    try:
        while process.poll() is None:
            _check_cancel(cancel)
            time.sleep(0.05)
        _check_cancel(cancel)
        return process.returncode == 0 and Path(output_path).is_file()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _write_builtin_piano(notes, output_path, duration, *, cancel=None, progress=None):
    """Stream the existing built-in piano timbre without a song-length cap."""
    rate = 44100
    frame_count = max(1, round(duration * rate))
    ordered = sorted(notes, key=lambda note: note["start_sec"])
    pending = 0
    active = []
    with wave.open(str(output_path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        for offset in range(0, frame_count, rate):
            _check_cancel(cancel)
            count = min(rate, frame_count - offset)
            block_start, block_end = offset / rate, (offset + count) / rate
            while pending < len(ordered) and ordered[pending]["start_sec"] < block_end:
                active.append(ordered[pending])
                pending += 1
            samples = np.zeros(count, dtype=np.float64)
            for note in active:
                _check_cancel(cancel)
                note_start = max(0, round(note["start_sec"] * rate))
                note_end = max(note_start + 1, round(note["end_sec"] * rate))
                start, end = max(offset, note_start), min(offset + count, note_end)
                if end <= start:
                    continue
                indices = np.arange(start, end, dtype=np.float64)
                local = indices - note_start
                seconds = local / rate
                frequency = 440.0 * 2 ** ((note["pitch"] - 69) / 12)
                phase = 2 * np.pi * frequency * seconds
                tone = (np.sin(phase) + .42 * np.sin(phase * 2.01 + .2)
                        + .18 * np.sin(phase * 3.02 + .4) + .08 * np.sin(phase * 4.01))
                envelope = (np.minimum(1, local / (rate * .008)) * np.exp(-seconds / .85)
                            * np.clip((note_end - indices) / (rate * .12), 0, 1))
                samples[start - offset:end - offset] += 2100 * note["velocity"] / 127 * envelope * tone
            active = [note for note in active if note["end_sec"] > block_end]
            output.writeframes(np.clip(samples, -32768, 32767).astype("<i2").tobytes())
            if progress:
                progress((offset + count) / frame_count)


def _mix_preview(piano, accompaniment, destination, *, balance=.5, cancel=None, progress=None):
    """Blend derived stems on one timeline; 0 is piano and 1 accompaniment.

    The gains sum to one to retain headroom. Even at the endpoints, preserve
    both stems' full duration and leading silence so changing the balance
    cannot move notes or shorten the playback timeline.
    """
    if not math.isfinite(balance) or not 0 <= balance <= 1:
        raise ValueError("Preview balance must be between 0 and 1.")
    _check_cancel(cancel)
    with wave.open(str(piano), "rb") as first, wave.open(str(accompaniment), "rb") as second:
        if any(source.getsampwidth() != 2 or source.getframerate() != 44100
               or source.getcomptype() != "NONE" for source in (first, second)):
            raise ValueError("Preview audio must be 16-bit PCM at 44.1 kHz.")
        if first.getnchannels() not in (1, 2):
            raise ValueError("Preview piano audio must be mono or stereo.")
        if second.getnchannels() != 1:
            raise ValueError("Preview accompaniment must be the decoded mono WAV.")
        total = max(first.getnframes(), second.getnframes())
        with wave.open(str(destination), "wb") as output:
            output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            for offset in range(0, total, 44100):
                _check_cancel(cancel)
                count = min(44100, total - offset)
                mixed = np.zeros((count, 2), dtype=np.float64)
                for source, gain in ((first, 1 - balance), (second, balance)):
                    samples = np.frombuffer(source.readframes(count), dtype="<i2")
                    samples = samples.reshape((-1, source.getnchannels()))
                    mixed[:len(samples)] += samples * gain
                output.writeframes(np.clip(mixed, -32768, 32767).astype("<i2").tobytes())
                if progress:
                    progress((offset + count) / max(1, total))
            _check_cancel(cancel)


def prepare_preview(decoded, directory, *, cancel=None, progress=None):
    """Return playable WAVs and a timed accompaniment envelope from decoded data."""
    from .main_window import _inspect_midi_bytes, _notes_with_sustain_pedal

    directory = Path(directory)
    piano_midi = directory / "piano-preview.mid"
    piano_wav = directory / "piano.wav"
    mixed_wav = directory / "piano-and-audio.wav"
    inspection = _inspect_midi_bytes(Path(decoded["midi_path"]).read_bytes())
    notes = _notes_with_sustain_pedal(inspection["notes"], inspection["pedals"])
    _check_cancel(cancel)
    if progress:
        progress("piano", 0)
    waveform = _accompaniment_waveform(decoded["accompaniment_path"], cancel=cancel)
    rendered = False
    if _piano_midi(decoded["midi_path"], piano_midi):
        try:
            if progress:
                progress("piano", -1)
            rendered = _render_soundfont(piano_midi, piano_wav, cancel)
        except OSError:
            pass
    if not rendered:
        _write_builtin_piano(notes, piano_wav, max(decoded["duration_seconds"], inspection["duration"]),
                             cancel=cancel, progress=lambda value: progress and progress("piano", value))
    _check_cancel(cancel)
    _mix_preview(piano_wav, decoded["accompaniment_path"], mixed_wav, cancel=cancel,
                 progress=lambda value: progress and progress("mix", value))
    durations = {}
    for key, path in (("piano_path", piano_wav), ("mixed_path", mixed_wav)):
        with wave.open(str(path), "rb") as audio:
            durations[key] = audio.getnframes() / audio.getframerate()
    return {**decoded, "piano_path": str(piano_wav), "mixed_path": str(mixed_wav),
            "preview_durations": durations, "notes": notes,
            "accompaniment_waveform": waveform,
            "renderer": "soundfont" if rendered else "builtin"}
