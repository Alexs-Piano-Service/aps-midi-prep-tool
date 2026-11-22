"""Verify synchronization through album export and the independent WAV reader."""

import json
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app.disklavier_audio import read_midi_events
from aps_midi_prep_tool_app.disklavier_decoder import decode_disklavier_wav
from aps_midi_prep_tool_app.smart_pianosoft_workflow import prepare_album, scan_album
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes
from test_smart_pianosoft_sync import _fingerprint_message, _wav, original_phrase


@pytest.mark.parametrize("offset, scale", [(0.18, 1.004), (-0.12, 0.996)])
def test_exported_notes_follow_matched_audio_at_start_middle_and_end(
    tmp_path, original_phrase, offset, scale,
):
    pcm, references = original_phrase
    source = tmp_path / "source"
    source.mkdir()
    midi = mido.MidiFile(type=0, ticks_per_beat=1000)
    track = mido.MidiTrack([
        _fingerprint_message(0, references.head.reference_seconds, references.head.samples),
        _fingerprint_message(2, references.tail.reference_seconds, references.tail.samples),
        mido.MetaMessage("set_tempo", tempo=1000000),
    ])
    # Distinct pitches make any missing, reordered, or mistimed note visible.
    expected_events = []
    last_seconds = 0.0
    for seconds, note in [(1.0, 60), (7.0, 64), (12.0, 67)]:
        for when, kind, velocity in [(seconds, "note_on", 80), (seconds + 0.25, "note_off", 0)]:
            message = mido.Message(kind, note=note, velocity=velocity,
                                   time=round((when - last_seconds) * 1000))
            track.append(message)
            expected_events.append((when, bytes(message.bytes())))
            last_seconds = when
    track.append(mido.MetaMessage("end_of_track", time=round((14 - last_seconds) * 1000)))
    midi.tracks.append(track)
    midi.save(source / "01.MID")
    (source / "PDISK.MNG").write_bytes(_pdisk_bytes("Synthetic timing check"))
    (source / "PSONG.MNG").write_bytes(_psong_bytes([("01.MID", "Head middle tail")]))

    # Model another pressing with a different start and playback rate. The
    # ground truth is this transformation, not the synchronizer's own result.
    audio_times = np.arange(round((14 * scale + offset) * 44100)) / 44100
    source_times = np.arange(len(pcm)) / 44100
    transformed = np.interp((audio_times - offset) / scale, source_times,
                            pcm[:, 0], left=0, right=0).astype("<i2")
    audio = tmp_path / "matching-cd.wav"
    _wav(audio, np.column_stack((transformed, transformed)))

    album = scan_album(source, tmp_path / "snapshot")
    output = prepare_album(album, tmp_path, audio_paths={1: audio})
    report = json.loads((output / "manifest.json").read_text())["tracks"][0]
    recovered_midi = tmp_path / "recovered.mid"
    recovered_audio = tmp_path / "recovered.wav"
    decode_disklavier_wav(output / report["encoded"], recovered_midi, recovered_audio)
    recovered_events = read_midi_events(recovered_midi)

    assert [message for _, message in recovered_events] == [message for _, message in expected_events]
    recovered_times = np.array([seconds for seconds, _ in recovered_events])
    source_event_times = np.array([seconds for seconds, _ in expected_events])
    # Both streams get the same half-second lead-in; the recovered MIDI must
    # not receive a second mechanical-piano delay or lose that common lead-in.
    expected_times = 0.5 + offset + source_event_times * scale
    assert recovered_times == pytest.approx(expected_times, abs=0.008)
    assert report["alignment"]["confidence"] > 0.98
    assert report["encoding"]["max_serialization_delay_seconds"] <= 1 / 3150

    # A start-only correction cannot pass: after aligning the first note,
    # uncorrected speed would put the final note more than 40 ms out of time.
    assert abs((source_event_times[-1] - source_event_times[0]) * (scale - 1)) > 0.04
    assert recovered_times[-1] - recovered_times[0] == pytest.approx(
        expected_times[-1] - expected_times[0], abs=0.008,
    )
    with wave.open(str(recovered_audio)) as decoded:
        assert (decoded.getnchannels(), decoded.getsampwidth(), decoded.getframerate()) == (1, 2, 44100)
        accompaniment = np.frombuffer(decoded.readframes(decoded.getnframes()), dtype="<i2")
    assert not np.any(accompaniment[:22050])
    np.testing.assert_array_equal(accompaniment[22050:22050 + len(transformed)], transformed)
    assert not np.any(accompaniment[22050 + len(transformed):])
