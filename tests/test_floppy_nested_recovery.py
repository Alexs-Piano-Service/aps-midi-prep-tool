"""FAT recovery follows usable nested directories and fragmented song chains."""

import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _vlq(number):
    encoded = [number & 0x7F]
    while number >> 7:
        number >>= 7
        encoded.insert(0, 0x80 | (number & 0x7F))
    return bytes(encoded)


def _song(kind, *, large=True):
    if kind == "MIDI":
        text = b"Preserve fragmented performance " * (22 if large else 1)
        track = (
            b"\0\xff\x01" + _vlq(len(text)) + text
            + b"\0\x90\x3c\x50\x20\x80\x3c\0\0\xff\x2f\0"
        )
        # FAT recovery knows the complete file extent, including opaque bytes
        # that a signature-based MIDI carve cannot establish as part of it.
        return b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk" + len(track).to_bytes(4, "big") + track + b"Opaque trailer"
    stream = b"\x90\x3c\x50\xf3\x20\x80\x3c\0" * (85 if large else 1) + b"\xf2"
    header = bytearray(0x77)
    header[0] = 0xFE
    header[3:7] = (len(header) + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x27:0x33] = image.build_eseq_order_key_from_path("SONG.FIL")
    header[0x33] = 91
    return bytes(header) + stream


def _set_fat(data, geometry, cluster, following, *, copy=None):
    copies = range(geometry.num_fats) if copy is None else (copy,)
    for index in copies:
        offset = geometry.fat_offset + index * geometry.fat_size + cluster + cluster // 2
        pair = int.from_bytes(data[offset:offset + 2], "little")
        pair = (pair & 0x000F) | (following << 4) if cluster & 1 else (pair & 0xF000) | following
        data[offset:offset + 2] = pair.to_bytes(2, "little")


def _directory_entry(name, cluster):
    return image._dos_directory_entry(name.encode("ascii").ljust(11), cluster, 0, 0x10)


def _blank_image(tmp_path):
    source = tmp_path / "nested.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[2], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    return source, data, geometry


def _nested_fragmented_image(tmp_path, kind):
    source, data, geometry = _blank_image(tmp_path)
    extension = "MID" if kind == "MIDI" else "FIL"
    payload = _song(kind)
    assert geometry.cluster_size < len(payload) <= 2 * geometry.cluster_size
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    data[geometry.root_offset:geometry.root_offset + 64] = (
        _directory_entry("MUSIC", 2)
        + image._dos_directory_entry(b"BROKEN  MID", 0, 4)
    )
    for cluster in (2, 3, 4, 5, 6, 8):
        _set_fat(data, geometry, cluster, {3: 8, 4: 6}.get(cluster, 0xFFF))
    data[offset(2):offset(2) + 96] = (
        _directory_entry(".", 2) + _directory_entry("..", 0) + _directory_entry("DEEP", 3)
    )
    data[offset(3):offset(3) + 64] = _directory_entry(".", 3) + _directory_entry("..", 2)
    data[offset(3) + 64:offset(3) + geometry.cluster_size] = b"\xe5" * (geometry.cluster_size - 64)
    source_entry_offset = offset(8)
    data[source_entry_offset:source_entry_offset + 32] = image._dos_directory_entry(
        b"SONG    " + extension.encode("ascii"), 4, len(payload),
    )
    data[offset(4):offset(4) + geometry.cluster_size] = payload[:geometry.cluster_size]
    tail = payload[geometry.cluster_size:]
    data[offset(6):offset(6) + len(tail)] = tail
    data[offset(5):offset(5) + geometry.cluster_size] = b"N" * geometry.cluster_size
    source.write_bytes(data)
    return source, data, geometry, payload, f"MUSIC/DEEP/SONG.{extension}", source_entry_offset


@pytest.mark.parametrize("kind", ["MIDI", "E-SEQ"])
def test_recovery_reads_fragmented_nested_directory_and_song_bytes_from_fat(tmp_path, kind):
    source, data, geometry, payload, path, entry_offset = _nested_fragmented_image(tmp_path, kind)
    original_image = source.read_bytes()
    with pytest.raises(image.FloppyImageError, match="corrupt"):
        image.read_image_listing(source)

    recovered = image._recover_files_from_fat_context(bytes(data), geometry)

    assert len(recovered) == 1
    song = recovered[0]
    assert (song.image_path, song.kind, song.origin) == (path, kind, "fat")
    assert song.source_offset == image._cluster_offset(geometry, 4)
    assert song.source_entry_offset == entry_offset
    assert song.data == payload
    assert source.read_bytes() == original_image

    recovered_image_files = image._recover_files_from_raw_image_bytes(bytes(data))
    fat_songs = [song for song in recovered_image_files if song.origin == "fat"]
    assert len(fat_songs) == 1
    assert fat_songs[0].image_path == path.rsplit("/", 1)[-1]
    assert fat_songs[0].data == payload
    assert fat_songs[0].source_entry_offset == entry_offset


