"""Mark IV catalog associations never substitute display order for CD identity."""
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app.markiv_backup.library import BackupPlan, CancelledError, PlannedFile
from aps_midi_prep_tool_app.markiv_backup.playback import (
    PlaybackPair, prepare_pair, resolve_playback_pairs,
)
from aps_midi_prep_tool_app.smart_pianosoft_sync import SynchronizationError, _envelope


def _timecode(seconds):
    minute, remainder = divmod(round(seconds * 9600), 60 * 9600)
    second, remainder = divmod(remainder, 9600)
    frame, subframe = divmod(remainder, 128)
    return [minute, second, frame, subframe]


def _fingerprint(kind, seconds, samples=None):
    return mido.Message('sysex', data=[0x43, 0x71, 0x7B, kind, *_timecode(seconds),
                                     kind // 2, 3, *[(v & 127) for v in
                                                    (samples if samples is not None else [1, 2] * 128)]])


def _cd_reference(track):
    positions = [0, 2, 0, 0, 20, 0, 0, 40, 0, 1, 0, 0]
    return mido.MetaMessage('sequencer_specific', data=[
        0x43, 0x71, 0x7D, *positions[(track - 1) * 3:track * 3], 4, *positions])


def _midi(root, source='songs/pianosoft/1/01.mid', *, cd_track=None, fingerprints=None,
          archive=None, **metadata):
    path = root / (archive or source)
    path.parent.mkdir(parents=True, exist_ok=True)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000)])
    if cd_track is not None:
        track.extend([_cd_reference(cd_track), *(fingerprints or [_fingerprint(0, 2), _fingerprint(2, 10)])])
    track.extend([mido.Message('note_on', note=60, velocity=80, time=480),
                  mido.MetaMessage('set_tempo', tempo=1000000),
                  mido.Message('note_off', note=60, time=480)])
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    midi.tracks.append(track)
    midi.save(path)
    item = PlannedFile(str(path.relative_to(root)), str(path.relative_to(root)), path.stat().st_size,
                       'MIDI', metadata)
    item.original_source = source
    return item


def _audio(root, source='songs/audio/9/track-03.WAV', *, archive=None, **metadata):
    path = root / (archive or source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'audio placeholder; resolving does not read PCM')
    item = PlannedFile(str(path.relative_to(root)), str(path.relative_to(root)), path.stat().st_size,
                       'WAV' if path.suffix.lower() == '.wav' else 'MP3', metadata)
    item.original_source = source
    return item


def _plan(root, *items, links=()):
    result = BackupPlan(root, list(items), [], [], sum(item.size for item in items))
    result.sync_links = list(links)
    return result


def _link(midi, audio, *, cd_id='disc', offsets='-35 -31'):
    return {'midi_path': '/home/' + midi.original_source, 'wave_path': '/home/' + audio.original_source,
            'cdtoc': cd_id, 'offsets': offsets}


def test_cd_track_comes_from_midi_toc_instead_of_album_order(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc', track_number=1)
    wrong = _audio(tmp_path, 'songs/audio/9/track-01.WAV', cd_id='disc', track_number=1)
    correct = _audio(tmp_path, cd_id='disc', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, midi, wrong, correct))
    assert [(pair.midi, pair.audio, pair.cd_track) for pair in result.pairs] == [(midi, correct, 3)]
    assert result.paired_audio_sources == {correct.source}
    assert not result.issues


def test_original_paths_resolve_renamed_archived_files(tmp_path):
    midi = _midi(tmp_path, cd_track=3, archive='PianoSoft/Album/01 - Song.mid', cd_id='disc')
    audio = _audio(tmp_path, archive='Audio/Album/03 - Recording.WAV', cd_id='disc', track_number=3)
    link = _link(midi, audio)
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio, links=[link]))
    assert len(result.pairs) == 1
    assert result.pairs[0].sync_links == (link,)
    assert result.pairs[0].association == 'sps_metadata'
    assert result.paired_audio_sources == {audio.source}


@pytest.mark.parametrize('stored_wave', ['track-03', '/home/songs/audio/old/track-03.WAV',
                                       '/home/songs/audio/9/track-03.WAV'])
