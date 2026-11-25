import threading
import wave
import os
from pathlib import Path

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import audio_tools, disklavier_audio_decode, disklavier_decoder
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.disklavier_playback_source import _check_plain_audio, prepare_playback_source


def _midi(path, *, midi_type=1, ticks=480, note=64):
    midi = mido.MidiFile(type=midi_type, ticks_per_beat=ticks)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000, time=0),
        mido.Message("note_on", note=note, velocity=80, time=480),
        mido.MetaMessage("set_tempo", tempo=1000000, time=480),
        mido.Message("note_off", note=note, time=480),
        mido.MetaMessage("end_of_track", time=480),
    ]))
    midi.save(path)
    return path


def _audio(path, *, channels=2, seconds=1, rate=44100):
    frames = round(seconds * rate)
    time = np.arange(frames) / rate
    first = np.rint(2500 * np.sin(2 * np.pi * 440 * time)).astype("<i2")
    first[:rate // 4] = 0
    samples = first[:, None]
    if channels == 2:
        second = np.rint(2000 * np.cos(2 * np.pi * 330 * time)).astype("<i2")
        second[:rate // 4] = 0
        samples = np.column_stack((first, second))
    _write_audio(path, samples, rate=rate)
    return path


def _write_audio(path, samples, rate=44100):
    samples = np.asarray(samples, dtype="<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setparams((samples.shape[1], 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(samples.tobytes())


def _pcm(path):
    with wave.open(str(path), "rb") as reader:
        return np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").reshape(
            -1, reader.getnchannels())


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "preview"
    folder.mkdir()
    return folder


@pytest.fixture
def encoded(tmp_path):
    source = _audio(tmp_path / "music.wav", seconds=.4)
    destination = tmp_path / "Song.wav"
    encode_disklavier_wav([(0, b"\x90\x3c\x50"), (.3, b"\x80\x3c\x00")], source, destination)
    return destination


@pytest.fixture
def mp3_pair(tmp_path):
    if not audio_tools.find_lame_command():
        if os.environ.get("APS_REQUIRE_AUDIO_TESTS") == "1":
            pytest.fail("The required LAME MP3 playback regression cannot run without LAME.")
        pytest.skip("LAME is unavailable")
    seconds = np.arange(44100 * 2) / 44100
    samples = np.column_stack((
        12000 * np.sin(2 * np.pi * 440 * seconds) * ((seconds >= .5) & (seconds < 1.6)),
        9000 * np.sin(2 * np.pi * 660 * seconds) * ((seconds >= .75) & (seconds < 1.8)),
    )).astype("<i2")
    source = tmp_path / "original-audio.wav"
    _write_audio(source, samples)
    mp3 = tmp_path / "Song.mp3"
    audio_tools.encode_mp3(source, mp3)
    midi = _midi(tmp_path / "Song.mid")
    return midi, mp3, samples


@pytest.mark.parametrize("extension,channels", [(".mid", 1), (".MIDI", 2)])
def test_selected_midi_preserves_pair_stereo_silence_and_tempo_map(tmp_path, workspace, extension, channels):
    midi = _midi(tmp_path / ("Song" + extension))
    audio = _audio(tmp_path / "Song.WAV", channels=channels)
    before = {path: path.read_bytes() for path in (midi, audio)}
    progress = []
    result = prepare_playback_source(midi, workspace, progress=lambda *item: progress.append(item))
    assert result["source_kind"] == "pair"
    assert result["channels"] == channels
    assert result["duration_seconds"] == 3
    assert result["source_midi_path"] == str(midi)
    assert result["source_audio_path"] == str(audio)
    assert Path(result["midi_path"]).read_bytes() == before[midi]
    assert Path(result["accompaniment_path"]).read_bytes() == before[audio]
    assert read_midi_events(result["midi_path"]) == read_midi_events(midi)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert progress[0] == ("inspect", 0)
    assert progress[-1] == ("audio", 1)


def test_plain_wav_selects_case_insensitive_midi_companion(tmp_path, workspace):
    midi = _midi(tmp_path / "SONG.MID")
    audio = _audio(tmp_path / "song.wav")
    result = prepare_playback_source(audio, workspace)
    assert result["source_midi_path"] == str(midi)
    assert result["source_kind"] == "pair"
    assert np.array_equal(_pcm(result["accompaniment_path"]), _pcm(audio))


def test_exact_stem_is_preferred_over_case_insensitive_companion(tmp_path, workspace):
    midi = _midi(tmp_path / "Song.mid")
    exact = _audio(tmp_path / "Song.WAV", channels=1)
    other = tmp_path / "song.wav"
    if other.exists():
        pytest.skip("Requires a case-sensitive filesystem")
    _audio(other, channels=2)
    result = prepare_playback_source(midi, workspace)
    assert result["source_audio_path"] == str(exact)


@pytest.mark.parametrize("select_midi", [True, False])
def test_ambiguous_companions_are_rejected_without_outputs(tmp_path, workspace, select_midi):
    if select_midi:
        selected = _midi(tmp_path / "Song.mid")
        _audio(tmp_path / "SONG.wav")
        other = tmp_path / "song.WAV"
        if other.exists():
            pytest.skip("Requires a case-sensitive filesystem")
        _audio(other)
    else:
        selected = _audio(tmp_path / "Song.wav")
        _midi(tmp_path / "Song.mid")
        _midi(tmp_path / "Song.midi")
    with pytest.raises(ValueError, match="More than one matching companion"):
        prepare_playback_source(selected, workspace)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize("select_midi", [True, False])
def test_missing_companion_is_reported_and_original_kept(tmp_path, workspace, select_midi):
    selected = _midi(tmp_path / "Song.mid") if select_midi else _audio(tmp_path / "Song.wav")
    original = selected.read_bytes()
    with pytest.raises(ValueError, match="No matching (WAV or MP3|MIDI) file"):
        prepare_playback_source(selected, workspace)
    assert selected.read_bytes() == original
    assert not list(workspace.iterdir())


def test_selected_encoded_wav_prefers_embedded_midi_over_ambiguous_companions(tmp_path, workspace, encoded):
    _midi(tmp_path / "Song.mid", note=70)
    _midi(tmp_path / "Song.midi", note=71)
    original = encoded.read_bytes()
    result = prepare_playback_source(encoded, workspace)
    assert result["source_kind"] == "encoded"
    assert result["source_midi_path"] is None
    assert result["source_audio_path"] == str(encoded)
    events = read_midi_events(result["midi_path"])
    assert [data[1] for _, data in events] == [60, 60]
    assert np.array_equal(_pcm(result["accompaniment_path"])[:, 0], _pcm(encoded)[:, 0])
    assert encoded.read_bytes() == original


def test_selected_midi_uses_explicit_midi_and_removes_encoded_control(tmp_path, workspace, encoded):
    midi = _midi(tmp_path / "Song.mid", note=70)
    result = prepare_playback_source(midi, workspace)
    assert result["source_kind"] == "pair"
    assert result["control_channel_removed"] is True
    assert result["channels"] == 1
    assert Path(result["midi_path"]).read_bytes() == midi.read_bytes()
    assert np.array_equal(_pcm(result["accompaniment_path"])[:, 0], _pcm(encoded)[:, 0])


def test_control_on_left_is_decoded_instead_of_played_raw(tmp_path, workspace, encoded):
    original = _pcm(encoded)
    _write_audio(encoded, original[:, ::-1])
    _midi(tmp_path / "Song.mid")
    result = prepare_playback_source(encoded, workspace)
    assert result["source_kind"] == "encoded"
    assert result["control_channel"] == "left"
    assert np.array_equal(_pcm(result["accompaniment_path"])[:, 0], original[:, 0])
    assert [data[1] for _, data in read_midi_events(result["midi_path"])] == [60, 60]


@pytest.mark.parametrize("select_midi", [True, False])
def test_damaged_control_is_never_played_as_plain_pair(tmp_path, workspace, encoded, select_midi):
    samples = _pcm(encoded).copy()
    samples[14000:14014, 1] = 0
    _write_audio(encoded, samples)
    midi = _midi(tmp_path / "Song.mid")
    with pytest.raises(ValueError, match="control signal|control stream|control audio"):
        prepare_playback_source(midi if select_midi else encoded, workspace)
    assert not list(workspace.iterdir())


def test_short_carrier_fragment_is_not_classified_as_plain_audio(tmp_path, workspace, encoded):
    samples = _pcm(encoded)[2000:2280]
    _write_audio(encoded, samples)
    midi = _midi(tmp_path / "Song.mid")
    with pytest.raises(ValueError, match="control audio could not be decoded safely"):
        prepare_playback_source(midi, workspace)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize("frequency", [3136, 3150, 6000, 6300, 6500])
def test_high_frequency_musical_tones_are_plain_accompaniment(tmp_path, workspace, frequency):
    midi = _midi(tmp_path / "Song.mid")
    audio = tmp_path / "Song.wav"
    samples = np.rint(7000 * np.cos(np.arange(4410) * 2 * np.pi * frequency / 44100))
    _write_audio(audio, np.column_stack((samples, samples)))
    result = prepare_playback_source(midi, workspace)
    assert result["source_kind"] == "pair"
    assert result["channels"] == 2
    assert np.array_equal(_pcm(result["accompaniment_path"]), _pcm(audio))


@pytest.mark.parametrize("select_midi", [True, False])
def test_control_in_both_channels_is_never_played(tmp_path, workspace, encoded, select_midi):
    samples = _pcm(encoded)
    _write_audio(encoded, np.column_stack((samples[:, 1], samples[:, 1])))
    midi = _midi(tmp_path / "Song.mid")
    with pytest.raises(ValueError, match="control audio could not be decoded safely"):
        prepare_playback_source(midi if select_midi else encoded, workspace)
    assert not list(workspace.iterdir())


def test_short_control_fragment_across_read_boundary_is_detected(tmp_path, workspace, encoded):
    idle = _pcm(encoded)[1400:1400 + 14 * 10, 1]
    samples = np.zeros((14 * 4096 + 500, 1), dtype="<i2")
    first = 14 * 4096 - 14 * 4
    samples[first:first + len(idle), 0] = idle
    _write_audio(encoded, samples)
    midi = _midi(tmp_path / "Song.mid")
    with pytest.raises(ValueError, match="control audio could not be decoded safely"):
        prepare_playback_source(midi, workspace)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize("kind", ["type2", "smpte", "invalid"])
def test_midi_without_shared_beat_timeline_is_rejected(tmp_path, workspace, kind):
    midi = tmp_path / "Song.mid"
    if kind == "invalid":
        midi.write_bytes(b"this is not MIDI")
    else:
        _midi(midi, midi_type=2 if kind == "type2" else 1, ticks=-6360 if kind == "smpte" else 480)
    _audio(tmp_path / "Song.wav", channels=1)
    with pytest.raises(ValueError, match="MIDI companion cannot be played"):
        prepare_playback_source(midi, workspace)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize("kind", ["rate", "truncated", "invalid", "empty"])
def test_bad_audio_is_rejected_without_partial_files(tmp_path, workspace, kind):
    midi = _midi(tmp_path / "Song.mid")
    audio = _audio(tmp_path / "Song.wav", channels=1, rate=48000 if kind == "rate" else 44100)
    if kind == "truncated":
        audio.write_bytes(audio.read_bytes()[:-2])
    elif kind == "invalid":
        audio.write_bytes(b"not a WAV")
    elif kind == "empty":
        _write_audio(audio, np.zeros((0, 1)))
    with pytest.raises(ValueError):
        prepare_playback_source(midi, workspace)
    assert not list(workspace.iterdir())


def test_cancellation_during_copy_removes_only_owned_files(tmp_path, workspace):
    selected = _midi(tmp_path / "Song.mid")
    audio = _audio(tmp_path / "Song.wav")
    unrelated = workspace / "keep.txt"
    unrelated.write_text("keep me")
    original = audio.read_bytes()
    cancel = threading.Event()

    def progress(phase, fraction):
        if phase == "inspect" and fraction > 0:
            cancel.set()

    with pytest.raises(disklavier_decoder.DisklavierDecodingCancelled):
        prepare_playback_source(selected, workspace, cancel=cancel, progress=progress)
    assert list(workspace.iterdir()) == [unrelated]
    assert unrelated.read_text() == "keep me"
    assert audio.read_bytes() == original


def test_broader_decoder_cancellation_uses_preview_cancellation_type(tmp_path, workspace, encoded, monkeypatch):
    def strict(*args, **kwargs):
        raise ValueError("Use broader receiver")

    def broad(*args, **kwargs):
        raise disklavier_audio_decode.DisklavierDecodingCancelled("Cancelled")

    monkeypatch.setattr(disklavier_decoder, "decode_disklavier_wav", strict)
    monkeypatch.setattr(disklavier_audio_decode, "decode_disklavier_audio", broad)
    with pytest.raises(disklavier_decoder.DisklavierDecodingCancelled):
        prepare_playback_source(encoded, workspace)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize("selected_kind", ["midi", "mp3"])
def test_real_mp3_pair_preserves_stereo_duration_onsets_and_midi(workspace, mp3_pair, selected_kind):
    midi, mp3, original_pcm = mp3_pair
    before = {path: path.read_bytes() for path in (midi, mp3)}
    progress = []
    result = prepare_playback_source(midi if selected_kind == "midi" else mp3, workspace,
                                     progress=lambda *item: progress.append(item))
    actual = _pcm(result["accompaniment_path"])
    assert result["source_kind"] == "pair"
    assert result["format"] == "MIDI/MP3 pair"
    assert result["source_audio_path"] == str(mp3)
    assert result["source_midi_path"] == str(midi)
    assert result["duration_seconds"] == 3  # MIDI ending extends beyond the 2-second audio.
    assert result["channels"] == 2
    assert abs(len(actual) - len(original_pcm)) <= 1
    assert Path(result["midi_path"]).read_bytes() == before[midi]
    assert read_midi_events(result["midi_path"]) == read_midi_events(midi)
    # Compare on the original sample timeline, without shifting the result.
    # Encoder priming or swapped/downmixed stereo would fail these checks.
    for channel in (0, 1):
        length = min(len(actual), len(original_pcm))
        assert np.corrcoef(actual[:length, channel], original_pcm[:length, channel])[0, 1] > .98
        onset = int(np.flatnonzero(np.abs(original_pcm[:, channel]) > 1000)[0])
        recovered_onset = int(np.flatnonzero(np.abs(actual[:, channel]) > 1000)[0])
        assert abs(recovered_onset - onset) <= 128
    assert abs(np.corrcoef(actual[:, 0], actual[:, 1])[0, 1]) < .02
    assert all(path.read_bytes() == content for path, content in before.items())
    assert ("mp3", -1) in progress
    assert ("mp3", 1) in progress
    assert not list(workspace.rglob("*.mp3"))


def test_mixed_case_mp3_companion_and_direct_selection(workspace, mp3_pair):
    midi, mp3, _samples = mp3_pair
    renamed = mp3.with_name("SONG.MP3")
    mp3.rename(renamed)
    from_midi = prepare_playback_source(midi, workspace)
    assert from_midi["source_audio_path"] == str(renamed)
    direct = prepare_playback_source(renamed, workspace)
    assert direct["source_midi_path"] == str(midi)
    assert direct["source_audio_path"] == str(renamed)


def test_midi_prefers_wav_but_direct_mp3_uses_explicit_audio(tmp_path, workspace, mp3_pair):
    midi, mp3, original_pcm = mp3_pair
    wav = _audio(tmp_path / "Song.WAV", channels=1)
    selected_midi = prepare_playback_source(midi, workspace)
    assert selected_midi["source_audio_path"] == str(wav)
    assert selected_midi["format"] == "MIDI/WAV pair"
    assert Path(selected_midi["accompaniment_path"]).read_bytes() == wav.read_bytes()
    selected_mp3 = prepare_playback_source(mp3, workspace)
    assert selected_mp3["source_audio_path"] == str(mp3)
    assert selected_mp3["channels"] == 2
    assert abs(len(_pcm(selected_mp3["accompaniment_path"])) - len(original_pcm)) <= 1


def test_exact_stem_mp3_is_preferred_over_case_insensitive_mp3(tmp_path, workspace, mp3_pair):
    midi, mp3, _samples = mp3_pair
    other = tmp_path / "SONG.MP3"
    if other.exists():
        pytest.skip("Requires a case-sensitive filesystem")
    other.write_bytes(b"not the selected audio")
    result = prepare_playback_source(midi, workspace)
    assert result["source_audio_path"] == str(mp3)


def test_ambiguous_mp3_companions_are_rejected_before_conversion(tmp_path, workspace, monkeypatch):
    midi = _midi(tmp_path / "Song.mid")
    (tmp_path / "SONG.mp3").write_bytes(b"first")
    other = tmp_path / "song.MP3"
    if other.exists():
        pytest.skip("Requires a case-sensitive filesystem")
    other.write_bytes(b"second")
    monkeypatch.setattr(audio_tools, "decode_mp3", lambda *a, **k: pytest.fail("Ambiguous audio decoded"))
    with pytest.raises(ValueError, match="More than one matching companion"):
        prepare_playback_source(midi, workspace)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize("select_midi", [True, False])
def test_missing_lame_is_reported_before_creating_workspace(tmp_path, workspace, monkeypatch, select_midi):
    midi = _midi(tmp_path / "Song.mid")
    mp3 = tmp_path / "Song.mp3"
    mp3.write_bytes(b"untouched")
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "")
    monkeypatch.setattr(audio_tools, "decode_mp3", lambda *a, **k: pytest.fail("LAME preflight was bypassed"))
    with pytest.raises(RuntimeError, match="LAME is required to play MP3"):
        prepare_playback_source(midi if select_midi else mp3, workspace)
    assert not list(workspace.iterdir())
    assert mp3.read_bytes() == b"untouched"


def test_wav_pair_does_not_require_lame_even_with_mp3_companion(tmp_path, workspace, monkeypatch):
    midi = _midi(tmp_path / "Song.mid")
    wav = _audio(tmp_path / "Song.wav", channels=1)
    (tmp_path / "Song.mp3").write_bytes(b"unused")
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "")
    result = prepare_playback_source(midi, workspace)
    assert result["source_audio_path"] == str(wav)


