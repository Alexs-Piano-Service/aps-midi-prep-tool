"""FAT selection preserves data and fails closed before external-reader fallback."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _set_fat(data, geometry, copy, cluster, following):
    offset = geometry.fat_offset + copy * geometry.fat_size + cluster + cluster // 2
    pair = int.from_bytes(data[offset:offset + 2], "little")
    pair = (pair & 0x000F) | (following << 4) if cluster & 1 else (pair & 0xF000) | following
    data[offset:offset + 2] = pair.to_bytes(2, "little")


def _vlq(number):
    encoded = [number & 0x7f]
    while number >> 7:
        number >>= 7
        encoded.insert(0, 0x80 | (number & 0x7f))
    return bytes(encoded)


def _fragmented_image(tmp_path):
    source = tmp_path / "source.img"
    image._create_blank_fat12_image_from_layout(source, image._PROTECTED_FAT12_LAYOUTS[0], "TEST")
    data = bytearray(source.read_bytes())
    geometry = image._geometry_from_boot_sector(data[:512])
    text = b"A" * (geometry.cluster_size + 60)
    track = b"\0\xff\x01" + _vlq(len(text)) + text + b"\0\xff\x2f\0"
    payload = b"MThd\0\0\0\x06\0\0\0\x01\x01\xe0MTrk" + len(track).to_bytes(4, "big") + track
    for copy in range(geometry.num_fats):
        _set_fat(data, geometry, copy, 2, 4)
        _set_fat(data, geometry, copy, 3, 0xfff)
        _set_fat(data, geometry, copy, 4, 0xfff)
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    MID", 2, len(payload))
    start = image._cluster_offset(geometry, 2)
    data[start:start + geometry.cluster_size] = payload[:geometry.cluster_size]
    tail = payload[geometry.cluster_size:]
    start = image._cluster_offset(geometry, 4)
    data[start:start + len(tail)] = tail
    start = image._cluster_offset(geometry, 3)
    data[start:start + len(tail)] = tail.replace(b"A", b"B")
    return source, data, geometry, payload


def _no_external_fallback(monkeypatch):
    monkeypatch.setattr(image.shutil, "which", lambda _command: "/fake/tool")
    monkeypatch.setattr(image, "_read_image_listing_with_7z", lambda *_args: pytest.fail("7z must not mask FAT corruption"))
    monkeypatch.setattr(image, "_require_command", lambda *_args: pytest.fail("mtools must not mask FAT corruption"))


@pytest.mark.parametrize("damage", ["signature", "chain"])
def test_good_second_fat_recovers_fragmented_file_and_prepares_only_working_copy(tmp_path, damage):
    source, data, geometry, payload = _fragmented_image(tmp_path)
    if damage == "signature":
        data[geometry.fat_offset] = 0
    else:
        _set_fat(data, geometry, 0, 2, 0)
    source.write_bytes(data)
    diagnostics = {}
    listing = image.read_image_listing(source, diagnostics=diagnostics)
    assert [entry.path for entry in listing.entries] == ["SONG.MID"]
    assert diagnostics["fat_selected_copy"] == 2
    assert diagnostics["fat_copies_structurally_valid"] == [2]
    assert "FAT 2" in diagnostics["fat_selection_note"]
    assert image._read_fat12_file_bytes(source, "SONG.MID") == payload

    output = tmp_path / "working.img"
    result = image.prepare_yamaha_image(source, output)
    assert result.changed and not result.boot_sector_repaired
    assert "FAT 2" in result.note
    repaired = output.read_bytes()
    assert repaired[geometry.fat_offset:geometry.fat_offset + geometry.fat_size] == repaired[geometry.fat_offset + geometry.fat_size:geometry.fat_offset + 2 * geometry.fat_size]
    assert repaired[geometry.root_offset:] == data[geometry.root_offset:]
    assert source.read_bytes() == data
    recovered = image._recover_files_from_fat_context(bytes(data), geometry)
    assert len(recovered) == 1 and recovered[0].data == payload


def test_conflicting_viable_chains_reject_listing_preparation_and_external_extraction(tmp_path, monkeypatch):
    source, data, geometry, _payload = _fragmented_image(tmp_path)
    _set_fat(data, geometry, 0, 2, 3)
    source.write_bytes(data)
    _no_external_fallback(monkeypatch)
    with pytest.raises(image.FloppyImageError, match="conflicting.*Recover Damaged Image"):
        image.read_image_listing(source)
    output = tmp_path / "working.img"
    with pytest.raises(image.FloppyImageError, match="conflicting"):
        image.prepare_yamaha_image(source, output)
    assert not output.exists()
    session = object.__new__(image.FloppyImageSession)
    with pytest.raises(image.FloppyImageError, match="conflicting"):
        session._extract_from_image(source, "SONG.MID", tmp_path / "song.mid")
    assert source.read_bytes() == data


def test_explicit_recovery_reports_ambiguous_fat_payloads_before_carving_or_deduplication(tmp_path, monkeypatch):
    _source, data, geometry, _payload = _fragmented_image(tmp_path)
    _set_fat(data, geometry, 0, 2, 3)
    monkeypatch.setattr(image, "_carve_recovery_files_from_bytes", lambda *_args: pytest.fail("Ambiguous FAT contents must not be hidden by carving"))
    monkeypatch.setattr(image, "_dedupe_recovered_files", lambda *_args: pytest.fail("Ambiguous FAT contents must not be collapsed"))
    with pytest.raises(image.FloppyImageError, match="different contents for SONG.MID"):
        image._recover_files_from_raw_image_bytes(bytes(data))


@pytest.mark.parametrize("source_kind", ["converted-image", "greaseweazle"])
def test_high_level_recovery_does_not_retry_after_conflicting_fat_contents(tmp_path, monkeypatch, source_kind):
    _source, data, geometry, _payload = _fragmented_image(tmp_path)
    _set_fat(data, geometry, 0, 2, 3)
    calls = []
    disk_format = image.DISK_FORMAT_BY_KEY["ibm.720"]
    monkeypatch.setattr(image, "_carve_recovery_files_from_bytes",
                        lambda *_args: pytest.fail("Known FAT alternatives must not be replaced by carved files"))

    def acquire(destination, operation):
        assert not calls, "Known FAT ambiguity must stop format/re-read retries"
        calls.append(operation)
        Path(destination).write_bytes(data)

    if source_kind == "converted-image":
        source = tmp_path / "source.hfe"
        source.write_bytes(b"Synthetic source; conversion is mocked")
        original = source.read_bytes()
        monkeypatch.setattr(image, "_conversion_candidate_formats", lambda *_a, **_k: [
            disk_format, image.DISK_FORMAT_BY_KEY["ibm.1440"],
        ])
        def convert(_source, destination, _format, **_kwargs):
            acquire(destination, "convert")
            return ""
        monkeypatch.setattr(image, "_gw_convert", convert)
        recover = lambda: image.FloppyImageSession._recover_image(source)
    else:
        def read(_source, destination, **_kwargs):
            acquire(destination, "read")
            return {}
        monkeypatch.setattr(image, "_gw_read_floppy", read)
        monkeypatch.setattr(image, "_gw_convert",
                            lambda *_a, **_k: pytest.fail("Ambiguity must stop the archival fallback"))
        source = image.GreaseweazleFloppySource("mock-device", "A", disk_format)
        recover = lambda: image.FloppyImageSession._recover_greaseweazle(source)

    with pytest.raises(image._Fat12RecoveryAmbiguityError, match="different contents for SONG.MID"):
        recover()
    assert len(calls) == 1
    if source_kind == "converted-image":
        assert source.read_bytes() == original


def test_recovery_does_not_collapse_eseq_alternatives_with_same_header_and_offset(tmp_path):
    import io
    import mido
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes

    _source, data, geometry, _payload = _fragmented_image(tmp_path)
    midi = mido.MidiFile(type=0)
    midi.tracks.append(mido.MidiTrack())
    for _ in range(150):
        midi.tracks[0].extend([
            mido.Message("note_on", note=60, velocity=80, time=0),
            mido.Message("note_off", note=60, velocity=0, time=120),
        ])
    buffer = io.BytesIO()
    midi.save(file=buffer)
    payload = convert_midi_bytes_to_eseq_bytes(buffer.getvalue())
    assert geometry.cluster_size < len(payload) <= 2 * geometry.cluster_size
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"SONG    FIL", 2, len(payload))
    start = image._cluster_offset(geometry, 2)
    data[start:start + geometry.cluster_size] = payload[:geometry.cluster_size]
    tail = payload[geometry.cluster_size:]
    for cluster in (3, 4):
        start = image._cluster_offset(geometry, cluster)
        data[start:start + len(tail)] = tail
    # A different note-on velocity leaves both candidate headers and source
    # offsets identical, which the old recovery deduplicator would collapse.
    start = image._cluster_offset(geometry, 3)
    velocity = tail.index(bytes([80]))
    data[start + velocity] = 70
    _set_fat(data, geometry, 0, 2, 3)
    with pytest.raises(image.FloppyImageError, match="different contents for SONG.FIL"):
        image._recover_files_from_raw_image_bytes(bytes(data))


@pytest.mark.parametrize("protected_boot", [False, True])
def test_fast_read_preserves_copies_until_complete_chain_selection(tmp_path, monkeypatch, protected_boot):
    _source, data, geometry, payload = _fragmented_image(tmp_path)
    data[geometry.fat_offset] = 0
    if protected_boot:
        data[:512] = b"\xf6" * 512

    class Device:
        def read_at(self, offset, size, _label):
            return bytes(data[offset:offset + size])

    monkeypatch.setattr(image, "_open_block_device_for_read", lambda *_args: Device())
    monkeypatch.setattr(image, "_close_block_device", lambda *_args: None)
    output = tmp_path / "fast-working.img"
    diagnostics = {}
    result = image._read_floppy_device_fast_image("mock", output, len(data), diagnostics=diagnostics)
    assert diagnostics["fat_selected_copy"] == 2
    assert "FAT 2" in result.note
    assert image._read_fat12_file_bytes(output, "SONG.MID") == payload


def test_recovery_uses_second_fat_with_protected_filled_boot_sector(tmp_path):
    _source, data, geometry, payload = _fragmented_image(tmp_path)
    data[:512] = b"\xf6" * 512
    data[geometry.fat_offset] = 0
    recovered = image._recover_files_from_fat_context(bytes(data), geometry)
    assert len(recovered) == 1 and recovered[0].data == payload


def test_unreadable_fat_sectors_are_not_treated_as_valid_zero_allocations(tmp_path):
    _source, data, geometry, _payload = _fragmented_image(tmp_path)
    root_dir = data[geometry.root_offset:geometry.root_offset + geometry.root_size]
    diagnostics = {}
    fat, _note = image._select_fat12_copy(
        data, geometry, root_dir, diagnostics=diagnostics,
        unreadable_ranges=[(geometry.fat_offset + 512, 512)],
    )
    assert diagnostics["fat_selected_copy"] == 2
    assert "unreadable" in diagnostics["fat_copy_errors"][0]
    assert fat == data[geometry.fat_offset + geometry.fat_size:geometry.fat_offset + 2 * geometry.fat_size]


@pytest.mark.skipif(not hasattr(image.os, "pread"), reason="POSIX block-device reader")
def test_metadata_only_block_listing_uses_readable_second_fat(tmp_path, monkeypatch):
    source, data, geometry, _payload = _fragmented_image(tmp_path)
    source.write_bytes(data)
    original_pread = image.os.pread

    def read(fd, size, offset):
        assert offset + size <= geometry.data_offset, "Metadata listing must not read file contents"
        if geometry.fat_offset <= offset < geometry.fat_offset + geometry.fat_size:
            raise OSError("unreadable first FAT")
        return original_pread(fd, size, offset)

    monkeypatch.setattr(image.os, "pread", read)
    diagnostics = {}
    listing = image._read_fat12_block_device_listing(source, diagnostics=diagnostics)
    assert [entry.path for entry in listing.entries] == ["SONG.MID"]
    assert diagnostics["fat_selected_copy"] == 2
    assert source.read_bytes() == data


def test_unreferenced_allocation_disagreement_is_not_silently_selected(tmp_path):
    source, data, geometry, _payload = _fragmented_image(tmp_path)
    _set_fat(data, geometry, 0, 10, 0xfff)
    source.write_bytes(data)
    with pytest.raises(image.FloppyImageError, match="conflicting"):
        image.read_image_listing(source)


def test_equivalent_eoc_markers_and_fat_padding_do_not_create_ambiguity(tmp_path):
    source, data, geometry, payload = _fragmented_image(tmp_path)
    _set_fat(data, geometry, 0, 4, 0xff8)
    data[geometry.fat_offset + geometry.fat_size - 1] = 0x7b
    source.write_bytes(data)
    assert image._read_fat12_file_bytes(source, "SONG.MID") == payload


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("location", ["first", "next"])
@pytest.mark.parametrize("appended_bytes", [False, True])
def test_listing_rejects_clusters_outside_geometry_even_with_trailing_image_data(tmp_path, monkeypatch, kind, location, appended_bytes):
    source, data, geometry, _payload = _fragmented_image(tmp_path)
    invalid_cluster = image._fat12_data_cluster_count(geometry) + 2
    first = invalid_cluster if location == "first" else 2
    attr = 0x10 if kind == "directory" else 0x20
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"BROKEN     ", first, 0 if kind == "directory" else 4, attr)
    if location == "next":
        for copy in range(geometry.num_fats):
            _set_fat(data, geometry, copy, 2, invalid_cluster)
    if appended_bytes:
        data.extend(bytes(geometry.cluster_size * 4))
    source.write_bytes(data)
    _no_external_fallback(monkeypatch)
    with pytest.raises(image.FloppyImageError, match="outside.*data area"):
        image.read_image_listing(source)
    assert source.read_bytes() == data


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_nonempty_file_and_directory_cannot_start_at_cluster_zero(tmp_path, kind):
    source, data, geometry, _payload = _fragmented_image(tmp_path)
    data[geometry.root_offset:geometry.root_offset + 32] = image._dos_directory_entry(b"BROKEN     ", 0, 0 if kind == "directory" else 4, 0x10 if kind == "directory" else 0x20)
    source.write_bytes(data)
    with pytest.raises(image.FloppyImageError, match="outside.*data area"):
        image.read_image_listing(source)