def test_saved_sync_link_identifies_actual_cd_edition_independently_of_midi_cd_id(tmp_path, stored_wave):
    midi = _midi(tmp_path, cd_track=3, cd_id='original-midi-edition')
    audio = _audio(tmp_path, cd_id='recorded-audio-edition', track_number=3)
    other = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='original-midi-edition', track_number=3)
    link = {**_link(midi, audio, cd_id='recorded-audio-edition'), 'wave_path': stored_wave}
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio, other, links=[link]))
    assert not result.issues
    assert [(pair.midi, pair.audio) for pair in result.pairs] == [(midi, audio)]
    assert result.pairs[0].sync_links == (link,)


def test_saved_audio_basename_does_not_match_a_different_cd_edition(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='original-midi-edition')
    audio = _audio(tmp_path, cd_id='unrelated-edition', track_number=3)
    link = {**_link(midi, audio, cd_id='recorded-audio-edition'), 'wave_path': 'track-03'}
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio, links=[link]))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['missing_audio']


def test_saved_cd_and_basename_with_two_recordings_remains_ambiguous(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='original-midi-edition')
    first = _audio(tmp_path, cd_id='recorded-audio-edition', track_number=3)
    second = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='recorded-audio-edition', track_number=3)
    link = {**_link(midi, first, cd_id='recorded-audio-edition'), 'wave_path': 'track-03'}
    result = resolve_playback_pairs(_plan(tmp_path, midi, first, second, links=[link]))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['ambiguous_audio']


def test_planning_uses_verified_copy_when_original_drive_is_absent(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc')
    audio = _audio(tmp_path, cd_id='disc', track_number=3)
    copied = tmp_path / 'verified.mid'
    (tmp_path / midi.source).rename(copied)
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio), source_paths={midi.source: copied})
    assert len(result.pairs) == 1
    assert result.pairs[0].midi.source == midi.source


def test_missing_verified_copy_never_falls_back_to_original_drive(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc')
    audio = _audio(tmp_path, cd_id='disc', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio), source_paths={})
    assert not result.pairs
    assert result.issues[0].code == 'invalid_midi'


def test_cd_id_must_match_even_when_title_and_order_match(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc-one', title='Shared title', track_number=3)
    audio = _audio(tmp_path, cd_id='disc-two', title='Shared title', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['missing_audio']


def test_stored_audio_path_and_cd_track_conflict_is_not_guessed(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc')
    one = _audio(tmp_path, 'songs/audio/9/track-01.WAV', cd_id='disc', track_number=1)
    three = _audio(tmp_path, cd_id='disc', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, midi, one, three, links=[_link(midi, one)]))
    assert not result.pairs
    assert result.issues[0].code == 'ambiguous_audio'


def test_two_recordings_with_the_same_cd_track_are_ambiguous(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc')
    first = _audio(tmp_path, cd_id='disc', track_number=3)
    second = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='disc', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, midi, first, second))
    assert not result.pairs
    assert result.issues[0].code == 'ambiguous_audio'


def test_distinct_performances_can_share_one_cd_recording(tmp_path):
    first = _midi(tmp_path, cd_track=3, cd_id='disc')
    second = _midi(tmp_path, 'songs/user/2/other.mid', cd_track=3, cd_id='disc')
    audio = _audio(tmp_path, cd_id='disc', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, first, second, audio))
    assert [pair.audio for pair in result.pairs] == [audio, audio]
    assert result.paired_audio_sources == {audio.source}


def test_missing_historical_midi_is_reported_on_the_audio(tmp_path):
    audio = _audio(tmp_path)
    link = {'midi_path': '/home/songs/pianosoft/99/missing.mid',
            'wave_path': '/home/' + audio.original_source, 'offsets': '0 0'}
    result = resolve_playback_pairs(_plan(tmp_path, audio, links=[link, link]))
    assert not result.pairs
    assert len(result.issues) == 1
    assert result.issues[0].code == 'missing_midi'
    assert result.issues[0].audio_source == audio.original_source


def test_removable_midi_reference_is_reported_without_resolving_outside_backup(tmp_path):
    audio = _audio(tmp_path)
    result = resolve_playback_pairs(_plan(tmp_path, audio, links=[{
        'midi_path': '/mnt/floppy/01.mid', 'wave_path': '/home/' + audio.original_source,
    }]))
    assert result.issues[0].code == 'missing_midi'
    assert result.issues[0].midi_source == '/mnt/floppy/01.mid'


