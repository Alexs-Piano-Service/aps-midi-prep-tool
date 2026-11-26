"""Both Mark IV playback formats preserve sources and use complete pairs."""

import io
import json
from pathlib import Path
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app.audio_preparation import ENCODED_WAV, MIDI_MP3
from aps_midi_prep_tool_app.audio_tools import find_lame_command
from aps_midi_prep_tool_app.disklavier_audio import encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.disklavier_audio_decode import decode_disklavier_audio
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.markiv_backup import backup
from aps_midi_prep_tool_app.markiv_backup.library import scan_library


def _native_source(tmp_path, *, eseq=False, encoded=False):
    source = tmp_path / 'source'
    album = source / 'songs/user/1'
    album.mkdir(parents=True)
    midi = mido.MidiFile(type=0, ticks_per_beat=1000)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage('set_tempo', tempo=1000000),
        mido.Message('note_on', note=60, velocity=70, time=500),
        mido.Message('note_off', note=60, time=1000),
        mido.MetaMessage('end_of_track', time=1500),
    ]))
    data = io.BytesIO()
    midi.save(file=data)
    path = album / ('Song.FIL' if eseq else 'Song.mid')
    path.write_bytes(convert_midi_bytes_to_eseq_bytes(data.getvalue()) if eseq else data.getvalue())
    frames = np.arange(3 * 44100)
    pcm = np.column_stack((np.sin(frames * 2 * np.pi * 220 / 44100) * 6000,
                           np.sin(frames * 2 * np.pi * 330 / 44100) * 4000)).astype('<i2')
    audio = album / 'Song.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((2, 2, 44100, len(pcm), 'NONE', 'not compressed'))
        stream.writeframes(pcm.tobytes())
    if encoded:
        ready = tmp_path / 'encoded.wav'
        encode_disklavier_wav([(0.2, b'\x90\x43\x40'), (1.2, b'\x80\x43\x00')], audio, ready)
        audio.write_bytes(ready.read_bytes())
    return source


def _require_lame(output):
    if output == MIDI_MP3 and not find_lame_command():
        pytest.skip('LAME is required for real paired export')


def _manifest(result):
    return json.loads((result.folder / 'manifest.json').read_text())


@pytest.mark.parametrize('output', [MIDI_MP3, ENCODED_WAV])
@pytest.mark.parametrize('archive', [False, True])
def test_native_pair_exports_both_formats_from_drive_or_existing_archive(tmp_path, output, archive):
    _require_lame(output)
    source = _native_source(tmp_path)
    if archive:
        source = backup.run_backup(scan_library(source), tmp_path / 'archives').folder
    originals = {path: path.read_bytes() for path in source.rglob('*') if path.is_file()}
    result = backup.run_backup(scan_library(source), tmp_path / 'output', audio_output=output)
    assert (result.status, result.decoded, result.audio_skipped) == ('complete', 1, 0), result.errors
    manifest = _manifest(result)
    assert manifest['options']['audio_output'] == output
    derivatives = manifest['derivatives']
    assert [row['kind'] for row in derivatives] == (['MIDI', 'MP3'] if output == MIDI_MP3 else ['WAV'])
    if output == MIDI_MP3:
        midi = result.folder / derivatives[0]['destination']
        assert midi.with_suffix('.mp3') == result.folder / derivatives[1]['destination']
        events = read_midi_events(midi)
        note = next((t, data) for t, data in events if data[0] == 0x90)
        assert note == (0.5, b'\x90\x3c\x46')
    else:
        decoded = decode_disklavier_audio(result.folder / derivatives[0]['destination'], tmp_path / 'decoded.wav')
        note = next(event for event in decoded.events if event.data[0] == 0x90)
        assert note.time_seconds == pytest.approx(1.0, abs=1/3150)
        assert note.data == b'\x90\x3c\x46'
    assert backup.verify_backup(result.folder) == []
    assert all(path.read_bytes() == content for path, content in originals.items())
    if archive:
        assert result.copied == 0
        assert result.folder.name.startswith('MarkIV-Prepared-')
        assert all(row['status'] == 'referenced' for row in manifest['files'])
        assert not (result.folder / 'User').exists()


def test_preparation_uses_verified_copies_after_drive_disappears(tmp_path):
    source = _native_source(tmp_path)
    plan = scan_library(source)
    disconnected = tmp_path / 'disconnected'

    def progress(event):
        if event['event'] == 'progress' and event.get('completed') == len(plan.files) and source.exists():
            source.rename(disconnected)

    result = backup.run_backup(plan, tmp_path / 'output', audio_output=ENCODED_WAV, progress=progress)
    assert not source.exists()
    assert result.status == 'complete' and result.decoded == 1, result.errors
    assert backup.verify_backup(result.folder) == []


def test_eseq_pair_is_prepared_before_optional_original_retirement(tmp_path):
    source = _native_source(tmp_path, eseq=True)
    result = backup.run_backup(scan_library(source), tmp_path / 'output',
                               audio_output=ENCODED_WAV, convert_eseq=True, keep_originals=False)
    assert (result.status, result.converted, result.decoded) == ('complete', 1, 1), result.errors
    manifest = _manifest(result)
    assert next(row for row in manifest['files'] if row['kind'] == 'E-SEQ')['status'] == 'replaced'
    assert backup.verify_backup(result.folder) == []


