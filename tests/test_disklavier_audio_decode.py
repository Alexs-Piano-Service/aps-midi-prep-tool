from pathlib import Path
import subprocess
import threading
import wave

import numpy as np
import pytest

from aps_midi_prep_tool_app.audio_tools import find_lame_command
from aps_midi_prep_tool_app.disklavier_audio import SAMPLE_RATE, encode_disklavier_wav
from aps_midi_prep_tool_app.disklavier_audio_decode import (
    DisklavierDecodeError,
    DisklavierDecodingCancelled,
    NotEncodedAudioError,
    _parse_messages,
    decode_disklavier_audio,
)


def _write(path, samples):
    if samples.ndim == 1:
        samples = samples[:, None]
    with wave.open(str(path), "wb") as output:
        output.setparams((samples.shape[1], 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        output.writeframes(samples.astype("<i2").tobytes())


def _read(path):
    with wave.open(str(path), "rb") as incoming:
        assert incoming.getsampwidth() == 2
        assert incoming.getframerate() == SAMPLE_RATE
        return np.frombuffer(incoming.readframes(incoming.getnframes()), dtype="<i2").reshape(
            -1, incoming.getnchannels()).copy()


def _encoded(tmp_path):
    frames = np.arange(SAMPLE_RATE * 3)
    music = np.column_stack((np.sin(2 * np.pi * 440 * frames / SAMPLE_RATE) * 8000,
                             np.sin(2 * np.pi * 880 * frames / SAMPLE_RATE) * 6000))
    source, encoded = tmp_path / "source.wav", tmp_path / "encoded.wav"
    _write(source, music)
    events = [
        (0.0, b"\xf0\x43\x10\x4c\x00\x00\x7e\x00\xf7"),
        (0.1, b"\xcf\x02"),
        (0.2, b"\x90\x3c\x4f"),
        (0.3, b"\xb0\x40\x7f"),
        (0.5, b"\xe0\x00\x40"),
        (0.7, b"\x80\x3c\x40"),
        (0.8, b"\xb0\x40\x00"),
        (1.2, b"\x91\x3e\x40"),
        (1.7, b"\x91\x3e\x00"),
    ]
    encode_disklavier_wav(events, source, encoded)
    return encoded, events


@pytest.mark.parametrize("swap,offset,invert", [(False, 0, False), (True, 5, False), (False, 13, True)])
def test_decodes_complete_performance_and_preserves_music_timeline(tmp_path, swap, offset, invert):
    encoded, events = _encoded(tmp_path)
    samples = _read(encoded)
    if invert:
        samples = samples.copy()
        samples[:, 1] *= -1
    if swap:
        samples = samples[:, ::-1]
    samples = np.r_[np.zeros((offset, 2), dtype="<i2"), samples]
    _write(encoded, samples)
    original = encoded.read_bytes()
    destination = tmp_path / "music.wav"
    progress = []
    result = decode_disklavier_audio(encoded, destination, progress=progress.append)
    assert [event.data for event in result.events] == [data for _, data in events]
    for actual, (seconds, _) in zip(result.events, events):
        assert actual.time_seconds == pytest.approx(seconds + 0.5 + offset / SAMPLE_RATE, abs=1 / 3150)
    assert result.control_channel == ("left" if swap else "right")
    assert np.array_equal(_read(destination)[:, 0], samples[:, 1 if swap else 0])
    assert encoded.read_bytes() == original
    assert result.music_path == destination
    assert result.report["hardware_verified"] is False
    assert result.report["midi_advance_seconds"] == 0
    assert progress == sorted(progress)
    assert progress[0] == 0 and progress[-1] == 1


def test_finds_control_after_a_long_quiet_intro_and_keeps_absolute_timing(tmp_path):
    encoded, expected = _encoded(tmp_path)
    original = _read(encoded)
    lead_frames = 9 * SAMPLE_RATE + 5
    samples = np.r_[np.zeros((lead_frames, 2), dtype="<i2"), original]
    _write(encoded, samples)
    progress = []
    result = decode_disklavier_audio(encoded, tmp_path / "music.wav", progress=progress.append)
    assert [event.data for event in result.events] == [data for _, data in expected]
    for actual, (seconds, _) in zip(result.events, expected):
        assert actual.time_seconds == pytest.approx(seconds + 0.5 + lead_frames / SAMPLE_RATE,
                                                   abs=1 / 3150)
    assert result.report["symbol_boundary_frames"] == 5
    assert np.array_equal(_read(result.music_path)[:, 0], samples[:, 0])
    assert progress == sorted(progress)


@pytest.mark.parametrize("rate,width", [(48000, 2), (44100, 3), (48000, 3)])
def test_unsupported_ordinary_pcm_is_skipped_without_a_failed_decode(tmp_path, rate, width):
    source, destination = tmp_path / "ordinary.wav", tmp_path / "music.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((2, width, rate, 0, "NONE", "not compressed"))
        output.writeframes(bytes(rate * 2 * width))
    original = source.read_bytes()
    with pytest.raises(NotEncodedAudioError, match="Unsupported audio format"):
        decode_disklavier_audio(source, destination)
    assert source.read_bytes() == original
    assert not destination.exists()
    assert not list(tmp_path.glob(".aps-decode-audio-*"))


def test_parser_handles_contiguous_messages_running_status_escapes_and_realtime():
    # Literal wire vectors avoid reusing the encoder's byte-escaping helper.
    groups = [
        ([12, 0, 4, 3, 1, 2, 0, 15, 12, 7], b"\xf0\x43\x12\x0f\xf7"),
        ([12, 4, 15, 0, 2], b"\xcf\x02"),
        ([9, 0, 3, 12, 4, 0], b"\x90\x3c\x40"),
        ([3, 14, 4, 15], b"\x90\x3e\x4f"),
        ([11, 0, 4, 0, 7, 15], b"\xb0\x40\x7f"),
        ([8, 0, 3, 12, 2, 0], b"\x80\x3c\x20"),
        ([3, 14, 2, 0], b"\x80\x3e\x20"),
        ([14, 0, 0, 0, 4, 0], b"\xe0\x00\x40"),
        ([12, 8], b"\xf8"),
    ]
    values = [15] * 16
    expected_times = []
    for symbols, _ in groups:
        values.extend(symbols)
        expected_times.append((7 + (30 + len(values)) * 14) / SAMPLE_RATE)
    values.extend([15] * 16)
    events = _parse_messages(np.asarray(values), 30, 7, None)
    assert [event.data for event in events] == [message for _, message in groups]
    assert [event.time_seconds for event in events] == expected_times


@pytest.mark.parametrize("music", ["sine", "noise", "silence", "mono"])
def test_ordinary_audio_is_not_silently_treated_as_control_data(tmp_path, music):
    samples = np.arange(SAMPLE_RATE)
    if music == "noise":
        pcm = np.random.default_rng(411).integers(-15000, 15000, size=(len(samples), 2))
    elif music == "silence":
        pcm = np.zeros((len(samples), 2))
    else:
        # A 6300-Hz tone is insufficient: the carrier must carry valid phase
        # framing, idle intervals, and complete note-on/note-off messages.
        tone = 12000 * np.cos(2 * np.pi * 6300 * samples / SAMPLE_RATE)
        pcm = tone if music == "mono" else np.column_stack((tone, tone))
    source, destination = tmp_path / "ordinary.wav", tmp_path / "music.wav"
    _write(source, pcm)
    with pytest.raises(NotEncodedAudioError):
        decode_disklavier_audio(source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".aps-decode-audio-*"))


def test_both_control_channels_are_rejected_as_ambiguous(tmp_path):
    source, _ = _encoded(tmp_path)
    samples = _read(source)
    samples[:, 0] = samples[:, 1]
    _write(source, samples)
    with pytest.raises(DisklavierDecodeError, match="Both"):
        decode_disklavier_audio(source, tmp_path / "music.wav")
    assert not (tmp_path / "music.wav").exists()


@pytest.mark.parametrize("damage", ["dropout", "uncertain", "truncated"])
def test_damaged_control_never_publishes_partial_music(tmp_path, damage):
    source, _ = _encoded(tmp_path)
    if damage == "truncated":
        source.write_bytes(source.read_bytes()[:-22])
    else:
        samples = _read(source)
        start = round(1.4 * 3150) * 14
        if damage == "dropout":
            samples[start:start + 140, 1] = 0
        else:
            samples[start:start + 140, 1] = np.random.default_rng(92).integers(-16000, 16000, size=140)
        _write(source, samples)
    original = source.read_bytes()
    with pytest.raises(DisklavierDecodeError):
        decode_disklavier_audio(source, tmp_path / "music.wav")
    assert source.read_bytes() == original
    assert not (tmp_path / "music.wav").exists()
    assert not list(tmp_path.glob(".aps-decode-audio-*"))


def test_unterminated_note_is_rejected_instead_of_guessed(tmp_path):
    source, _ = _encoded(tmp_path)
    music = tmp_path / "source.wav"
    source.unlink()
    encode_disklavier_wav([(0.1, b"\x90\x3c\x40"), (0.2, b"\x80\x3e\x40")], music, source)
    with pytest.raises(DisklavierDecodeError, match="active notes"):
        decode_disklavier_audio(source, tmp_path / "music.wav")


@pytest.mark.parametrize("cancel_at", [0.0, 0.3, 0.8, 1.0])
def test_cancellation_cleans_staging_without_touching_source(tmp_path, cancel_at):
    source, _ = _encoded(tmp_path)
    original = source.read_bytes()
    cancel = threading.Event()

    def progress(value):
        if value >= cancel_at:
            cancel.set()

    with pytest.raises(DisklavierDecodingCancelled):
        decode_disklavier_audio(source, tmp_path / "music.wav", cancel=cancel, progress=progress)
    assert not (tmp_path / "music.wav").exists()
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".aps-decode-audio-*"))


