"""The accompaniment overview preserves source time without plotting control data."""

import threading
import wave

import numpy as np
import pytest

from aps_midi_prep_tool_app import disklavier_decoder_preview as preview
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav
from aps_midi_prep_tool_app.disklavier_decoder import (
    DisklavierDecodingCancelled, decode_disklavier_wav,
)


def _write_wav(path, samples, *, channels=1, rate=44100, width=2):
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        output.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def test_waveform_keeps_timed_pulses_leading_silence_and_absolute_amplitude(tmp_path):
    samples = np.zeros(2 * 44100, dtype="<i2")
    samples[22050] = 16384  # 0.5 s, after two silent quarter-second bins.
    samples[44100] = -32768
    samples[77175] = 32767  # 1.75 s, the last bin still covers its silence.
    source = tmp_path / "accompaniment.wav"
    _write_wav(source, samples)

    result = preview._accompaniment_waveform(source, max_bins=8)

    assert result["frame_count"] == len(samples)
    assert result["sample_rate"] == 44100
    assert result["duration_seconds"] == 2.0
    assert result["frames_per_bin"] == 11025
    assert result["minimums"] == [0, 0, 0, 0, -1, 0, 0, 0]
    assert result["maximums"] == [0, 0, .5, 0, 0, 0, 0, 32767 / 32768]


def test_partial_bins_crossing_stream_chunks_retain_both_extrema(tmp_path, monkeypatch):
    samples = np.random.default_rng(812).integers(-32768, 32768, 101, dtype=np.int16)
    source = tmp_path / "accompaniment.wav"
    _write_wav(source, samples)
    monkeypatch.setattr(preview, "_WAVEFORM_BLOCK_FRAMES", 13)

    result = preview._accompaniment_waveform(source, max_bins=10)

    # Eleven-frame bins cross thirteen-frame read buffers. The final two
    # frames need their own bin, not padding or a stretched time coordinate.
    expected = [samples[index:index + 11] for index in range(0, len(samples), 11)]
    assert result["frames_per_bin"] == 11
    assert result["frame_count"] == 101
    assert result["minimums"] == [int(part.min()) / 32768 for part in expected]
    assert result["maximums"] == [int(part.max()) / 32768 for part in expected]


def test_long_waveform_has_bounded_reads_and_output(monkeypatch):
    class LongAccompaniment:
        """Exercise a multi-hour stream without materializing it in the test."""

        frames = 44100 * 60 * 120 + 17
        position = 0
        requests = []
        silence = bytes(65536 * 2)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def getnchannels(self):
            return 1

        def getsampwidth(self):
            return 2

        def getframerate(self):
            return 44100

        def getcomptype(self):
            return "NONE"

        def getnframes(self):
            return self.frames

        def readframes(self, count):
            self.requests.append(count)
            assert 0 < count <= 65536
            self.position += count
            assert self.position <= self.frames
            return self.silence[:count * 2]

    source = LongAccompaniment()
    monkeypatch.setattr(preview.wave, "open", lambda *_args: source)
    progress = []

    result = preview._accompaniment_waveform("decoded.wav", progress=progress.append)

    assert source.position == source.frames
    assert len(source.requests) > 1
    assert len(result["minimums"]) == len(result["maximums"]) <= 2048
    assert not any(result["minimums"])
    assert not any(result["maximums"])
    assert result["duration_seconds"] == source.frames / 44100
    assert progress[0] == 0 and progress[-1] == 1
    assert progress == sorted(progress)


@pytest.mark.parametrize("cancel_at", [0, .2, 1])
def test_waveform_cancellation_during_stream_or_at_completion(tmp_path, cancel_at):
    source = tmp_path / "accompaniment.wav"
    _write_wav(source, np.zeros(3 * 65536, dtype="<i2"))
    original = source.read_bytes()
    canceled = threading.Event()
    values = []

    def progress(value):
        values.append(value)
        if value >= cancel_at:
            canceled.set()

    with pytest.raises(DisklavierDecodingCancelled):
        preview._accompaniment_waveform(source, cancel=canceled, progress=progress)

    assert values[-1] >= cancel_at
    if cancel_at < 1:
        assert values[-1] < 1
    assert source.read_bytes() == original


@pytest.mark.parametrize("channels,rate,width", [(2, 44100, 2), (1, 48000, 2), (1, 44100, 1)])
def test_waveform_rejects_encoded_stereo_and_unexpected_pcm(tmp_path, channels, rate, width):
    source = tmp_path / "unsafe.wav"
    _write_wav(source, np.zeros(200, dtype="<i2"), channels=channels, rate=rate, width=width)
    with pytest.raises(ValueError, match="decoded mono"):
        preview._accompaniment_waveform(source)


def test_truncated_accompaniment_is_not_presented_as_silence(tmp_path):
    source = tmp_path / "truncated.wav"
    _write_wav(source, np.zeros(200, dtype="<i2"))
    source.write_bytes(source.read_bytes()[:-10])
    with pytest.raises(ValueError, match="truncated"):
        preview._accompaniment_waveform(source)


def test_prepared_waveform_uses_decoded_left_channel_and_retains_midi_preroll(tmp_path, monkeypatch):
    music = tmp_path / "music.wav"
    encoded = tmp_path / "encoded.wav"
    samples = np.zeros(44100, dtype="<i2")
    onset = 44100 // 4
    samples[onset:onset + 100] = 16384
    _write_wav(music, samples)
    encode_disklavier_wav([
        (.25, b"\x90\x3c\x64"), (.5, b"\x80\x3c\x00"),
    ], music, encoded)
    decoded = decode_disklavier_wav(encoded, tmp_path / "decoded.mid", tmp_path / "safe.wav")
    monkeypatch.setattr(preview, "_render_soundfont", lambda *_args: False)

    result = preview.prepare_preview(decoded, tmp_path)

    waveform = result["accompaniment_waveform"]
    assert waveform["duration_seconds"] == decoded["duration_seconds"]
    assert max(waveform["maximums"]) == .5
    assert min(waveform["minimums"]) == 0
    nonzero = [index for index, value in enumerate(waveform["maximums"]) if value]
    width_seconds = waveform["frames_per_bin"] / waveform["sample_rate"]
    first_time = nonzero[0] * width_seconds
    last_time = min((nonzero[-1] + 1) * width_seconds, waveform["duration_seconds"])
    # The audio pulse and recovered note both retain the encoder's 0.5 s
    # preroll. A plot of the right channel would be nonzero throughout.
    assert first_time <= .75 < first_time + width_seconds
    assert first_time <= result["notes"][0]["start_sec"] < last_time
    assert not any(waveform["maximums"][:nonzero[0]])
    assert not any(waveform["maximums"][nonzero[-1] + 1:])
