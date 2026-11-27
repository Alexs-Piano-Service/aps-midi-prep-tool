"""Paired output keeps every musical event on the clean audio timeline."""

from pathlib import Path
import errno
import json
import struct
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import audio_tools, paired_audio
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.disklavier_audio_decode import decode_disklavier_audio
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


@pytest.mark.parametrize('header_shortfall', [1, 2, 3, 18])
def test_partial_final_frame_in_wav_header_preserves_all_declared_complete_frames(
    tmp_path, music, monkeypatch, header_shortfall,
):
    source, pcm = music
    malformed = bytearray(source.read_bytes())
    for position in (4, 40):
        size = struct.unpack_from('<I', malformed, position)[0]
        struct.pack_into('<I', malformed, position, size - header_shortfall)
    source.write_bytes(malformed)
    captured = _fake_encoder(monkeypatch)

    result = paired_audio.export_midi_mp3(
        [(0, b'\x90\x3c\x50'), (.01, b'\x80\x3c\x00')], source, tmp_path / 'song')

    omitted_frames = (header_shortfall + 3) // 4
    params, payload = captured[0]
    assert params.nframes == len(pcm) - omitted_frames
    np.testing.assert_array_equal(np.frombuffer(payload, dtype='<i2').reshape(-1, 2),
                                  pcm[:-omitted_frames])
    assert (tmp_path / result['midi']).is_file()
    assert (tmp_path / result['audio']).is_file()
    assert source.read_bytes() == malformed


def test_riff_metadata_after_audio_is_not_exported_as_pcm(tmp_path, music, monkeypatch):
    source, pcm = music
    tagged = bytearray(source.read_bytes())
    tagged.extend(struct.pack('<4sI', b'JUNK', 12) + b'not-audio!!!')
    struct.pack_into('<I', tagged, 4, len(tagged) - 8)
    source.write_bytes(tagged)
    captured = _fake_encoder(monkeypatch)

    paired_audio.export_midi_mp3(
        [(0, b'\x90\x3c\x50'), (.01, b'\x80\x3c\x00')], source, tmp_path / 'song')

    params, payload = captured[0]
    assert params.nframes == len(pcm)
    assert payload == pcm.astype('<i2').tobytes()
    assert source.read_bytes() == tagged


@pytest.mark.parametrize('missing_bytes', [1, 4, 18])
def test_genuinely_truncated_pcm_is_rejected_without_playback_outputs(
    tmp_path, music, monkeypatch, missing_bytes,
):
    source, _pcm = music
    truncated = source.read_bytes()[:-missing_bytes]
    source.write_bytes(truncated)
    captured = _fake_encoder(monkeypatch)

    with pytest.raises(ValueError, match='music WAV is truncated'):
        paired_audio.export_midi_mp3(
            [(0, b'\x90\x3c\x50'), (.01, b'\x80\x3c\x00')], source, tmp_path / 'song')

    assert not captured
    assert not (tmp_path / 'song.mid').exists()
    assert not (tmp_path / 'song.mp3').exists()
    assert not list(tmp_path.glob('.aps-paired-audio-*'))
    assert source.read_bytes() == truncated


def test_pair_exports_with_full_system_temp_using_destination_recovery(tmp_path, music, monkeypatch):
    source, _pcm = music
    original = source.read_bytes()
    _fake_encoder(monkeypatch)
    destination = tmp_path / "external drive"
    destination.mkdir()
    directories = []
    mkdtemp = paired_audio.tempfile.mkdtemp

    def destination_only(suffix=None, prefix=None, dir=None):
        if dir is None:
            raise OSError(errno.ENOSPC, "System temporary storage is full")
        directory = Path(mkdtemp(suffix=suffix, prefix=prefix, dir=dir))
        directories.append(directory)
        return str(directory)

    monkeypatch.setattr(paired_audio.tempfile, "mkdtemp", destination_only)
    events = [(0, b"\x90\x3c\x50"), (0.05, b"\x80\x3c\x00")]
    result = paired_audio.export_midi_mp3(events, source, destination / "song")

    assert read_midi_events(destination / result["midi"]) == events
    assert (destination / result["audio"]).read_bytes() == b"test MP3"
    recovery = [path for path in directories if path.name.startswith("aps_file_batch_")]
    assert len(recovery) == 1
    assert recovery[0].parent == destination
    assert all(path.parent == destination and not path.exists() for path in directories)
    assert source.read_bytes() == original


@pytest.mark.parametrize("status,name", [
    (b"\xf1\x01", "MIDI Time Code Quarter Frame"),
    (b"\xf2\x00\x00", "Song Position Pointer"),
    (b"\xf3\x01", "Song Select"),
    (b"\xf6", "Tune Request"),
    (b"\xff", "System Reset"),
])
def test_stateful_system_messages_are_reported_before_creating_output(
    tmp_path, music, monkeypatch, status, name,
):
    source, _pcm = music
    original = source.read_bytes()
    captured = _fake_encoder(monkeypatch)
    with pytest.raises(ValueError) as caught:
        paired_audio.export_midi_mp3(
            [(0, b"\x90\x3c\x50"), (0.025, status), (0.05, b"\x80\x3c\x00")],
            source, tmp_path / "song")
    message = str(caught.value)
    assert name in message
    assert f"0x{status[0]:02X}" in message
    assert "0.025000 seconds" in message
    assert "cannot be safely omitted" in message
    assert not captured
    assert not (tmp_path / "song.mid").exists()
    assert not (tmp_path / "song.mp3").exists()
    assert not list(tmp_path.glob(".aps-paired-audio-*"))
    assert source.read_bytes() == original


