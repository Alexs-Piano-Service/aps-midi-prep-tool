"""Current-list snapshots retain catalog identities and staged MIDI bytes."""

from dataclasses import FrozenInstanceError, replace
import io
import json
import os
from pathlib import Path
import stat
import threading
from types import SimpleNamespace

import mido
import pytest

from aps_midi_prep_tool_app.smart_pianosoft import parse_smart_pianosoft_song_catalog
from aps_midi_prep_tool_app.smart_pianosoft_workflow import (
    ListedAlbumTrack, LoadedAlbumSource, prepare_album, scan_loaded_album,
    _guard_output_source,
)
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes
from test_smart_pianosoft_sync import _fingerprint_message, _wav, original_phrase


def _midi(note=60, *, fingerprints=True, tail=True, metadata=None):
    track = mido.MidiTrack()
    if fingerprints:
        track.append(_fingerprint_message(0, metadata.head.reference_seconds if metadata else 2,
                                          metadata.head.samples if metadata else None))
        if tail:
            track.append(_fingerprint_message(2, metadata.tail.reference_seconds if metadata else 10,
                                              metadata.tail.samples if metadata else None))
    track.extend([
        mido.MetaMessage("set_tempo", tempo=1000000),
        mido.MetaMessage("track_name", name="Staged MIDI title"),
        mido.Message("note_on", note=note, velocity=89, time=2000),
        mido.Message("note_off", note=note, time=1000),
        mido.MetaMessage("end_of_track", time=11000),
    ])
    midi = mido.MidiFile(type=0, ticks_per_beat=1000)
    midi.tracks.append(track)
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


@pytest.fixture
def loaded_source(tmp_path):
    records = [(f"{number:02d}.MID", f"Original {number}") for number in (1, 2, 3)]
    return LoadedAlbumSource(
        "Current list", tmp_path / "source-removed.img",
        _psong_bytes(records), _pdisk_bytes("Original album"),
        tuple(ListedAlbumTrack(name, name, title, _midi(59 + number))
              for number, (name, title) in enumerate(records, 1)),
    )


def test_subset_keeps_track_two_identity_staged_bytes_and_current_display(tmp_path, loaded_source):
    item = replace(loaded_source.tracks[1], filename="02 - Edited name.mid", title="Edited title", midi_bytes=_midi(77))
    source = replace(loaded_source, tracks=(item,))
    album = scan_loaded_album(source, tmp_path / "snapshot")
    track, = album.tracks
    assert track.number == 2
    assert track.filename == "02.MID"
    assert track.display_filename == "02 - Edited name.mid"
    assert track.title == "Edited title"
    assert track.midi_path.read_bytes() == item.midi_bytes
    assert track.metadata.events[0].data == bytes((0x90, 77, 89))
    copied = (album.source_directory / "PSONG.MNG").read_bytes()
    assert copied[:0x80] == source.song_catalog[:0x80]  # FILE003 remains the slot count.
    songs = parse_smart_pianosoft_song_catalog(copied)
    assert [(song.track_number, song.filename, song.title) for song in songs] == [(2, "02.MID", "Edited title")]
    assert source.song_catalog == loaded_source.song_catalog
    assert not source.original_source.exists()
    assert album.title == "Original album"
    assert (album.source_directory / "PDISK.MNG").read_bytes() == source.disk_catalog


def test_reordered_rows_keep_original_catalog_slots_and_identity(tmp_path, loaded_source):
    source = replace(loaded_source, tracks=(loaded_source.tracks[2], loaded_source.tracks[0]), disk_catalog=b"")
    album = scan_loaded_album(source, tmp_path / "snapshot")
    assert [track.number for track in album.tracks] == [3, 1]
    assert [track.filename for track in album.tracks] == ["03.MID", "01.MID"]
    assert album.title == source.label
    assert not (album.source_directory / "PDISK.MNG").exists()
    catalog = parse_smart_pianosoft_song_catalog((album.source_directory / "PSONG.MNG").read_bytes())
    assert [song.track_number for song in catalog] == [1, 3]


