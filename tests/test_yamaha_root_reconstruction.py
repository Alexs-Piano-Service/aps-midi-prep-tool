import math

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.eseq_converter import parse_eseq_bytes


def _song(stream, *, total_length=None, stream_length=None):
    header = bytearray(0x77)
    header[0] = 0xFE
    header[7:15] = b"COM-ESEQ"
    header[0x27:0x32] = b"SONG       "
    header[0x33] = 91
    header[3:7] = (len(header) + len(stream) if total_length is None else total_length).to_bytes(4, "little")
    header[0x1F:0x23] = (len(stream) if stream_length is None else stream_length).to_bytes(4, "little")
    return bytes(header) + stream


def _set_fat(fat, cluster, value):
    offset = cluster + cluster // 2
    pair = int.from_bytes(fat[offset:offset + 2], "little")
    pair = (pair & 0x000F) | (value << 4) if cluster & 1 else (pair & 0xF000) | value
    fat[offset:offset + 2] = pair.to_bytes(2, "little")


def _rebuild(payload):
    """A damaged directory, intact catalog/FAT, and a separate adjacent file."""
    geometry = image._yamaha_720_geometry()
    data = bytearray(geometry.total_size)
    fat = bytearray(geometry.fat_size)
    fat[:3] = bytes((image._YAMAHA_MEDIA_DESCRIPTOR, 0xFF, 0xFF))
    next_cluster = 2

    def allocate(value):
        nonlocal next_cluster
        count = math.ceil(len(value) / geometry.cluster_size)
        chain = list(range(next_cluster, next_cluster + count))
        next_cluster += count
        for index, cluster in enumerate(chain):
            _set_fat(fat, cluster, chain[index + 1] if index + 1 < len(chain) else 0xFFF)
            chunk = value[index * geometry.cluster_size:(index + 1) * geometry.cluster_size]
            offset = image._cluster_offset(geometry, cluster)
            data[offset:offset + len(chunk)] = chunk
        return chain

    catalog = bytearray(image.PIANODIR_TARGET_FILE_SIZE)
    catalog[:len(image.PIANODIR_HEADER)] = image.PIANODIR_HEADER
    catalog[len(image.PIANODIR_HEADER):len(image.PIANODIR_HEADER) + image.PIANODIR_TRACK_SIZE] = payload[0x27:0x77]
    allocate(catalog)
    song_chain = allocate(payload)
    allocate(b"\xF2NEIGHBOR FILE MUST STAY SEPARATE" * 32)
    data[geometry.fat_offset:geometry.fat_offset + len(fat)] = fat
    data[geometry.fat_offset + geometry.fat_size:geometry.fat_offset + 2 * geometry.fat_size] = fat

    root = image._reconstruct_yamaha_root_dir_from_pianodir(bytes(data))
    assert root is not None
    entries = list(image._iter_fat_directory_entries(root))
    assert {entry["name"] for entry in entries} == {"PIANODIR.FIL", "SONG"}
    song = next(entry for entry in entries if entry["name"] == "SONG")
    recovered = image._read_cluster_chain_from_image(data, geometry, song_chain, song["size"])
    return song, recovered, len(song_chain) * geometry.cluster_size


def test_reconstruction_uses_stream_length_when_total_header_is_eight_bytes_short():
    stream = b"\x90\x3c\x40\xf3\x20\x80\x3c\x00\xf4\x20\x03\xb0\x40\x00\xf2"
    payload = _song(stream, total_length=0x77 + len(stream) - 8)
    entry, recovered, _allocated = _rebuild(payload)
    assert entry["size"] == len(payload)
    assert recovered == payload
    assert parse_eseq_bytes(recovered).events[-1][2] == b"\xb0\x40\x00"


def test_reconstruction_preserves_song_larger_than_saturated_ffff_header():
    stream = b"\x90\x3c\x40\xf3\x01\x80\x3c\x00" * 9000 + b"\xf2"
    payload = _song(stream, total_length=0xFFFF)
    entry, recovered, allocated = _rebuild(payload)
    assert 0xFFFF < entry["size"] == len(payload) <= allocated
    assert recovered == payload
    assert len(parse_eseq_bytes(recovered).events) == 18000


def test_reconstruction_keeps_valid_trailer_after_both_declared_lengths():
    prefix = b"\x90\x3c\x40\xf3\x20\x80\x3c\x00"
    trailer = b"\xf4\x20\x03\xb0\x40\x00\xf2"
    payload = _song(prefix + trailer, total_length=0x77 + len(prefix), stream_length=len(prefix))
    entry, recovered, _allocated = _rebuild(payload)
    assert entry["size"] == len(payload)
    parsed = parse_eseq_bytes(recovered)
    assert parsed.end_tick == 448
    assert parsed.events[-1][2] == b"\xb0\x40\x00"


def test_reconstruction_never_reads_past_fat_chain_for_oversized_lengths_or_missing_end():
    payload = _song(b"\xf0\x43\x01", total_length=0xFFFFFFFF, stream_length=0xFFFFFFFF)
    entry, recovered, allocated = _rebuild(payload)
    assert entry["size"] == allocated
    assert recovered.startswith(payload)
    assert recovered[len(payload):] == bytes(allocated - len(payload))
    assert b"NEIGHBOR" not in recovered


def test_reconstruction_does_not_treat_sysex_f2_as_end_of_song():
    stream = b"\xf0\x43\xf2\x11\xf7\xf3\x20\xf2"
    payload = _song(stream, total_length=0x7A, stream_length=3)
    entry, recovered, _allocated = _rebuild(payload)
    assert entry["size"] == len(payload)
    assert recovered == payload


def test_reconstruction_skips_sysex_delay_operands_before_searching_for_end():
    stream = b"\xf0\x43\xf3\xf7\x01\xf7\xf3\x20\xf2"
    payload = _song(stream, total_length=0x7A, stream_length=3)
    entry, recovered, _allocated = _rebuild(payload)
    assert entry["size"] == len(payload)
    assert recovered == payload


def test_reconstruction_does_not_claim_a_trailer_after_an_unknown_opcode():
    payload = _song(b"\xf5unrecognized\xf2", total_length=0x78, stream_length=1)
    entry, recovered, _allocated = _rebuild(payload)
    assert entry["size"] == 0x78
    assert recovered == payload[:0x78]
