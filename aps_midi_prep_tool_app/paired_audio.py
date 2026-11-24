"""Export a synchronized, same-basename MIDI/MP3 pair for USB playback."""

import math
import os
from pathlib import Path
import tempfile
import wave

import mido

from .audio_tools import check_cancel, encode_mp3
from .disklavier_audio import is_pianosmart_message
from .helpers.file_batch import FileBatchWriteError, publish_file_batch


SAMPLE_RATE = 44100
TICKS_PER_BEAT = 10000
TEMPO = 1000000
# Playback timing is already captured in the absolute event timestamps. Live
# clock/transport/connection messages do not belong in a Standard MIDI File.
# System Reset is deliberately excluded: dropping it can change musical state.
_OMITTED_REALTIME_TYPES = {"clock", "start", "continue", "stop", "active_sensing"}
_SYSTEM_MESSAGE_NAMES = {
    "quarter_frame": "MIDI Time Code Quarter Frame",
    "songpos": "Song Position Pointer",
    "song_select": "Song Select",
    "tune_request": "Tune Request",
    "reset": "System Reset",
}


class PairedAudioCancelled(InterruptedError):
    """A cancelled pair with any rollback recovery locations preserved."""

    def __init__(self, failure):
        self.recovery_directory = failure.recovery_directory
        self.retained_paths = failure.retained_paths
        message = str(failure.original_exception)
        if self.recovery_directory:
            message += f"\nPrepared recovery files: {self.recovery_directory}"
        if self.retained_paths:
            message += "\nRetained rollback files: " + ", ".join(self.retained_paths)
        super().__init__(message)


def _mapped_events(events, offset, scale, cancel):
    if not math.isfinite(offset) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("MIDI alignment requires a finite offset and positive time scale.")
    mapped = []
    previous = -math.inf
    skipped = 0
    skipped_realtime = 0
    for event in events:
        check_cancel(cancel)
        seconds, data = ((event.time_seconds, event.data) if hasattr(event, "time_seconds") else event)
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < previous:
            raise ValueError("MIDI events must have finite times in playback order.")
        previous = seconds
        data = bytes(data)
        if is_pianosmart_message(data):
            skipped += 1
            continue
        message = mido.Message.from_bytes(data)
        if message.type in _OMITTED_REALTIME_TYPES:
            skipped_realtime += 1
            continue
        if data[0] >= 0xF0 and message.type != "sysex":
            name = _SYSTEM_MESSAGE_NAMES.get(message.type, message.type)
            kind = "realtime" if message.type == "reset" else "system-common"
            raise ValueError(
                f"Cannot export MIDI {kind} message {name} (0x{data[0]:02X}) "
                f"at {seconds:.6f} seconds to a Standard MIDI File. "
                "It may change synchronization or instrument state and cannot be safely omitted."
            )
        prepared_seconds = seconds * scale + offset
        if not math.isfinite(prepared_seconds):
            raise ValueError("The mapped MIDI event time is not finite.")
        mapped.append((prepared_seconds, message))
    if not mapped:
        raise ValueError("There are no musical MIDI events to export.")
    return mapped, skipped, skipped_realtime


def _copy_pcm(source, destination, preroll_frames, cancel):
    with wave.open(str(source), "rb") as incoming:
        if (incoming.getnchannels() not in (1, 2) or incoming.getsampwidth() != 2
                or incoming.getframerate() != SAMPLE_RATE or incoming.getcomptype() != "NONE"):
            raise ValueError("Paired music must be mono or stereo, 44.1 kHz, 16-bit PCM WAV.")
        count, channels = incoming.getnframes(), incoming.getnchannels()
        if not count:
            raise ValueError("The music WAV is empty.")
        with wave.open(str(destination), "wb") as outgoing:
            outgoing.setparams((channels, 2, SAMPLE_RATE, count + preroll_frames, "NONE", "not compressed"))
            remaining = preroll_frames
            while remaining:
                check_cancel(cancel)
                frames = min(65536, remaining)
                outgoing.writeframesraw(bytes(frames * channels * 2))
                remaining -= frames
            copied = 0
            while block := incoming.readframes(65536):
                check_cancel(cancel)
                outgoing.writeframesraw(block)
                copied += len(block)
            if copied != count * channels * 2:
                raise ValueError("The music WAV is truncated.")
    return count, channels


