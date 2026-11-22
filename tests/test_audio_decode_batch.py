"""Standalone batch decoding preserves sources and complete ENSPIRE pairs."""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import wave

import mido
import numpy as np
import pytest

from aps_midi_prep_tool_app import audio_decode_batch as batch
from aps_midi_prep_tool_app import audio_tools, disklavier_audio_decode as decoder, paired_audio
from aps_midi_prep_tool_app.disklavier_audio import SAMPLE_RATE, encode_disklavier_wav, read_midi_events
from aps_midi_prep_tool_app.helpers.file_batch import FileBatchWriteError


def _files(root, contents):
    for name, payload in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def _report(result):
    return json.loads((result.output_directory / batch.REPORT_NAME).read_text())


@pytest.fixture
def fake_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "fake-lame")

    def decode(source, output, *, cancel, progress):
        calls.append(source)
        payload = source.read_bytes()
        if payload == b"ordinary":
            raise decoder.NotEncodedAudioError("No reliable control channel")
        if payload == b"damaged":
            raise decoder.DisklavierDecodeError("Damaged control stream")
        if payload == b"unreadable":
            raise PermissionError("Audio file is unreadable")
        progress(0)
        Path(output).write_bytes(b"clean PCM")
        progress(1)
        return SimpleNamespace(events=((0.5, b"\x90\x3c\x40"), (1.5, b"\x80\x3c\x00")))

    def export(events, music, stem, *, cancel, progress):
        assert Path(music).read_bytes() == b"clean PCM"
        progress(0)
        Path(str(stem) + ".mid").write_bytes(b"paired MIDI")
        Path(str(stem) + ".mp3").write_bytes(b"paired MP3")
        progress(1)

    monkeypatch.setattr(decoder, "decode_disklavier_audio", decode)
    monkeypatch.setattr(paired_audio, "export_midi_mp3", export)
    return calls


def test_recursive_sources_are_deduplicated_and_keep_hierarchy_and_originals(tmp_path, fake_tools):
    source = _files(tmp_path / "Library", {
        "Album/Song.WAV": b"encoded", "Album/Song.MP3": b"encoded",
        "Album/Song.mid": b"existing MIDI", "Album/Song.FIL": b"existing E-SEQ",
        "Album/Nested/Encore.wav": b"encoded", "Readme.txt": b"readme",
    })
    loose = _files(tmp_path / "other", {"Loose.mp3": b"encoded"}) / "Loose.mp3"
    originals = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
    progress = []
    result = batch.run_audio_decode_batch(
        [source / "Album/Song.WAV", source / "Album", source, loose, source, loose],
        source / "Decoded", random_suffixes=False, progress=progress.append)
    assert (result.converted, result.skipped, result.failed, result.cancelled) == (4, 0, 0, False)
    assert len(fake_tools) == len(set(fake_tools)) == 4
    relative = {record.midi_path.relative_to(result.output_directory).as_posix() for record in result.records}
    assert relative == {"Library/Album/Song.mid", "Library/Album/Song (2).mid",
                        "Library/Album/Nested/Encore.mid", "Loose.mid"}
    assert all(record.midi_path.with_suffix(".mp3") == record.mp3_path for record in result.records)
    assert all(path.read_bytes() == payload for path, payload in originals.items())
    assert loose.read_bytes() == b"encoded"
    assert _report(result)["converted"] == 4
    assert all(event["phase"] == "scanning" for event in progress[:1])
    assert progress[-1]["completed"] == progress[-1]["total"] == 4
    assert [event["fraction"] for event in progress] == sorted(event["fraction"] for event in progress)
    assert not list(result.output_directory.glob(".decode-*"))


def test_same_stems_and_root_names_are_safe_on_case_insensitive_disks(tmp_path, fake_tools):
    left = _files(tmp_path / "left/Album", {"Song.wav": b"one", "song.MP3": b"two",
                                            "Song.mid/Track.WAV": b"three"})
    right = _files(tmp_path / "right/album", {"Song.wav": b"four"})
    result = batch.run_audio_decode_batch([left, right], tmp_path / "output")
    paths = [str(path.relative_to(result.output_directory)).casefold()
             for record in result.records for path in (record.midi_path, record.mp3_path)]
    assert len(paths) == len(set(paths)) == 8
    assert result.converted == 4
    assert all(record.midi_path.is_file() and record.mp3_path.is_file() for record in result.records)
    roots = {record.midi_path.relative_to(result.output_directory).parts[0] for record in result.records}
    assert roots == {"Album", "album (2)"}


