import math
from pathlib import Path

import pytest

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


def test_reconstruction_preserves_trailer_without_parsing_past_declared_lengths():
    prefix = b"\x90\x3c\x40\xf3\x20\x80\x3c\x00"
    trailer = b"\xf4\x20\x03\xb0\x40\x00\xf2"
    payload = _song(prefix + trailer, total_length=0x77 + len(prefix), stream_length=len(prefix))
    entry, recovered, _allocated = _rebuild(payload)
    assert entry["size"] == len(payload)
    # Recovery retains all bytes even though normal playback obeys the sane
    # declared stream boundary and must not interpret the opaque suffix.
    assert recovered == payload
    parsed = parse_eseq_bytes(recovered)
    assert parsed.end_tick == 32
    assert parsed.events == [(0, 2, b"\x90\x3c\x40"), (32, 2, b"\x80\x3c\x00")]


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


def _fragmented_image_with_destroyed_root(*, conflicting=False, invalid_first_signature=False,
                                          broken_link=0, second_song=False):
    geometry = image._yamaha_720_geometry()
    data = bytearray(geometry.total_size)
    data[:geometry.bytes_per_sector] = b"\xe5" * geometry.bytes_per_sector
    data[geometry.root_offset:geometry.root_offset + geometry.root_size] = b"\xe5" * geometry.root_size
    payload = _song(b"\x90\x3c\x40\xf3\x01\x80\x3c\x00" * 200 + b"\xf2")
    catalog = bytearray(image.PIANODIR_TARGET_FILE_SIZE)
    catalog[:len(image.PIANODIR_HEADER)] = image.PIANODIR_HEADER
    catalog[len(image.PIANODIR_HEADER):len(image.PIANODIR_HEADER) + image.PIANODIR_TRACK_SIZE] = payload[0x27:0x77]
    catalog_chain = list(range(2, 2 + math.ceil(len(catalog) / geometry.cluster_size)))
    first = catalog_chain[-1] + 1
    song_chain = [first, first + 2]
    assert geometry.cluster_size < len(payload) <= 2 * geometry.cluster_size
    fat = bytearray(geometry.fat_size)
    fat[:3] = b"\xf9\xff\xff"
    allocations = [(catalog_chain, catalog), (song_chain, payload)]
    if second_song:
        second_payload = bytearray(_song(b"\x90\x3d\x40\xf2"))
        second_payload[0x27:0x32] = b"SECOND     "
        offset = len(image.PIANODIR_HEADER) + image.PIANODIR_TRACK_SIZE
        catalog[offset:offset + image.PIANODIR_TRACK_SIZE] = second_payload[0x27:0x77]
        allocations.append(([first + 4], second_payload))
    for chain, contents in allocations:
        for index, cluster in enumerate(chain):
            _set_fat(fat, cluster, chain[index + 1] if index + 1 < len(chain) else 0xFFF)
            chunk = contents[index * geometry.cluster_size:(index + 1) * geometry.cluster_size]
            offset = image._cluster_offset(geometry, cluster)
            data[offset:offset + len(chunk)] = chunk

    neighbor = image._cluster_offset(geometry, first + 1)
    data[neighbor:neighbor + geometry.cluster_size] = b"N" * geometry.cluster_size
    _set_fat(fat, first + 1, 0xFFF)
    if conflicting:
        alternate_tail = first + 3
        offset = image._cluster_offset(geometry, alternate_tail)
        alternate = bytearray(payload[geometry.cluster_size:])
        alternate[2] ^= 1
        data[offset:offset + len(alternate)] = alternate
        _set_fat(fat, alternate_tail, 0xFFF)
    first_fat = bytearray(fat)
    _set_fat(first_fat, first, alternate_tail if conflicting else broken_link)
    if invalid_first_signature:
        first_fat[:3] = bytes(3)
    data[geometry.fat_offset:geometry.fat_offset + geometry.fat_size] = first_fat
    data[geometry.fat_offset + geometry.fat_size:geometry.fat_offset + 2 * geometry.fat_size] = fat
    return bytes(data), geometry, payload


@pytest.mark.parametrize("invalid_first_signature,broken_link", [(False, 0), (False, 0xFF7), (True, 0)])
def test_reconstruction_uses_good_second_fat_for_fragmented_song(tmp_path, invalid_first_signature, broken_link):
    data, geometry, payload = _fragmented_image_with_destroyed_root(
        invalid_first_signature=invalid_first_signature,
        broken_link=broken_link,
    )
    assert image._detect_yamaha_layout(data) is None
    assert image._detect_protected_fat12_layout(data) is None
    output = tmp_path / "recovered.img"
    result = image.prepare_yamaha_bytes(data, output)
    assert result.boot_sector_repaired
    assert "Selected FAT 2" in result.note
    assert image._read_fat12_file_bytes(output, "SONG") == payload
    assert output.read_bytes()[geometry.data_offset:] == data[geometry.data_offset:]


def test_reconstruction_uses_complete_catalog_map_over_partly_readable_first_fat(tmp_path):
    data, _geometry, payload = _fragmented_image_with_destroyed_root(second_song=True)
    output = tmp_path / "recovered.img"
    result = image.prepare_yamaha_bytes(data, output)
    assert "Selected FAT 2" in result.note
    assert {entry.path for entry in image.read_image_listing(output).entries} == {"PIANODIR.FIL", "SONG", "SECOND"}
    assert image._read_fat12_file_bytes(output, "SONG") == payload


def test_reconstruction_rejects_conflicting_fragmented_song_maps_before_writing(tmp_path):
    data, _geometry, _payload = _fragmented_image_with_destroyed_root(conflicting=True)
    output = tmp_path / "recovered.img"
    with pytest.raises(image._Fat12RecoveryAmbiguityError, match="conflicting.*PIANODIR"):
        image.prepare_yamaha_bytes(data, output)
    assert not output.exists()


@pytest.mark.parametrize("mode", ["recover", "open-converted"])
def test_root_reconstruction_ambiguity_stops_high_level_fallbacks(tmp_path, monkeypatch, mode):
    data, _geometry, _payload = _fragmented_image_with_destroyed_root(conflicting=True)
    source = tmp_path / "source.img"
    source.write_bytes(data)
    monkeypatch.setattr(image, "_recover_file_candidates_from_raw_image_bytes",
                        lambda *_a, **_k: pytest.fail("Conflicting catalog maps must not fall back to carving"))
    calls = []
    if mode == "recover":
        def run():
            return image.FloppyImageSession._recover_from_raw_image(
                source, str(tmp_path), source_name="source.img",
            )
    else:
        monkeypatch.setattr(image, "_conversion_candidate_formats", lambda *_a, **_k: [
            image.DISK_FORMAT_BY_KEY["ibm.720"], image.DISK_FORMAT_BY_KEY["ibm.1440"],
        ])

        def convert(_source, destination, _format, **_kwargs):
            assert not calls, "Conflicting catalog maps must not retry another geometry"
            calls.append(destination)
            Path(destination).write_bytes(data)
            return ""

        monkeypatch.setattr(image, "_gw_convert", convert)

        def run():
            return image.FloppyImageSession._load_converted(source, "scp", str(tmp_path))

    with pytest.raises(image._Fat12RecoveryAmbiguityError, match="conflicting.*PIANODIR"):
        run()
    assert source.read_bytes() == data
