"""Complete album preparation with original synthetic MIDI/CD music."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import struct
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import smart_pianosoft_media as media
from aps_midi_prep_tool_app import audio_tools
from aps_midi_prep_tool_app.disklavier_audio import read_midi_events
from aps_midi_prep_tool_app.smart_pianosoft_workflow import AlbumBuildError, prepare_album, scan_album
from aps_midi_prep_tool_app.smart_pianosoft import (
    parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog,
)
from test_smart_pianosoft import _psong_bytes, _pdisk_bytes
from test_smart_pianosoft_sync import (
    _cd_reference_message, _fingerprint_message, _reference_envelope, _wav, original_phrase,
)


@pytest.fixture
def album_files(tmp_path, original_phrase):
    pcm, metadata = original_phrase
    source = tmp_path / "source"
    source.mkdir()
    midi = mido.MidiFile(type=0, ticks_per_beat=1000)
    midi.tracks.append(mido.MidiTrack([
        _fingerprint_message(0, metadata.head.reference_seconds, metadata.head.samples),
        _fingerprint_message(2, metadata.tail.reference_seconds, metadata.tail.samples),
        mido.MetaMessage("set_tempo", tempo=1000000),
        mido.Message("note_on", note=60, velocity=80, time=2000),
        mido.Message("note_off", note=60, time=1000),
        mido.MetaMessage("end_of_track", time=11000),
    ]))
    midi.save(source / "01.MID")
    (source / "PSONG.MNG").write_bytes(_psong_bytes([("01.MID", "Original test phrase")]))
    (source / "PDISK.MNG").write_bytes(_pdisk_bytes("Original test album"))
    audio = tmp_path / "music.wav"
    _wav(audio, pcm)
    return source, audio, pcm


def _scan(tmp_path, source):
    return scan_album(source, tmp_path / "snapshot", cancel=threading.Event())


def test_complete_album_preserves_sources_and_separates_originals_from_encoded_audio(tmp_path, album_files):
    source, audio, pcm = album_files
    originals = {p.name: p.read_bytes() for p in source.iterdir()}
    audio_before = audio.read_bytes()
    album = _scan(tmp_path, source)
    updates = []
    result = prepare_album(album, tmp_path, audio_paths={1: audio}, cancel=threading.Event(),
                           progress=updates.append)
    assert any(detail["phase"] == "encode" for detail in updates)
    assert not any(detail["phase"] == "export_pairs" for detail in updates)
    assert {p.name for p in result.iterdir()} == {"MIDI", "WAV", "Disklavier", "manifest.json"}
    assert {p.name: p.read_bytes() for p in source.iterdir()} == originals
    assert (result / "MIDI/01.MID").read_bytes() == originals["01.MID"]
    assert (result / "MIDI/PSONG.MNG").read_bytes() == originals["PSONG.MNG"]
    assert (result / "WAV/Paired01.wav").read_bytes() == audio_before == audio.read_bytes()
    report = json.loads((result / "manifest.json").read_text())
    assert report["status"] == "complete"
    assert report["hardware_verified"] is False
    track = report["tracks"][0]
    assert track["alignment"]["confidence"] > .98
    with wave.open(str(result / track["encoded"]), "rb") as encoded:
        assert (encoded.getnchannels(), encoded.getframerate(), encoded.getsampwidth()) == (2, 44100, 2)
        encoded.setpos(round(track["encoding"]["audio_preroll_seconds"] * 44100))
        frames = np.frombuffer(encoded.readframes(1000), dtype="<i2").reshape(-1, 2)
    np.testing.assert_array_equal(frames[:, 0], pcm[:1000, 0])
    assert np.any(frames[:, 1] != frames[:, 0])


@pytest.mark.parametrize("output_format", ["encoded_wav", "midi_mp3"])
def test_clean_album_output_names_preserve_previous_completed_album(tmp_path, album_files, output_format):
    if output_format == "midi_mp3" and not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    first = prepare_album(album, tmp_path, audio_paths={1: audio}, output_format=output_format, random_suffixes=False)
    assert first.name == "Smart PianoSoft - Original test album"
    original = {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second = prepare_album(album, tmp_path, audio_paths={1: audio}, output_format=output_format, random_suffixes=False)
    assert second.name == "Smart PianoSoft - Original test album (2)"
    assert {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()} == original
    assert json.loads((second / "manifest.json").read_text())["status"] == "complete"


@pytest.mark.parametrize("device_path", ["/dev/serial/by-id/disconnected-greaseweazle", "COM42"])
def test_greaseweazle_snapshot_can_encode_after_controller_disconnected(
    tmp_path, album_files, monkeypatch, device_path,
):
    from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, GreaseweazleFloppySource

    folder, audio, _pcm = album_files
    source = GreaseweazleFloppySource(device_path, "A", DISK_FORMAT_BY_KEY["ibm.720"])
    reads = []

    def copy_source(selected, destination, **_kwargs):
        reads.append(selected)
        return shutil.copytree(folder, destination)

    monkeypatch.setattr(media, "read_floppy_source", copy_source)
    album = _scan(tmp_path, source)
    assert album.title == "Original test album"
    # Snapshots store absolute paths; Windows adds a drive to rooted /dev paths.
    assert album.original_source == Path(source.device_path).absolute()
    assert album.source_kind == "floppy_gw"
    result = prepare_album(album, tmp_path, audio_paths={1: audio})
    assert len(list((result / "Disklavier").glob("*.wav"))) == 1
    assert json.loads((result / "manifest.json").read_text())["status"] == "complete"
    assert reads == [source]


def test_paired_album_reuses_fingerprint_alignment_and_delivers_matching_midi_mp3(tmp_path, album_files):
    if not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    source, audio, _pcm = album_files
    original_midi = (source / "01.MID").read_bytes()
    original_audio = audio.read_bytes()
    album = _scan(tmp_path, source)
    updates = []
    folder = prepare_album(album, tmp_path, audio_paths={1: audio}, output_format="midi_mp3",
                           progress=updates.append)
    assert any(detail["phase"] == "export_pairs" for detail in updates)
    assert not any(detail["phase"] == "encode" for detail in updates)
    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    assert report["output_format"] == "midi_mp3"
    assert not (folder / "Disklavier").exists()
    track = report["tracks"][0]
    midi, music = folder / track["prepared_midi"], folder / track["prepared_audio"]
    assert midi.parent == music.parent == folder / "ENSPIRE"
    assert midi.stem == music.stem
    assert midi.suffix == ".mid" and music.suffix == ".mp3"
    assert music.stat().st_size > 0
    assert (folder / "MIDI/01.MID").read_bytes() == original_midi
    assert (folder / "WAV/Paired01.wav").read_bytes() == original_audio
    assert audio.read_bytes() == original_audio
    alignment = track["alignment"]
    events = read_midi_events(midi)
    assert [data for _, data in events] == [event.data for event in album.tracks[0].metadata.events]
    preroll = track["preparation"]["audio_preroll_seconds"]
    np.testing.assert_allclose([seconds for seconds, _ in events],
                               [event.time_seconds * alignment["time_scale"] + alignment["offset_seconds"] + preroll
                                for event in album.tracks[0].metadata.events], atol=0.00005)


@pytest.mark.parametrize("output_format", ["encoded_wav", "midi_mp3"])
def test_paired_mp3_preserves_source_and_synchronizes_decoded_pcm(tmp_path, album_files, output_format, monkeypatch):
    if not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    source, audio, _pcm = album_files
    mp3 = tmp_path / "Accompaniment.MP3"
    audio_tools.encode_mp3(audio, mp3)
    original = mp3.read_bytes()
    decoded = tmp_path / "expected.wav"
    audio_tools.decode_mp3(mp3, decoded)
    album = _scan(tmp_path, source)
    updates = []
    # Enough for this album's actual PCM and playback files, but less than a
    # worst-case low-bitrate multiplier would demand for its ordinary MP3.
    usage_type = type(shutil.disk_usage(tmp_path))
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage_type(200 << 20, 120 << 20, 80 << 20))

    folder = prepare_album(album, tmp_path, audio_paths={1: mp3}, output_format=output_format,
                           progress=updates.append)

    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    assert mp3.read_bytes() == original == (folder / "MP3/Paired01.mp3").read_bytes()
    assert (folder / "WAV/Paired01.wav").read_bytes() == decoded.read_bytes()
    preserved = {entry["file"]: entry for entry in report["audio"]}
    assert preserved["MP3/Paired01.mp3"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert preserved["WAV/Paired01.wav"]["sha256"] == hashlib.sha256(decoded.read_bytes()).hexdigest()
    assert preserved["WAV/Paired01.wav"]["decoded_from"] == "MP3/Paired01.mp3"
    assert any(event["phase"] == "prepare_audio" for event in updates)
    track = report["tracks"][0]
    assert track["audio"] == "WAV/Paired01.wav"
    assert track["alignment"]["confidence"] > .98
    if output_format == "encoded_wav":
        assert (folder / track["encoded"]).is_file()
    else:
        midi, music = folder / track["prepared_midi"], folder / track["prepared_audio"]
        assert midi.is_file() and music.is_file() and midi.stem == music.stem
        assert [event for _, event in read_midi_events(midi)] == [event.data for event in album.tracks[0].metadata.events]


def test_mp3_input_requires_lame_before_creating_encoded_wav_album(tmp_path, album_files, monkeypatch):
    source, _audio, _pcm = album_files
    mp3 = tmp_path / "music.mp3"
    mp3.write_bytes(b"unread compressed source")
    album = _scan(tmp_path, source)
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "")
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_kw: pytest.fail("CD accessed before LAME preflight"))
    with pytest.raises(RuntimeError, match="LAME is required to read or export MP3"):
        prepare_album(album, tmp_path, audio_paths={1: mp3}, output_format="encoded_wav")
    assert set(tmp_path.iterdir()) == before


def test_cancelled_mp3_decode_retains_verified_original_without_partial_pcm(tmp_path, album_files, monkeypatch):
    source, _audio, _pcm = album_files
    mp3 = tmp_path / "music.mp3"
    original = b"synthetic compressed source"
    mp3.write_bytes(original)
    cancelled = threading.Event()
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "synthetic-lame")

    def decode(copied, destination, *, cancel):
        assert copied != mp3 and copied.read_bytes() == original and cancel is cancelled
        destination.write_bytes(b"unfinished PCM")
        cancel.set()
        raise InterruptedError("Audio conversion cancelled.")

    monkeypatch.setattr(audio_tools, "decode_mp3", decode)
    with pytest.raises(AlbumBuildError, match="cancelled") as caught:
        prepare_album(_scan(tmp_path, source), tmp_path, audio_paths={1: mp3}, cancel=cancelled)
    folder = caught.value.output_directory
    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "cancelled" and not report["tracks"]
    assert report["audio"] == [{"paired_song": 1, "file": "MP3/Paired01.mp3",
                                "sha256": hashlib.sha256(original).hexdigest()}]
    assert (folder / "MP3/Paired01.mp3").read_bytes() == mp3.read_bytes() == original
    assert not list((folder / "WAV").iterdir()) and not list((folder / "Disklavier").iterdir())
    assert not list(folder.glob(".paired-audio-*"))


@pytest.mark.parametrize("sample_rate,channels", [(22050, 2), (44100, 1)])
def test_mp3_decoding_does_not_silently_resample_or_upmix(tmp_path, album_files, monkeypatch, sample_rate, channels):
    source, _audio, _pcm = album_files
    mp3 = tmp_path / "music.mp3"
    mp3.write_bytes(b"synthetic compressed source")
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "synthetic-lame")

    def decode(_source, destination, **_kwargs):
        with wave.open(str(destination), "wb") as output:
            output.setparams((channels, 2, sample_rate, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * channels * 100)

    monkeypatch.setattr(audio_tools, "decode_mp3", decode)
    with pytest.raises(AlbumBuildError, match="must decode to stereo, 44.1 kHz") as caught:
        prepare_album(_scan(tmp_path, source), tmp_path, audio_paths={1: mp3})
    folder = caught.value.output_directory
    assert not list((folder / "WAV").iterdir()) and not list((folder / "Disklavier").iterdir())
    assert not list(folder.glob(".paired-audio-*"))


@pytest.mark.parametrize("partial_bytes", [1, 2, 3])
def test_paired_wav_preserves_complete_frames_and_original_trailing_bytes(tmp_path, partial_bytes):
    from aps_midi_prep_tool_app.smart_pianosoft_workflow import _copy_wav

    source, copied = tmp_path / "source.wav", tmp_path / "copied.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00\x02\x00" * 10)
    payload = bytearray(source.read_bytes())
    struct.pack_into("<I", payload, 40, 40 + partial_bytes)
    struct.pack_into("<I", payload, 4, len(payload) - 8 + partial_bytes)
    payload.extend(b"\x7f" * partial_bytes + b"outside RIFF")
    source.write_bytes(payload)

    _copy_wav(source, copied)

    assert copied.read_bytes() == source.read_bytes() == payload
    missing_frame = tmp_path / "missing.wav"
    missing_frame.write_bytes(payload[:44 + 39])
    with pytest.raises(ValueError, match="empty or truncated"):
        _copy_wav(missing_frame, tmp_path / "rejected.wav")
    assert not (tmp_path / "rejected.wav").exists()


@pytest.mark.parametrize("catalog_option", ["generate_yamaha_catalogs", "preserve_yamaha_catalogs"])
def test_compatible_paired_album_writes_current_titles_and_retains_opaque_catalog_fields(tmp_path, album_files,
                                                                                     catalog_option):
    if not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    source, audio, _pcm = album_files
    catalog = bytearray((source / "PSONG.MNG").read_bytes())
    catalog[0x80 + 0x90:0x80 + 0x94] = b"KEEP"
    (source / "PSONG.MNG").write_bytes(catalog)
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    album = _scan(tmp_path, source)
    album = replace(album, title="Updated album", tracks=(replace(album.tracks[0], title="Updated song"),))

    folder = prepare_album(album, tmp_path, audio_paths={1: audio}, output_format="midi_mp3",
                           **{catalog_option: True})

    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    playback = folder / "ENSPIRE"
    assert {path.name for path in playback.iterdir()} == {"01.mid", "01.mp3", "PSONG.MNG", "PDISK.MNG"}
    song, = parse_smart_pianosoft_song_catalog((playback / "PSONG.MNG").read_bytes())
    assert (song.filename, song.title) == ("01.mid", "Updated song")
    assert song.raw_record[0x90:0x94] == b"KEEP"
    assert parse_smart_pianosoft_disk_title((playback / "PDISK.MNG").read_bytes()) == "Updated album"
    assert {item["file"] for item in report["catalogs"]} == {"ENSPIRE/PSONG.MNG", "ENSPIRE/PDISK.MNG"}
    assert all(hashlib.sha256((folder / item["file"]).read_bytes()).hexdigest() == item["sha256"]
               for item in report["catalogs"])
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
    assert (folder / "MIDI/PSONG.MNG").read_bytes() == originals["PSONG.MNG"]


@pytest.mark.parametrize("catalog_source", ["absent", "invalid", "empty_pianodir", "PDISK.MNG", "PSONG.MNG"])
def test_preservation_only_paired_output_requires_valid_source_catalog(tmp_path, album_files, catalog_source):
    if not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    from aps_midi_prep_tool_app.eseq_pianodir import build_pianodir_bytes

    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    for path in album.source_directory.iterdir():
        if path.suffix.casefold() == ".mng":
            path.unlink()
    if catalog_source == "invalid":
        (album.source_directory / "PSONG.MNG").write_bytes(b"not a Yamaha catalog")
    elif catalog_source == "empty_pianodir":
        (album.source_directory / "PIANODIR.FIL").write_bytes(build_pianodir_bytes([], disk_title=""))
    elif catalog_source in {"PDISK.MNG", "PSONG.MNG"}:
        (album.source_directory / catalog_source).write_bytes((source / catalog_source).read_bytes())

    folder = prepare_album(album, tmp_path, audio_paths={1: audio}, output_format="midi_mp3",
                           preserve_yamaha_catalogs=True)

    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "complete"
    playback = folder / "ENSPIRE"
    expected = ({"PSONG.MNG", "PDISK.MNG"} if catalog_source == "empty_pianodir" else
                {catalog_source} if catalog_source in {"PDISK.MNG", "PSONG.MNG"} else set())
    assert {path.name for path in playback.glob("*.MNG")} == expected
    assert {Path(item["file"]).name for item in report.get("catalogs", [])} == expected
    midi, = playback.glob("*.mid")
    if expected:
        assert midi.name == "01.mid"
    else:
        assert "Original test phrase" in midi.name


@pytest.mark.parametrize("failure", ["write", "competing_file"])
def test_paired_catalog_failure_retains_music_and_does_not_report_complete(tmp_path, album_files,
                                                                        monkeypatch, failure):
    if not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    from aps_midi_prep_tool_app.helpers import file_batch

    source, audio, _pcm = album_files
    publish = file_batch.atomic_write_bytes

    def fail_second_catalog(path, *args, **kwargs):
        if Path(path).name == "PDISK.MNG":
            if failure == "competing_file":
                Path(path).write_bytes(b"Another writer's catalog")
            else:
                raise OSError("Catalog write failed")
        return publish(path, *args, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_second_catalog)
    with pytest.raises(AlbumBuildError) as caught:
        prepare_album(_scan(tmp_path, source), tmp_path, audio_paths={1: audio}, output_format="midi_mp3",
                      generate_yamaha_catalogs=True)

    folder = caught.value.output_directory
    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "failed"
    assert (folder / "ENSPIRE/01.mid").is_file()
    assert (folder / "ENSPIRE/01.mp3").is_file()
    assert not (folder / "ENSPIRE/PSONG.MNG").exists()
    if failure == "competing_file":
        assert (folder / "ENSPIRE/PDISK.MNG").read_bytes() == b"Another writer's catalog"
    else:
        assert not (folder / "ENSPIRE/PDISK.MNG").exists()


def test_ordinary_cataloged_midi_is_not_mistaken_for_smart_pianosoft(tmp_path, album_files):
    source, _audio, _pcm = album_files
    midi = mido.MidiFile()
    midi.tracks.append(mido.MidiTrack([mido.Message("note_on", note=60)]))
    midi.save(source / "01.MID")
    with pytest.raises(ValueError, match="fingerprint"):
        _scan(tmp_path, source)


def test_extracted_long_filename_maps_to_original_catalog_order(tmp_path, album_files):
    source, _audio, _pcm = album_files
    original = (source / "01.MID").read_bytes()
    (source / "01.MID").rename(source / "01 - Original test phrase.mid")
    album = _scan(tmp_path, source)
    assert album.tracks[0].filename == "01.MID"
    assert album.tracks[0].midi_path.read_bytes() == original


def test_changed_snapshot_refuses_to_encode(tmp_path, album_files):
    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    album.tracks[0].midi_path.write_bytes(b"changed")
    before = set(tmp_path.iterdir())
    with pytest.raises(AlbumBuildError, match="changed after scanning") as error:
        prepare_album(album, tmp_path, audio_paths={1: audio})
    assert error.value.output_directory is None
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("input_format", ["wav", "mp3"])
def test_wrong_manual_pairing_retains_originals_but_never_encodes_a_guess(tmp_path, album_files, input_format):
    source, audio, pcm = album_files
    _wav(audio, np.random.default_rng(929).integers(-12000, 12000, pcm.shape, dtype=np.int16))
    if input_format == "mp3":
        if not audio_tools.find_lame_command():
            pytest.skip("LAME is unavailable")
        mp3 = tmp_path / "wrong.mp3"
        audio_tools.encode_mp3(audio, mp3)
        audio = mp3
    original_audio = audio.read_bytes()
    album = _scan(tmp_path, source)
    with pytest.raises(AlbumBuildError, match="does not match") as error:
        prepare_album(album, tmp_path, audio_paths={1: audio})
    folder = error.value.output_directory
    assert json.loads((folder / "manifest.json").read_text())["status"] == "failed"
    assert not list((folder / "Disklavier").iterdir())
    assert (folder / "MIDI/01.MID").read_bytes() == (source / "01.MID").read_bytes()
    assert audio.read_bytes() == original_audio
    if input_format == "mp3":
        assert (folder / "MP3/Paired01.mp3").read_bytes() == original_audio


@pytest.mark.parametrize("ambiguous", [False, True])
def test_cd_track_order_is_only_a_hint_and_ambiguous_fallback_is_rejected(tmp_path, album_files, monkeypatch, ambiguous):
    source, audio, pcm = album_files
    # An embedded reference from a different CD edition is also only a hint.
    midi = mido.MidiFile(source / "01.MID")
    midi.tracks[0].insert(0, _cd_reference_message(1))
    midi.save(source / "01.MID")
    wrong = tmp_path / "wrong.wav"
    _wav(wrong, np.random.default_rng(556).integers(-12000, 12000, pcm.shape, dtype=np.int16))
    toc = tuple(media.CDTrack(n, (n - 1) * 1050, 1050) for n in range(1, 4 if ambiguous else 3))
    monkeypatch.setattr(media, "read_cd_toc", lambda *args, **kwargs: toc)
    monkeypatch.setattr(media, "rip_cd_track", lambda device, track, path, **kw:
                        shutil.copyfile(wrong if track.number == 1 else audio, path))
    album = _scan(tmp_path, source)
    if ambiguous:
        with pytest.raises(AlbumBuildError, match="unique matching") as error:
            prepare_album(album, tmp_path, cd_device="fake-cd")
        assert not list((error.value.output_directory / "Disklavier").iterdir())
    else:
        result = prepare_album(album, tmp_path, cd_device="fake-cd")
        report = json.loads((result / "manifest.json").read_text())
        assert report["tracks"][0]["audio"] == "WAV/CD02.wav"
        assert len(report["audio"]) == 2


@pytest.mark.parametrize("output_format", ["encoded_wav", "midi_mp3"])
@pytest.mark.parametrize("cd_reference", [False, True])
def test_missing_middle_cd_song_does_not_shift_later_floppy_pairings(tmp_path, monkeypatch, output_format, cd_reference):
    """Four floppy performances match CD 1, 3, 4, 5 using real fingerprints."""
    if output_format == "midi_mp3" and not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    cd_numbers = (1, 3, 4, 5)
    recordings, catalog = {}, []
    # All recordings have the same duration, and CD audio has no song titles.
    # The only way to distinguish them is their independent audio content.
    times = np.arange(14 * 44100) / 44100
    knots = np.arange(0, 14.02, .02)
    carrier = np.sin(2 * np.pi * 437 * times) + .3 * np.sin(2 * np.pi * 731 * times)
    for cd_number in range(1, 6):
        rng = np.random.default_rng(1247 + cd_number)
        amplitude = np.interp(times, knots, rng.uniform(.05, .8, len(knots)))
        mono = np.asarray(20000 * amplitude * carrier, dtype=np.int16)
        pcm = np.column_stack((mono, mono))
        audio = tmp_path / f"recording-{cd_number}.wav"
        _wav(audio, pcm)
        recordings[cd_number] = audio
        if cd_number not in cd_numbers:
            continue
        number = cd_numbers.index(cd_number) + 1
        envelope = _reference_envelope(pcm)
        track = mido.MidiTrack()
        if cd_reference:
            track.append(_cd_reference_message(cd_number, tuple(150 + n * 1050 for n in range(6))))
        for kind, index in ((0, 345), (2, 1723)):
            segment = envelope[index:index + 256]
            samples = np.rint(segment / np.max(np.abs(segment)) * 60).astype(int)
            track.append(_fingerprint_message(kind, index * 256 / 44100, samples))
        track.extend([
            mido.MetaMessage("set_tempo", tempo=1000000),
            mido.Message("note_on", note=60 + number, velocity=80, time=2000),
            mido.Message("note_off", note=60 + number, time=1000),
            mido.MetaMessage("end_of_track", time=11000),
        ])
        midi = mido.MidiFile(type=0, ticks_per_beat=1000)
        midi.tracks.append(track)
        filename = f"{number:02d}.MID"
        midi.save(source / filename)
        catalog.append((filename, f"Original phrase {number}"))
    (source / "PSONG.MNG").write_bytes(_psong_bytes(catalog))
    (source / "PDISK.MNG").write_bytes(_pdisk_bytes("Missing middle song"))
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    toc = tuple(media.CDTrack(number, (number - 1) * 1050, 1050) for number in range(1, 6))
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: toc)
    monkeypatch.setattr(media, "rip_cd_track", lambda device, track, path, **kw:
                        shutil.copyfile(recordings[track.number], path))

    result = prepare_album(_scan(tmp_path, source), tmp_path, cd_device="fake-cd", output_format=output_format)

    report = json.loads((result / "manifest.json").read_text())
    assert report["status"] == "complete"
    assert [(track["number"], track["audio"]) for track in report["tracks"]] == [
        (number, f"WAV/CD{cd_number:02d}.wav") for number, cd_number in enumerate(cd_numbers, 1)
    ]
    assert len(report["audio"]) == 5  # Unpaired CD track 2 is still preserved.
    for cd_number, audio in recordings.items():
        assert (result / f"WAV/CD{cd_number:02d}.wav").read_bytes() == audio.read_bytes()
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
    for filename, content in originals.items():
        assert (result / "MIDI" / filename).read_bytes() == content
    for number, track in enumerate(report["tracks"], 1):
        assert track["alignment"]["confidence"] > .98
        assert track["alignment"]["offset_seconds"] == pytest.approx(0, abs=.01)
        assert track["alignment"]["time_scale"] == pytest.approx(1, abs=.001)
        if output_format == "encoded_wav":
            with wave.open(str(result / track["encoded"]), "rb") as encoded:
                encoded.setpos(round(track["encoding"]["audio_preroll_seconds"] * 44100))
                frames = np.frombuffer(encoded.readframes(1000), dtype="<i2").reshape(-1, 2)
            with wave.open(str(recordings[cd_numbers[number - 1]]), "rb") as audio:
                expected = np.frombuffer(audio.readframes(1000), dtype="<i2").reshape(-1, 2)
            np.testing.assert_array_equal(frames[:, 0], expected[:, 0])
        else:
            midi, audio = result / track["prepared_midi"], result / track["prepared_audio"]
            assert midi.stem == audio.stem and audio.stat().st_size > 0
            assert [data[1] for _, data in read_midi_events(midi) if data[0] & 0xF0 == 0x90] == [60 + number]
    delivery = result / ("Disklavier" if output_format == "encoded_wav" else "ENSPIRE")
    assert len(list(delivery.iterdir())) == (4 if output_format == "encoded_wav" else 8)


def test_cancellation_never_publishes_partial_encoded_wav(tmp_path, album_files):
    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    cancel = threading.Event()
    def progress(detail):
        if detail["phase"] == "encode" and detail["completed"] > 0:
            cancel.set()
    with pytest.raises(AlbumBuildError) as error:
        prepare_album(album, tmp_path, audio_paths={1: audio}, cancel=cancel, progress=progress)
    folder = error.value.output_directory
    assert json.loads((folder / "manifest.json").read_text())["status"] == "cancelled"
    assert not list((folder / "Disklavier").iterdir())


def test_cd_progress_counts_audio_bytes_across_unequal_nonconsecutive_tracks(
    tmp_path, album_files, monkeypatch,
):
    from aps_midi_prep_tool_app import smart_pianosoft_sync as sync

    source, audio, _pcm = album_files
    toc = (media.CDTrack(1, 0, 75), media.CDTrack(3, 150, 225))
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: toc)

    def rip(device, track, path, *, cancel, progress):
        size = track.sectors * 2352
        for step in (0, size // 2, size):
            progress(step, size, "Reading with secure error correction…")
        return shutil.copyfile(audio, path)

    def match(metadata, path, **kwargs):
        if path.name == "CD01.wav":
            raise sync.SynchronizationError("Not this recording")
        return sync.Alignment(0, 1, .99, .99, .99)

    monkeypatch.setattr(media, "rip_cd_track", rip)
    monkeypatch.setattr(sync, "synchronize", match)
    updates = []
    prepare_album(_scan(tmp_path, source), tmp_path, cd_device="fake-cd", progress=updates.append)
    rip_updates = [detail for detail in updates if detail["phase"] == "rip" and detail["total"]]
    assert {detail["total"] for detail in rip_updates} == {300 * 2352}
    completed = [detail["completed"] for detail in rip_updates]
    assert completed == sorted(completed)
    assert (completed[0], completed[-1]) == (0, 300 * 2352)
    reading = [detail for detail in rip_updates if detail["message"].startswith("Reading")]
    assert [100 * detail["completed"] / detail["total"] for detail in reading] == [
        0, 12.5, 25, 25, 62.5, 100,
    ]
    assert [(detail["cd_track"], detail["track_index"], detail["track_count"]) for detail in reading] == [
        (1, 1, 2), (1, 1, 2), (1, 1, 2), (3, 2, 2), (3, 2, 2), (3, 2, 2),
    ]
    assert [detail["cd_track"] for detail in rip_updates
            if detail["message"] == "Calculating audio checksum…"] == [1, 3]


@pytest.mark.parametrize("failure_stage", ["toc", "second_track", "final_toc"])
@pytest.mark.parametrize("output_format", ["encoded_wav", "midi_mp3"])
def test_cd_read_failure_retains_verified_files_and_restarts_from_reconnected_drive(
        tmp_path, album_files, monkeypatch, failure_stage, output_format):
    if output_format == "midi_mp3" and not audio_tools.find_lame_command():
        pytest.skip("LAME is unavailable")
    source, audio, pcm = album_files
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    album = _scan(tmp_path, source)
    wrong_audio = tmp_path / "other-track.wav"
    _wav(wrong_audio, np.zeros_like(pcm))
    toc = (media.CDTrack(1, 0, 1050), media.CDTrack(2, 1050, 1050))
    failing, toc_calls = True, 0
    ripped = []

    def read_toc(device, **_kwargs):
        nonlocal toc_calls
        assert device == ("old-cd" if failing else "reconnected-cd")
        toc_calls += 1
        if failing and (failure_stage == "toc" or failure_stage == "final_toc" and toc_calls == 2):
            raise media.CDReadError("The CD drive disconnected while reading its track list.")
        return toc

    def rip(device, track, destination, **_kwargs):
        assert device == ("old-cd" if failing else "reconnected-cd")
        assert not destination.exists()
        ripped.append((device, track.number))
        if failing and failure_stage == "second_track" and track.number == 2:
            raise media.CDReadError("The CD drive disconnected while reading track 2.")
        return shutil.copyfile(wrong_audio if track.number == 1 else audio, destination)

    monkeypatch.setattr(media, "read_cd_toc", read_toc)
    monkeypatch.setattr(media, "rip_cd_track", rip)
    with pytest.raises(AlbumBuildError) as caught:
        prepare_album(album, tmp_path, cd_device="old-cd", output_format=output_format,
                      random_suffixes=False)
    assert caught.value.cd_read_error
    failed_folder = caught.value.output_directory
    delivery = "ENSPIRE" if output_format == "midi_mp3" else "Disklavier"
    if failure_stage == "toc":
        assert failed_folder is None
        assert not list(tmp_path.glob("Smart PianoSoft*"))
    else:
        report = json.loads((failed_folder / "manifest.json").read_text())
        assert report["status"] == "failed" and report["failure_kind"] == "cd_read"
        assert report["cd_device"] == "old-cd"
        assert len(report["audio"]) == {"second_track": 1, "final_toc": 2}[failure_stage]
        assert not list((failed_folder / delivery).iterdir())
        retained = {path.relative_to(failed_folder): path.read_bytes()
                    for path in failed_folder.rglob("*") if path.is_file()}

    failing = False
    result = prepare_album(album, tmp_path, cd_device="reconnected-cd", output_format=output_format,
                           random_suffixes=False)
    assert result != failed_folder
    assert result.name == ("Smart PianoSoft - Original test album" +
                           ("" if failure_stage == "toc" else " (2)"))
    assert ripped[-2:] == [("reconnected-cd", 1), ("reconnected-cd", 2)]
    assert json.loads((result / "manifest.json").read_text())["status"] == "complete"
    assert list((result / delivery).iterdir())
    if failed_folder is not None:
        assert {path.relative_to(failed_folder): path.read_bytes()
                for path in failed_folder.rglob("*") if path.is_file()} == retained
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


@pytest.mark.parametrize("cancelled", [False, True])
def test_cd_storage_failure_or_cancellation_is_not_reported_as_drive_failure(
        tmp_path, album_files, monkeypatch, cancelled):
    source, _audio, _pcm = album_files
    cancel = threading.Event()
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_kw: (media.CDTrack(1, 0, 1050),))

    def fail(*_args, **_kwargs):
        if cancelled:
            cancel.set()
            raise media.FloppyOperationCancelled("Operation cancelled.")
        raise OSError("The output disk is full.")

    monkeypatch.setattr(media, "rip_cd_track", fail)
    album = _scan(tmp_path, source)
    before = set(tmp_path.iterdir())
    with pytest.raises(AlbumBuildError) as caught:
        prepare_album(album, tmp_path, cd_device="fake-cd", cancel=cancel)
    assert not caught.value.cd_read_error
    assert caught.value.output_directory is None
    assert set(tmp_path.iterdir()) == before


def test_insufficient_space_fails_before_creating_output_or_ripping(tmp_path, album_files, monkeypatch):
    source, _audio, _pcm = album_files
    album = _scan(tmp_path, source)
    before = set(tmp_path.iterdir())
    toc = (media.CDTrack(1, 0, 1050),)
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_kw: toc)
    usage_type = type(shutil.disk_usage(tmp_path))
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage_type(100, 100, 0))
    monkeypatch.setattr(media, "rip_cd_track", lambda *_a, **_kw: pytest.fail("Ripping must not start"))

    with pytest.raises(AlbumBuildError, match="not enough free space") as caught:
        prepare_album(album, tmp_path, cd_device="fake-cd")

    assert caught.value.output_directory is None
    assert set(tmp_path.iterdir()) == before


def test_failed_first_audio_copy_removes_only_new_output(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app import smart_pianosoft_workflow as workflow

    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    previous = tmp_path / "Smart PianoSoft - Original test album"
    previous.mkdir()
    (previous / "finished.wav").write_bytes(b"previous completed output")
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    before = set(tmp_path.iterdir())

    def fail_copy(_source, destination, **_kwargs):
        destination.write_bytes(b"unfinished audio")
        raise OSError("The output disk is full.")

    monkeypatch.setattr(workflow, "_copy_wav", fail_copy)
    with pytest.raises(AlbumBuildError, match="output disk is full") as caught:
        prepare_album(album, tmp_path, audio_paths={1: audio}, random_suffixes=False)

    assert caught.value.output_directory is None
    assert set(tmp_path.iterdir()) == before
    assert (previous / "finished.wav").read_bytes() == b"previous completed output"
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


def test_cleanup_failure_reports_remaining_folder_for_user_decision(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app import smart_pianosoft_workflow as workflow

    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)

    def fail_copy(*_args, **_kwargs):
        raise OSError("The output disk is full.")

    def fail_cleanup(*_args, **_kwargs):
        raise PermissionError("The output folder could not be removed.")

    monkeypatch.setattr(workflow, "_copy_wav", fail_copy)
    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)
    with pytest.raises(AlbumBuildError, match="output disk is full") as caught:
        prepare_album(album, tmp_path, audio_paths={1: audio})

    folder = caught.value.output_directory
    assert folder.is_dir()
    assert json.loads((folder / "manifest.json").read_text())["status"] == "failed"
    assert (folder / "MIDI/01.MID").read_bytes() == (source / "01.MID").read_bytes()


@pytest.mark.parametrize("cancel_message", ["Audio track saved.", "Calculating audio checksum…"])
def test_cancel_after_first_cd_track_is_saved_keeps_finished_audio(
        tmp_path, album_files, monkeypatch, cancel_message):
    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    cancel = threading.Event()
    monkeypatch.setattr(media, "_linux_required", lambda: None)
    monkeypatch.setattr(media, "_device_identity", lambda _device: ("drive", 1))
    monkeypatch.setattr(media, "_cdparanoia", lambda: "cdparanoia")
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_kw: (media.CDTrack(1, 0, 1050),))

    def reader(args, **_options):
        shutil.copyfile(audio, args[-1])
        return ""

    # Exercise the real reader's validation and publication, including its final
    # callback before it returns the completed destination to the workflow.
    monkeypatch.setattr(media, "_run_command", reader)

    def progress(detail):
        if detail["message"] == cancel_message:
            cancel.set()
            raise InterruptedError("Operation cancelled.")

    with pytest.raises(AlbumBuildError) as caught:
        prepare_album(album, tmp_path, cd_device="fake-cd", cancel=cancel, progress=progress)

    folder = caught.value.output_directory
    assert (folder / "WAV/CD01.wav").read_bytes() == audio.read_bytes()
    assert json.loads((folder / "manifest.json").read_text())["status"] == "cancelled"
    assert not list((folder / "Disklavier").iterdir())


def test_output_cannot_be_inside_original_source_and_pairings_must_be_complete(tmp_path, album_files):
    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    with pytest.raises(ValueError, match="outside"):
        prepare_album(album, source, audio_paths={1: audio})
    with pytest.raises(ValueError, match="every MIDI"):
        prepare_album(album, tmp_path)
    with pytest.raises(ValueError, match="unknown MIDI"):
        prepare_album(album, tmp_path, audio_paths={7: audio})


def test_cancelled_before_start_does_not_create_output(tmp_path, album_files):
    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    cancel = threading.Event()
    cancel.set()
    before = set(tmp_path.iterdir())
    with pytest.raises(InterruptedError):
        prepare_album(album, tmp_path, audio_paths={1: audio}, cancel=cancel)
    assert set(tmp_path.iterdir()) == before


def test_matching_catalog_hint_still_rejects_another_matching_cd_track(tmp_path, album_files, monkeypatch):
    source, audio, _pcm = album_files
    toc = (media.CDTrack(1, 0, 1050), media.CDTrack(2, 1050, 1050))
    monkeypatch.setattr(media, "read_cd_toc", lambda *args, **kwargs: toc)
    monkeypatch.setattr(media, "rip_cd_track", lambda device, track, path, **kwargs:
                        shutil.copyfile(audio, path))
    album = _scan(tmp_path, source)
    with pytest.raises(AlbumBuildError, match="unique matching") as error:
        prepare_album(album, tmp_path, cd_device="fake-cd")
    folder = error.value.output_directory
    assert json.loads((folder / "manifest.json").read_text())["status"] == "failed"
    assert not list((folder / "Disklavier").iterdir())


def test_used_cd_track_does_not_hide_an_ambiguous_later_song(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app import smart_pianosoft_sync as sync

    source, audio, _pcm = album_files
    midi = mido.MidiFile(source / "01.MID")
    for message in midi.tracks[0]:
        if message.type in {"note_on", "note_off"}:
            message.note = 65
    midi.save(source / "02.MID")
    (source / "PSONG.MNG").write_bytes(_psong_bytes([
        ("01.MID", "First phrase"), ("02.MID", "Second phrase"),
    ]))
    toc = (media.CDTrack(1, 0, 1050), media.CDTrack(2, 1050, 1050))
    monkeypatch.setattr(media, "read_cd_toc", lambda *args, **kwargs: toc)
    monkeypatch.setattr(media, "rip_cd_track", lambda device, track, path, **kwargs:
                        shutil.copyfile(audio, path))
    calls = []

    def match(metadata, path, **kwargs):
        note = next(event.data[1] for event in metadata.events if event.data[0] & 0xF0 == 0x90)
        calls.append((note, Path(path).name))
        # The first MIDI uniquely matches CD01. The second matches both CD01
        # and its catalog-number hint, CD02. Using CD01 cannot erase ambiguity.
        if note == 60 and Path(path).name == "CD02.wav":
            raise sync.SynchronizationError("Not this recording")
        return sync.Alignment(0, 1, 1, 1, 1)

    monkeypatch.setattr(sync, "synchronize", match)
    with pytest.raises(AlbumBuildError, match="unique matching") as error:
        prepare_album(_scan(tmp_path, source), tmp_path, cd_device="fake-cd")
    assert (65, "CD01.wav") in calls
    assert (65, "CD02.wav") in calls
    assert not list((error.value.output_directory / "Disklavier").iterdir())


def test_output_on_the_source_block_devices_filesystem_is_rejected_before_writing(tmp_path, album_files, monkeypatch):
    import stat
    from types import SimpleNamespace

    source, audio, _pcm = album_files
    album = _scan(tmp_path, source)
    device = tmp_path / "fake-floppy-device"
    device.write_bytes(b"Never write this source")
    album = replace(album, original_source=device)
    original_stat = Path.stat
    output_filesystem = tmp_path.stat().st_dev

    def device_stat(path, *args, **kwargs):
        if path == device:
            return SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=output_filesystem)
        return original_stat(path, *args, **kwargs)

    before = set(tmp_path.iterdir())
    monkeypatch.setattr(Path, "stat", device_stat)
    with pytest.raises(ValueError, match="different device"):
        prepare_album(album, tmp_path, audio_paths={1: audio})
    assert set(tmp_path.iterdir()) == before
    assert device.read_bytes() == b"Never write this source"


def test_audio_changed_during_encoding_fails_and_reports_retained_untrusted_copy(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app import disklavier_audio

    source, audio, pcm = album_files
    original_audio = audio.read_bytes()
    encode = disklavier_audio.encode_disklavier_wav

    def changed_input(events, paired_audio, destination, **kwargs):
        # Simulate an outside edit after workflow verification but before the
        # encoder reads its input. Canonical WAV shape and length stay valid.
        _wav(paired_audio, np.zeros_like(pcm))
        return encode(events, paired_audio, destination, **kwargs)

    monkeypatch.setattr(disklavier_audio, "encode_disklavier_wav", changed_input)
    with pytest.raises(AlbumBuildError, match="changed during encoding") as error:
        prepare_album(_scan(tmp_path, source), tmp_path, audio_paths={1: audio})
    folder = error.value.output_directory
    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "failed"
    track = report["tracks"][0]
    assert track["output_status"] == "unverified"
    assert len(track["retained_outputs"]) == 1
    assert (folder / track["retained_outputs"][0]).exists()
    assert "encoded_sha256" not in track
    assert audio.read_bytes() == original_audio


def test_paired_source_failure_preserves_competing_files_at_published_paths(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app import paired_audio

    source, audio, pcm = album_files
    original_audio = audio.read_bytes()
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "test-lame")

    def changed_input(_events, paired_source, stem, **_kwargs):
        midi_path = Path(str(stem) + ".mid")
        music_path = Path(str(stem) + ".mp3")
        midi_path.write_bytes(b"prepared MIDI")
        music_path.write_bytes(b"prepared MP3")
        # Another writer replaces one public path and edits the other after
        # publication. Neither may be deleted when the source check fails.
        replacement = tmp_path / "replacement.mid"
        replacement.write_bytes(b"other writer MIDI")
        replacement.replace(midi_path)
        music_path.write_bytes(b"other writer music")
        _wav(paired_source, np.zeros_like(pcm))
        return {"midi": midi_path.name, "audio": music_path.name}

    monkeypatch.setattr(paired_audio, "export_midi_mp3", changed_input)
    with pytest.raises(AlbumBuildError, match="retained and are unverified") as error:
        prepare_album(_scan(tmp_path, source), tmp_path, audio_paths={1: audio}, output_format="midi_mp3")
    folder = error.value.output_directory
    report = json.loads((folder / "manifest.json").read_text())
    assert report["status"] == "failed"
    track = report["tracks"][0]
    assert track["output_status"] == "unverified"
    assert len(track["retained_outputs"]) == 2
    assert (folder / track["retained_outputs"][0]).read_bytes() == b"other writer MIDI"
    assert (folder / track["retained_outputs"][1]).read_bytes() == b"other writer music"
    assert "prepared_midi_sha256" not in track
    assert "prepared_audio_sha256" not in track
    assert audio.read_bytes() == original_audio


def test_missing_lame_fails_before_creating_album_or_reading_cd(tmp_path, album_files, monkeypatch):
    source, _audio, _pcm = album_files
    album = _scan(tmp_path, source)
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "")
    monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_kw: pytest.fail("CD accessed before LAME preflight"))
    monkeypatch.setattr(media, "rip_cd_track", lambda *_a, **_kw: pytest.fail("CD ripped before LAME preflight"))
    with pytest.raises(RuntimeError, match="LAME is required.*before ripping"):
        prepare_album(album, tmp_path, cd_device="test-cd", output_format="midi_mp3")
    assert set(tmp_path.iterdir()) == before


def test_audio_changed_after_synchronization_is_not_encoded(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app import smart_pianosoft_sync as sync

    source, audio, pcm = album_files
    synchronize = sync.synchronize

    def changed_input(metadata, paired_audio, **kwargs):
        result = synchronize(metadata, paired_audio, **kwargs)
        _wav(paired_audio, np.zeros_like(pcm))
        return result

    monkeypatch.setattr(sync, "synchronize", changed_input)
    with pytest.raises(AlbumBuildError, match="changed") as error:
        prepare_album(_scan(tmp_path, source), tmp_path, audio_paths={1: audio})
    folder = error.value.output_directory
    assert json.loads((folder / "manifest.json").read_text())["status"] == "failed"
    assert not list((folder / "Disklavier").iterdir())