@pytest.mark.parametrize('stored_audio', ['track-03', '/home/songs/audio/old/track-03.WAV'])
def test_missing_midi_warning_resolves_historical_audio_name_by_exact_cd_id(tmp_path, stored_audio):
    audio = _audio(tmp_path, cd_id='disc')
    other = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='another disc')
    result = resolve_playback_pairs(_plan(tmp_path, audio, other, links=[{
        'midi_path': '/home/songs/pianosoft/99/missing.mid', 'wave_path': stored_audio, 'cdtoc': 'disc',
    }]))
    assert not result.pairs
    assert [(issue.code, issue.audio_source) for issue in result.issues] == [('missing_midi', audio.original_source)]


def test_historical_audio_name_does_not_guess_between_duplicate_cd_recordings(tmp_path):
    first = _audio(tmp_path, cd_id='disc')
    second = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='disc')
    result = resolve_playback_pairs(_plan(tmp_path, first, second, links=[{
        'midi_path': '/home/songs/pianosoft/99/missing.mid', 'wave_path': 'track-03', 'cdtoc': 'disc',
    }]))
    assert not result.pairs and not result.issues


def test_native_same_stem_companion_preserves_tempo_and_timeline(tmp_path):
    midi = _midi(tmp_path, 'songs/user/1/01.mid')
    audio = _audio(tmp_path, 'songs/user/1/01.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert len(result.pairs) == 1
    prepared = prepare_pair(result.pairs[0], tmp_path / midi.source, tmp_path / audio.source)
    assert prepared.events == ((.5, b'\x90\x3c\x50'), (1.5, b'\x80\x3c\x40'))
    assert (prepared.offset_seconds, prepared.time_scale) == (0, 1)
    assert prepared.alignment_details['method'] == 'existing_companion_timeline'


def test_native_plus_audio_cd_start_command_does_not_require_sps_fingerprints(tmp_path):
    midi = _midi(tmp_path, 'songs/demo/1/01.mid', type='PSPA')
    parsed = mido.MidiFile(tmp_path / midi.source)
    parsed.tracks[0].insert(0, mido.Message('sysex', data=[0x43, 0x71, 0x05, 0x01]))
    parsed.save(tmp_path / midi.source)
    audio = _audio(tmp_path, 'songs/demo/1/01.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert len(result.pairs) == 1 and not result.issues
    prepared = prepare_pair(result.pairs[0], tmp_path / midi.source, tmp_path / audio.source)
    assert (prepared.offset_seconds, prepared.time_scale) == (0, 1)
    assert prepared.events[-2:] == ((.5, b'\x90\x3c\x50'), (1.5, b'\x80\x3c\x40'))


def test_audio_only_catalog_wrapper_is_not_a_piano_performance(tmp_path):
    midi = _midi(tmp_path, 'songs/diag/1/01.mid', type='AUDIO')
    wrapper = mido.MidiFile(type=0)
    wrapper.tracks.append(mido.MidiTrack([mido.MetaMessage('track_name', name='Audio only')]))
    wrapper.save(tmp_path / midi.source)
    audio = _audio(tmp_path, 'songs/diag/1/01.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert not result.pairs and not result.issues


def test_audio_type_label_does_not_discard_actual_piano_events(tmp_path):
    midi = _midi(tmp_path, 'songs/diag/1/01.mid', type='AUDIO')
    audio = _audio(tmp_path, 'songs/diag/1/01.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert len(result.pairs) == 1


def test_catalog_add_file_companion_need_not_share_filename(tmp_path):
    midi = _midi(tmp_path, 'songs/user/1/piano.mid', add_file='recording.wav')
    audio = _audio(tmp_path, 'songs/user/1/recording.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert len(result.pairs) == 1
    assert result.pairs[0].association == 'catalog_companion'


@pytest.mark.parametrize('filename', ['missing.wav', 'nested/missing.MP3'])
def test_missing_declared_audio_companion_is_not_treated_as_a_solo(tmp_path, filename):
    midi = _midi(tmp_path, 'songs/user/1/piano.mid', add_file=filename)
    result = resolve_playback_pairs(_plan(tmp_path, midi))
    assert not result.pairs
    assert [(issue.code, issue.midi_source, issue.audio_source) for issue in result.issues] == [
        ('missing_audio', midi.original_source, 'songs/user/1/' + filename),
    ]


def test_missing_declared_audio_is_not_replaced_by_a_different_same_stem_companion(tmp_path):
    midi = _midi(tmp_path, 'songs/user/1/piano.mid', add_file='missing.wav')
    audio = _audio(tmp_path, 'songs/user/1/piano.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['missing_audio']


@pytest.mark.parametrize('value', [None, 0, 1, '', '0', '1', 'notes.txt', 'other.mid', '../missing.wav', '/missing.wav'])
def test_add_file_flags_and_unrelated_values_do_not_require_an_audio_companion(tmp_path, value):
    midi = _midi(tmp_path, add_file=value, cd_id='album-without-sps-reference')
    result = resolve_playback_pairs(_plan(tmp_path, midi))
    assert not result.pairs and not result.issues


def test_native_pair_prefers_wav_to_mp3_copy(tmp_path):
    midi = _midi(tmp_path, 'songs/user/1/01.mid')
    audio = _audio(tmp_path, 'songs/user/1/01.WAV')
    compressed = _audio(tmp_path, 'songs/user/1/01.mp3')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio, compressed))
    assert [pair.audio for pair in result.pairs] == [audio]


def test_native_audio_with_two_midi_companions_is_not_reused_as_two_pairs(tmp_path):
    first = _midi(tmp_path, 'songs/user/1/01.mid')
    second = _midi(tmp_path, 'songs/user/1/01.midi')
    audio = _audio(tmp_path, 'songs/user/1/01.wav')
    result = resolve_playback_pairs(_plan(tmp_path, first, second, audio))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['ambiguous_midi', 'ambiguous_midi']


def test_incomplete_sps_headers_never_fall_back_to_native_timing(tmp_path):
    midi = _midi(tmp_path, 'songs/user/1/01.mid', cd_track=3, fingerprints=[_fingerprint(0, 2)])
    audio = _audio(tmp_path, 'songs/user/1/01.wav')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert not result.pairs
    assert result.issues[0].code == 'invalid_midi'


def test_missing_cd_reference_does_not_substitute_display_order(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc', track_number=3)
    parsed = mido.MidiFile(tmp_path / midi.source)
    parsed.tracks[0] = mido.MidiTrack(message for message in parsed.tracks[0]
                                       if message.type != 'sequencer_specific')
    parsed.save(tmp_path / midi.source)
    audio = _audio(tmp_path, cd_id='disc', track_number=3)
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert not result.pairs
    assert result.issues[0].code == 'missing_audio'


def test_explicit_path_can_identify_audio_without_cd_track(tmp_path):
    midi = _midi(tmp_path, cd_track=3, cd_id='disc')
    parsed = mido.MidiFile(tmp_path / midi.source)
    parsed.tracks[0] = mido.MidiTrack(message for message in parsed.tracks[0]
                                       if message.type != 'sequencer_specific')
    parsed.save(tmp_path / midi.source)
    audio = _audio(tmp_path, cd_id='disc')
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio, links=[_link(midi, audio)]))
    assert len(result.pairs) == 1
    assert result.pairs[0].cd_track is None


def test_piano_only_midi_does_not_claim_an_unrelated_audio_file(tmp_path):
    midi = _midi(tmp_path)
    audio = _audio(tmp_path, title='Same title', track_number=1)
    result = resolve_playback_pairs(_plan(tmp_path, midi, audio))
    assert not result.pairs and not result.issues


def test_pianodir_catalog_with_album_cd_id_is_not_parsed_as_a_performance(tmp_path):
    from aps_midi_prep_tool_app.eseq_pianodir import build_pianodir_bytes

    source = 'songs/user/1/PIANODIR.FIL'
    path = tmp_path / source
    path.parent.mkdir(parents=True)
    path.write_bytes(build_pianodir_bytes([]))
    item = PlannedFile(source, source, path.stat().st_size, 'E-SEQ', {'cd_id': 'DISC'})
    result = resolve_playback_pairs(_plan(tmp_path, item))
    assert not result.pairs and not result.issues


def test_cancellation_stops_planning_and_pair_preparation(tmp_path):
    midi = _midi(tmp_path)
    audio = _audio(tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CancelledError):
        resolve_playback_pairs(_plan(tmp_path, midi, audio), cancel)
    with pytest.raises(CancelledError):
        prepare_pair(PlaybackPair(midi, audio, 'same_stem'), tmp_path / midi.source,
                     tmp_path / audio.source, cancel=cancel)


@pytest.fixture
def fingerprint_pair(tmp_path):
    rng = np.random.default_rng(761)
    times = np.arange(14 * 44100) / 44100
    knots = np.arange(0, 14.02, .02)
    amplitude = np.interp(times, knots, rng.uniform(.05, .8, len(knots)))
    mono = np.asarray(18000 * amplitude * (np.sin(2 * np.pi * 437 * times)
                                          + .3 * np.sin(2 * np.pi * 731 * times)), dtype=np.int16)
    pcm = np.column_stack((mono, mono))
    envelope = _envelope(pcm)
    messages = []
    for kind, index in [(0, 345), (2, 1723)]:
        segment = envelope[index:index + 256]
        samples = np.rint(segment / np.max(np.abs(segment)) * 60).astype(int)
        messages.append(_fingerprint(kind, index * 256 / 44100, samples))
    midi = _midi(tmp_path, cd_track=3, cd_id='disc', fingerprints=messages)
    audio = _audio(tmp_path, cd_id='disc', track_number=3)
    # Deliberately wrong stored calibration must not govern exported timing.
    link = _link(midi, audio, offsets='9000 -9000')
    pair = resolve_playback_pairs(_plan(tmp_path, midi, audio, links=[link])).pairs[0]
    with wave.open(str(tmp_path / audio.source), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(round(.18 * 44100) * 4) + pcm.astype('<i2').tobytes())
    return pair, tmp_path / midi.source, tmp_path / audio.source


def test_fingerprints_determine_timing_instead_of_unverified_stored_offsets(fingerprint_pair):
    pair, midi, audio = fingerprint_pair
    prepared = prepare_pair(pair, midi, audio)
    assert prepared.offset_seconds == pytest.approx(.18, abs=.008)
    assert prepared.time_scale == pytest.approx(1, abs=.0008)
    assert prepared.alignment_details['confidence'] > .98
    assert prepared.alignment_details['stored_sync_links'][0]['offsets'] == '9000 -9000'
    assert prepared.events == ((.5, b'\x90\x3c\x50'), (1.5, b'\x80\x3c\x40'))


def test_wrong_recording_fails_even_when_catalog_links_and_offsets_exist(fingerprint_pair):
    pair, midi, audio = fingerprint_pair
    with wave.open(str(audio), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(14 * 44100 * 4))
    with pytest.raises(SynchronizationError, match='does not match'):
        prepare_pair(pair, midi, audio)


def _different_cd_edition(pair):
    # The same full TOC with a one-frame lead-in change and a two-frame
    # boundary change: close enough to shortlist, but insufficient to pair.
    pair.midi.metadata['cd_id'] = '000200001400002800010000'
    pair.audio.metadata['cd_id'] = '000201001403002801010001'


def test_alternate_cd_edition_requires_both_fingerprints_and_records_evidence(fingerprint_pair, tmp_path):
    pair, midi, audio = fingerprint_pair
    _different_cd_edition(pair)
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, pair.audio))
    assert not result.issues
    assert [p.audio for p in result.pairs] == [pair.audio]
    evidence = result.pairs[0].association_details
    assert evidence['method'] == 'whole_cd_toc_and_two_fingerprints'
    assert evidence['toc_max_difference_frames'] == 2
    assert evidence['candidates'][0]['alignment']['confidence'] > .98
    assert prepare_pair(result.pairs[0], midi, audio).alignment_details['association_validation'] == evidence
    # Planning does not allow stale fingerprints to bypass export validation.
    with wave.open(str(audio), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(14 * 44100 * 4))
    with pytest.raises(SynchronizationError):
        prepare_pair(result.pairs[0], midi, audio)


def test_alternate_cd_edition_does_not_accept_only_the_opening_fingerprint(fingerprint_pair, tmp_path):
    pair, _midi_path, audio = fingerprint_pair
    _different_cd_edition(pair)
    with wave.open(str(audio), 'rb') as source:
        pcm = source.readframes(source.getnframes())
    with wave.open(str(audio), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm[:6 * 44100 * 4] + bytes(len(pcm) - 6 * 44100 * 4))
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, pair.audio))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['missing_audio']


