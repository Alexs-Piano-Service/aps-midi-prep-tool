"""Raw carving uses Yamaha stream boundaries even with unreliable lengths."""

import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _song(stream, *, total_length=None, stream_length=None):
    header = bytearray(0x77)
    header[0] = 0xFE
    header[7:15] = b"COM-ESEQ"
    header[0x27:0x32] = b"SONG    FIL"
    header[0x33] = 91
    header[3:7] = (len(header) + len(stream) if total_length is None else total_length).to_bytes(4, "little")
    header[0x1F:0x23] = (len(stream) if stream_length is None else stream_length).to_bytes(4, "little")
    return bytes(header) + stream


@pytest.mark.parametrize("length_convention", ["eight-short", "saturated", "both-saturated", "trailer", "sysex-f2"])
def test_carving_preserves_complete_eseq_with_unreliable_declared_lengths(length_convention):
    stream = b"\x90\x3c\x40\xf3\x20\x80\x3c\x00\xf4\x20\x03\xb0\x40\x00\xf2"
    stream_length = None
    if "saturated" in length_convention:
        stream = b"\x90\x3c\x40\xf3\x01\x80\x3c\x00" * 9000 + b"\xf2"
        total_length = 0xFFFF
        if length_convention == "both-saturated":
            stream_length = 0xFFFF
    elif length_convention == "sysex-f2":
        stream = b"\xf0\x43\xf2\x11\xf7\xf3\x20\xf2"
        total_length, stream_length = 0x7A, 3
    else:
        total_length = 0x77 + len(stream) - 8
        if length_convention == "trailer":
            stream_length = len(stream) - 8
    payload = _song(stream, total_length=total_length, stream_length=stream_length)
    neighbor = _song(b"\x90\x43\x40\xf3\x20\x80\x43\x00\xf2")
    raw = bytearray(720 * 1024)  # Neither FAT nor root can supply the sizes.
    start = 8192
    raw[start:start + len(payload) + len(neighbor)] = payload + neighbor

    recovered = image._carve_recovery_files_from_bytes(bytes(raw))

    assert [(song.source_offset, song.data, song.origin) for song in recovered] == [
        (start, payload, "carve"),
        (start + len(payload), neighbor, "carve"),
    ]


@pytest.mark.parametrize("neighbor_kind", ["E-SEQ", "MIDI", "PIANODIR"])
def test_carving_bounds_missing_end_and_oversized_lengths_at_next_file(neighbor_kind):
    stream = b"\x90\x3c\x40\xf3\x20\x80\x3c\x00"
    payload = _song(stream, total_length=0xFFFFFFFF, stream_length=0xFFFFFFFF)
    if neighbor_kind == "E-SEQ":
        neighbor = _song(b"\xf2")
    elif neighbor_kind == "MIDI":
        neighbor = b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk\0\0\0\x04\0\xff\x2f\0"
    else:
        neighbor = image._padded_pianodir_bytes(image.PIANODIR_HEADER)
    raw = payload + neighbor + bytes(1024)

    recovered = image._carve_recovery_files_from_bytes(raw)

    song = next(item for item in recovered if item.source_offset == 0)
    assert song.data == payload
    assert any(item.source_offset == len(payload) and item.kind == neighbor_kind for item in recovered)


@pytest.mark.parametrize("marker_location", ["title", "sysex"])
@pytest.mark.parametrize("damaged_first_byte", [False, True])
def test_signature_text_within_a_song_does_not_split_it(marker_location, damaged_first_byte):
    stream = b"\xf0\x43\x01COM-ESEQ\xf7\xf2" if marker_location == "sysex" else b"\xf2"
    payload = bytearray(_song(stream))
    if marker_location == "title":
        payload[0x57:0x5F] = b"COM-ESEQ"
    if damaged_first_byte:
        payload[0] = 0
    payload = bytes(payload)
    neighbor = _song(b"\xf2")

    recovered = image._carve_recovery_files_from_bytes(payload + neighbor + bytes(1024))

    assert [(song.source_offset, song.data) for song in recovered] == [
        (0, payload), (len(payload), neighbor),
    ]
