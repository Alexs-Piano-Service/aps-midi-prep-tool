"""OEM short filenames retain their identity through image repair and export."""

import io
from contextlib import redirect_stdout
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


def _oem_image(tmp_path, *, protected_boot=False, raw_names=OEM_NAMES, payloads=None):
    source = tmp_path / "original.img"
    payloads = payloads or (_song("First song", 60), _song("Second song", 67))
    specs = []
    for index, payload in enumerate(payloads):
        host = tmp_path / f"SONG{index}.FIL"
        host.write_bytes(payload)
        specs.append({"host_path": str(host), "image_path": host.name})
    image.create_floppy_images_from_files(specs, source, "img", image.DISK_FORMAT_BY_KEY["ibm.720"])
    data, geometry, _fat, root = image._read_fat12_image_context(source)
    data = bytearray(data)
    for index, raw_name in enumerate(raw_names):
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
        # Windows CI tees stdout to a strict legacy-code-page stream. Console
        # diagnostics must not prevent exporting songs with OEM filenames.
        with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as console, redirect_stdout(console):
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


@pytest.mark.parametrize("raw_name", (OEM_NAMES[0], b"\x05FILE   FIL", b"\xffFILE   FIL", b"CAF\x82    FIL"))
@pytest.mark.parametrize("operation", ("rename", "delete", "replace", "title"))
def test_mtools_edits_find_the_exact_oem_filename(tmp_path, operation, raw_name):
    raw_names = (raw_name, OEM_NAMES[1])
    source, payloads = _oem_image(tmp_path, raw_names=raw_names)
    original = source.read_bytes()
    names = [image._decode_dos_directory_name(name) for name in raw_names]
    session = image.FloppyImageSession.load(source)
    try:
        # Bound failures so a protection prompt or failed lookup cannot hang CI.
        def run(args, message, cancel_callback=None):
            # Catch Windows' narrow-argv limitation even on Linux CI.
            assert all(arg.isascii() for arg in args if arg.startswith("::/"))
            return image._run_command(args, message, cancel_callback=cancel_callback, timeout=5)
        session._run_mtools = run
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


@pytest.mark.parametrize("operation", ("rename", "add"))
def test_oem_destination_preserves_exact_short_name_bytes(tmp_path, operation):
    source, payloads = _oem_image(tmp_path)
    original = source.read_bytes()
    old_name = image._decode_dos_directory_name(OEM_NAMES[0])
    new_raw = b"\x05NEW    FIL"
    new_name = image._decode_dos_directory_name(new_raw)
    session = image.FloppyImageSession.load(source)
    try:
        replacement = tmp_path / "new.mid"
        replacement.write_bytes(b"new payload")
        kwargs = ({"renames": {old_name: new_name}} if operation == "rename" else
                  {"additions": {new_name: str(replacement)}})
        output = session.create_modified_image(**kwargs)
        expected = payloads[0] if operation == "rename" else replacement.read_bytes()
        assert image._read_fat12_file_bytes(output, new_name) == expected
        assert image._read_fat12_file_bytes(output, image._decode_dos_directory_name(OEM_NAMES[1])) == payloads[1]
        _, _, _, root = image._read_fat12_image_context(output)
        entry = next(item for item in image._iter_fat_directory_entries(root) if item["name"] == new_name)
        assert root[entry["offset"]:entry["offset"] + 11] == new_raw
        assert source.read_bytes() == original
        assert Path(session.working_img_path).read_bytes() == original
    finally:
        session.cleanup()


@pytest.mark.parametrize("failure", (image.FloppyImageError, image.FloppyOperationCancelled))
def test_failed_oem_command_restores_image_and_avoids_alias_collisions(tmp_path, failure):
    source, _ = _oem_image(tmp_path, raw_names=(OEM_NAMES[0], b"APS00000TMP"))
    original = source.read_bytes()
    name = image._decode_dos_directory_name(OEM_NAMES[0])

    def fail(args, message, cancel_callback=None):
        assert args[-2].isascii()
        assert args[-2] not in {"::/APS00000.TMP", "::/APS00001.TMP"}
        raise failure("Interrupted edit")

    with pytest.raises(failure, match="Interrupted edit"):
        image._run_mtools_image_command(
            fail, ["mren", "-i", str(source), image.mtools_path(name), "::/APS00001.TMP"], "Rename failed",
        )
    assert source.read_bytes() == original


