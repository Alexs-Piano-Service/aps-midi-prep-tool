"""A completed control WAV must recover notes that a real MIDI piano can sound."""

import wave

import numpy as np

from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav
from aps_midi_prep_tool_app.disklavier_decoder import decode_disklavier_wav


def test_encoded_wav_recovers_audible_piano_with_timing_and_pitch(
    tmp_path, render_midi, audio_tools, monkeypatch,
):
    music = tmp_path / "accompaniment.wav"
    encoded = tmp_path / "completed.wav"
    recovered = tmp_path / "decoded.mid"
    accompaniment = tmp_path / "decoded-accompaniment.wav"
    with wave.open(str(music), "wb") as writer:
        writer.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        writer.writeframes(np.zeros(44100 * 3, dtype="<i2").tobytes())
    # Original test phrase: middle C, a quiet gap, then A above middle C.
    encode_disklavier_wav([
        (0.0, b"\xc0\x00"),
        (0.1, b"\x90\x3c\x64"),
        (0.7, b"\x80\x3c\x00"),
        (1.7, b"\x90\x45\x64"),
        (2.3, b"\x80\x45\x00"),
    ], music, encoded)
    decode_disklavier_wav(encoded, recovered, accompaniment)
    audio = render_midi(recovered)
    # Encoded MIDI and accompaniment retain their common half-second lead-in.
    assert audio.rms(0.1, 0.4) < 0.0001
    assert audio.rms(0.7, 1.0) > 0.002
    assert audio.rms(1.8, 2.1) < 0.001
    assert audio.rms(2.3, 2.6) > 0.002
    audio.assert_pitch(0.7, 1.0, 60)
    audio.assert_pitch(2.3, 2.6, 69)
    with wave.open(str(accompaniment), "rb") as reader:
        assert reader.getnchannels() == 1
        assert not np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").any()

    # Exercise the player's own offline SoundFont path as well as the
    # independent synth oracle above. No audio device or server is required.
    from aps_midi_prep_tool_app import main_window
    from aps_midi_prep_tool_app.disklavier_decoder_preview import prepare_preview

    command, soundfont = audio_tools
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: command)
    monkeypatch.setattr(main_window, "_find_preview_soundfont", lambda: str(soundfont))
    result = prepare_preview({
        "midi_path": str(recovered), "accompaniment_path": str(accompaniment),
        "duration_seconds": 3.5,
    }, tmp_path)
    assert result["renderer"] == "soundfont"
    assert len(result["notes"]) == 2
    with wave.open(result["piano_path"], "rb") as reader:
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 44100
        channels = reader.getnchannels()
        samples = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").reshape(-1, channels)
        assert np.abs(samples[round(.7 * 44100):round(1.0 * 44100)]).max() > 100
