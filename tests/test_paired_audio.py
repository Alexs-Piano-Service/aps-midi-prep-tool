"""Paired output keeps every musical event on the clean audio timeline."""

from pathlib import Path
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import audio_tools, paired_audio
from aps_midi_prep_tool_app.disklavier_audio import read_midi_events
from aps_midi_prep_tool_app.midi_type0_converter import _parse_track_events
from aps_midi_prep_tool_app.smart_pianosoft_sync import TimedMidiEvent
from aps_midi_prep_tool_app.smf import parse_smf_layout


def _wav(path, pcm):
    with wave.open(str(path), "wb") as output:
        output.setparams((pcm.shape[1], 2, 44100, len(pcm), "NONE", "not compressed"))
        output.writeframes(pcm.astype("<i2").tobytes())


@pytest.fixture
def music(tmp_path):
    samples = np.arange(4410, dtype=np.int16)
    pcm = np.column_stack((samples, -samples))
    source = tmp_path / "music.wav"
    _wav(source, pcm)
    return source, pcm


def _fake_encoder(monkeypatch):
    captured = []
    def encode(source, destination, *, cancel=None):
        with wave.open(str(source), "rb") as audio:
            captured.append((audio.getparams(), audio.readframes(audio.getnframes())))
        Path(destination).write_bytes(b"test MP3")
    monkeypatch.setattr(paired_audio, "encode_mp3", encode)
    return captured


def test_pair_retimes_notes_pedals_sysex_and_preserves_stereo_with_shared_negative_lead_in(
    tmp_path, music, monkeypatch,
):
    source, pcm = music
    before = source.read_bytes()
    captured = _fake_encoder(monkeypatch)
    events = [
        TimedMidiEvent(0.0, b"\xf0\x43\x71\x7e\x01\xf7"),
        TimedMidiEvent(0.0, b"\xf0\x7e\x7f\x09\x01\xf7"),
        TimedMidiEvent(0.1, b"\xb0\x40\x7f"),
        TimedMidiEvent(0.1, b"\x90\x3c\x50"),
        TimedMidiEvent(0.1, b"\x91\x40\x45"),
        TimedMidiEvent(0.7, b"\x80\x3c\x20"),
        TimedMidiEvent(0.7, b"\x81\x40\x00"),
        TimedMidiEvent(0.8, b"\xb0\x40\x00"),
    ]
    result = paired_audio.export_midi_mp3(events, source, tmp_path / "01 - Song.part",
                                         midi_offset_seconds=-0.123, midi_time_scale=1.002)
    assert result["midi"] == "01 - Song.part.mid"
    assert result["audio"] == "01 - Song.part.mp3"
    assert result["hardware_verified"] is False
    assert result["skipped_pianosmart_messages"] == 1
    preroll = result["audio_preroll_seconds"]
    assert 0.123 <= preroll < 0.123 + 1 / 44100
    actual = read_midi_events(tmp_path / result["midi"])
    payload = (tmp_path / result["midi"]).read_bytes()
    _header, chunks, _end = parse_smf_layout(payload)
    for chunk in chunks:
        if chunk["id"] == b"MTrk":
            _parse_track_events(payload[chunk["data_start"]:chunk["data_end"]])
    assert [data for _, data in actual] == [event.data for event in events[1:]]
    np.testing.assert_allclose([time for time, _ in actual],
                               [event.time_seconds * 1.002 - 0.123 + preroll for event in events[1:]],
                               atol=0.00005)
    params, payload = captured[0]
    assert params.nchannels == 2
    audio = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
    lead_frames = round(preroll * 44100)
    assert not np.any(audio[:lead_frames])
    np.testing.assert_array_equal(audio[lead_frames:], pcm)
    assert source.read_bytes() == before


def test_tempo_changes_are_resolved_before_affine_mapping(tmp_path, music, monkeypatch):
    source, _pcm = music
    _fake_encoder(monkeypatch)
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.MetaMessage("set_tempo", tempo=1000000, time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", note=60, velocity=80, time=240),
        mido.Message("note_off", note=60, time=720),
    ]))
    midi.save(tmp_path / "source.mid")
    events = read_midi_events(tmp_path / "source.mid")
    assert [event[0] for event in events] == [0.25, 1.5]
    result = paired_audio.export_midi_mp3(events, source, tmp_path / "song",
                                         midi_offset_seconds=0.2, midi_time_scale=1.01)
    actual = read_midi_events(tmp_path / result["midi"])
    assert [event[0] for event in actual] == pytest.approx([0.4525, 1.715], abs=0.00005)
    assert mido.MidiFile(tmp_path / result["midi"]).type == 0


def test_silent_audio_tail_is_retained_in_midi_song_length(tmp_path, music, monkeypatch):
    source, _pcm = music
    _fake_encoder(monkeypatch)
    paired_audio.export_midi_mp3([(0, b"\x90\x3c\x50"), (0.01, b"\x80\x3c\x00")],
                                 source, tmp_path / "song")
    assert mido.MidiFile(tmp_path / "song.mid").length == pytest.approx(0.1)


