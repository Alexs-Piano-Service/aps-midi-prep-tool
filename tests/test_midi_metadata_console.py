"""Metadata reads do not depend on the console's character encoding."""

import io
from contextlib import redirect_stdout

import pytest

from aps_midi_prep_tool_app import midi_metadata


def _midi_bytes(title):
    name = title.encode("latin1")
    track = b"\x00\xff\x03" + bytes([len(name)]) + name + b"\x00\xff\x2f\x00"
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk"
            + len(track).to_bytes(4, "big") + track)


@pytest.mark.parametrize("kind", ["midi", "eseq"])
@pytest.mark.parametrize("filename,title", [
    ("3\u00a6\u2518\u252c\u2514\u00cc\u25934", "Original title"),
    ("SONG", "Title \x81"),
])
def test_title_readers_preserve_metadata_with_a_legacy_console(tmp_path, kind, filename, title):
    source = tmp_path / filename
    if kind == "midi":
        payload = _midi_bytes(title)
        reader = midi_metadata.extract_first_title_from_midi
    else:
        payload = midi_metadata._set_eseq_title_in_bytes(bytes(0x77), title)
        reader = midi_metadata.extract_eseq_title_from_file
    source.write_bytes(payload)

    with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as console, redirect_stdout(console):
        result = reader(source)

    assert result == title
    assert source.read_bytes() == payload


@pytest.mark.parametrize("kind", ["midi", "eseq", "midi_type"])
@pytest.mark.parametrize("missing", [False, True])
def test_read_errors_keep_their_contract_with_a_legacy_console(tmp_path, kind, missing):
    source = tmp_path / "\u66f2.mid"
    if not missing:
        source.write_bytes(b"truncated")
    reader = {
        "midi": midi_metadata.extract_first_title_from_midi,
        "eseq": midi_metadata.extract_eseq_title_from_file,
        "midi_type": midi_metadata.extract_midi_type_label_from_midi,
    }[kind]

    with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as console, redirect_stdout(console):
        result = reader(source)

    if kind == "midi_type":
        assert result == "Error"
    else:
        assert result.startswith(f"Error reading {'MIDI' if kind == 'midi' else 'E-SEQ'} title from {source.name}: ")
        assert "charmap" not in result
    assert source.exists() == (not missing)
    if not missing:
        assert source.read_bytes() == b"truncated"
