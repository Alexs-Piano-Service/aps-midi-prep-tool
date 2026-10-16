"""Concurrent converters must publish only their own complete song bytes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from aps_midi_prep_tool_app import eseq_converter


def _midi(note):
    track = (b"\x00\x90" + bytes([note, 100])
             + b"\x83\x00\x80" + bytes([note, 0]) + b"\x00\xff\x2f\x00")
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\x80"
            + b"MTrk" + len(track).to_bytes(4, "big") + track)


def _prepare_conversion(tmp_path, direction, note, destination):
    midi = _midi(note)
    if direction == "midi-to-eseq":
        source = tmp_path / f"source-{note}.mid"
        source.write_bytes(midi)
        expected = eseq_converter.convert_midi_bytes_to_eseq_bytes(
            midi, filename_hint=destination.name,
        )
        converter = eseq_converter.convert_midi_file_to_eseq_path
    else:
        source = tmp_path / f"source-{note}.fil"
        source.write_bytes(eseq_converter.convert_midi_bytes_to_eseq_bytes(midi))
        expected = eseq_converter.convert_eseq_bytes_to_midi_bytes(
            source.read_bytes(), filename=source.name,
        )
        converter = eseq_converter.convert_eseq_file_to_midi_path
    return source, expected, converter


@pytest.mark.parametrize("direction", ["midi-to-eseq", "eseq-to-midi"])
@pytest.mark.parametrize("existing_destination", [False, True])
def test_concurrent_exports_publish_their_own_payloads(
    tmp_path, monkeypatch, direction, existing_destination,
):
    destination = tmp_path / ("song.fil" if direction == "midi-to-eseq" else "song.mid")
    original = b"previous complete song"
    if existing_destination:
        destination.write_bytes(original)
    jobs = [_prepare_conversion(tmp_path, direction, note, destination) for note in (60, 72)]
    assert jobs[0][1] != jobs[1][1]
    ready = threading.Barrier(2)
    first_publication = threading.Event()
    second_publication = threading.Event()
    release_publication = threading.Event()
    writer = threading.local()
    publications = []
    write_bytes = eseq_converter._write_destination_bytes
    replace = eseq_converter.os.replace

    def start_together(path, payload):
        ready.wait(timeout=5)
        write_bytes(path, payload)

    def inspect_publication(staged, path):
        staged = Path(staged)
        assert staged.parent == destination.parent
        assert staged.read_bytes() == writer.expected
        publications.append((staged, writer.expected))
        if len(publications) == 1:
            first_publication.set()
            assert release_publication.wait(timeout=5)
        else:
            second_publication.set()
        replace(staged, path)
        assert destination.read_bytes() == writer.expected

    def convert(job):
        source, expected, converter = job
        writer.expected = expected
        converter(source, destination)

    monkeypatch.setattr(eseq_converter, "_write_destination_bytes", start_together)
    monkeypatch.setattr(eseq_converter.os, "replace", inspect_publication)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(convert, job) for job in jobs]
        try:
            assert first_publication.wait(timeout=5)
            # Both exports have started, but a writer targeting the same path
            # must wait until the first writer has finished publication.
            assert not second_publication.wait(timeout=0.2)
            if existing_destination:
                assert destination.read_bytes() == original
            else:
                assert not destination.exists()
        finally:
            release_publication.set()
        for future in futures:
            future.result(timeout=5)

    assert second_publication.is_set()
    assert len({staged for staged, _payload in publications}) == 2
    assert {payload for _staged, payload in publications} == {job[1] for job in jobs}
    assert destination.read_bytes() == publications[-1][1]
    assert set(tmp_path.iterdir()) == {destination, *(job[0] for job in jobs)}


@pytest.mark.parametrize("direction", ["midi-to-eseq", "eseq-to-midi"])
def test_failed_publication_preserves_destination_and_cleans_temporary_file(
    tmp_path, monkeypatch, direction,
):
    destination = tmp_path / ("song.fil" if direction == "midi-to-eseq" else "song.mid")
    original = b"previous complete song"
    destination.write_bytes(original)
    source, expected, converter = _prepare_conversion(tmp_path, direction, 60, destination)

    def reject_publication(staged, _path):
        assert Path(staged).read_bytes() == expected
        raise OSError("Simulated publication failure")

    monkeypatch.setattr(eseq_converter.os, "replace", reject_publication)
    with pytest.raises(OSError, match="Simulated publication failure"):
        converter(source, destination)

    assert destination.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source, destination}