def test_oem_edit_in_fragmented_directory_restores_parent_and_file_names(tmp_path):
    from test_repair_file_visibility import nested_image

    source, data, geometry, _ = nested_image(tmp_path)
    data = bytearray(data)
    data[geometry.root_offset:geometry.root_offset + 11] = OEM_NAMES[0]
    nested_offset = image._cluster_offset(geometry, 5)
    data[nested_offset:nested_offset + 11] = OEM_NAMES[1]
    source.write_bytes(data)
    parent, name = (image._decode_dos_directory_name(raw) for raw in OEM_NAMES)
    session = image.FloppyImageSession.load(source)
    try:
        replacement = tmp_path / "replacement.mid"
        replacement.write_bytes(b"REPLACED")
        output = session.create_modified_image(replacements={f"{parent}/{name}": str(replacement)})
        assert image._read_fat12_file_bytes(output, f"{parent}/{name}") == b"REPLACED"
        assert image._read_fat12_file_bytes(output, f"{parent}/DEEP/DEEP.FIL") == b"DEEP"
        result = Path(output).read_bytes()
        assert result[geometry.root_offset:geometry.root_offset + 32] == data[geometry.root_offset:geometry.root_offset + 32]
        assert source.read_bytes() == data
        assert Path(session.working_img_path).read_bytes() == data
    finally:
        session.cleanup()


@pytest.mark.parametrize("selected", (0, 1))
@pytest.mark.parametrize("operation", ("rename", "delete", "replace", "title", "unprotect"))
def test_unicode_uppercase_collision_never_selects_neighbor(tmp_path, selected, operation):
    raw_names = (b"STRA\xe1E  FIL", b"STRASSE FIL")
    source, payloads = _oem_image(tmp_path, raw_names=raw_names)
    original = source.read_bytes()
    names = [image._decode_dos_directory_name(raw) for raw in raw_names]
    assert names[0].upper() == names[1].upper()
    listing = image.read_image_listing(source)
    assert {entry.short_name_bytes for entry in listing.entries} == set(raw_names)
    for name, payload in zip(names, payloads):
        assert image._read_fat12_file_bytes(source, name) == payload
    session = image.FloppyImageSession.load(source)
    try:
        replacement = tmp_path / "new.fil"
        replacement.write_bytes(_song("New", 72))
        target, other = names[selected], names[1 - selected]
        if operation == "unprotect":
            private = Path(session.working_img_path)
            data, geometry, _, root = image._read_fat12_image_context(private)
            protected = bytearray(data)
            for entry in image._iter_fat_directory_entries(root):
                protected[geometry.root_offset + entry["offset"] + 11] = 0x27
            private.write_bytes(protected)
            image._clear_fat12_file_protection(private, target)
            entries = {entry.path: entry for entry in image.read_image_listing(private).entries}
            assert entries[target].attributes == "20"
            assert entries[other].attributes == "27"
            return
        kwargs = {
            "rename": {"renames": {target: "RENAMED.FIL"}},
            "delete": {"deletes": {target}},
            "replace": {"replacements": {target: str(replacement)}},
            "title": {"title_edits": {target: "Changed"}},
        }[operation]
        output = session.create_modified_image(**kwargs)
        assert image._read_fat12_file_bytes(output, other) == payloads[1 - selected]
        if operation == "delete":
            assert {entry.path for entry in image.read_image_listing(output).entries} == {other}
        else:
            result = image._read_fat12_file_bytes(output, "RENAMED.FIL" if operation == "rename" else target)
            if operation == "title":
                assert b"Changed" in result and result[0x7C:] == payloads[selected][0x7C:]
            else:
                assert result == (replacement.read_bytes() if operation == "replace" else payloads[selected])
        assert source.read_bytes() == original
    finally:
        session.cleanup()