@pytest.mark.parametrize("line_ending", ["crlf", "lf"])
def test_unchanged_catalogs_are_preserved_byte_for_byte_without_original_reads(tmp_path, loaded_source, monkeypatch, line_ending):
    # Opaque trailing bytes and legacy LF copies must survive unchanged.
    songs = loaded_source.song_catalog + b"opaque-trailer\x00\xff"
    disk = loaded_source.disk_catalog + b"opaque-disk-trailer"
    if line_ending == "lf":
        songs, disk = songs.replace(b"\r\n", b"\n"), disk.replace(b"\r\n", b"\n")
    source = replace(loaded_source, song_catalog=songs, disk_catalog=disk)
    real_open, real_stat = Path.open, Path.stat

    def deny_original_open(path, *args, **kwargs):
        assert path != source.original_source, "Original media must not be reopened"
        return real_open(path, *args, **kwargs)

    def deny_original_stat(path, *args, **kwargs):
        assert path != source.original_source, "Original media must not be queried"
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_original_open)
    monkeypatch.setattr(Path, "stat", deny_original_stat)
    album = scan_loaded_album(source, tmp_path / "snapshot")
    assert (album.source_directory / "PSONG.MNG").read_bytes() == songs
    assert (album.source_directory / "PDISK.MNG").read_bytes() == disk
    assert [track.midi_path.read_bytes() for track in album.tracks] == [item.midi_bytes for item in source.tracks]


@pytest.mark.parametrize("problem,match", [
    ("ordinary", "fingerprints"), ("missing_tail", "fingerprints"),
    ("uncataloged", "No PSONG"), ("missing_identity", "No PSONG"),
    ("duplicate_identity", "Duplicate"), ("duplicate_display", "Ambiguous current-list"),
    ("ambiguous_catalog", "Ambiguous MIDI identity"), ("missing_catalog", "PSONG"),
    ("empty", "no Smart PianoSoft"), ("adapter_error", "mixed folders"),
    ("unsafe_catalog", "Unsupported"),
])
def test_invalid_or_mixed_current_list_never_leaves_a_snapshot(tmp_path, loaded_source, problem, match):
    source = loaded_source
    tracks = list(source.tracks)
    if problem == "ordinary":
        tracks[1] = replace(tracks[1], midi_bytes=_midi(fingerprints=False))
    elif problem == "missing_tail":
        tracks[1] = replace(tracks[1], midi_bytes=_midi(tail=False))
    elif problem == "uncataloged":
        tracks[1] = replace(tracks[1], catalog_filename="OTHER.MID")
    elif problem == "missing_identity":
        tracks[1] = replace(tracks[1], catalog_filename="")
    elif problem == "duplicate_identity":
        tracks[1] = replace(tracks[1], catalog_filename="01.mid")
    elif problem == "duplicate_display":
        tracks[1] = replace(tracks[1], filename="01.mid")
    elif problem == "ambiguous_catalog":
        source = replace(source, song_catalog=_psong_bytes([("01.MID", "One"), ("01.MID", "Duplicate")]))
    elif problem == "missing_catalog":
        source = replace(source, song_catalog=b"")
    elif problem == "empty":
        tracks = []
    elif problem == "adapter_error":
        source = replace(source, error="The list contains mixed folders")
    elif problem == "unsafe_catalog":
        source = replace(source, song_catalog=_psong_bytes([("CON.MID", "Device")]))
    source = replace(source, tracks=tuple(tracks))
    destination = tmp_path / "snapshot"
    with pytest.raises(ValueError, match=match):
        scan_loaded_album(source, destination)
    assert not destination.exists()


def test_cancelled_scan_removes_only_its_own_snapshot(tmp_path, loaded_source):
    cancelled = threading.Event()
    destination = tmp_path / "snapshot"
    with pytest.raises(InterruptedError):
        scan_loaded_album(loaded_source, destination, cancel=cancelled,
                          progress=lambda _detail: cancelled.set())
    assert not destination.exists()
    destination.mkdir()
    marker = destination / "existing.txt"
    marker.write_bytes(b"retain")
    with pytest.raises(FileExistsError):
        scan_loaded_album(loaded_source, destination)
    assert marker.read_bytes() == b"retain"