@pytest.mark.parametrize("concurrent", [False, True])
def test_existing_output_is_never_replaced(tmp_path, concurrent):
    source, _ = _encoded(tmp_path)
    destination = tmp_path / "music.wav"
    if not concurrent:
        destination.write_bytes(b"keep this")

    def progress(value):
        if concurrent and value == 1:
            destination.write_bytes(b"keep this")

    with pytest.raises(FileExistsError):
        decode_disklavier_audio(source, destination, progress=progress)
    assert destination.read_bytes() == b"keep this"


@pytest.mark.parametrize("bitrate", [192, 320])
def test_mp3_decoder_recovers_the_same_notes_and_timeline_when_signal_survives(tmp_path, bitrate):
    command = find_lame_command()
    if not command:
        pytest.skip("LAME is not available")
    source, _ = _encoded(tmp_path)
    encoded_mp3 = tmp_path / "encoded.mp3"
    # This is a newly made fixture, not copyrighted Yamaha/customer music.
    subprocess.run([command, "--silent", "--cbr", "-b", str(bitrate), "-m", "s", "-q", "2",
                    str(source), str(encoded_mp3)], check=True, capture_output=True)
    from_wav = decode_disklavier_audio(source, tmp_path / "wav-music.wav")
    from_mp3 = decode_disklavier_audio(encoded_mp3, tmp_path / "mp3-music.wav")
    assert [event.data for event in from_mp3.events] == [event.data for event in from_wav.events]
    assert [event.time_seconds for event in from_mp3.events] == [event.time_seconds for event in from_wav.events]
    assert _read(tmp_path / "mp3-music.wav").shape == _read(tmp_path / "wav-music.wav").shape
    assert from_mp3.control_channel == "right"