def test_known_pair_uses_its_midi_while_cleaning_embedded_audio_control(tmp_path):
    _require_lame(MIDI_MP3)
    result = backup.run_backup(scan_library(_native_source(tmp_path, encoded=True)),
                               tmp_path / 'output', audio_output=MIDI_MP3)
    assert result.status == 'complete' and result.decoded == 1, result.errors
    midi_row = next(row for row in _manifest(result)['derivatives'] if row['kind'] == 'MIDI')
    notes = [data[1] for _, data in read_midi_events(result.folder / midi_row['destination'])
             if data[0] == 0x90]
    assert notes == [60]  # Embedded track plays 67; the cataloged MIDI is selected.


def test_existing_encoded_wav_keeps_identical_bytes_for_encoded_output(tmp_path):
    source = _native_source(tmp_path, encoded=True)
    (source / 'songs/user/1/Song.mid').unlink()
    original = (source / 'songs/user/1/Song.wav').read_bytes()
    result = backup.run_backup(scan_library(source), tmp_path / 'output', audio_output=ENCODED_WAV)
    assert result.status == 'complete' and result.decoded == 1, result.errors
    row = _manifest(result)['derivatives'][0]
    assert (result.folder / row['destination']).read_bytes() == original


def test_archive_checksum_corruption_prevents_playback_publication(tmp_path):
    source = _native_source(tmp_path)
    archive = backup.run_backup(scan_library(source), tmp_path / 'archives')
    plan = scan_library(archive.folder)
    audio = next(item for item in plan.files if item.kind == 'WAV')
    path = archive.folder / audio.source
    data = bytearray(path.read_bytes())
    data[-20] ^= 1
    path.write_bytes(data)
    result = backup.run_backup(plan, tmp_path / 'output', audio_output=ENCODED_WAV)
    assert result.status == 'incomplete' and result.decoded == 0
    assert any('SHA-256 checksum' in error for error in result.errors)
    assert not _manifest(result)['derivatives']