@pytest.mark.parametrize('other_toc', [
    '000201001603002801010001',  # One boundary differs by more than one second.
    '000201001403002801003000010001',  # Different number of tracks.
    '000201003C03002801010001',  # Invalid seconds in one position.
])
def test_matching_audio_without_a_matching_whole_toc_is_not_guessed(fingerprint_pair, tmp_path, other_toc):
    pair, _midi_path, _audio_path = fingerprint_pair
    _different_cd_edition(pair)
    pair.audio.metadata['cd_id'] = other_toc
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, pair.audio))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['missing_audio']


def test_two_fingerprint_matches_in_alternate_editions_remain_ambiguous(fingerprint_pair, tmp_path):
    pair, _midi_path, audio = fingerprint_pair
    _different_cd_edition(pair)
    second = _audio(tmp_path, 'songs/audio/10/track-03.WAV',
                    cd_id=pair.audio.metadata['cd_id'], track_number=3)
    (tmp_path / second.source).write_bytes(audio.read_bytes())
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, pair.audio, second))
    assert not result.pairs
    assert [issue.code for issue in result.issues] == ['ambiguous_audio']


def test_wrong_saved_edition_can_be_replaced_only_by_unique_full_fingerprint_match(fingerprint_pair, tmp_path):
    pair, _midi_path, _audio_path = fingerprint_pair
    _different_cd_edition(pair)
    wrong = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='wrong-edition', track_number=3)
    link = _link(pair.midi, wrong, cd_id='wrong-edition')
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, pair.audio, wrong, links=[link]))
    assert not result.issues
    assert [p.audio for p in result.pairs] == [pair.audio]
    assert result.pairs[0].sync_links == (link,)
    assert result.pairs[0].association_details['catalog_audio_sources'] == [wrong.original_source]
    assert 'error' in result.pairs[0].association_details['candidates'][0]


