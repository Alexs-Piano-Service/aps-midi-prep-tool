"""OEM short filenames retain their identity through image repair and export."""

import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.boot_sector_repair import inspect_boot_sector_image
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_HEADER, PIANODIR_TRACK_SIZE, build_dos83_name_bytes, decode_dos83_name,
)


# Different names that both became '3������4' when decoded as ASCII.
OEM_NAMES = (b"3\xdc\xd9\xc2\xc0\xde\xb24   ", b"3\xdd\xd9\xc2\xc0\xde\xb24   ")


def _song(title, note):
    song = mido.MidiFile(type=0)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.Message("note_on", note=note, velocity=80),
        mido.Message("note_off", note=note, time=480),
    ]))
    output = io.BytesIO()
    song.save(file=output)
    return convert_midi_bytes_to_eseq_bytes(output.getvalue()) + bytes(177)


def _oem_image(tmp_path, *, protected_boot=False):
    source = tmp_path / "original.img"
    payloads = (_song("First song", 60), _song("Second song", 67))
    specs = []
    for index, payload in enumerate(payloads):
        host = tmp_path / f"SONG{index}.FIL"
        host.write_bytes(payload)
        specs.append({"host_path": str(host), "image_path": host.name})
    image.create_floppy_images_from_files(specs, source, "img", image.DISK_FORMAT_BY_KEY["ibm.720"])
    data, geometry, _fat, root = image._read_fat12_image_context(source)
    data = bytearray(data)
    for index, raw_name in enumerate(OEM_NAMES):
        entry = next(entry for entry in image._iter_fat_directory_entries(root)
                     if entry["name"] == f"SONG{index}.FIL")
        offset = geometry.root_offset + entry["offset"]
        data[offset:offset + 11] = raw_name
    if protected_boot:
        data[:512] = b"\xe5" * 512
    source.write_bytes(data)
    return source, payloads


@pytest.mark.parametrize("raw_name", (*OEM_NAMES, b"\x05FILE   FIL", b"\xffFILE   FIL", b"CAF\x82    FIL"))
def test_short_name_decoding_preserves_oem_bytes_and_escaped_e5(raw_name):
    expected = (b"\xe5" + raw_name[1:]) if raw_name[0] == 0x05 else raw_name
    stem, extension = expected[:8].strip(b" "), expected[8:11].strip(b" ")
    expected_path = stem + (b"." + extension if extension else b"")
    decoded = image._decode_dos_directory_name(raw_name)
    assert "\ufffd" not in decoded
    assert decoded.encode("cp850") == expected_path
    assert image._entry_name_looks_plausible(raw_name)


@pytest.mark.parametrize("bad_byte", (0x01, ord("/"), ord("\\"), ord("*"), ord("?")))
def test_non_ascii_support_does_not_accept_invalid_dos_name_bytes(bad_byte):
    assert not image._entry_name_looks_plausible(bytes([bad_byte]) + b"FILE   FIL")


def test_protected_image_with_oem_names_only_needs_boot_repair(tmp_path):
    source, payloads = _oem_image(tmp_path, protected_boot=True)
    original = source.read_bytes()
    # Both the boot-only utility and normal loading must preserve the directory,
    # including declared file sizes and padding beyond the E-SEQ event stream.
    repair = inspect_boot_sector_image(source)
    assert repair.repaired[512:] == original[512:]
    session = image.FloppyImageSession.load(source)
    try:
        assert Path(session.working_img_path).read_bytes()[512:] == original[512:]
        listing = session.list_entries().entries
        assert len({entry.path for entry in listing}) == 2
        for raw_name, payload in zip(OEM_NAMES, payloads):
            name = raw_name.strip(b" ").decode("cp850")
            assert Path(session.extract_file(name)).read_bytes() == payload
        assert source.read_bytes() == original
    finally:
        session.cleanup()


def test_regenerated_catalog_keeps_original_oem_filenames(tmp_path):
    source, payloads = _oem_image(tmp_path, protected_boot=True)
    original = source.read_bytes()
    session = image.FloppyImageSession.load(source)
    try:
        output = tmp_path / "catalog.img"
        session.export_to_images(str(output), "img", session.disk_format, generate_pianodir=True)
        catalog = image._read_fat12_file_bytes(output, "PIANODIR.FIL")
        names = {catalog[offset:offset + 11] for offset in
                 (len(PIANODIR_HEADER), len(PIANODIR_HEADER) + PIANODIR_TRACK_SIZE)}
        assert names == set(OEM_NAMES)
        for raw_name, payload in zip(OEM_NAMES, payloads):
            name = decode_dos83_name(raw_name)
            assert build_dos83_name_bytes(name) == raw_name
            assert image._read_fat12_file_bytes(output, name) == payload
        assert source.read_bytes() == original
    finally:
        session.cleanup()


@pytest.mark.parametrize("operation", ("rename", "delete", "replace", "title"))
def test_mtools_edits_find_the_exact_oem_filename(tmp_path, operation):
    source, payloads = _oem_image(tmp_path)
    original = source.read_bytes()
    names = [name.strip(b" ").decode("cp850") for name in OEM_NAMES]
    session = image.FloppyImageSession.load(source)
    try:
        # Bound failures so a protection prompt or failed lookup cannot hang CI.
        session._run_mtools = lambda args, message, cancel_callback=None: image._run_command(
            args, message, cancel_callback=cancel_callback, timeout=5,
        )
        replacement = tmp_path / "replacement.fil"
        replacement.write_bytes(_song("Replaced", 72))
        kwargs = {
            "rename": {"renames": {names[0]: "34"}},
            "delete": {"deletes": {names[0]}},
            "replace": {"replacements": {names[0]: str(replacement)}},
            "title": {"title_edits": {names[0]: "Changed"}},
        }[operation]
        output = tmp_path / "export.img"
        session.export_to_images(str(output), "img", session.disk_format, **kwargs)
        entries = {entry.path for entry in image.read_image_listing(output).entries}
        assert image._read_fat12_file_bytes(output, names[1]) == payloads[1]
        if operation == "delete":
            assert entries == {names[1]}
        elif operation == "rename":
            assert entries == {"34", names[1]}
            assert image._read_fat12_file_bytes(output, "34") == payloads[0]
        else:
            assert entries == set(names)
            if operation == "replace":
                assert image._read_fat12_file_bytes(output, names[0]) == replacement.read_bytes()
            else:
                result = image._read_fat12_file_bytes(output, names[0])
                assert b"Changed" in result
                assert result[0x7C:] == payloads[0][0x7C:]
        assert source.read_bytes() == original
        assert Path(session.working_img_path).read_bytes() == original
    finally:
        session.cleanup()