def test_random_suffix_setting_uses_numbered_output_folder_collisions(tmp_path, fake_tools, monkeypatch):
    monkeypatch.setattr(batch, "datetime", SimpleNamespace(now=lambda: datetime(2026, 11, 21, 13, 14, 15)))
    song = _files(tmp_path / "source", {"Song.wav": b"encoded"}) / "Song.wav"
    first = batch.run_audio_decode_batch([song], tmp_path / "output", random_suffixes=False)
    second = batch.run_audio_decode_batch([song], tmp_path / "output", random_suffixes=False)
    assert first.output_directory.name == "ENSPIRE-MIDI-MP3-20261121-131415"
    assert second.output_directory.name == first.output_directory.name + " (2)"
    assert first.records[0].midi_path.read_bytes() == b"paired MIDI"


def test_ordinary_damaged_and_unreadable_files_do_not_stop_later_pairs(tmp_path, fake_tools):
    source = _files(tmp_path / "source", {"A.wav": b"ordinary", "B.mp3": b"damaged",
                                           "C.wav": b"unreadable", "D.wav": b"encoded"})
    result = batch.run_audio_decode_batch([source], tmp_path / "output")
    assert (result.converted, result.skipped, result.failed) == (1, 1, 2)
    assert [record.status for record in result.records] == ["skipped", "failed", "failed", "converted"]
    assert [record["error"] for record in _report(result)["files"]][:3] == [
        "No reliable control channel", "Damaged control stream", "Audio file is unreadable"]
    assert not list(result.output_directory.glob(".decode-*"))


def test_missing_lame_fails_before_creating_any_output(tmp_path, monkeypatch):
    song = _files(tmp_path / "source", {"Song.wav": b"encoded"}) / "Song.wav"
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: None)
    with pytest.raises(ValueError, match="LAME.*before starting"):
        batch.run_audio_decode_batch([song], tmp_path / "new/output")
    assert not (tmp_path / "new").exists()


def test_empty_audio_tree_needs_neither_lame_nor_an_output_directory(tmp_path, monkeypatch):
    source = _files(tmp_path / "source", {"Song.mid": b"MIDI"})
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: pytest.fail("No audio needs LAME"))
    result = batch.run_audio_decode_batch([source], tmp_path / "output")
    assert result.output_directory is None
    assert result.records == ()
    assert not (tmp_path / "output").exists()


def test_symlink_files_directories_and_explicit_children_are_not_followed(tmp_path, fake_tools):
    source = _files(tmp_path / "source", {"Song.wav": b"encoded"})
    external = _files(tmp_path / "external", {"Other.wav": b"encoded"})
    try:
        (source / "linked").symlink_to(external, target_is_directory=True)
        (source / "link.wav").symlink_to(external / "Other.wav")
    except (OSError, NotImplementedError):
        pytest.skip("Creating symbolic links is not available")
    result = batch.run_audio_decode_batch([source, source / "linked/Other.wav"], tmp_path / "output")
    assert fake_tools == [source / "Song.wav"]
    assert result.converted == 1
    assert result.skipped == 2
    assert external.joinpath("Other.wav").read_bytes() == b"encoded"


