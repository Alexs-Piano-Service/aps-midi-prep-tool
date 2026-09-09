"""Metadata edits preserve the source's complete ESEQ timing/header layout."""

import pytest

from aps_midi_prep_tool_app.eseq_pianodir import (
    PianodirTrackEntry,
    build_eseq_order_key_from_path,
    build_pianodir_bytes,
    update_eseq_order_key_to_path,
)
from aps_midi_prep_tool_app.midi_metadata import update_eseq_title_to_path


def _external_eseq():
    # Handwritten valid events with leading silence and a 1498-tick trailer.
    # Legacy headers leave before/after and note/controller flags at zero.
    stream = (
        b"\xF1\x00\xF0\x7E\x7F\x09\x01\xF7"
        b"\xF4\x6C\x05\x90\x3C\x40\xB0\x40\x7F"
        b"\xF4\x00\x03\x80\x3C\x00\xB0\x40\x00"
        b"\xF4\x5A\x0B\xF2"
    )
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x24] = header[0x33] = 117 - 29
    header[0x27:0x33] = b"ORIGINALFIL\x00"
    header[0x34:0x36] = b"\x04\x04"
    header[0x37:0x3B] = (748 + 384 + 1498).to_bytes(4, "little")
    header[0x41:0x43] = ((len(header) + len(stream) - 1) & 0x7FF).to_bytes(2, "big")
    header[0x43:0x47] = b"\x00\x77\x00\x00"
    header[0x4F] = 0x80
    header[0x57:0x77] = b"Original title".ljust(32, b" ")
    return bytes(header) + stream


@pytest.mark.parametrize("in_place", (False, True))
@pytest.mark.parametrize("title", ("Original title", "Edited title"))
def test_title_edit_changes_only_the_title_bytes(tmp_path, in_place, title):
    source = tmp_path / "ORIGINAL.FIL"
    destination = source if in_place else tmp_path / "copy.FIL"
    original = _external_eseq()
    source.write_bytes(original)

    assert update_eseq_title_to_path(source, title, destination) is None

    expected = original[:0x57] + title.encode("latin1").ljust(32, b" ") + original[0x77:]
    assert destination.read_bytes() == expected
    if not in_place:
        assert source.read_bytes() == original


@pytest.mark.parametrize("in_place", (False, True))
def test_order_key_edit_changes_only_the_key_bytes(tmp_path, in_place):
    source = tmp_path / "ORIGINAL.FIL"
    destination = source if in_place else tmp_path / "RENAMED.FIL"
    original = _external_eseq()
    source.write_bytes(original)
    key = build_eseq_order_key_from_path("RENAMED.FIL")

    assert update_eseq_order_key_to_path(source, key, destination) is None

    assert destination.read_bytes() == original[:0x27] + key + original[0x33:]
    if not in_place:
        assert source.read_bytes() == original


def test_catalog_after_title_and_filename_edits_copies_preserved_timing_header(tmp_path):
    source = tmp_path / "ORIGINAL.FIL"
    output = tmp_path / "RENAMED.FIL"
    original = _external_eseq()
    source.write_bytes(original)

    assert update_eseq_title_to_path(source, "Edited title", output) is None
    assert update_eseq_order_key_to_path(output, build_eseq_order_key_from_path(output.name), output) is None
    prepared = output.read_bytes()
    catalog = build_pianodir_bytes([
        PianodirTrackEntry(output.name, str(output), "Edited title"),
    ])

    assert prepared[0x33:0x57] == original[0x33:0x57]
    assert prepared[0x77:] == original[0x77:]
    # PIANODIR's first record starts after its 16-byte signature/header.
    assert catalog[0x10:0x60] == prepared[0x27:0x77]
    assert source.read_bytes() == original
