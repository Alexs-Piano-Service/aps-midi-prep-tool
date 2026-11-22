import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import disklavier_decoder as decoder
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events


def _wav(path, pcm, rate=44100):
    pcm = np.asarray(pcm, dtype="<i2")
    with wave.open(str(path), "wb") as out:
        out.setparams((pcm.shape[1], 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(pcm.tobytes())


def _independent_signal(nibbles, initial_phase=0):
    # Explicit phases from the published symbol diagram, not the encoder API.
    increments = (13, 12, 4, 5, 14, 11, 3, 6, 0, 9, 1, 8, 15, 10, 2, 7)
    phase = initial_phase
    pcm = []
    for nibble in nibbles:
        phase += increments[nibble]
        pcm.extend(np.rint(7000 * np.cos(np.arange(14) * 2 * np.pi / 7 - phase * np.pi / 8)))
    return np.asarray(pcm, dtype="<i2")


def _decode(tmp_path, source, **kwargs):
    midi, audio = tmp_path / "decoded.mid", tmp_path / "accompaniment.wav"
    return decoder.decode_disklavier_wav(source, midi, audio, **kwargs), midi, audio


def test_independent_pcm_decodes_escaped_program_sysex_and_note_timing(tmp_path):
    # Idle, program 0xC3=12,4,3; note 0x93 0x3C 0x4F; musical SysEx.
    packets = [([12, 4, 3, 0, 2], b"\xc3\x02"),
               ([9, 3, 3, 12, 4, 15], b"\x93\x3c\x4f"),
               ([12, 0, 7, 14, 7, 15, 0, 9, 0, 1, 12, 7], b"\xf0\x7e\x7f\x09\x01\xf7")]
    nibbles = [15] * 30
    expected = []
    for symbols, message in packets:
        nibbles += symbols
        expected.append((len(nibbles) * 14 / 44100, message))
        nibbles += [15] * 40
    control = _independent_signal(nibbles)
    left = np.arange(len(control), dtype="<i2")
    source = tmp_path / "unrelated-name.wav"
    _wav(source, np.column_stack((left, control)))
    before = source.read_bytes()
    metadata, midi, audio = _decode(tmp_path, source)
    recovered = read_midi_events(midi)
    assert [message for _, message in recovered] == [message for _, message in expected]
    assert [seconds for seconds, _ in recovered] == pytest.approx(
        [seconds for seconds, _ in expected], abs=1 / 44100)
    assert metadata["midi_events"] == 3
    assert metadata["note_events"] == 1
    assert metadata["duration_seconds"] == len(control) / 44100
    assert source.read_bytes() == before
    with wave.open(str(audio)) as wav:
        assert wav.getnchannels() == 1
        assert np.array_equal(np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2"), left)


def test_encoder_roundtrip_preserves_shared_timeline_and_chunk_boundaries(tmp_path, monkeypatch):
    source, encoded = tmp_path / "input.wav", tmp_path / "encoded.wav"
    _wav(source, np.full((44100 * 2, 2), 1234, dtype="<i2"))
    messages = [(0, b"\xc0\x00"), (0.03, b"\x90\x3c\x50"),
                (0.7, b"\xb0\x40\x7f"), (1.1, b"\xe0\x00\x40"),
                (1.3, b"\xf0\x43\x10\x4c\x00\x00\x7e\x00\xf7"),
                (1.9, b"\x80\x3c\x20")]
    encode_disklavier_wav(messages, source, encoded)
    monkeypatch.setattr(decoder, "_BLOCK_FRAMES", 14 * 19)
    progress = []
    metadata, midi, audio = _decode(tmp_path, encoded, progress=progress.append)
    decoded = read_midi_events(midi)
    assert [message for _, message in decoded] == [message for _, message in messages]
    assert [seconds for seconds, _ in decoded] == pytest.approx(
        [seconds + 0.5 for seconds, _ in messages], abs=1 / 3150)
    assert mido.MidiFile(midi).length == pytest.approx(metadata["duration_seconds"])
    assert progress[0] == 0 and progress[-1] == 1
    assert progress == sorted(progress)
    with wave.open(str(encoded)) as original, wave.open(str(audio)) as safe:
        stereo = np.frombuffer(original.readframes(original.getnframes()), dtype="<i2").reshape(-1, 2)
        mono = np.frombuffer(safe.readframes(safe.getnframes()), dtype="<i2")
        assert np.array_equal(mono, stereo[:, 0])


def test_long_initial_sysex_decodes_from_first_symbol_after_silent_leader(tmp_path):
    source, encoded = tmp_path / "input.wav", tmp_path / "encoded.wav"
    _wav(source, np.zeros((4410, 2), dtype="<i2"))
    # 1,484 nibbles end at 1,576, beginning exactly at the encoder's first
    # unmuted symbol, number 92. There is no audible idle acquisition run.
    message = b"\xf0" + b"\x01" * 740 + b"\xf7"
    encode_disklavier_wav([(1 / 3150, message)], source, encoded)
    _, midi, _ = _decode(tmp_path, encoded)
    recovered = read_midi_events(midi)
    assert len(recovered) == 1 and recovered[0][1] == message
    assert recovered[0][0] == pytest.approx(1576 / 3150, abs=1 / 44100)


def test_independent_absolute_phase_can_acquire_idle_after_silent_leader(tmp_path):
    symbols = [15] * 92 + [15] * 30 + [9, 0, 3, 12, 4, 0] + [15] * 30
    # Shift the carrier by a quarter cycle, as in the independent reference,
    # without changing differential data. The first differential looks like
    # nibble 6, which cannot begin a complete MIDI message.
    control = _independent_signal(symbols, initial_phase=-4)
    control[:92 * 14] = 0
    source = tmp_path / "shifted.wav"
    _wav(source, np.column_stack((np.zeros(len(control), dtype="<i2"), control)))
    _, midi, _ = _decode(tmp_path, source)
    assert read_midi_events(midi)[0][1] == b"\x90\x3c\x40"


@pytest.mark.parametrize("kind", ["silence", "music", "tone", "bad_phase", "dropout", "unfinished", "data", "short", "mono", "rate", "truncated", "text"])
def test_nonencoded_or_malformed_files_are_rejected_without_outputs(tmp_path, kind):
    nibbles = [15] * 30 + [9, 0, 3, 12, 4, 0] + [15] * 30
    control = _independent_signal(nibbles)
    if kind == "silence":
        control[:] = 0
    elif kind == "music":
        control = np.random.default_rng(8).integers(-16000, 16000, len(control), dtype=np.int16)
    elif kind == "tone":
        control = _independent_signal([8] * len(nibbles))
    elif kind == "bad_phase":
        control[30 * 14:31 * 14] = np.rint(7000 * np.cos(np.arange(14) * 2 * np.pi / 7 + 0.14))
    elif kind == "dropout":
        control[35 * 14:36 * 14] = 0
    elif kind == "unfinished":
        control = _independent_signal([15] * 30 + [9, 0, 3])
    elif kind == "data":
        control = _independent_signal([15] * 30 + [0, 1])
    elif kind == "short":
        control = control[:-1]
    stereo = np.column_stack((np.zeros(len(control), dtype="<i2"), control))
    source = tmp_path / "bad.wav"
    _wav(source, stereo[:, :1] if kind == "mono" else stereo, 48000 if kind == "rate" else 44100)
    if kind == "truncated":
        source.write_bytes(source.read_bytes()[:-40])
    elif kind == "text":
        source.write_text("This is not an audio file.")
    original = source.read_bytes()
    with pytest.raises(ValueError):
        _decode(tmp_path, source)
    assert source.read_bytes() == original
    assert not (tmp_path / "decoded.mid").exists()
    assert not (tmp_path / "accompaniment.wav").exists()
    assert not list(tmp_path.glob(".disklavier-decode-*"))


@pytest.mark.parametrize("at", [0, 0.1, 1])
def test_cancel_removes_staging_and_preserves_source(tmp_path, at):
    source, encoded = tmp_path / "input.wav", tmp_path / "encoded.wav"
    _wav(source, np.zeros((44100 * 3, 2), dtype="<i2"))
    encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, encoded)
    event = threading.Event()
    def progress(value):
        if value >= at:
            event.set()
    with pytest.raises(decoder.DisklavierDecodingCancelled):
        _decode(tmp_path, encoded, cancel=event, progress=progress)
    assert not (tmp_path / "decoded.mid").exists()
    assert not (tmp_path / "accompaniment.wav").exists()
    assert not list(tmp_path.glob(".disklavier-decode-*"))


def test_existing_and_aliased_paths_are_preserved(tmp_path):
    source = tmp_path / "input.wav"
    source.write_bytes(b"Existing source")
    midi, audio = tmp_path / "decoded.mid", tmp_path / "accompaniment.wav"
    midi.write_bytes(b"Existing MIDI")
    with pytest.raises(FileExistsError):
        decoder.decode_disklavier_wav(source, midi, audio)
    assert midi.read_bytes() == b"Existing MIDI"
    with pytest.raises(ValueError, match="different paths"):
        decoder.decode_disklavier_wav(source, source, audio)
    assert source.read_bytes() == b"Existing source"


def test_wire_transport_is_validated_but_omitted_from_playable_midi(tmp_path):
    source, encoded = tmp_path / "input.wav", tmp_path / "encoded.wav"
    _wav(source, np.zeros((4410, 2), dtype="<i2"))
    encode_disklavier_wav([(0, b"\xff"), (0.1, b"\xf2\x00\x00"),
                           (0.2, b"\x90\x3c\x40")], source, encoded)
    metadata, midi, _ = _decode(tmp_path, encoded)
    assert metadata["system_events_omitted"] == 2
    recovered = read_midi_events(midi)
    assert len(recovered) == 1 and recovered[0][1] == b"\x90\x3c\x40"
    assert recovered[0][0] == pytest.approx(0.7, abs=1 / 44100)


def test_publication_conflict_preserves_concurrent_file_and_withdraws_our_other_output(tmp_path):
    source, encoded = tmp_path / "input.wav", tmp_path / "encoded.wav"
    _wav(source, np.zeros((4410, 2), dtype="<i2"))
    encode_disklavier_wav([(0, b"\x90\x3c\x40")], source, encoded)
    audio = tmp_path / "accompaniment.wav"
    def progress(value):
        if value == 1:
            audio.write_bytes(b"Created by another process")
    with pytest.raises(OSError, match="recovery"):
        _decode(tmp_path, encoded, progress=progress)
    assert audio.read_bytes() == b"Created by another process"
    assert not (tmp_path / "decoded.mid").exists()
    assert not list(tmp_path.glob(".disklavier-decode-*"))