@pytest.mark.parametrize("when", ["before", "scanning", "decoding", "exporting"])
def test_cancellation_keeps_only_completed_pairs_and_reports_it(tmp_path, fake_tools, monkeypatch, when):
    source = _files(tmp_path / "source", {"A.wav": b"one", "B.wav": b"two"})
    cancel = threading.Event()
    if when == "before":
        cancel.set()
    original_decode = decoder.decode_disklavier_audio
    original_export = paired_audio.export_midi_mp3

    def decode(path, *args, **kwargs):
        if when == "decoding" and path.name == "B.wav":
            cancel.set()
            raise decoder.DisklavierDecodingCancelled("Cancelled")
        return original_decode(path, *args, **kwargs)

    exports = 0

    def export(*args, **kwargs):
        nonlocal exports
        exports += 1
        if when == "exporting" and exports == 2:
            cancel.set()
            raise InterruptedError("Cancelled")
        return original_export(*args, **kwargs)

    def progress(event):
        if when == "scanning":
            cancel.set()

    monkeypatch.setattr(decoder, "decode_disklavier_audio", decode)
    monkeypatch.setattr(paired_audio, "export_midi_mp3", export)
    result = batch.run_audio_decode_batch([source], tmp_path / "output", cancel=cancel, progress=progress)
    assert result.cancelled
    if when in {"before", "scanning"}:
        assert result.output_directory is None
        assert not (tmp_path / "output").exists()
    else:
        assert (result.converted, result.failed) == (1, 0)
        assert result.records[0].midi_path.is_file() and result.records[0].mp3_path.is_file()
        assert _report(result)["cancelled"]
        assert not list(result.output_directory.glob(".decode-*"))


@pytest.mark.parametrize("cancelled", [False, True])
def test_publication_recovery_locations_are_kept_in_result_and_report(tmp_path, fake_tools, monkeypatch, cancelled):
    source = _files(tmp_path / "source", {"A.wav": b"one", "B.wav": b"two"})
    original = paired_audio.export_midi_mp3

    def export(events, music, stem, **kwargs):
        if stem.name == "A":
            recovery = stem.parent / "recovery"
            recovery.mkdir()
            (recovery / "retained.mid").write_bytes(b"preserve recovery")
            raise FileBatchWriteError("Pair publication stopped", recovery_directory=str(recovery),
                                      retained_paths=(str(recovery / "retained.mid"),),
                                      original_exception=InterruptedError() if cancelled else OSError())
        return original(events, music, stem, **kwargs)

    monkeypatch.setattr(paired_audio, "export_midi_mp3", export)
    result = batch.run_audio_decode_batch([source], tmp_path / "output")
    assert result.cancelled is cancelled
    assert result.failed == 1
    assert result.converted == (0 if cancelled else 1)
    error = result.records[0].error
    assert "Prepared recovery files:" in error and "retained.mid" in error
    assert _report(result)["files"][0]["error"] == error
    assert next(result.output_directory.rglob("retained.mid")).read_bytes() == b"preserve recovery"


def test_changed_source_is_not_exported(tmp_path, fake_tools, monkeypatch):
    source = _files(tmp_path / "source", {"Song.wav": b"one"}) / "Song.wav"
    original = decoder.decode_disklavier_audio

    def decode(path, *args, **kwargs):
        decoded = original(path, *args, **kwargs)
        path.write_bytes(b"concurrently changed")
        return decoded

    monkeypatch.setattr(decoder, "decode_disklavier_audio", decode)
    result = batch.run_audio_decode_batch([source], tmp_path / "output")
    assert result.failed == 1 and result.converted == 0
    assert "changed during decoding" in result.records[0].error
    assert not list(result.output_directory.rglob("*.mid"))
    assert source.read_bytes() == b"concurrently changed"


def test_blank_destination_is_rejected(tmp_path, fake_tools):
    source = _files(tmp_path / "source", {"Song.wav": b"encoded"}) / "Song.wav"
    with pytest.raises(ValueError, match="Choose an output directory"):
        batch.run_audio_decode_batch([source], " ")
    assert fake_tools == []