def test_explicit_saved_track_reordering_requires_unique_two_fingerprint_match(fingerprint_pair, tmp_path):
    pair, _midi_path, _audio_path = fingerprint_pair
    # The actual saved track is 1 although the original MIDI CD reference is 3.
    pair.audio.metadata['track_number'] = 1
    wrong = _audio(tmp_path, 'songs/audio/10/track-03.WAV', cd_id='disc', track_number=3)
    link = _link(pair.midi, pair.audio)
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, pair.audio, wrong, links=[link]))
    assert not result.issues
    assert [p.audio for p in result.pairs] == [pair.audio]
    assert result.pairs[0].cd_track == 3
    assert result.pairs[0].association_details['method'] == 'stored_cd_link_and_two_fingerprints'


def test_alternate_edition_validation_reads_only_supplied_snapshots(fingerprint_pair, tmp_path):
    pair, midi, audio = fingerprint_pair
    _different_cd_edition(pair)
    snapshot = tmp_path / 'verified.wav'
    audio.rename(snapshot)
    audio.write_bytes(b'unverified original')
    plan = _plan(tmp_path, pair.midi, pair.audio)
    result = resolve_playback_pairs(plan, source_paths={pair.midi.source: midi, pair.audio.source: snapshot})
    assert len(result.pairs) == 1
    missing = resolve_playback_pairs(plan, source_paths={pair.midi.source: midi})
    assert not missing.pairs
    assert [issue.code for issue in missing.issues] == ['missing_audio']


