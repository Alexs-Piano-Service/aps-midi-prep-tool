"""Synthetic music verifies pairing and timing without shipping customer audio."""

from dataclasses import replace
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app.smart_pianosoft_sync import (
    Fingerprint, SPSMetadata, SynchronizationCancelled, SynchronizationError,
    TimedMidiEvent, _envelope, _timecode as decode_timecode, parse_sps_midi, synchronize,
)


def _timecode(seconds):
    negative = seconds < 0
    seconds = abs(seconds)
    subframes = round(seconds * 9600)
    minute, subframes = divmod(subframes, 60 * 9600)
    second, subframes = divmod(subframes, 9600)
    frame, subframe = divmod(subframes, 128)
    return [minute + (100 if negative else 0), second, frame, subframe]


def _fingerprint_message(kind, seconds, samples=None):
    samples = samples if samples is not None else [1, -2] * 128
    return mido.Message("sysex", data=[0x43, 0x71, 0x7B, kind, *_timecode(seconds), kind // 2, 3,
                                       *[value & 127 for value in samples]])


def _midi(tmp_path, *, head=True, tail=True, duplicate=False):
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    tempo = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.MetaMessage("set_tempo", tempo=1000000, time=480),
    ])
    music = mido.MidiTrack()
    if head:
        music.append(_fingerprint_message(0, 2.125))
    if duplicate:
        music.append(_fingerprint_message(0, 2.125))
    if tail:
        music.append(_fingerprint_message(2, 10.375))
    music.extend([
        mido.Message("sysex", data=[0x43, 0x71, 5, 1]),
        mido.Message("program_change", program=2),
        mido.Message("note_on", note=60, velocity=80, time=240),
        mido.Message("note_off", note=60, time=480),
        mido.Message("sysex", data=[0x43, 0x10, 0x4C, 0, 0, 0x7E, 0], time=240),
    ])
    midi.tracks.extend([tempo, music])
    path = tmp_path / "song.mid"
    midi.save(path)
    return path


def test_parse_preserves_tempo_changes_and_musical_sysex(tmp_path):
    metadata = parse_sps_midi(_midi(tmp_path))
    assert metadata.cd_track is None  # 43 71 05 01 is not track number 1.
    assert metadata.head.reference_seconds == pytest.approx(2.125)
    assert metadata.tail.reference_seconds == pytest.approx(10.375)
    assert metadata.head.samples[:2] == (1, -2)
    assert [event.time_seconds for event in metadata.events] == pytest.approx([0, .25, 1.0, 1.5])
    assert metadata.events[-1].data == bytes([0xF0, 0x43, 0x10, 0x4C, 0, 0, 0x7E, 0, 0xF7])
    assert metadata.duration_seconds == pytest.approx(1.5)


def test_source_smart_pianosoft_header_is_removed_without_dropping_other_yamaha_sysex(tmp_path):
    path = _midi(tmp_path)
    midi = mido.MidiFile(path)
    midi.tracks[1].insert(0, mido.Message("sysex", data=[0x43, 0x71, 0x7E, 1, 0x40]))
    midi.tracks[1].insert(1, mido.Message("sysex", data=[0x43, 0x71, 0, 1]))
    midi.save(path)
    metadata = parse_sps_midi(path)
    assert all(not event.data.startswith(b"\xf0\x43\x71\x7e") for event in metadata.events)
    assert metadata.events[0] == TimedMidiEvent(0, b"\xf0\x43\x71\x00\x01\xf7")
    assert metadata.events[2].time_seconds == pytest.approx(.25)


@pytest.mark.parametrize("encoded, expected", [
    ([100, 0, 67, 77], -(67 / 75 + 77 / 9600)),
    ([100, 1, 7, 43], -(1 + 7 / 75 + 43 / 9600)),
    ([101, 2, 3, 4], -(62 + 3 / 75 + 4 / 9600)),
    ([99, 59, 74, 127], 5999 + 74 / 75 + 127 / 9600),
])
def test_signed_timecode_uses_negative_magnitude_above_100_minutes(encoded, expected):
    assert decode_timecode(encoded) == pytest.approx(expected)