def test_report_failure_identifies_folder_with_completed_pairs(tmp_path, fake_tools, monkeypatch):
    source = _files(tmp_path / "source", {"Song.wav": b"encoded"}) / "Song.wav"
    original = batch.atomic_write_bytes
    writes = 0

    def write(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("Disk is full")
        return original(*args, **kwargs)

    monkeypatch.setattr(batch, "atomic_write_bytes", write)
    with pytest.raises(ValueError, match="Completed pairs are kept in that output folder") as failure:
        batch.run_audio_decode_batch([source], tmp_path / "output")
    folder = next((tmp_path / "output").iterdir())
    assert str(folder) in str(failure.value)
    assert (folder / "Song.mid").read_bytes() == b"paired MIDI"
    assert (folder / "Song.mp3").read_bytes() == b"paired MP3"


def test_report_failure_preserves_external_publication_recovery_locations(tmp_path, fake_tools, monkeypatch):
    source = _files(tmp_path / "source", {"Song.wav": b"encoded"}) / "Song.wav"
    recovery = _files(tmp_path / "external-recovery", {"retained.mid": b"preserve recovery"})
    original = batch.atomic_write_bytes
    writes = 0

    def export(*args, **kwargs):
        raise FileBatchWriteError("Pair publication stopped", recovery_directory=str(recovery),
                                  retained_paths=(str(recovery / "retained.mid"),),
                                  original_exception=OSError("Publication failed"))

    def write(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("Disk is full")
        return original(*args, **kwargs)

    monkeypatch.setattr(paired_audio, "export_midi_mp3", export)
    monkeypatch.setattr(batch, "atomic_write_bytes", write)
    with pytest.raises(ValueError) as failure:
        batch.run_audio_decode_batch([source], tmp_path / "output")
    folder = next((tmp_path / "output").iterdir())
    message = str(failure.value)
    assert str(folder) in message
    assert "Pair publication stopped" in message
    assert str(recovery) in message and str(recovery / "retained.mid") in message
    assert (recovery / "retained.mid").read_bytes() == b"preserve recovery"


def _pcm(path):
    with wave.open(str(path), "rb") as stream:
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").reshape(-1, stream.getnchannels()).copy()


@pytest.mark.parametrize("extension", ["WAV", "MP3"])
def test_real_encoded_audio_exports_synchronized_pairs_and_preserves_companions(tmp_path, extension):
    command = audio_tools.find_lame_command()
    if not command:
        if os.environ.get("APS_REQUIRE_AUDIO_TESTS") == "1":
            pytest.fail("LAME is required for batch decoding integration tests")
        pytest.skip("LAME is not available")
    music = tmp_path / "music.wav"
    frames = np.arange(3 * SAMPLE_RATE)
    samples = np.column_stack((8000 * np.sin(2 * np.pi * 440 * frames / SAMPLE_RATE),
                               6000 * np.sin(2 * np.pi * 880 * frames / SAMPLE_RATE))).astype("<i2")
    with wave.open(str(music), "wb") as stream:
        stream.setparams((2, 2, SAMPLE_RATE, len(samples), "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    events = [(0.0, b"\xf0\x43\x10\x4c\x00\x00\x7e\x00\xf7"), (0.1, b"\xcf\x02"),
              (0.2, b"\x90\x3c\x4f"), (0.3, b"\xb0\x40\x7f"), (0.5, b"\xe0\x00\x40"),
              (0.7, b"\x80\x3c\x40"), (0.8, b"\xb0\x40\x00"),
              (1.2, b"\x91\x3e\x40"), (1.7, b"\x91\x3e\x00")]
    source = _files(tmp_path / "Library/Album", {"Phrase.mid": b"original companion", "Phrase.FIL": b"E-SEQ"})
    encoded = tmp_path / "encoded.wav"
    encode_disklavier_wav(events, music, encoded)
    song = source / f"Phrase.{extension}"
    if extension == "MP3":
        subprocess.run([command, "--silent", "--cbr", "-b", "320", "-m", "s", "-q", "2",
                        str(encoded), str(song)], check=True, capture_output=True)
    else:
        song.write_bytes(encoded.read_bytes())
    originals = {path: path.read_bytes() for path in source.iterdir()}
    result = batch.run_audio_decode_batch([source.parent], tmp_path / "output")
    assert result.converted == 1 and result.failed == 0, result.records
    record = result.records[0]
    recovered = read_midi_events(record.midi_path)
    assert [data for _, data in recovered] == [data for _, data in events]
    for actual, (seconds, _) in zip(recovered, events):
        assert actual[0] == pytest.approx(seconds + 0.5, abs=0.0004)
    clean = tmp_path / "clean.wav"
    audio_tools.decode_mp3(record.mp3_path, clean)
    clean_pcm, encoded_pcm = _pcm(clean), _pcm(encoded)
    assert clean_pcm.shape == (len(encoded_pcm), 1)
    assert np.corrcoef(clean_pcm[:, 0], encoded_pcm[:, 0])[0, 1] > 0.99
    assert mido.MidiFile(record.midi_path).length == pytest.approx(len(clean_pcm) / SAMPLE_RATE, abs=0.0001)
    assert all(path.read_bytes() == payload for path, payload in originals.items())