@pytest.mark.parametrize('recovery', ['alternate_edition', 'saved_track_conflict'])
def test_mp3_recovery_decodes_verified_snapshot_privately_before_matching(
        fingerprint_pair, tmp_path, monkeypatch, recovery):
    from aps_midi_prep_tool_app import audio_tools

    pair, midi, audio = fingerprint_pair
    if recovery == 'alternate_edition':
        _different_cd_edition(pair)
    compressed = _audio(tmp_path, 'songs/audio/10/track-03.MP3', **pair.audio.metadata)
    snapshot = tmp_path / 'verified-audio.input'
    snapshot.write_bytes(b'verified MP3 snapshot')
    original_bytes = (tmp_path / compressed.source).read_bytes()
    decoded_paths = []
    cancel = threading.Event()

    def decode(source, destination, *, cancel):
        assert source == snapshot
        assert source.read_bytes() == b'verified MP3 snapshot'
        assert cancel is event
        assert destination.parent != snapshot.parent
        decoded_paths.append(destination)
        destination.write_bytes(audio.read_bytes())

    event = cancel
    monkeypatch.setattr(audio_tools, 'decode_mp3', decode)
    items, links = [pair.midi, compressed], []
    if recovery == 'saved_track_conflict':
        compressed.metadata['track_number'] = 1
        wrong = _audio(tmp_path, 'songs/audio/11/track-03.WAV', cd_id='disc', track_number=3)
        items.append(wrong)
        links.append(_link(pair.midi, compressed))
    result = resolve_playback_pairs(
        _plan(tmp_path, *items, links=links), cancel,
        source_paths={pair.midi.source: midi, compressed.source: snapshot},
    )
    assert not result.issues
    assert [p.audio for p in result.pairs] == [compressed]
    evidence = result.pairs[0].association_details['candidates'][0]
    assert evidence['audio_source'] == compressed.original_source
    assert evidence['alignment']['confidence'] > .98
    assert len(decoded_paths) == 1 and not decoded_paths[0].parent.exists()
    assert (tmp_path / compressed.source).read_bytes() == original_bytes
    assert snapshot.read_bytes() == b'verified MP3 snapshot'