def test_mp3_with_lossy_control_damage_is_retained_without_guessed_midi(tmp_path):
    command = find_lame_command()
    if not command:
        pytest.skip("LAME is not available")
    source, _ = _encoded(tmp_path)
    damaged = tmp_path / "lossy.mp3"
    # With this reproducible phrase, 128 kbps changes control nibbles: the
    # receiver must reject it even though the music still sounds reasonable.
    subprocess.run([command, "--silent", "--cbr", "-b", "128", "-m", "s", "-q", "2",
                    str(source), str(damaged)], check=True, capture_output=True)
    original = damaged.read_bytes()
    with pytest.raises(DisklavierDecodeError, match="damaged or uncertain"):
        decode_disklavier_audio(damaged, tmp_path / "music.wav")
    assert damaged.read_bytes() == original
    assert not (tmp_path / "music.wav").exists()
    assert not list(tmp_path.glob(".aps-decode-audio-*"))


def test_mp3_failure_is_preserved_and_does_not_leave_staging(tmp_path, monkeypatch):
    source, destination = tmp_path / "bad.mp3", tmp_path / "music.wav"
    source.write_bytes(b"not an MP3")

    def failed_decode(_source, output, *, cancel):
        Path(output).write_bytes(b"partial PCM")
        raise RuntimeError("MP3 decoder failed")

    monkeypatch.setattr("aps_midi_prep_tool_app.disklavier_audio_decode.decode_mp3", failed_decode)
    with pytest.raises(RuntimeError, match="decoder failed"):
        decode_disklavier_audio(source, destination)
    assert source.read_bytes() == b"not an MP3"
    assert not destination.exists()
    assert not list(tmp_path.glob(".aps-decode-audio-*"))
