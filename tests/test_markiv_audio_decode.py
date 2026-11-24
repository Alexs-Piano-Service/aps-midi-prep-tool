"""Archive integration keeps originals and publishes verifiable audio pairs."""

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
import threading
from types import SimpleNamespace
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import disklavier_audio_decode as decoder
from aps_midi_prep_tool_app import audio_tools, paired_audio
from aps_midi_prep_tool_app.audio_tools import find_lame_command
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.helpers.file_batch import FileBatchWriteError
from aps_midi_prep_tool_app.markiv_backup import backup
from aps_midi_prep_tool_app.markiv_backup.library import scan_library
from aps_midi_prep_tool_app.smart_pianosoft import (
    parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog,
    update_smart_pianosoft_disk_title,
)


def _source(tmp_path, files):
    source = tmp_path / 'source'
    for name, payload in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return source


def _manifest(result):
    return json.loads((result.folder / backup.MANIFEST).read_text())


@pytest.fixture
def fake_decode(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_tools, 'find_lame_command', lambda: 'fake-lame')

    def decode(source, output, **kwargs):
        calls.append(Path(source).name)
        if Path(source).read_bytes() in {b'ordinary music', b'native accompaniment'}:
            raise decoder.NotEncodedAudioError('No reliable control channel')
        Path(output).write_bytes(b'clean music PCM')
        return SimpleNamespace(events=((1.0, b'\x90\x3c\x40'), (2.0, b'\x80\x3c\x00')),
                               control_channel='right', report={'validated': True})

    def export(events, source, stem, **kwargs):
        assert Path(source).read_bytes() == b'clean music PCM'
        Path(str(stem) + '.mid').write_bytes(b'MThd paired MIDI')
        Path(str(stem) + '.mp3').write_bytes(b'clean MP3')
        return {'hardware_verified': False}

    monkeypatch.setattr(decoder, 'decode_disklavier_audio', decode)
    monkeypatch.setattr(paired_audio, 'export_midi_mp3', export)
    return calls


def _assert_originals(result, source):
    for record in _manifest(result)['files']:
        original = (source / record['source']).read_bytes()
        assert (result.folder / record['destination']).read_bytes() == original
        assert record['sha256'] == hashlib.sha256(original).hexdigest()


def test_audio_split_is_opt_in_and_retains_native_pairs_and_ordinary_music(tmp_path, fake_decode):
    source = _source(tmp_path, {
        'songs/user/1/Encoded.WAV': b'control and music',
        'songs/user/1/Native.mid': b'native MIDI',
        'songs/user/1/Native.mp3': b'native accompaniment',
        'songs/user/1/Ordinary.wav': b'ordinary music',
        'songs/tool/metadata.wav': b'metadata asset',
        'songs/pspg/archive/cache/1/Show.WAV': b'presentation dependency',
    })
    plan = scan_library(source)
    plain = backup.run_backup(plan, tmp_path / 'backup')
    assert plain.status == 'complete'
    assert fake_decode == []
    assert _manifest(plain)['derivatives'] == []
    events = []
    result = backup.run_backup(plan, tmp_path / 'backup', split_audio=True, progress=events.append)
    assert (result.status, result.decoded, result.audio_skipped) == ('complete', 1, 2)
    assert set(fake_decode) == {'Encoded.WAV', 'Native.mp3', 'Ordinary.wav'}
    _assert_originals(result, source)
    derivatives = _manifest(result)['derivatives']
    assert [item['kind'] for item in derivatives] == ['MIDI', 'MP3']
    assert Path(derivatives[0]['destination']).with_suffix('') == Path(derivatives[1]['destination']).with_suffix('')
    assert all(item['destination'].startswith('Paired MIDI and MP3/') for item in derivatives)
    assert backup.verify_backup(result.folder) == []
    event = [event for event in events if event['event'] == 'audio_decode'][-1]
    assert event['completed'] == event['total'] == event['audio_total'] == 3
    assert (event['decoded'], event['audio_skipped']) == (1, 2)
    # Both files participate in ordinary backup verification.
    (result.folder / derivatives[1]['destination']).write_bytes(b'damaged MP3')
    assert any(derivatives[1]['destination'] in message for message in backup.verify_backup(result.folder))


