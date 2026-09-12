"""Real IMG/HFE operations on Windows, with mtools removed from PATH."""

from contextlib import contextmanager
from pathlib import Path
import sys

import pytest

from aps_midi_prep_tool_app import floppy_image as image
from aps_midi_prep_tool_app.emulator_image_builder import discover_song_files, sanitize_image_set_name
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import PIANODIR_COUNT_OFFSET
from aps_midi_prep_tool_app.midi_metadata import extract_eseq_title_from_file
from scripts.build_windows_test_kit import midi_bytes


FORMAT = image.DISK_FORMAT_BY_KEY["ibm.720"]


@contextmanager
def opened(path):
    session = image.FloppyImageSession.load(str(path))
    temporary = Path(session.temp_dir)
    try:
        yield session
    finally:
        session.cleanup()
    assert not temporary.exists(), "Session left temporary files or open handles behind"


def contents(session):
    return {entry.path: Path(session.extract_file(entry.path)).read_bytes()
            for entry in session.list_entries().entries if not entry.directory}


def make_image(tmp_path, files, extension="img"):
    source = tmp_path / "Source songs é with spaces"
    source.mkdir(exist_ok=True)
    specs = []
    for name, data in files.items():
        host = source / name
        host.write_bytes(data)
        specs.append({"host_path": str(host), "image_path": name})
    output = tmp_path / ("Test disk é." + extension)
    assert image.create_floppy_images_from_files(specs, str(output), extension, FORMAT) == [str(output)]
    assert all((source / name).read_bytes() == data for name, data in files.items())
    return output