@pytest.mark.parametrize("encoded", [
    [-1, 0, 0, 0], [128, 0, 0, 0], [0, -1, 0, 0], [100, 60, 0, 0],
    [0, 0, 75, 0], [0, 0, -1, 0], [100, 0, 0, 128], [0, 0, 0, -1],
])
def test_invalid_timecode_fields_remain_rejected(encoded):
    with pytest.raises(SynchronizationError, match="Invalid"):
        decode_timecode(encoded)


@pytest.mark.parametrize("options", [{"head": False}, {"tail": False}, {"duplicate": True}])
def test_missing_or_duplicate_fingerprints_are_rejected(tmp_path, options):
    with pytest.raises(SynchronizationError, match="fingerprint"):
        parse_sps_midi(_midi(tmp_path, **options))


def _reference_envelope(pcm):
    """Independent sample-by-sample state equations from the sequencer."""
    delay = [0.0] * 4410
    pointer = 0
    highpass_state = average_state = lowpass_state = 0.0
    result = []
    for frame, pair in enumerate(pcm):
        mono = (float(pair[0]) + float(pair[1])) / 2
        highpass = mono + highpass_state
        highpass_state = .996444 * highpass - mono
        rectified = abs(highpass)
        delayed = delay[pointer]
        delay[pointer] = rectified
        pointer = (pointer + 1) % 4410
        average_state = rectified - delayed + .999858 * average_state
        output = lowpass_state * .00355556
        lowpass_state = average_state * .000226757 + .996444 * lowpass_state
        if frame % 256 == 0:
            result.append(output)
    return np.asarray(result)


def test_vector_filter_matches_independent_scalar_reference():
    pcm = np.random.default_rng(45).integers(-20000, 20001, size=(24000, 2), dtype=np.int16)
    np.testing.assert_allclose(_envelope(pcm), _reference_envelope(pcm), rtol=1e-9, atol=1e-8)


def _wav(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(np.asarray(samples, dtype="<i2").tobytes())


@pytest.fixture(scope="module")
def original_phrase():
    # Original synthetic performance, with irregular amplitude changes so
    # genuine ambiguity rejection can distinguish it from a looped pattern.
    rng = np.random.default_rng(1248)
    times = np.arange(14 * 44100) / 44100
    knots = np.arange(0, 14.02, .02)
    amplitude = np.interp(times, knots, rng.uniform(.05, .8, len(knots)))
    carrier = np.sin(2 * np.pi * 437 * times) + .3 * np.sin(2 * np.pi * 731 * times)
    mono = np.asarray(20000 * amplitude * carrier, dtype=np.int16)
    pcm = np.column_stack((mono, mono))
    envelope = _reference_envelope(pcm)
    fingerprints = []
    for index, flags in [(345, 0), (1723, 1)]:
        segment = envelope[index:index + 256]
        samples = np.rint(segment / np.max(np.abs(segment)) * 60).astype(int)
        fingerprints.append(Fingerprint(index * 256 / 44100, tuple(samples), flags, 3))
    metadata = SPSMetadata(None, *fingerprints, (TimedMidiEvent(2, b"\x90\x3c\x50"),), 14)
    return pcm, metadata


def test_alignment_recovers_offset_and_drift_from_both_fingerprints(tmp_path, original_phrase):
    pcm, metadata = original_phrase
    offset, scale = .18, 1.002
    target_time = np.arange(int((14 * scale + offset) * 44100)) / 44100
    source_time = np.arange(len(pcm)) / 44100
    stretched = np.interp((target_time - offset) / scale, source_time, pcm[:, 0], left=0, right=0)
    path = tmp_path / "matching.wav"
    _wav(path, np.column_stack((stretched, stretched)))
    progress = []
    alignment = synchronize(metadata, path, progress=progress.append)
    assert alignment.offset_seconds == pytest.approx(offset, abs=.008)
    assert alignment.time_scale == pytest.approx(scale, abs=.0008)
    assert alignment.confidence > .98
    assert alignment.map_time(8) == pytest.approx(offset + 8 * scale, abs=.005)
    assert progress == [0.0, .5, 1.0]


def test_negative_head_reference_recovers_offset_and_drift_from_real_pcm(tmp_path, original_phrase):
    pcm, metadata = original_phrase
    padding = 100 * 256
    reference_seconds = -padding / 44100
    early = _reference_envelope(np.concatenate((np.zeros((padding, 2), dtype=np.int16), pcm[:65536])))[:256]
    samples = np.rint(early / np.max(np.abs(early)) * 60).astype(int)
    # Exercise the actual MIDI parser, including the signed hundred-minute
    # encoding, rather than injecting an already-decoded negative timestamp.
    midi = mido.MidiFile(type=0)
    midi.tracks.append(mido.MidiTrack([
        _fingerprint_message(0, reference_seconds, samples),
        _fingerprint_message(2, metadata.tail.reference_seconds, metadata.tail.samples),
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=480),
    ]))
    midi_path = tmp_path / "negative-start.mid"
    midi.save(midi_path)
    parsed = parse_sps_midi(midi_path)
    assert parsed.head.reference_seconds == pytest.approx(reference_seconds, abs=1 / 9600)
    offset, scale = .18, 1.002
    target_time = np.arange(int((14 * scale + offset) * 44100)) / 44100
    original_time = np.arange(len(pcm)) / 44100
    stretched = np.interp((target_time - offset) / scale, original_time, pcm[:, 0], left=0, right=0)
    audio_path = tmp_path / "negative-start.wav"
    _wav(audio_path, np.column_stack((stretched, stretched)))
    alignment = synchronize(parsed, audio_path)
    assert alignment.map_time(reference_seconds) < 0
    assert alignment.offset_seconds == pytest.approx(offset, abs=.008)
    assert alignment.time_scale == pytest.approx(scale, abs=.0008)
    assert alignment.confidence > .98