@pytest.mark.parametrize('preserve_yamaha_catalogs', [False, True])
def test_same_stem_sources_produce_distinct_complete_pairs(tmp_path, fake_decode, preserve_yamaha_catalogs):
    source = _source(tmp_path, {'songs/user/1/Song.part.WAV': b'encoded WAV',
                                'songs/user/1/Song.part.MP3': b'encoded MP3'})
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=True,
                               preserve_yamaha_catalogs=preserve_yamaha_catalogs)
    assert result.decoded == 2
    paths = [Path(item['destination']) for item in _manifest(result)['derivatives']]
    assert len({str(path).casefold() for path in paths}) == 4
    assert {path.name for path in paths} == {'Song.part.mid', 'Song.part.mp3',
                                           'Song.part (2).mid', 'Song.part (2).mp3'}
    _assert_originals(result, source)


@pytest.mark.parametrize('catalog_option', ['generate_yamaha_catalogs', 'preserve_yamaha_catalogs'])
def test_compatible_audio_pairs_have_matching_short_names_and_verified_catalogs(tmp_path, fake_decode,
                                                                              monkeypatch, catalog_option):
    source = _source(tmp_path, {
        'songs/user/1/Song with a long name.WAV': b'encoded WAV',
        'songs/user/1/Song with a long name.MP3': b'encoded MP3',
    })
    if catalog_option == 'preserve_yamaha_catalogs':
        (source / 'songs/user/1/PDISK.MNG').write_bytes(update_smart_pianosoft_disk_title(b'', 'User 1'))

    def export(events, source, stem, **kwargs):
        midi = mido.MidiFile(type=0)
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('end_of_track')]))
        midi.save(str(stem) + '.mid')
        Path(str(stem) + '.mp3').write_bytes(b'clean MP3')
        return {'hardware_verified': False}

    monkeypatch.setattr(paired_audio, 'export_midi_mp3', export)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=True,
                               **{catalog_option: True})

    assert result.status == 'complete', result.errors
    assert result.decoded == 2
    derivatives = _manifest(result)['derivatives']
    midis = [Path(record['destination']) for record in derivatives if record['kind'] == 'MIDI']
    assert {path.name for path in midis} == {'SONGWITH.mid', 'SONGWI~2.mid'}
    playback = result.folder / midis[0].parent
    assert parse_smart_pianosoft_disk_title((playback / 'PDISK.MNG').read_bytes()) == 'User 1'
    if catalog_option == 'generate_yamaha_catalogs':
        songs = parse_smart_pianosoft_song_catalog((playback / 'PSONG.MNG').read_bytes())
        assert {song.filename for song in songs} == {path.name for path in midis}
        assert all(song.title == 'Song with a long name' for song in songs)
    else:
        assert not (playback / 'PSONG.MNG').exists()
    assert all((result.folder / path).with_suffix('.mp3').is_file() for path in midis)
    _assert_originals(result, source)
    assert backup.verify_backup(result.folder) == []


def test_decoded_backups_without_random_suffixes_preserve_existing_pairs(tmp_path, fake_decode, monkeypatch):
    source = _source(tmp_path, {'songs/user/1/Encoded.WAV': b'control and music'})
    monkeypatch.setattr(backup, 'datetime', SimpleNamespace(
        now=lambda tz=None: datetime(2026, 11, 21, 12, 34, 56, tzinfo=tz)))
    plan = scan_library(source)
    first = backup.run_backup(plan, tmp_path / 'backup', split_audio=True, random_suffixes=False)
    contents = {path.relative_to(first.folder): path.read_bytes()
                for path in first.folder.rglob('*') if path.is_file()}
    second = backup.run_backup(plan, tmp_path / 'backup', split_audio=True, random_suffixes=False)

    assert first.folder.name == 'MarkIV-Backup-20261121-123456'
    assert second.folder.name == first.folder.name + ' (2)'
    assert contents == {path.relative_to(first.folder): path.read_bytes()
                        for path in first.folder.rglob('*') if path.is_file()}
    for result in (first, second):
        assert (result.status, result.decoded) == ('complete', 1)
        assert len(_manifest(result)['derivatives']) == 2
        assert backup.verify_backup(result.folder) == []
        _assert_originals(result, source)