@pytest.mark.parametrize("kind", ["MIDI", "E-SEQ"])
def test_nested_recovery_uses_a_readable_directory_chain_from_the_second_fat(tmp_path, kind):
    _source, data, geometry, payload, path, _entry_offset = _nested_fragmented_image(tmp_path, kind)
    _set_fat(data, geometry, 3, 0, copy=0)

    recovered = image._recover_files_from_fat_context(bytes(data), geometry)

    assert [(song.image_path, song.data) for song in recovered] == [(path, payload)]


@pytest.mark.parametrize("kind", ["MIDI", "E-SEQ"])
def test_nested_recovery_does_not_choose_between_conflicting_fat_song_chains(tmp_path, kind):
    _source, data, geometry, payload, path, _entry_offset = _nested_fragmented_image(tmp_path, kind)
    _set_fat(data, geometry, 4, 5, copy=0)
    offset = image._cluster_offset(geometry, 5)
    tail = bytearray(payload[geometry.cluster_size:])
    tail[0] ^= 1
    data[offset:offset + len(tail)] = tail

    with pytest.raises(image._Fat12RecoveryAmbiguityError, match=f"different contents for {path}"):
        image._recover_files_from_fat_context(bytes(data), geometry)


@pytest.mark.parametrize("damage", ["self_reference", "two_directory_cycle", "sibling_alias", "fat_cycle"])
def test_directory_cycles_are_bounded_and_reachable_songs_survive(tmp_path, damage):
    _source, data, geometry = _blank_image(tmp_path)
    payload = _song("MIDI", large=False)
    offset = lambda cluster: image._cluster_offset(geometry, cluster)
    root_entries = [_directory_entry("MUSIC", 2)]
    for cluster in (2, 3, 4):
        _set_fat(data, geometry, cluster, 0xFFF)
    directory_entry = image._dos_directory_entry(b"SONG    MID", 4, len(payload))
    expected_path = "MUSIC/SONG.MID"
    if damage == "self_reference":
        data[offset(2):offset(2) + 64] = _directory_entry("LOOP", 2) + directory_entry
    elif damage == "two_directory_cycle":
        data[offset(2):offset(2) + 32] = _directory_entry("DEEP", 3)
        data[offset(3):offset(3) + 64] = _directory_entry("LOOP", 2) + directory_entry
        expected_path = "MUSIC/DEEP/SONG.MID"
    elif damage == "sibling_alias":
        root_entries.append(_directory_entry("ALIAS", 2))
        data[offset(2):offset(2) + 32] = directory_entry
    else:
        _set_fat(data, geometry, 2, 2)
        root_entries.append(directory_entry)
        expected_path = "SONG.MID"
    data[geometry.root_offset:geometry.root_offset + 32 * len(root_entries)] = b"".join(root_entries)
    data[offset(4):offset(4) + len(payload)] = payload

    recovered = image._recover_files_from_fat_context(bytes(data), geometry)

    assert [(song.image_path, song.data) for song in recovered] == [(expected_path, payload)]


def test_directory_cycle_deeper_than_python_recursion_limit_keeps_the_reachable_song(tmp_path):
    _source, data, geometry = _blank_image(tmp_path)
    payload = _song("MIDI", large=False)
    data[geometry.root_offset:geometry.root_offset + 32] = _directory_entry("MUSIC", 2)
    for cluster in range(2, 1103):
        _set_fat(data, geometry, cluster, 0xFFF)
        offset = image._cluster_offset(geometry, cluster)
        following = cluster + 1 if cluster < 1102 else 2
        data[offset:offset + 32] = _directory_entry("D", following)
    offset = image._cluster_offset(geometry, 1102)
    data[offset + 32:offset + 64] = image._dos_directory_entry(b"SONG    MID", 2000, len(payload))
    _set_fat(data, geometry, 2000, 0xFFF)
    offset = image._cluster_offset(geometry, 2000)
    data[offset:offset + len(payload)] = payload

    recovered = image._recover_files_from_fat_context(bytes(data), geometry)

    assert len(recovered) == 1
    assert recovered[0].image_path == "MUSIC/" + "D/" * 1100 + "SONG.MID"
    assert recovered[0].data == payload