@pytest.mark.parametrize("cancelled", [False, True])
def test_mp3_conversion_failure_or_cancellation_cleans_private_files(
        tmp_path, workspace, monkeypatch, cancelled):
    midi = _midi(tmp_path / "Song.mid")
    mp3 = tmp_path / "Song.mp3"
    mp3.write_bytes(b"untouched")
    unrelated = workspace / "keep.txt"
    unrelated.write_text("keep me")
    cancel = threading.Event()
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "test-lame")

    def conversion(source, destination, *, cancel):
        assert Path(source).read_bytes() == mp3.read_bytes()
        assert Path(source) != mp3
        Path(destination).write_bytes(b"partial PCM")
        if cancelled:
            cancel.set()
            raise InterruptedError("Audio conversion cancelled.")
        raise RuntimeError("Invalid MP3 bitstream")

    monkeypatch.setattr(audio_tools, "decode_mp3", conversion)
    error = disklavier_decoder.DisklavierDecodingCancelled if cancelled else RuntimeError
    with pytest.raises(error):
        prepare_playback_source(midi, workspace, cancel=cancel)
    assert list(workspace.iterdir()) == [unrelated]
    assert unrelated.read_text() == "keep me"
    assert mp3.read_bytes() == b"untouched"


def test_mp3_decoded_control_uses_encoding_validation_pipeline(tmp_path, workspace, encoded, monkeypatch):
    mp3 = tmp_path / "Song.mp3"
    mp3.write_bytes(b"compressed carrier fixture")
    _midi(tmp_path / "Song.mid", note=70)
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "test-lame")

    def conversion(source, destination, *, cancel):
        Path(destination).write_bytes(encoded.read_bytes())

    monkeypatch.setattr(audio_tools, "decode_mp3", conversion)
    result = prepare_playback_source(mp3, workspace)
    assert result["source_kind"] == "encoded"
    assert result["source_audio_path"] == str(mp3)
    assert result["source_midi_path"] is None
    assert result["channels"] == 1
    assert [data[1] for _, data in read_midi_events(result["midi_path"])] == [60, 60]
    assert np.array_equal(_pcm(result["accompaniment_path"])[:, 0], _pcm(encoded)[:, 0])