@pytest.mark.parametrize("status", [b"\xf8", b"\xfa", b"\xfb", b"\xfc", b"\xfe"])
def test_live_realtime_messages_are_omitted_without_changing_musical_times(
    tmp_path, music, monkeypatch, status,
):
    source, _pcm = music
    _fake_encoder(monkeypatch)
    events = [(0, status), (0.01, b"\x90\x3c\x50"), (0.02, status),
              (0.06, b"\x80\x3c\x00"), (0.09, status)]
    result = paired_audio.export_midi_mp3(events, source, tmp_path / "song")
    actual = read_midi_events(tmp_path / result["midi"])
    assert [data for _, data in actual] == [events[1][1], events[3][1]]
    assert [time for time, _ in actual] == pytest.approx([0.01, 0.06], abs=0.00005)
    assert result["skipped_realtime_messages"] == 3
    assert result["skipped_pianosmart_messages"] == 0
    assert result["events"] == 2
    assert (tmp_path / result["audio"]).exists()


def test_decoded_control_stream_with_clock_exports_notes_and_pedals_on_original_timeline(
    tmp_path, monkeypatch,
):
    # Exercise the actual encoded waveform and decoder, including clocks before,
    # between and after musical events. The source fixture contains no library music.
    samples = np.arange(44100)
    pcm = np.column_stack((6000 * np.sin(2 * np.pi * 440 * samples / 44100),
                           4000 * np.sin(2 * np.pi * 660 * samples / 44100)))
    source, encoded = tmp_path / "source.wav", tmp_path / "encoded.wav"
    _wav(source, pcm)
    events = [(0.02, b"\xfa"), (0.04, b"\xf8"), (0.1, b"\x90\x3c\x50"),
              (0.15, b"\xf8"), (0.2, b"\xb0\x40\x7f"), (0.3, b"\xf8"),
              (0.4, b"\x80\x3c\x00"), (0.45, b"\xf8"), (0.5, b"\xb0\x40\x00"),
              (0.6, b"\xf8"), (0.65, b"\xfc")]
    encode_disklavier_wav(events, source, encoded)
    original = encoded.read_bytes()
    decoded = decode_disklavier_audio(encoded, tmp_path / "clean.wav")
    assert [event.data for event in decoded.events] == [data for _, data in events]
    captured = _fake_encoder(monkeypatch)
    result = paired_audio.export_midi_mp3(decoded.events, decoded.music_path, tmp_path / "song")
    actual = read_midi_events(tmp_path / result["midi"])
    musical = [event for event in decoded.events if event.data[0] < 0xF0]
    assert [data for _, data in actual] == [event.data for event in musical]
    assert [time for time, _ in actual] == pytest.approx(
        [event.time_seconds for event in musical], abs=0.00005)
    assert result["events"] == 4
    assert result["skipped_realtime_messages"] == 7
    assert result["audio_preroll_seconds"] == 0
    assert (tmp_path / result["audio"]).exists()
    with wave.open(str(decoded.music_path), "rb") as clean:
        assert captured[0][0] == clean.getparams()
        assert captured[0][1] == clean.readframes(clean.getnframes())
    assert encoded.read_bytes() == original


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
    recovery = Path(caught.value.recovery_directory)
    assert recovery.parent == tmp_path
    manifest = json.loads((recovery / "manifest.json").read_text())
    assert not manifest["rollback_errors"]
    assert all((recovery / record["prepared_file"]).is_file() for record in manifest["files"])
    assert (recovery / manifest["files"][1]["prepared_file"]).read_bytes() == b"test MP3"
    assert all(Path(path).is_file() and Path(path).is_relative_to(tmp_path)
               for path in caught.value.retained_paths)
    assert not list(tmp_path.glob(".aps-paired-audio-*"))
    assert not (tmp_path / "song.mid").exists()
    assert not (tmp_path / "song.mp3").exists()


@pytest.mark.parametrize("events,offset,scale", [
    ([], 0, 1),
    ([(0, b"\xf8"), (0.1, b"\xfe")], 0, 1),
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
    result = paired_audio.export_midi_mp3([(0, b"\xf8"), (0.35, b"\x90\x3c\x50"),
                                         (1.0, b"\xf8"), (1.7, b"\x80\x3c\x00")],
                                         source, tmp_path / "song")
    assert result["skipped_realtime_messages"] == 2
    assert [time for time, _data in read_midi_events(tmp_path / result["midi"])] == pytest.approx(
        [0.35, 1.7], abs=0.00005)
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
