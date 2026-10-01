import errno
import math
import os
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app.disklavier_audio import (
    CONTROL_AMPLITUDE,
    CONTROL_LEADER_FRAMES,
    DisklavierEncodingCancelled,
    PHASE_STEPS,
    SAMPLE_RATE,
    encode_disklavier_wav,
    is_pianosmart_message,
    midi_message_nibbles,
    modulate_nibbles,
    read_midi_events,
)


def _audio(path, seconds=0.05, channels=2, rate=SAMPLE_RATE):
    frames = np.full((round(seconds * rate), channels), 30000, dtype="<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(frames.tobytes())


def _read(path):
    with wave.open(str(path), "rb") as reader:
        assert reader.getparams()[:3] == (2, 2, SAMPLE_RATE)
        return np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").reshape(-1, 2)


def _demodulate(control):
    """Independent quadrature receiver, without reusing the encoder's phase table."""
    carrier = np.exp(2j * np.pi * np.arange(14) / 7)
    phases = np.angle(control.reshape(-1, 14) @ carrier)
    differentials = np.mod(np.rint(np.diff(np.r_[0, phases]) / (np.pi / 8)), 16).astype(int)
    # Read independently from Yamaha patent Figure 14, in increasing phase order.
    phase_to_data = np.array([8, 10, 14, 6, 2, 3, 7, 15, 11, 9, 13, 5, 1, 0, 4, 12])
    return phase_to_data[differentials]


def test_yamaha_patent_phase_vectors_and_wire_escape_examples():
    assert PHASE_STEPS[8] == 0
    assert PHASE_STEPS[15] == 7  # Idle must continually rotate by 157.5 degrees.
    assert PHASE_STEPS[9] == 9
    assert midi_message_nibbles(b"\xcf\x02") == bytes.fromhex("0c 04 0f 00 02")
    assert midi_message_nibbles(b"\xf0\x43\x00\xf7") == bytes.fromhex("0c 00 04 03 00 00 0c 07")
    assert midi_message_nibbles(b"\x90\x4f\x0f") == bytes.fromhex("09 00 04 0f 00 0f")
    samples, phase = modulate_nibbles([8, 15])
    assert samples[0] == CONTROL_AMPLITUDE
    assert samples[14] == round(CONTROL_AMPLITUDE * math.cos(7 * math.pi / 8))
    assert phase == 7


def test_quadrature_receiver_recovers_every_nibble_across_chunk_boundary():
    symbols = np.tile(np.arange(16), 73)
    first, phase = modulate_nibbles(symbols[:333])
    last, _phase = modulate_nibbles(symbols[333:], phase)
    assert np.array_equal(_demodulate(np.r_[first, last]), symbols)


def test_wav_preserves_audio_and_applies_alignment_on_shared_timeline(tmp_path):
    source, destination = tmp_path / "music.wav", tmp_path / "encoded.wav"
    _audio(source)
    original = source.read_bytes()
    events = [
        (0.0, b"\xf0\x43\x71\x05\x01\xf7"),
        (0.0, b"\xf0\x43\x71\x7b\x01\x00\x00\x00\x00\xf7"),
        (0.1, b"\x90\x3c\x50"),
        (0.3, b"\xb0\x40\x7f"),
        (0.5, b"\x80\x3c\x20"),
    ]
    progress = []
    result = encode_disklavier_wav(events, source, destination,
                                  midi_offset_seconds=0.2, midi_time_scale=1.1,
                                  progress=progress.append)
    pcm = _read(destination)
    audio_start = SAMPLE_RATE // 2
    assert np.all(pcm[:audio_start, 0] == 0)
    assert np.all(pcm[audio_start:audio_start + round(0.05 * SAMPLE_RATE), 0] == 30000)
    assert np.all(pcm[:CONTROL_LEADER_FRAMES, 1] == 0)
    assert pcm[CONTROL_LEADER_FRAMES, 1] != 0
    assert source.read_bytes() == original
    assert result["pianosmart_messages_removed"] == 2
    assert result["midi_events"] == 3
    assert result["hardware_verified"] is False
    assert result["midi_advance_seconds"] == 0.0
    assert result["audio_preroll_seconds"] == 0.5
    assert progress[0] == 0 and progress[-1] == 1
    assert progress == sorted(progress)
    symbols = _demodulate(pcm[:, 1])
    note = midi_message_nibbles(events[2][1])
    target_end = round((0.5 + 0.1 * 1.1 + 0.2) * 3150)
    assert symbols[target_end - len(note):target_end].tolist() == list(note)
    assert symbols[target_end - len(note) - 1] == 15


def test_serialization_keeps_same_time_messages_complete_and_rejects_overload(tmp_path):
    source = tmp_path / "music.wav"
    _audio(source)
    events = [(0.0, bytes((0x90, note, 70))) for note in range(60, 70)]
    destination = tmp_path / "chord.wav"
    result = encode_disklavier_wav(events, source, destination)
    symbols = _demodulate(_read(destination)[:, 1])
    expected = b"".join(midi_message_nibbles(message) for _, message in events)
    assert symbols[1569:1569 + len(expected)].tolist() == list(expected)
    assert 0 < result["max_serialization_delay_seconds"] < 0.02
    overloaded = [(0.0, bytes((0x90, note, 70))) for note in range(88)]
    with pytest.raises(ValueError, match="capacity"):
        encode_disklavier_wav(overloaded, source, tmp_path / "overloaded.wav")
    assert not (tmp_path / "overloaded.wav").exists()


def test_advancing_music_increases_preroll_without_losing_early_notes(tmp_path):
    source = tmp_path / "music.wav"
    _audio(source, channels=1)
    result = encode_disklavier_wav([(0, b"\x90\x3c\x40")], source,
                                  tmp_path / "early.wav", midi_offset_seconds=-2)
    assert result["audio_preroll_seconds"] == 2.5
    symbols = _demodulate(_read(tmp_path / "early.wav")[:, 1])
    assert symbols[1569:1575].tolist() == [9, 0, 3, 12, 4, 0]


def test_cancel_and_truncated_input_never_publish_partial_output(tmp_path):
    source, destination = tmp_path / "music.wav", tmp_path / "encoded.wav"
    _audio(source, seconds=3)
    canceled = threading.Event()
    def progress(fraction):
        if fraction > 0:
            canceled.set()
    with pytest.raises(DisklavierEncodingCancelled):
        encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, destination,
                              cancel=canceled, progress=progress)
    assert not destination.exists()
    assert not list(tmp_path.glob(".disklavier-*"))
    source.write_bytes(source.read_bytes()[:-20])
    with pytest.raises(ValueError, match="truncated"):
        encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".disklavier-*"))