@pytest.mark.parametrize("channels,leading_silence", [(1, .25), (2, .25), (2, 2.5)])
def test_ordinary_pair_reports_audio_checks_without_announcing_piano_decode(
        tmp_path, workspace, channels, leading_silence):
    midi = _midi(tmp_path / "Song.mid")
    audio = _audio(tmp_path / "Song.wav", channels=channels, seconds=4)
    samples = _pcm(audio).copy()
    samples[:round(leading_silence * 44100)] = 0
    _write_audio(audio, samples)
    progress = []
    result = prepare_playback_source(midi, workspace, progress=lambda *item: progress.append(item))
    assert result["source_kind"] == "pair"
    assert "decode" not in [phase for phase, _fraction in progress]
    preparation = [fraction for phase, fraction in progress if phase in {"check_audio", "audio"}]
    assert preparation[0] == 0
    assert preparation[-1] == 1
    assert preparation == sorted(preparation)
    checks = [value for phase, value in progress if phase == "check_audio"]
    assert len([value for value in checks if 0.6 < value < 1]) >= 2
    assert Path(result["accompaniment_path"]).read_bytes() == audio.read_bytes()


def test_full_audio_guard_reports_each_completed_block(tmp_path):
    audio = _audio(tmp_path / "audio.wav", channels=1, seconds=4)
    progress = []
    _check_plain_audio(audio, None, progress.append)
    frames, block = 4 * 44100, 14 * 4096
    expected = [0] + [min(first + block, frames) / frames for first in range(0, frames, block)]
    assert progress == expected


