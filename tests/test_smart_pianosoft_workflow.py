"""Complete album preparation with original synthetic MIDI/CD music."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
import threading
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import smart_pianosoft_media as media
from aps_midi_prep_tool_app import audio_tools
from aps_midi_prep_tool_app.disklavier_audio import read_midi_events
from aps_midi_prep_tool_app.smart_pianosoft_workflow import AlbumBuildError, prepare_album, scan_album
from test_smart_pianosoft import _psong_bytes, _pdisk_bytes
from test_smart_pianosoft_sync import _fingerprint_message, _wav, original_phrase


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
    result = prepare_album(album, tmp_path, audio_paths={1: audio}, cancel=threading.Event())
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


def test_greaseweazle_snapshot_can_encode_after_controller_disconnected(tmp_path, album_files, monkeypatch):
    from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, GreaseweazleFloppySource

    folder, audio, _pcm = album_files
    source = GreaseweazleFloppySource("/dev/serial/by-id/disconnected-greaseweazle", "A",
                                    DISK_FORMAT_BY_KEY["ibm.720"])
    reads = []

    def copy_source(selected, destination, **_kwargs):
        reads.append(selected)
        return shutil.copytree(folder, destination)

    monkeypatch.setattr(media, "read_floppy_source", copy_source)
    album = _scan(tmp_path, source)
    assert album.title == "Original test album"
    assert album.original_source == Path(source.device_path)
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
    folder = prepare_album(album, tmp_path, audio_paths={1: audio}, output_format="midi_mp3")
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
    with pytest.raises(AlbumBuildError, match="changed after scanning") as error:
        prepare_album(album, tmp_path, audio_paths={1: audio})
    assert not list((error.value.output_directory / "Disklavier").iterdir())


def test_wrong_manual_pairing_retains_originals_but_never_encodes_a_guess(tmp_path, album_files):
    source, audio, pcm = album_files
    _wav(audio, np.random.default_rng(929).integers(-12000, 12000, pcm.shape, dtype=np.int16))
    album = _scan(tmp_path, source)
    with pytest.raises(AlbumBuildError, match="does not match") as error:
        prepare_album(album, tmp_path, audio_paths={1: audio})
    folder = error.value.output_directory
    assert json.loads((folder / "manifest.json").read_text())["status"] == "failed"
    assert not list((folder / "Disklavier").iterdir())
    assert (folder / "MIDI/01.MID").read_bytes() == (source / "01.MID").read_bytes()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_cd_track_order_is_only_a_hint_and_ambiguous_fallback_is_rejected(tmp_path, album_files, monkeypatch, ambiguous):
    source, audio, pcm = album_files
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