def test_immutable_source_contract(loaded_source):
    with pytest.raises(FrozenInstanceError):
        loaded_source.label = "changed"
    with pytest.raises(FrozenInstanceError):
        loaded_source.tracks[0].midi_bytes = b"changed"


def test_loaded_snapshot_carries_cached_device_identity_without_querying_source(tmp_path, loaded_source, monkeypatch):
    source = replace(loaded_source, source_device=12345)
    real_stat = Path.stat

    def no_source_stat(path, *args, **kwargs):
        assert path != source.original_source
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", no_source_stat)
    album = scan_loaded_album(source, tmp_path / "snapshot")
    assert album.source_device == 12345


@pytest.mark.skipif(os.name == "nt", reason="POSIX cached block identity and sysfs partition topology")
@pytest.mark.parametrize("output_kind", ["unrelated", "same_device", "partition"])
def test_unplugged_cached_source_still_guards_its_device_and_partitions(tmp_path, monkeypatch, output_kind):
    source = Path("/dev/aps-missing-test-floppy")
    source_device = os.makedev(240, 16)
    output_device = source_device if output_kind == "same_device" else os.makedev(240, 17)
    source_sys = Path("/sys/dev/block/240:16")
    output_sys = Path("/sys/dev/block/240:17")
    real_stat, real_exists, real_resolve = Path.stat, Path.exists, Path.resolve

    def fake_stat(path, *args, **kwargs):
        if path == source:
            raise FileNotFoundError("Source was unplugged")
        if path == tmp_path:
            return SimpleNamespace(st_dev=output_device)
        return real_stat(path, *args, **kwargs)

    def fake_exists(path):
        if path in (source_sys, output_sys):
            return output_kind == "partition"
        return real_exists(path)

    def fake_resolve(path, *args, **kwargs):
        if path == source_sys:
            return Path("/sys/devices/mock/block/sdx")
        if path == output_sys:
            return Path("/sys/devices/mock/block/sdx/sdx1")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    if output_kind == "unrelated":
        _guard_output_source(source, tmp_path, source_device=source_device)
    else:
        with pytest.raises(ValueError, match="different device"):
            _guard_output_source(source, tmp_path, source_device=source_device)
    with pytest.raises(ValueError, match="unavailable"):
        _guard_output_source(source, tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX block-device identity")
def test_reused_source_path_does_not_discard_cached_original_device_guard(tmp_path, monkeypatch):
    source = Path("/dev/aps-reused-test-floppy")
    cached_device, replacement_device = os.makedev(240, 16), os.makedev(241, 16)
    real_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        if path == source:
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=replacement_device)
        if path == tmp_path:
            return SimpleNamespace(st_dev=cached_device)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    with pytest.raises(ValueError, match="different device"):
        _guard_output_source(source, tmp_path, source_device=cached_device)


def test_subset_build_pairs_track_two_and_preserves_staged_midi(tmp_path, loaded_source, original_phrase):
    pcm, metadata = original_phrase
    item = replace(loaded_source.tracks[1], filename="Changed filename.mid", title="Changed title",
                   midi_bytes=_midi(72, metadata=metadata))
    source = replace(loaded_source, tracks=(item,))
    album = scan_loaded_album(source, tmp_path / "snapshot")
    wav = tmp_path / "matching.wav"
    _wav(wav, pcm)
    result = prepare_album(album, tmp_path, audio_paths={2: wav})
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["tracks"][0]["number"] == 2
    assert manifest["tracks"][0]["audio"] == "WAV/Paired02.wav"
    assert manifest["tracks"][0]["midi"] == "MIDI/02.MID"
    assert (result / "MIDI/02.MID").read_bytes() == item.midi_bytes
    assert parse_smart_pianosoft_song_catalog((result / "MIDI/PSONG.MNG").read_bytes())[0].track_number == 2