def _identity(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def export_midi_mp3(events, audio_path, output_stem, *, midi_offset_seconds=0.0,
                    midi_time_scale=1.0, cancel=None, progress=None):
    """Apply the shared timing map and publish a complete MIDI/MP3 pair.

    ``events`` are ordered (absolute seconds, complete MIDI bytes) tuples or
    objects with ``time_seconds`` and ``data``. Every musical event receives
    the same affine timing map. A sample-aligned shared lead-in protects
    negative mapped events without dropping or clamping notes/controllers.
    Live clock, transport and active-sensing messages are omitted and counted;
    system-common and System Reset messages are reported as unsupported.
    Original source files remain untouched. LAME's gapless timing metadata is
    retained; no unverified mechanical or decoder-latency correction is added.
    """
    check_cancel(cancel)
    source, stem = Path(audio_path), Path(output_stem)
    midi_path, mp3_path = Path(str(stem) + ".mid"), Path(str(stem) + ".mp3")
    for path in (midi_path, mp3_path):
        if os.path.lexists(path):
            raise FileExistsError(f"Output already exists: {path}")
    mapped, skipped, skipped_realtime = _mapped_events(
        events, float(midi_offset_seconds), float(midi_time_scale), cancel)
    preroll_frames = math.ceil(max(0.0, -mapped[0][0]) * SAMPLE_RATE)
    preroll = preroll_frames / SAMPLE_RATE
    identity = _identity(source)

    def report(fraction):
        check_cancel(cancel)
        if progress is not None:
            progress(fraction)
        check_cancel(cancel)

    report(0.0)
    with tempfile.TemporaryDirectory(prefix=".aps-paired-audio-", dir=stem.parent) as temporary:
        folder = Path(temporary)
        pcm = folder / "music.wav"
        frames, channels = _copy_pcm(source, pcm, preroll_frames, cancel)
        duration = (frames + preroll_frames) / SAMPLE_RATE
        report(0.2)
        midi = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT)
        track = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=TEMPO)])
        previous_tick = 0
        sounding = {}
        for seconds, message in mapped:
            check_cancel(cancel)
            tick = round((seconds + preroll) * TICKS_PER_BEAT)
            if tick - previous_tick > 0x0FFFFFFF:
                raise ValueError("A MIDI event gap exceeds the Standard MIDI File timing limit.")
            if message.type in {"note_on", "note_off"}:
                key = message.channel, message.note
                if message.type == "note_on" and message.velocity:
                    sounding[key] = (seconds, tick)
                else:
                    start = sounding.pop(key, None)
                    if start is not None and seconds > start[0] and tick <= start[1]:
                        raise ValueError("A positive MIDI note duration is too short for the paired file's 0.1 ms timing resolution.")
            track.append(message.copy(time=tick - previous_tick))
            previous_tick = tick
        end_tick = max(previous_tick, round(duration * TICKS_PER_BEAT))
        if end_tick - previous_tick > 0x0FFFFFFF:
            raise ValueError("The MIDI ending gap exceeds the Standard MIDI File timing limit.")
        track.append(mido.MetaMessage("end_of_track", time=end_tick - previous_tick))
        midi.tracks.append(track)
        prepared_midi, prepared_mp3 = folder / "song.mid", folder / "song.mp3"
        midi.save(prepared_midi)
        # Parse the written SMF before exposing either half of the pair.
        mido.MidiFile(prepared_midi)
        encode_mp3(pcm, prepared_mp3, cancel=cancel)
        report(0.85)
        if _identity(source) != identity:
            raise ValueError("The music source changed during paired export.")
        try:
            publish_file_batch(
                [(prepared_midi, midi_path), (prepared_mp3, mp3_path)],
                recovery_parent=stem.parent,
                expected_hashes={midi_path: None, mp3_path: None},
                progress_callback=lambda completed, total, _path: report(0.85 + 0.15 * completed / total),
            )
        except FileBatchWriteError as failure:
            if isinstance(failure.original_exception, InterruptedError) and not failure.rollback_errors:
                raise PairedAudioCancelled(failure) from failure
            raise
    return {
        "format": "Synchronized MIDI/MP3 pair", "midi": midi_path.name, "audio": mp3_path.name,
        "midi_offset_seconds": float(midi_offset_seconds), "midi_time_scale": float(midi_time_scale),
        "audio_preroll_seconds": preroll, "midi_advance_seconds": 0.0,
        "duration_seconds": end_tick / TICKS_PER_BEAT,
        "events": len(mapped), "skipped_pianosmart_messages": skipped,
        "skipped_realtime_messages": skipped_realtime,
        "sample_rate": SAMPLE_RATE, "channels": channels, "mp3_bitrate_kbps": 192,
        "mp3_delay_handling": "LAME Info encoder-delay and padding metadata",
        "hardware_verified": False,
    }