@pytest.mark.parametrize('failure', ['decode_failure', 'cancelled_decode', 'cancelled_after_decode'])
def test_mp3_recovery_cleans_temporary_pcm_and_preserves_cancellation(
        fingerprint_pair, tmp_path, monkeypatch, failure):
    from aps_midi_prep_tool_app import audio_tools

    pair, _midi, _audio_path = fingerprint_pair
    _different_cd_edition(pair)
    compressed = _audio(tmp_path, 'songs/audio/10/track-03.mp3', **pair.audio.metadata)
    original_bytes = (tmp_path / compressed.source).read_bytes()
    decoded_paths = []
    cancel = threading.Event()

    def decode(_source, destination, *, cancel):
        decoded_paths.append(destination)
        destination.write_bytes(b'partial decoded PCM')
        if failure == 'cancelled_decode':
            raise InterruptedError('Audio conversion cancelled.')
        if failure == 'cancelled_after_decode':
            cancel.set()
            return
        raise RuntimeError('LAME audio conversion failed')

    monkeypatch.setattr(audio_tools, 'decode_mp3', decode)
    plan = _plan(tmp_path, pair.midi, compressed)
    if failure == 'decode_failure':
        result = resolve_playback_pairs(plan, cancel)
        assert not result.pairs
        assert [issue.code for issue in result.issues] == ['missing_audio']
    else:
        with pytest.raises((InterruptedError, CancelledError)):
            resolve_playback_pairs(plan, cancel)
    assert len(decoded_paths) == 1 and not decoded_paths[0].parent.exists()
    assert (tmp_path / compressed.source).read_bytes() == original_bytes


def test_direct_mp3_pair_defers_decoding_until_export(fingerprint_pair, tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import audio_tools

    pair, _midi, _audio_path = fingerprint_pair
    compressed = _audio(tmp_path, 'songs/audio/10/track-03.mp3', **pair.audio.metadata)
    monkeypatch.setattr(audio_tools, 'decode_mp3', lambda *_args, **_kwargs: pytest.fail('Decoded direct pair'))
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, compressed))
    assert not result.issues
    assert [p.audio for p in result.pairs] == [compressed]


def test_real_mp3_alternate_edition_passes_both_fingerprints(fingerprint_pair, tmp_path):
    from aps_midi_prep_tool_app import audio_tools

    if not audio_tools.find_lame_command():
        pytest.skip('LAME is unavailable')
    pair, _midi, audio = fingerprint_pair
    _different_cd_edition(pair)
    compressed = _audio(tmp_path, 'songs/audio/10/track-03.mp3', **pair.audio.metadata)
    destination = tmp_path / compressed.source
    destination.unlink()
    audio_tools.encode_mp3(audio, destination)
    result = resolve_playback_pairs(_plan(tmp_path, pair.midi, compressed))
    assert not result.issues
    assert [p.audio for p in result.pairs] == [compressed]
    evidence = result.pairs[0].association_details['candidates'][0]
    assert evidence['alignment']['head_correlation'] > .98
    assert evidence['alignment']['tail_correlation'] > .98