def test_decode_failure_keeps_original_and_continues(tmp_path, fake_decode, monkeypatch):
    source = _source(tmp_path, {'songs/user/1/Bad.WAV': b'bad carrier',
                                'songs/user/1/Good.WAV': b'good carrier'})
    real_decode = decoder.decode_disklavier_audio

    def decode(path, *args, **kwargs):
        if path.name == 'Bad.WAV':
            raise decoder.DisklavierDecodeError('Corrupted control data')
        return real_decode(path, *args, **kwargs)

    monkeypatch.setattr(decoder, 'decode_disklavier_audio', decode)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=True)
    assert (result.status, result.decoded) == ('incomplete', 1)
    assert 'Corrupted control data' in result.errors[0]
    assert (result.folder / '.incomplete').exists()
    _assert_originals(result, source)
    assert [record['audio_decode']['status'] for record in _manifest(result)['files']] == ['failed', 'decoded']
    assert not list(result.folder.glob('.decode-*'))


@pytest.mark.parametrize('wrapped', [False, True])
def test_cancel_preserves_previous_pair_counts_and_archived_originals(tmp_path, fake_decode, monkeypatch, wrapped):
    source = _source(tmp_path, {'songs/user/1/A.WAV': b'first', 'songs/user/1/B.WAV': b'second'})
    cancel = threading.Event()
    real_export = paired_audio.export_midi_mp3
    count = 0

    def export(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            cancel.set()
            if wrapped:
                raise FileBatchWriteError('Cancelled; recovery files retained',
                                          original_exception=InterruptedError(), retained_paths=('recovery',))
            raise InterruptedError('Cancelled')
        return real_export(*args, **kwargs)

    monkeypatch.setattr(paired_audio, 'export_midi_mp3', export)
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=True, cancel=cancel)
    assert (result.status, result.decoded, result.audio_skipped) == ('cancelled', 1, 0)
    if wrapped:
        assert 'recovery files retained' in result.errors[0]
        assert _manifest(result)['files'][1]['audio_decode']['retained_paths'] == ['recovery']
    else:
        assert result.errors == []
    _assert_originals(result, source)
    assert [record['status'] for record in _manifest(result)['derivatives']] == [
        'verified', 'verified', 'cancelled', 'cancelled']
    assert not list(result.folder.glob('.decode-*'))


def test_audio_headroom_is_checked_before_creating_backup(tmp_path, monkeypatch):
    source = _source(tmp_path, {'songs/user/1/Encoded.MP3': b'mp3'})
    plan = scan_library(source)
    free = plan.total_bytes + backup._manifest_allowance(plan)
    monkeypatch.setattr(audio_tools, 'find_lame_command', lambda: 'fake-lame')
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=free))
    with pytest.raises(ValueError, match='decoded audio pairs'):
        backup.run_backup(plan, tmp_path / 'backup', split_audio=True)
    assert not (tmp_path / 'backup').exists()


def _encoded_library(tmp_path, companion_extension=None):
    source = tmp_path / 'source'
    album = source / 'songs/user/1'
    album.mkdir(parents=True)
    music = tmp_path / 'music.wav'
    frames = np.arange(3 * 44100)
    pcm = (5000 * np.sin(2 * np.pi * 440 * frames / 44100)).astype('<i2')
    with wave.open(str(music), 'wb') as stream:
        stream.setparams((1, 2, 44100, len(pcm), 'NONE', 'not compressed'))
        stream.writeframes(pcm.tobytes())
    events = [(.5, bytes([0x90, 60, 80])), (1.5, bytes([0x80, 60, 0]))]
    encoded = album / 'Phrase.wav'
    encode_disklavier_wav(events, music, encoded)
    if companion_extension:
        companion = mido.MidiFile(type=0, ticks_per_beat=480)
        companion.tracks.append(mido.MidiTrack([
            mido.Message('note_on', note=72, velocity=64),
            mido.Message('note_off', note=72, velocity=0, time=480),
            mido.MetaMessage('end_of_track'),
        ]))
        payload = io.BytesIO()
        companion.save(file=payload)
        data = payload.getvalue()
        if companion_extension == 'FIL':
            data = convert_midi_bytes_to_eseq_bytes(data)
        (album / f'Phrase.{companion_extension}').write_bytes(data)
    return source, encoded, events