@pytest.mark.parametrize("status", [b"\xf1\x01", b"\xf2\x00\x00", b"\xf3\x01", b"\xf8"])
def test_non_smf_system_messages_are_rejected(tmp_path, music, monkeypatch, status):
    source, _pcm = music
    _fake_encoder(monkeypatch)
    with pytest.raises(ValueError, match="system-common or realtime"):
        paired_audio.export_midi_mp3([(0, status)], source, tmp_path / "song")
    assert not (tmp_path / "song.mid").exists()


@pytest.mark.parametrize("note_end", [b"\x80\x3c\x00", b"\x90\x3c\x00"])
def test_quantization_cannot_silently_drop_a_positive_note_duration(tmp_path, music, monkeypatch, note_end):
    source, _pcm = music
    captured = _fake_encoder(monkeypatch)
    with pytest.raises(ValueError, match="note duration is too short"):
        paired_audio.export_midi_mp3([(0.5, b"\x90\x3c\x50"), (0.50001, note_end)],
                                     source, tmp_path / "song")
    assert not captured
    assert not (tmp_path / "song.mid").exists()


@pytest.mark.parametrize("suffix", [".mid", ".mp3"])
def test_existing_half_of_pair_is_never_overwritten(tmp_path, music, monkeypatch, suffix):
    source, _pcm = music
    captured = _fake_encoder(monkeypatch)
    target = tmp_path / ("song" + suffix)
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        paired_audio.export_midi_mp3([(0, b"\x90\x3c\x50")], source, tmp_path / "song")
    assert target.read_bytes() == b"existing"
    assert not captured
    assert not (tmp_path / ("song" + (".mp3" if suffix == ".mid" else ".mid"))).exists()


def test_cancel_between_publications_rolls_back_the_pair(tmp_path, music, monkeypatch):
    source, _pcm = music
    _fake_encoder(monkeypatch)
    cancel = threading.Event()
    def progress(fraction):
        if fraction > 0.9:
            cancel.set()
    with pytest.raises(InterruptedError) as caught:
        paired_audio.export_midi_mp3([(0, b"\x90\x3c\x50")], source, tmp_path / "song",
                                     cancel=cancel, progress=progress)
    assert caught.value.recovery_directory
    assert caught.value.retained_paths
    assert not (tmp_path / "song.mid").exists()
    assert not (tmp_path / "song.mp3").exists()


@pytest.mark.parametrize("events,offset,scale", [
    ([], 0, 1),
    ([(0, b"\xf0\x43\x71\x7b\x00\xf7")], 0, 1),
    ([(float("nan"), b"\x90\x3c\x50")], 0, 1),
    ([(1, b"\x90\x3c\x50"), (0, b"\x80\x3c\x00")], 0, 1),
    ([(0, b"\x90\x3c\x50")], float("inf"), 1),
    ([(0, b"\x90\x3c\x50")], 0, 0),
    ([(0, b"\x90\x3c\x50")], 0, -1),
])
def test_invalid_timing_cannot_produce_playback_files(tmp_path, music, monkeypatch, events, offset, scale):
    source, _pcm = music
    captured = _fake_encoder(monkeypatch)
    with pytest.raises(ValueError):
        paired_audio.export_midi_mp3(events, source, tmp_path / "song",
                                     midi_offset_seconds=offset, midi_time_scale=scale)
    assert not captured
    assert not (tmp_path / "song.mid").exists()
    assert not (tmp_path / "song.mp3").exists()


def test_real_lame_preserves_clean_audio_timeline_and_stereo(tmp_path):
    if not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    rate = 44100
    seconds = np.arange(rate * 2) / rate
    envelope = (seconds > 0.35) & (seconds < 1.7)
    pcm = np.column_stack((12000 * np.sin(2 * np.pi * 440 * seconds) * envelope,
                           9000 * np.sin(2 * np.pi * 660 * seconds) * envelope)).astype(np.int16)
    source = tmp_path / "music.wav"
    _wav(source, pcm)
    result = paired_audio.export_midi_mp3([(0.35, b"\x90\x3c\x50"), (1.7, b"\x80\x3c\x00")],
                                         source, tmp_path / "song")
    decoded = tmp_path / "decoded.wav"
    audio_tools.decode_mp3(tmp_path / result["audio"], decoded)
    with wave.open(str(decoded), "rb") as audio:
        assert audio.getnchannels() == 2
        actual = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").reshape(-1, 2)
    assert abs(len(actual) - len(pcm)) <= 1
    # Gapless-aware decode has no frame-size/encoder-priming shift. Different
    # stereo channels remain different, unlike the encoded control WAV.
    for channel in (0, 1):
        assert np.corrcoef(actual[:, channel], pcm[:, channel])[0, 1] > 0.98
    assert abs(np.corrcoef(actual[:, 0], pcm[:, 1])[0, 1]) < 0.02