@pytest.mark.parametrize("image_extension", ["img", "hfe"], indirect=True)
def test_create_modify_reopen_and_clean_export(tmp_path, image_extension):
    songs = {f"SONG{n}.FIL": convert_midi_bytes_to_eseq_bytes(midi_bytes(f"Test Song {n}"))
             for n in range(1, 4)}
    sidecars = {"NOTES.TXT": b"Keep these notes\r\n", "EXTRA.DAT": bytes(range(256)),
                "PSONG.MNG": b"Original song management", "PDISK.MNG": b"Original disk management"}
    path = make_image(tmp_path, {**songs, **sidecars}, image_extension)
    if image_extension == "img":
        assert path.stat().st_size == 737280
    else:
        header = path.read_bytes()[:512]
        assert header[:8] == b"HXCPICFE"
        assert header[9:11] == bytes((80, 2))
        assert int.from_bytes(header[12:14], "little") == 250
        assert header[16] == 0  # DD interface, independently of app constants.
    with opened(path) as session:
        assert contents(session) == {**songs, **sidecars}
        assert session.disk_format.key == "ibm.720"
        assert 0 < session.list_entries().free_space < 737280
        added = tmp_path / "Added song.fil"
        added.write_bytes(convert_midi_bytes_to_eseq_bytes(midi_bytes("Added Song")))
        session.commit_to_source(
            renames={"SONG1.FIL": "RENAMED.FIL"}, deletes={"SONG2.FIL"},
            additions={"ADDED.FIL": str(added)}, title_edits={"SONG3.FIL": "Edited title"},
            generate_pianodir=True, eseq_variant="disklavier",
        )
    saved_bytes = path.read_bytes()
    clean = tmp_path / ("Clean delivery." + image_extension)
    with opened(path) as session:
        payloads = contents(session)
        assert set(payloads) == {"RENAMED.FIL", "SONG3.FIL", "ADDED.FIL", "PIANODIR.FIL", *sidecars}
        assert payloads["RENAMED.FIL"] == songs["SONG1.FIL"]
        assert all(payloads[name] == data for name, data in sidecars.items())
        assert extract_eseq_title_from_file(session.extract_file("SONG3.FIL")) == "Edited title"
        catalog = payloads["PIANODIR.FIL"]
        assert int.from_bytes(catalog[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") == 4
        session.export_to(str(clean), image_extension, generate_pianodir=True, eseq_variant="disklavier")
    assert path.read_bytes() == saved_bytes
    with opened(clean) as session:
        delivered = contents(session)
        assert set(delivered) == {"RENAMED.FIL", "SONG3.FIL", "ADDED.FIL", "PIANODIR.FIL"}
        assert all(delivered[name] == payloads[name] for name in delivered)


@pytest.mark.parametrize("image_extension", ["hfe"], indirect=True)
def test_img_hfe_img_roundtrip_preserves_every_payload(tmp_path, image_extension):
    files = {f"SONG{n}.MID": midi_bytes(f"Distinct title {n}") for n in range(1, 4)}
    original = make_image(tmp_path, files)
    original_bytes = original.read_bytes()
    hfe = tmp_path / "DSKA0000.HFE"
    restored = tmp_path / "from-hfe.img"
    with opened(original) as session:
        free_space = session.list_entries().free_space
        session.export_to(str(hfe), "hfe")
    with opened(hfe) as session:
        assert contents(session) == files
        assert session.list_entries().free_space == free_space
        session.export_to(str(restored), "img")
    with opened(restored) as session:
        assert contents(session) == files
        assert session.list_entries().free_space == free_space
    assert original.read_bytes() == original_bytes


@pytest.mark.parametrize("image_extension", ["img", "hfe"], indirect=True)
def test_full_image_overflow_preserves_existing_output_and_can_retry(tmp_path, image_extension):
    path = make_image(tmp_path, {"SMALL.MID": midi_bytes("Small")}, image_extension)
    before = path.read_bytes()
    with opened(path) as session:
        capacity = session.list_entries().free_space
        filler = tmp_path / "FILL.DAT"
        filler.write_bytes(bytes(capacity))
        near_full = tmp_path / ("Full." + image_extension)
        session.export_to(str(near_full), image_extension, additions={"FILL.DAT": str(filler)})
        with opened(near_full) as full:
            assert full.list_entries().free_space == 0
            assert contents(full)["FILL.DAT"] == filler.read_bytes()
        full_bytes = near_full.read_bytes()
        filler.write_bytes(bytes(capacity + session.list_entries().cluster_size))
        with pytest.raises(image.FloppyImageError, match="(?i)(full|space|capacity|large)"):
            session.export_to(str(near_full), image_extension, additions={"FILL.DAT": str(filler)})
        assert near_full.read_bytes() == full_bytes
        assert path.read_bytes() == before
        session.export_to(str(near_full), image_extension)
        with opened(near_full) as retried:
            assert contents(retried) == {"SMALL.MID": midi_bytes("Small")}


def test_all_five_bundled_mtools_execute_with_no_mtools_on_path(tmp_path, bundled_mtools):
    path = make_image(tmp_path, {"SONG.MID": midi_bytes("Original")})
    run = image._run_command
    run([image._require_command("mren"), "-i", str(path), "::SONG.MID", "::RENAMED.MID"], "Rename failed")
    assert "RENAMED" in run([image._require_command("mdir"), "-i", str(path), "::"], "List failed")
    run([image._require_command("mdel"), "-i", str(path), "::RENAMED.MID"], "Delete failed")
    assert not image.read_image_listing(str(path)).entries


def test_filename_discovery_on_case_insensitive_windows(tmp_path, bundled_mtools):
    source = tmp_path / "Input"
    source.mkdir()
    names = ["Long Song Name Number One.mid", "Test Song.mid", "NOEXTENSION"]
    for name in names + ["SONG.MID.bak", "SONG.FIL.old", "SONG~", ".tmp.mid", ".hidden.mid"]:
        (source / name).write_bytes(midi_bytes(name))
    # Windows aliases these paths; do not accidentally overwrite a separate fixture.
    assert (source / "test song.mid").samefile(source / "Test Song.mid")
    assert {Path(path).name for path in discover_song_files(source)} == set(names)
    for reserved in ("CON", "AUX", "NUL", "COM1", "LPT1", "CON.mid", "AUX.mid"):
        name = sanitize_image_set_name(reserved)
        folder = tmp_path / name
        folder.mkdir(exist_ok=True)
        (folder / "probe.txt").write_text("Usable on Windows", encoding="utf-8")


def test_reopen_in_fresh_process_releases_windows_file_handles(tmp_path, bundled_mtools):
    files = {"SONG.MID": midi_bytes("Fresh process")}
    path = make_image(tmp_path, files)
    root = Path(__file__).resolve().parents[2]
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aps_midi_prep_tool_app.floppy_image import FloppyImageSession
s = FloppyImageSession.load(sys.argv[2])
try:
    assert Path(s.extract_file('SONG.MID')).read_bytes() == Path(sys.argv[3]).read_bytes()
finally:
    s.cleanup()
"""
    image._run_command([sys.executable, "-c", code, str(root), str(path),
                        str(tmp_path / "Source songs é with spaces/SONG.MID")], "Reopen failed", timeout=20)
    renamed = path.with_name("Reopened and released.img")
    path.rename(renamed)
    renamed.unlink()


def test_damaged_boot_recovery_preserves_source_and_reconstructs_readable_image(tmp_path, bundled_mtools):
    song = convert_midi_bytes_to_eseq_bytes(midi_bytes("Recoverable song"))
    path = make_image(tmp_path, {"SONG.FIL": song, "NOTES.TXT": b"Retained sidecar"})
    with opened(path) as session:
        session.commit_to_source(generate_pianodir=True, eseq_variant="disklavier")
        expected = contents(session)
    damaged = bytearray(path.read_bytes())
    damaged[:512] = bytes(512)
    path.write_bytes(damaged)
    recovered = image.FloppyImageSession.recover("image", str(path))
    temp_dir = Path(recovered.temp_dir)
    output = tmp_path / "Recovered.img"
    try:
        assert contents(recovered) == expected
        recovered.export_to(str(output), "img")
    finally:
        recovered.cleanup()
    assert not temp_dir.exists()
    assert path.read_bytes() == damaged
    with opened(output) as session:
        assert contents(session) == expected


def test_severely_truncated_image_has_actionable_failure_without_mutation(tmp_path, bundled_mtools):
    path = tmp_path / "Truncated.img"
    before = b"not a usable floppy image" + bytes(256)
    path.write_bytes(before)
    with pytest.raises(image.FloppyImageError) as failure:
        image.FloppyImageSession.load(str(path))
    assert str(failure.value).strip()
    assert path.read_bytes() == before