def test_ambiguous_case_insensitive_lookup_is_rejected(tmp_path, monkeypatch):
    source, _ = _oem_image(tmp_path, raw_names=(b"Foo     FIL", b"FOO     FIL"))
    original = source.read_bytes()
    for operation in (image._read_fat12_file_bytes, image._clear_fat12_file_protection):
        with pytest.raises(image.FloppyImageError, match="Ambiguous DOS filename"):
            operation(source, "foo.fil")
    assert source.read_bytes() == original
    session = image.FloppyImageSession.load(source)
    try:
        monkeypatch.setattr(session, "_run_mtools", lambda *_a, **_k: pytest.fail("Ambiguous lookup must not fall back to mtools"))
        with pytest.raises(image.AmbiguousDosFilenameError):
            session.extract_file("foo.fil")
    finally:
        session.cleanup()


@pytest.mark.parametrize("name", ("Foo.FIL", "FOO.FIL"))
@pytest.mark.parametrize("operation", ("delete", "rename", "replace", "title"))
def test_exact_case_collision_cannot_edit_multiple_files(tmp_path, monkeypatch, name, operation):
    source, payloads = _oem_image(tmp_path, raw_names=(b"Foo     FIL", b"FOO     FIL"))
    original = source.read_bytes()
    session = image.FloppyImageSession.load(source)
    try:
        # The native reader can identify both exact records. mtools cannot:
        # deleting Foo.FIL would also delete FOO.FIL without this guard.
        for filename, payload in zip(("Foo.FIL", "FOO.FIL"), payloads):
            assert Path(session.extract_file(filename)).read_bytes() == payload
        monkeypatch.setattr(session, "_run_mtools", lambda *_a, **_k: pytest.fail("Ambiguous mutations must not reach mtools"))
        replacement = tmp_path / "replacement.fil"
        replacement.write_bytes(_song("Replacement", 72))
        kwargs = {
            "delete": {"deletes": {name}},
            "rename": {"renames": {name: "RENAMED.FIL"}},
            "replace": {"replacements": {name: str(replacement)}},
            "title": {"title_edits": {name: "Changed"}},
        }[operation]
        with pytest.raises(image.AmbiguousDosFilenameError, match="Ambiguous DOS filename"):
            session.create_modified_image(**kwargs)
        assert source.read_bytes() == original
        assert Path(session.working_img_path).read_bytes() == original
        assert not list(Path(session.temp_dir).glob("modified_*.img"))
    finally:
        session.cleanup()


def test_catalog_keeps_exact_short_names_that_unicode_uppercase_would_merge(tmp_path):
    raw_names = (b"STRA\xe1E  FIL", b"STRASSE FIL")
    source, _ = _oem_image(tmp_path, raw_names=raw_names)
    session = image.FloppyImageSession.load(source)
    try:
        output = session.create_modified_image(generate_pianodir=True)
        catalog = image._read_fat12_file_bytes(output, "PIANODIR.FIL")
        names = {catalog[offset:offset + 11] for offset in
                 (len(PIANODIR_HEADER), len(PIANODIR_HEADER) + PIANODIR_TRACK_SIZE)}
        assert names == set(raw_names)
    finally:
        session.cleanup()


def test_clavinova_catalog_preserves_existing_dos_bytes_without_case_expansion(tmp_path):
    from aps_midi_prep_tool_app.eseq_converter import ESEQ_CONTAINER_CLAVINOVA_MDA
    from aps_midi_prep_tool_app.eseq_pianodir import CLAVINOVA_MUSICDIR_HEADER_SIZE, CLAVINOVA_MUSICDIR_RECORD_SIZE
    from test_save_as_overwrite import _song as midi_song

    raw_names = (b"STRA\xe1E  MDA", b"STRASSE MDA")
    payloads = tuple(convert_midi_bytes_to_eseq_bytes(
        midi_song("Song", 60 + index), filename_hint=f"SONG{index}.MDA",
        container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA,
    ) for index in range(2))
    source, _ = _oem_image(tmp_path, raw_names=raw_names, payloads=payloads)
    session = image.FloppyImageSession.load(source)
    try:
        output = session.create_modified_image(generate_pianodir=True, eseq_variant="clavinova")
        catalog = image._read_fat12_file_bytes(output, "MUSIC.DIR")
        start = CLAVINOVA_MUSICDIR_HEADER_SIZE
        assert {catalog[offset:offset + 11] for offset in (start, start + CLAVINOVA_MUSICDIR_RECORD_SIZE)} == set(raw_names)
    finally:
        session.cleanup()
