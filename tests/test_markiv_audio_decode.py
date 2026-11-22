"""Archive integration keeps originals and publishes verifiable audio pairs."""

import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import disklavier_audio_decode as decoder
from aps_midi_prep_tool_app import paired_audio
from aps_midi_prep_tool_app.audio_tools import find_lame_command
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav
from aps_midi_prep_tool_app.helpers.file_batch import FileBatchWriteError
from aps_midi_prep_tool_app.markiv_backup import backup
from aps_midi_prep_tool_app.markiv_backup.library import scan_library


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

    def decode(source, output, **kwargs):
        calls.append(Path(source).name)
        if Path(source).read_bytes() == b'ordinary music':
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
    assert set(fake_decode) == {'Encoded.WAV', 'Ordinary.wav'}
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


def test_same_stem_sources_produce_distinct_complete_pairs(tmp_path, fake_decode):
    source = _source(tmp_path, {'songs/user/1/Song.part.WAV': b'encoded WAV',
                                'songs/user/1/Song.part.MP3': b'encoded MP3'})
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=True)
    assert result.decoded == 2
    paths = [Path(item['destination']) for item in _manifest(result)['derivatives']]
    assert len({str(path).casefold() for path in paths}) == 4
    assert {path.name for path in paths} == {'Song.part.mid', 'Song.part.mp3',
                                           'Song.part (2).mid', 'Song.part (2).mp3'}
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
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=free))
    with pytest.raises(ValueError, match='decoded audio pairs'):
        backup.run_backup(plan, tmp_path / 'backup', split_audio=True)
    assert not (tmp_path / 'backup').exists()


def test_real_encoded_wav_import_produces_playable_midi_and_clean_mp3(tmp_path):
    if not find_lame_command():
        pytest.skip('LAME is needed for the real MP3 integration check')
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
    original_hash = hashlib.sha256(encoded.read_bytes()).hexdigest()
    result = backup.run_backup(scan_library(source), tmp_path / 'backup', split_audio=True)
    assert result.status == 'complete', result.errors
    assert result.decoded == 1
    manifest = _manifest(result)
    midi_record, audio_record = manifest['derivatives']
    song = mido.MidiFile(result.folder / midi_record['destination'])
    actual = [(message.type, message.note) for message in song if message.type in {'note_on', 'note_off'}]
    assert actual == [('note_on', 60), ('note_off', 60)]
    assert (result.folder / audio_record['destination']).stat().st_size > 1000
    assert hashlib.sha256(encoded.read_bytes()).hexdigest() == original_hash
    assert backup.verify_backup(result.folder) == []