@pytest.mark.parametrize("reference", [-1620.0, -10.0, 40.0])
def test_reference_search_must_overlap_audio_without_unbounded_padding(tmp_path, original_phrase, reference):
    pcm, metadata = original_phrase
    metadata = replace(metadata, head=replace(metadata.head, reference_seconds=reference),
                       tail=replace(metadata.tail, reference_seconds=max(50, reference + 10)))
    audio_path = tmp_path / "outside.wav"
    _wav(audio_path, pcm)
    with pytest.raises(SynchronizationError, match="outside"):
        synchronize(metadata, audio_path)


def test_wrong_audio_and_silent_fingerprints_fail_closed(tmp_path, original_phrase):
    pcm, metadata = original_phrase
    path = tmp_path / "wrong.wav"
    _wav(path, np.random.default_rng(567).integers(-20000, 20000, pcm.shape, dtype=np.int16))
    with pytest.raises(SynchronizationError, match="does not match"):
        synchronize(metadata, path)
    bad = replace(metadata, head=replace(metadata.head, samples=(0,) * 256))
    with pytest.raises(SynchronizationError, match="empty or invalid"):
        synchronize(bad, path)


def test_repeated_audio_is_rejected_as_ambiguous(tmp_path):
    # Identical irregular phrases repeat within the head search window.
    rng = np.random.default_rng(93)
    t = np.arange(2 * 44100) / 44100
    knots = np.arange(0, 2.01, .01)
    levels = np.interp(t, knots, rng.uniform(.1, .9, len(knots)))
    mono = (12000 * np.sin(2 * np.pi * 440 * t) * levels).astype(np.int16)
    pcm = np.column_stack((np.tile(mono, 7), np.tile(mono, 7)))
    envelope = _reference_envelope(pcm)
    index = round(6 * 44100 / 256)
    sample = envelope[index:index + 256]
    fingerprint = Fingerprint(index * 256 / 44100, tuple(np.rint(sample / max(abs(sample)) * 60).astype(int)))
    metadata = SPSMetadata(None, fingerprint, replace(fingerprint, reference_seconds=10), (), 14)
    path = tmp_path / "repeating.wav"
    _wav(path, pcm)
    with pytest.raises(SynchronizationError, match="ambiguous"):
        synchronize(metadata, path)


def test_cancellation_does_not_read_audio(original_phrase):
    event = threading.Event()
    event.set()
    with pytest.raises(SynchronizationCancelled):
        synchronize(original_phrase[1], "does-not-exist.wav", cancel=event)


def test_truncated_wav_is_rejected(tmp_path, original_phrase):
    pcm, metadata = original_phrase
    path = tmp_path / "truncated.wav"
    _wav(path, pcm)
    with path.open("r+b") as handle:
        handle.truncate(44 + 5000 * 4)
    with pytest.raises(SynchronizationError, match="truncated"):
        synchronize(metadata, path)