@pytest.mark.parametrize('companion_extension', [None, 'mid', 'FIL'],
                         ids=['audio-only', 'midi-companion', 'eseq-companion'])
def test_real_encoded_wav_import_produces_playable_midi_and_clean_mp3(tmp_path, companion_extension):
    if not find_lame_command():
        pytest.skip('LAME is needed for the real MP3 integration check')
    source, encoded, events = _encoded_library(tmp_path, companion_extension)
    originals = {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    plan = scan_library(source)
    if companion_extension == 'FIL':
        assert next(item for item in plan.files if item.kind == 'E-SEQ').convertible_eseq
    result = backup.run_backup(plan, tmp_path / 'backup', split_audio=True)
    assert result.status == 'complete', result.errors
    assert result.decoded == 1
    manifest = _manifest(result)
    midi_record, audio_record = manifest['derivatives']
    song = mido.MidiFile(result.folder / midi_record['destination'])
    actual = [(message.type, message.note) for message in song if message.type in {'note_on', 'note_off'}]
    assert actual == [('note_on', 60), ('note_off', 60)]
    actual_events = read_midi_events(result.folder / midi_record['destination'])
    assert [data for _, data in actual_events] == [data for _, data in events]
    assert [seconds for seconds, _ in actual_events] == pytest.approx(
        [seconds + .5 for seconds, _ in events], abs=1 / 3150)
    assert Path(midi_record['destination']).with_suffix('') == Path(audio_record['destination']).with_suffix('')

    clean_audio = tmp_path / 'decoded-mp3.wav'
    audio_tools.decode_mp3(result.folder / audio_record['destination'], clean_audio)
    with wave.open(str(encoded), 'rb') as stream:
        expected_music = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').reshape(-1, 2)[:, 0]
    with wave.open(str(clean_audio), 'rb') as stream:
        assert stream.getnchannels() == 1
        assert stream.getframerate() == 44100
        actual_music = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2')
    assert len(actual_music) == len(expected_music)
    assert np.corrcoef(actual_music, expected_music)[0, 1] > .98
    assert song.length == pytest.approx(len(actual_music) / 44100, abs=.0001)
    assert originals == {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    _assert_originals(result, source)
    assert backup.verify_backup(result.folder) == []


def test_missing_lame_is_reported_before_creating_or_copying_backup(tmp_path, monkeypatch):
    source, encoded, _events = _encoded_library(tmp_path)
    original = encoded.read_bytes()
    plan = scan_library(source)
    monkeypatch.setattr(audio_tools, 'find_lame_command', lambda: '')
    monkeypatch.setattr(backup, '_hash', lambda *args, **kwargs: pytest.fail('Copy started before LAME validation'))
    target = tmp_path / 'backup'
    with pytest.raises(ValueError, match='LAME'):
        backup.run_backup(plan, target, split_audio=True)
    assert not target.exists()
    assert encoded.read_bytes() == original


@pytest.mark.parametrize('split_audio,files', [
    (False, {'songs/user/1/Encoded.wav': b'control and music'}),
    (True, {'songs/user/1/Native.mid': b'native MIDI'}),
    (True, {'songs/tool/metadata.wav': b'metadata asset',
            'songs/pspg/archive/cache/1/Show.WAV': b'presentation dependency'}),
], ids=['splitting-disabled', 'no-audio', 'excluded-audio'])
def test_backup_without_audio_to_split_does_not_require_lame(tmp_path, monkeypatch, split_audio, files):
    source = _source(tmp_path, files)
    monkeypatch.setattr(audio_tools, 'find_lame_command', lambda: '')
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=split_audio)
    assert result.status == 'complete', result.errors
    assert result.decoded == 0
    assert _manifest(result)['derivatives'] == []
    _assert_originals(result, source)
