"""Balanced previews mix only derived stems without moving their timeline."""

import threading
import wave

import numpy as np
import pytest

from aps_midi_prep_tool_app import disklavier_decoder_preview as preview
from aps_midi_prep_tool_app.disklavier_decoder import DisklavierDecodingCancelled


def _write_wav(path, samples, *, channels=1, width=2, rate=44100):
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        output.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def _read_wav(path):
    with wave.open(str(path), "rb") as source:
        assert source.getparams()[:3] == (2, 2, 44100)
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").reshape(-1, 2)


@pytest.mark.parametrize("balance", [0, .25, .5, .75, 1])
def test_balance_has_exact_gains_and_preserves_stereo_piano(tmp_path, balance):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    keys = np.array([[0, 0], [16000, -8000], [-32768, 32767], [32767, 32767]], dtype="<i2")
    audio = np.array([[0], [-4000], [-32768], [32767]], dtype="<i2")
    _write_wav(piano, keys, channels=2)
    _write_wav(accompaniment, audio)
    originals = piano.read_bytes(), accompaniment.read_bytes()

    preview._mix_preview(piano, accompaniment, mixed, balance=balance)

    expected = (keys.astype(float) * (1 - balance) + audio.astype(float) * balance).astype("<i2")
    assert np.array_equal(_read_wav(mixed), expected)
    assert (piano.read_bytes(), accompaniment.read_bytes()) == originals


def test_default_balance_preserves_existing_half_gain_mix(tmp_path):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    _write_wav(piano, [0, 8000, -8000])
    _write_wav(accompaniment, [0, 4000, 4000])

    preview._mix_preview(piano, accompaniment, mixed)

    assert np.array_equal(_read_wav(mixed), [[0, 0], [6000, 6000], [-2000, -2000]])


@pytest.mark.parametrize("balance", [0, .25, .5, 1])
@pytest.mark.parametrize("piano_channels", [1, 2])
@pytest.mark.parametrize("longer_stem", ["piano", "accompaniment"])
def test_pair_balance_preserves_stereo_channels_and_shared_timeline(
        tmp_path, balance, piano_channels, longer_stem):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    short_frames, long_frames = 44100 + 3, 2 * 44100 + 7
    keys = np.zeros((long_frames if longer_stem == "piano" else short_frames, piano_channels), dtype="<i2")
    audio = np.zeros((long_frames if longer_stem == "accompaniment" else short_frames, 2), dtype="<i2")
    keys[22050] = 8000 if piano_channels == 1 else [8000, -4000]
    audio[22051] = [16000, -12000]
    keys[44100:44103] = -8000 if piano_channels == 1 else [-8000, 16000]
    audio[44100:44103] = [4000, 20000]
    keys[-1] = 12000
    audio[-1] = [-20000, 10000]
    _write_wav(piano, keys, channels=piano_channels)
    _write_wav(accompaniment, audio, channels=2)
    originals = piano.read_bytes(), accompaniment.read_bytes()
    progress = []

    preview._mix_preview(piano, accompaniment, mixed, balance=balance,
                         allow_stereo=True, progress=progress.append)

    expected = np.zeros((long_frames, 2), dtype=float)
    expected[:len(keys)] += keys * (1 - balance)
    expected[:len(audio)] += audio * balance
    result = _read_wav(mixed)
    assert np.array_equal(result, expected.astype("<i2"))
    assert not result[:22050].any()
    assert progress == sorted(progress) and len(progress) == 3 and progress[-1] == 1
    assert (piano.read_bytes(), accompaniment.read_bytes()) == originals


@pytest.mark.parametrize("truncated_stem", ["piano", "accompaniment"])
@pytest.mark.parametrize("balance", [0, 1])
@pytest.mark.parametrize("allow_stereo", [False, True])
def test_mix_rejects_truncated_pcm_even_when_stem_is_muted(
        tmp_path, truncated_stem, balance, allow_stereo):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    _write_wav(piano, np.zeros(44100 + 10, dtype="<i2"))
    channels = 2 if allow_stereo else 1
    _write_wav(accompaniment, np.zeros((44100 + 10, channels), dtype="<i2"), channels=channels)
    path = piano if truncated_stem == "piano" else accompaniment
    path.write_bytes(path.read_bytes()[:-2])

    with pytest.raises(ValueError, match="truncated"):
        preview._mix_preview(piano, accompaniment, mixed, balance=balance, allow_stereo=allow_stereo)