@pytest.mark.parametrize("channels", [1, 2])
def test_cancel_during_full_audio_guard_removes_only_private_files(tmp_path, workspace, channels):
    midi = _midi(tmp_path / "Song.mid")
    audio = _audio(tmp_path / "Song.wav", channels=channels, seconds=4)
    original = audio.read_bytes()
    unrelated = workspace / "keep.txt"
    unrelated.write_text("keep me")
    cancel = threading.Event()
    updates = []

    def progress(phase, value):
        updates.append((phase, value))
        # Stereo reaches this range only during the full fragment check,
        # after broad acquisition; mono goes straight to the full check.
        if phase == "check_audio" and .6 < value < 1:
            cancel.set()

    with pytest.raises(disklavier_decoder.DisklavierDecodingCancelled):
        prepare_playback_source(midi, workspace, cancel=cancel, progress=progress)
    assert cancel.is_set()
    assert "audio" not in [phase for phase, _value in updates]
    assert list(workspace.iterdir()) == [unrelated]
    assert unrelated.read_text() == "keep me"
    assert audio.read_bytes() == original


def test_broad_receiver_announces_decode_only_after_acquisition(tmp_path, workspace, encoded):
    samples = _pcm(encoded)
    _write_audio(encoded, samples[:, ::-1])
    progress = []
    result = prepare_playback_source(encoded, workspace, progress=lambda *item: progress.append(item))
    assert result["source_kind"] == "encoded"
    decoding = [value for phase, value in progress if phase == "decode"]
    assert decoding and min(decoding) > .6 and max(decoding) <= .8
    preparation = [(phase, value) for phase, value in progress if phase in {"check_audio", "decode"}]
    assert [value for _phase, value in preparation] == sorted(value for _phase, value in preparation)
    first_decode = next(index for index, (phase, _value) in enumerate(preparation) if phase == "decode")
    assert preparation[first_decode - 1][0] == "check_audio"
    assert preparation[first_decode - 1][1] == pytest.approx(.6)
    assert preparation[-1] == ("check_audio", 1)
