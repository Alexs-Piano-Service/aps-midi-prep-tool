"""E-SEQ delivery exports prune payloads; ordinary saves preserve them."""

import struct
from pathlib import Path

import pytest

from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    ESEQ_CONTAINER_DISKLAVIER,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_COUNT_OFFSET,
    parse_music_dir,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageSession,
    create_floppy_images_from_files,
    read_image_listing,
)


def _midi():
    track = b"\x00\x90\x3C\x50\x60\x80\x3C\x00\x00\xFF\x2F\x00"
    return struct.pack(">4sIHHH", b"MThd", 6, 0, 1, 96) + b"MTrk" + len(track).to_bytes(4, "big") + track


def _image(tmp_path, files):
    sources = {}
    for name, data in files.items():
        path = tmp_path / name
        path.write_bytes(data)
        sources[name] = str(path)
    image_path = tmp_path / "source.img"
    create_floppy_images_from_files(
        [{"host_path": path, "image_path": name} for name, path in sources.items()],
        str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    return image_path, sources


@pytest.mark.parametrize("variant", ("disklavier", "clavinova"))
@pytest.mark.parametrize("save_route", ("modified", "repacked", "export"))
def test_eseq_exports_filter_final_payloads_and_catalogs(tmp_path, variant, save_route):
    container = ESEQ_CONTAINER_DISKLAVIER if variant == "disklavier" else ESEQ_CONTAINER_CLAVINOVA_MDA
    other_container = ESEQ_CONTAINER_CLAVINOVA_MDA if variant == "disklavier" else ESEQ_CONTAINER_DISKLAVIER
    song = convert_midi_bytes_to_eseq_bytes(_midi(), container_variant=container, title_override="Kept song")
    other_song = convert_midi_bytes_to_eseq_bytes(_midi(), container_variant=other_container)
    original_files = {
        "NOEXT": song,
        "SONG.FIL": song,
        "CONVERT.MID": _midi(),
        "UNUSED.MID": _midi(),
        "OTHER.FIL": other_song,
        "BOGUS.FIL": b"This filename does not make it an E-SEQ song.",
        "PDISK.MNG": b"Disk title input",
        "PSONG.MNG": b"Song title input",
        "PIANODIR.FIL": b"Old catalog",
        "MUSIC.DIR": b"Other old catalog",
    }
    image_path, sources = _image(tmp_path, original_files)
    original_image = image_path.read_bytes()
    converted = tmp_path / "converted-output"
    converted.write_bytes(song)
    large_sidecar = tmp_path / "added-management-file"
    large_sidecar.write_bytes(b"x" * (2 * 1024 * 1024))
    changes = dict(
        generate_pianodir=True,
        eseq_variant=variant,
        replacements={"CONVERT.MID": str(converted)},
        renames={"CONVERT.MID": "CONVERT.FIL", "PDISK.MNG": "DISK.MNG"},
        additions={"ADDED": str(converted), "EXTRA.MNG": str(large_sidecar)},
        title_edits={"PDISK.MNG": "Catalog title", "EXTRA.MNG": "Another catalog title"},
        order_key_edits={"PSONG.MNG": b"UNUSED  MNG\x00", "EXTRA.MNG": b"EXTRA   MNG\x00"},
    )
    session = FloppyImageSession.load(str(image_path))
    saved_session = None
    try:
        if save_route == "export":
            output = tmp_path / "export.img"
            session.export_to(str(output), "img", **changes)
            saved_session = FloppyImageSession.load(str(output))
        elif save_route == "repacked":
            output = tmp_path / "repacked.img"
            paths = session.export_to_images(
                str(output), "img", DISK_FORMAT_BY_KEY["ibm.1440"], **changes,
            )
            assert paths == [str(output)]
            saved_session = FloppyImageSession.load(str(output))
        else:
            output = session.create_modified_image(**changes, clean_eseq_delivery=True)
            saved_session = FloppyImageSession.load(output)

        directory = "PIANODIR.FIL" if variant == "disklavier" else "MUSIC.DIR"
        expected_songs = {"NOEXT", "SONG.FIL", "CONVERT.FIL", "ADDED"}
        assert {entry.path for entry in saved_session.list_entries().entries} == expected_songs | {directory}
        for name in expected_songs:
            assert Path(saved_session.extract_file(name)).read_bytes() == song
        catalog = Path(saved_session.extract_file(directory)).read_bytes()
        if variant == "disklavier":
            assert int.from_bytes(catalog[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") == 5
        else:
            assert len(parse_music_dir(catalog)) == 4
        assert image_path.read_bytes() == original_image
        assert all(Path(sources[name]).read_bytes() == data for name, data in original_files.items())
        assert converted.read_bytes() == song
    finally:
        if saved_session is not None and saved_session is not session:
            saved_session.cleanup()
        session.cleanup()


def test_ordinary_mixed_image_save_keeps_non_eseq_files(tmp_path):
    image_path, _sources = _image(tmp_path, {
        "SONG.MID": _midi(), "PDISK.MNG": b"Disk title input", "BOGUS.FIL": b"Not E-SEQ",
    })
    session = FloppyImageSession.load(str(image_path))
    try:
        output = session.create_modified_image(renames={"PDISK.MNG": "PSONG.MNG"})
        assert {entry.path for entry in read_image_listing(output).entries} == {
            "SONG.MID", "PSONG.MNG", "BOGUS.FIL",
        }
    finally:
        session.cleanup()


def test_directory_generation_itself_preserves_unlisted_payloads(tmp_path):
    song = convert_midi_bytes_to_eseq_bytes(_midi())
    image_path, _sources = _image(tmp_path, {"NOEXT": song, "PSONG.MNG": b"Title catalog"})
    session = FloppyImageSession.load(str(image_path))
    try:
        session._write_generated_pianodir(session.working_img_path)
        assert {entry.path for entry in session.list_entries().entries} == {"NOEXT", "PIANODIR.FIL", "PSONG.MNG"}
        assert {entry.path for entry in read_image_listing(str(image_path)).entries} == {"NOEXT", "PSONG.MNG"}
    finally:
        session.cleanup()


@pytest.mark.parametrize("variant", ("disklavier", "clavinova"))
def test_commit_preserves_unrelated_payloads_and_honors_explicit_deletions(tmp_path, variant):
    container = ESEQ_CONTAINER_DISKLAVIER if variant == "disklavier" else ESEQ_CONTAINER_CLAVINOVA_MDA
    other_container = ESEQ_CONTAINER_CLAVINOVA_MDA if variant == "disklavier" else ESEQ_CONTAINER_DISKLAVIER
    song = convert_midi_bytes_to_eseq_bytes(_midi(), container_variant=container)
    unrelated = {
        "NOTES.TXT": b"Original notes", "PSONG.MNG": b"Song catalog", "PDISK.MNG": b"Album catalog",
        "BOGUS.FIL": b"Not a song", "MIDI.MID": _midi(),
        "OTHER.FIL": convert_midi_bytes_to_eseq_bytes(_midi(), container_variant=other_container),
        "MUSIC.DIR" if variant == "disklavier" else "PIANODIR.FIL": b"Opposite catalog",
    }
    image_path, _ = _image(tmp_path, {"SONG.FIL": song, "DELETE.TXT": b"Explicit deletion", **unrelated})
    session = FloppyImageSession.load(str(image_path))
    try:
        session.commit_to_source(
            generate_pianodir=True, eseq_variant=variant, deletes={"DELETE.TXT"},
            title_edits={"SONG.FIL": "Edited title"},
        )
        assert "DELETE.TXT" not in {entry.path for entry in session.list_entries().entries}
        for name, contents in unrelated.items():
            assert Path(session.extract_file(name)).read_bytes() == contents
    finally:
        session.cleanup()