@pytest.mark.parametrize("balance", [0, .25, .5, 1])
@pytest.mark.parametrize("longer_stem", ["piano", "accompaniment"])
def test_balance_retains_leading_silence_block_boundary_and_longer_tail(tmp_path, balance, longer_stem):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    short_frames, long_frames = 44100 + 3, 2 * 44100 + 7
    keys = np.zeros(long_frames if longer_stem == "piano" else short_frames, dtype="<i2")
    audio = np.zeros(long_frames if longer_stem == "accompaniment" else short_frames, dtype="<i2")
    keys[22050] = 8000
    audio[22051] = 16000
    keys[44100:44103] = -8000
    audio[44100:44103] = 4000
    keys[-1] = 12000
    audio[-1] = -20000
    _write_wav(piano, keys)
    _write_wav(accompaniment, audio)
    progress = []

    preview._mix_preview(piano, accompaniment, mixed, balance=balance, progress=progress.append)

    result = _read_wav(mixed)
    expected = np.zeros(long_frames, dtype=float)
    expected[:len(keys)] += keys * (1 - balance)
    expected[:len(audio)] += audio * balance
    assert result.shape == (long_frames, 2)
    assert np.array_equal(result[:, 0], expected.astype("<i2"))
    assert np.array_equal(result[:, 0], result[:, 1])
    assert not result[:22050].any()
    assert len(progress) == 3
    assert progress == sorted(progress)
    assert progress[-1] == 1


@pytest.mark.parametrize("balance", [-.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_invalid_balance_is_rejected_before_creating_output(tmp_path, balance):
    destination = tmp_path / "mix.wav"
    with pytest.raises(ValueError, match="balance"):
        preview._mix_preview("unused-piano.wav", "unused-audio.wav", destination, balance=balance)
    assert not destination.exists()


@pytest.mark.parametrize("invalid_stem,params", [
    ("piano", {"channels": 3}),
    ("accompaniment", {"channels": 2}),
    ("piano", {"width": 1}),
    ("accompaniment", {"width": 1}),
    ("piano", {"rate": 48000}),
    ("accompaniment", {"rate": 48000}),
])
def test_only_supported_piano_and_safe_mono_accompaniment_are_accepted(tmp_path, invalid_stem, params):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    for name, path in (("piano", piano), ("accompaniment", accompaniment)):
        _write_wav(path, np.zeros(12, dtype="<i2"), **(params if name == invalid_stem else {}))

    with pytest.raises(ValueError):
        preview._mix_preview(piano, accompaniment, mixed)
    assert not mixed.exists()


@pytest.mark.parametrize("cancel_at", [0, .2, 1])
def test_mix_can_cancel_before_during_and_at_completion(tmp_path, cancel_at):
    piano, accompaniment, mixed = (tmp_path / name for name in ("piano.wav", "audio.wav", "mix.wav"))
    _write_wav(piano, np.zeros(3 * 44100, dtype="<i2"))
    _write_wav(accompaniment, np.zeros(3 * 44100, dtype="<i2"))
    cancelled = threading.Event()
    values = []
    if cancel_at == 0:
        cancelled.set()

    def progress(value):
        values.append(value)
        if value >= cancel_at:
            cancelled.set()

    with pytest.raises(DisklavierDecodingCancelled):
        preview._mix_preview(piano, accompaniment, mixed, balance=.25,
                             cancel=cancelled, progress=progress)
    if cancel_at == 0:
        assert not mixed.exists()
        assert not values
    elif cancel_at == 1:
        assert values[-1] == 1
    else:
        assert cancel_at <= values[-1] < 1