def test_preexisting_output_and_invalid_audio_are_preserved(tmp_path):
    source, destination = tmp_path / "music.wav", tmp_path / "encoded.wav"
    _audio(source, rate=48000)
    events = [(0, b"\x90\x3c\x40")]
    with pytest.raises(ValueError, match="44.1"):
        encode_disklavier_wav(events, source, destination)
    assert not destination.exists()
    destination.write_bytes(b"Existing result")
    with pytest.raises(FileExistsError):
        encode_disklavier_wav(events, source, destination)
    assert destination.read_bytes() == b"Existing result"


def test_pianosmart_headers_are_removed_without_stripping_musical_sysex():
    for data in (b"\x05\x01", b"\x7b\x01\x00\x00\x00\x00", b"\x7e\x01\x40"):
        assert is_pianosmart_message(b"\xf0\x43\x71" + data + b"\xf7")
    assert not is_pianosmart_message(b"\xf0\x43\x71\x00\x01\xf7")
    assert not is_pianosmart_message(b"\xf0\x43\x10\x4c\x00\x00\x7e\x00\xf7")


@pytest.mark.parametrize("no_hardlinks", [False, True])
def test_concurrent_destination_creation_is_never_overwritten(tmp_path, monkeypatch, no_hardlinks):
    source, destination = tmp_path / "music.wav", tmp_path / "encoded.wav"
    _audio(source)
    if no_hardlinks:
        def unsupported_link(*args):
            raise OSError(errno.EOPNOTSUPP, "Filesystem has no hard links")
        monkeypatch.setattr(os, "link", unsupported_link)
    def progress(fraction):
        if fraction == 1:
            destination.write_bytes(b"Concurrent result")
    with pytest.raises(FileExistsError):
        encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, destination, progress=progress)
    assert destination.read_bytes() == b"Concurrent result"
    assert not list(tmp_path.glob(".disklavier-*"))


def test_filesystem_without_hardlinks_copies_checked_output_and_cleans_cancellation(tmp_path, monkeypatch):
    source, destination = tmp_path / "music.wav", tmp_path / "encoded.wav"
    _audio(source)
    cancel = threading.Event()
    def unsupported_link(*args):
        raise OSError(errno.EOPNOTSUPP, "Filesystem has no hard links")
    monkeypatch.setattr(os, "link", unsupported_link)
    encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, destination)
    assert _read(destination).shape[0] > SAMPLE_RATE
    assert not list(tmp_path.glob(".disklavier-*"))
    destination.unlink()
    def cancel_at_publication(*args):
        cancel.set()
        unsupported_link(*args)
    monkeypatch.setattr(os, "link", cancel_at_publication)
    with pytest.raises(DisklavierEncodingCancelled):
        encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, destination, cancel=cancel)
    assert not destination.exists()
    assert not list(tmp_path.glob(".disklavier-*"))


@pytest.mark.parametrize("event", [b"", b"\x3c\x40", b"\x90\x3c", b"\x90\x80\x40",
                                   b"\xf0\x43", b"\xf0\x90\xf7", b"\xf7"])
def test_malformed_wire_events_are_rejected(event):
    with pytest.raises(ValueError):
        midi_message_nibbles(event)


def test_midi_reader_uses_tempo_changes_and_keeps_controller_and_sysex(tmp_path):
    midi = mido.MidiFile(ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.Message("note_on", note=60, velocity=70, time=480),
        mido.MetaMessage("set_tempo", tempo=1000000, time=480),
        mido.Message("control_change", control=64, value=127, time=480),
        mido.Message("sysex", data=[0x7E, 0x7F, 9, 1]),
    ]))
    path = tmp_path / "tempo.mid"
    midi.save(path)
    assert read_midi_events(path) == [(0.5, b"\x90\x3c\x46"),
                                      (2.0, b"\xb0\x40\x7f"),
                                      (2.0, b"\xf0\x7e\x7f\x09\x01\xf7")]