def test_encoded_export_uses_verified_snapshot_when_archive_changes_after_preparation(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import disklavier_audio

    archive = backup.run_backup(scan_library(_native_source(tmp_path)), tmp_path / 'archives')
    plan = scan_library(archive.folder)
    audio = next(item for item in plan.files if item.kind == 'WAV')
    original = archive.folder / audio.source
    with wave.open(str(original), 'rb') as reader:
        expected = np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2').reshape(-1, 2)
        expected = (expected[:, 0].astype(np.int32) + expected[:, 1].astype(np.int32)) // 2
    encode = disklavier_audio.encode_disklavier_wav
    inputs = []

    def replace_after_validation(events, music, destination, **kwargs):
        inputs.append(Path(music))
        # Simulate another application replacing the audio after alignment and
        # the last original checksum check. Export must use its verified copy.
        content = bytearray(original.read_bytes())
        content[44:] = bytes(len(content) - 44)
        original.write_bytes(content)
        return encode(events, music, destination, **kwargs)

    monkeypatch.setattr(disklavier_audio, 'encode_disklavier_wav', replace_after_validation)
    result = backup.run_backup(plan, tmp_path / 'output', audio_output=ENCODED_WAV)
    assert result.status == 'complete' and result.decoded == 1, result.errors
    row, = _manifest(result)['derivatives']
    with wave.open(str(result.folder / row['destination']), 'rb') as reader:
        exported = np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2').reshape(-1, 2)
    assert np.array_equal(exported[22050:22050 + len(expected), 0], expected)
    assert inputs and all(path != original for path in inputs)
    assert all(not path.exists() for path in inputs)


def test_alignment_and_decode_read_verified_temporary_inputs(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import disklavier_audio_decode
    from aps_midi_prep_tool_app.markiv_backup import playback

    archive = backup.run_backup(scan_library(_native_source(tmp_path)), tmp_path / 'archives')
    plan = scan_library(archive.folder)
    expected = {item.kind: (archive.folder / item.source).read_bytes() for item in plan.files}
    decode, prepare = disklavier_audio_decode.decode_disklavier_audio, playback.prepare_pair
    observed = []

    def check(path, kind):
        path = Path(path)
        observed.append(path)
        assert not path.is_relative_to(archive.folder)
        assert path.read_bytes() == expected[kind]

    def decode_snapshot(source, *args, **kwargs):
        check(source, 'WAV')
        return decode(source, *args, **kwargs)

    def prepare_snapshots(pair, midi, audio, **kwargs):
        check(midi, 'MIDI')
        check(audio, 'WAV')
        return prepare(pair, midi, audio, **kwargs)

    monkeypatch.setattr(disklavier_audio_decode, 'decode_disklavier_audio', decode_snapshot)
    monkeypatch.setattr(playback, 'prepare_pair', prepare_snapshots)
    result = backup.run_backup(plan, tmp_path / 'output', audio_output=ENCODED_WAV)
    assert result.status == 'complete' and result.decoded == 1, result.errors
    assert len(observed) == 3
    assert all(not path.exists() for path in observed)


def test_snapshot_checksum_must_match_before_audio_is_decoded(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import disklavier_audio_decode
    from aps_midi_prep_tool_app.markiv_backup import playback_export

    archive = backup.run_backup(scan_library(_native_source(tmp_path)), tmp_path / 'archives')
    plan = scan_library(archive.folder)
    snapshot = playback_export._snapshot_source

    def change_before_snapshot(source, destination, *args, **kwargs):
        if source.suffix.lower() == '.wav':
            content = bytearray(source.read_bytes())
            content[-20] ^= 1
            source.write_bytes(content)
        return snapshot(source, destination, *args, **kwargs)

    monkeypatch.setattr(playback_export, '_snapshot_source', change_before_snapshot)
    monkeypatch.setattr(disklavier_audio_decode, 'decode_disklavier_audio',
                        lambda *args, **kwargs: pytest.fail('Unverified audio reached the decoder'))
    result = backup.run_backup(plan, tmp_path / 'output', audio_output=ENCODED_WAV)
    assert result.status == 'incomplete' and result.decoded == 0
    assert any('checksum changed before playback preparation' in error for error in result.errors)
    assert not _manifest(result)['derivatives']
    assert not list(result.folder.glob('.playback-*'))


def test_cancellation_during_encoded_wav_generation_is_not_a_failure(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import disklavier_audio

    source = _native_source(tmp_path)
    originals = {path: path.read_bytes() for path in source.rglob('*') if path.is_file()}
    encode = disklavier_audio.encode_disklavier_wav
    cancel = threading.Event()

    def cancel_while_encoding(*args, **kwargs):
        def progress(fraction):
            if fraction > 0:
                cancel.set()
        return encode(*args, progress=progress, **kwargs)

    monkeypatch.setattr(disklavier_audio, 'encode_disklavier_wav', cancel_while_encoding)
    result = backup.run_backup(scan_library(source), tmp_path / 'output',
                               audio_output=ENCODED_WAV, cancel=cancel)
    assert result.status == 'cancelled' and result.decoded == 0
    assert result.errors == []
    manifest = _manifest(result)
    preparing = [row['playback_prepare'] for row in manifest['files'] if 'playback_prepare' in row]
    assert [row['status'] for row in preparing] == ['cancelled']
    assert [row['status'] for row in manifest['derivatives']] == ['cancelled']
    assert all(not (result.folder / row['destination']).exists() for row in manifest['derivatives'])
    assert not list(result.folder.glob('.playback-*'))
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_missing_encoder_fails_before_output_creation(tmp_path, monkeypatch):
    source = _native_source(tmp_path)
    monkeypatch.setattr('aps_midi_prep_tool_app.audio_tools.find_lame_command', lambda: None)
    with pytest.raises(ValueError, match='LAME'):
        backup.run_backup(scan_library(source), tmp_path / 'output', audio_output=MIDI_MP3)
    assert not (tmp_path / 'output').exists()


def test_cancellation_after_pair_publication_keeps_verified_derivatives(tmp_path, monkeypatch):
    _require_lame(MIDI_MP3)
    from aps_midi_prep_tool_app import paired_audio
    original_export = paired_audio.export_midi_mp3
    cancel = threading.Event()

    def export(*args, **kwargs):
        result = original_export(*args, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(paired_audio, 'export_midi_mp3', export)
    result = backup.run_backup(scan_library(_native_source(tmp_path)), tmp_path / 'output',
                               audio_output=MIDI_MP3, cancel=cancel)
    assert result.status == 'cancelled' and result.decoded == 1
    assert [row['status'] for row in _manifest(result)['derivatives']] == ['verified', 'verified']
    assert all('not complete' in error for error in backup.verify_backup(result.folder))


def test_unknown_format_is_rejected_without_outputs(tmp_path):
    with pytest.raises(ValueError, match='Choose paired'):
        backup.run_backup(scan_library(_native_source(tmp_path)), tmp_path / 'output', audio_output='guess')
    assert not (tmp_path / 'output').exists()


def test_archived_presentation_audio_is_preserved_without_preparation(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    asset = source / 'songs/pspg/archive/cache/1/Show.wav'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'presentation dependency')
    original = backup.run_backup(scan_library(source), tmp_path / 'archives')
    monkeypatch.setattr('aps_midi_prep_tool_app.audio_tools.find_lame_command',
                        lambda: pytest.fail('Presentation assets do not require an encoder'))
    result = backup.run_backup(scan_library(original.folder), tmp_path / 'output', audio_output=MIDI_MP3)
    assert result.status == 'complete' and result.decoded == 0
    assert not _manifest(result)['derivatives']


def test_legacy_split_option_prepares_existing_backup(tmp_path):
    _require_lame(MIDI_MP3)
    source = _native_source(tmp_path)
    original = backup.run_backup(scan_library(source), tmp_path / 'archives')
    result = backup.run_backup(scan_library(original.folder), tmp_path / 'output', split_audio=True)
    assert result.status == 'complete' and result.decoded == 1, result.errors
    assert backup.verify_backup(result.folder) == []
