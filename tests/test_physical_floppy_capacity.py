"""Preparing one physical floppy must not publish a split image set."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from test_save_as_overwrite import window


@pytest.mark.parametrize("route", ["files", "repack"])
def test_single_floppy_overflow_leaves_existing_outputs_unchanged_and_default_still_splits(tmp_path, route):
    files = {}
    for index in range(2):
        source = tmp_path / f"SONG{index}.MID"
        source.write_bytes(bytes([65 + index]) * 400_000)
        files[source.name] = source
    specs = [(str(path), name) for name, path in files.items()]
    source_image = tmp_path / "source.img"
    session = None
    if route == "repack":
        floppy_image.create_floppy_images_from_files(
            specs, str(source_image), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"],
        )
        session = floppy_image.FloppyImageSession.load(source_image)
    output = tmp_path / "prepared.img"
    for path in (output, tmp_path / "prepared_01.img", tmp_path / "prepared_02.img"):
        path.write_bytes(b"Existing delivery: " + path.name.encode())
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file() and path.suffix != ".ini"}

    def export(**options):
        if session is not None:
            return session.export_to_images(
                str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"], **options,
            )
        return floppy_image.create_floppy_images_from_files(
            specs, str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"], **options,
        )

    try:
        with pytest.raises(floppy_image.FloppyImageError, match="do not fit on one floppy disk"):
            export(allow_split=False)
        assert {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file() and path.suffix != ".ini"} == before

        outputs = export()
        assert [Path(path).name for path in outputs] == ["prepared_01.img", "prepared_02.img"]
        delivered = {}
        for path in outputs:
            saved = floppy_image.FloppyImageSession.load(path)
            try:
                for entry in saved.list_entries().entries:
                    delivered[entry.path] = Path(saved.extract_file(entry.path)).read_bytes()
            finally:
                saved.cleanup()
        assert delivered == {name: path.read_bytes() for name, path in files.items()}
        assert output.read_bytes() == before[output]
        if session is not None:
            assert source_image.read_bytes() == before[source_image]
    finally:
        if session is not None:
            session.cleanup()


def test_single_floppy_mode_still_publishes_a_fitting_set(tmp_path):
    source = tmp_path / "SONG.MID"
    source.write_bytes(b"Prepared song")
    output = tmp_path / "prepared.img"
    assert floppy_image.create_floppy_images_from_files(
        [(str(source), source.name)], str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"],
        allow_split=False,
    ) == [str(output)]
    session = floppy_image.FloppyImageSession.load(output)
    try:
        assert Path(session.extract_file(source.name)).read_bytes() == source.read_bytes()
    finally:
        session.cleanup()


@pytest.mark.parametrize("image_mode", [False, True])
def test_guided_physical_delivery_blocks_overflow_before_publishing_or_choosing_a_drive(window, monkeypatch, tmp_path, image_mode):
    # Two playable MIDI tracks with large sequencer metadata each fit alone.
    length = 400_000
    encoded = [length & 0x7f]
    while length >> 7:
        length >>= 7
        encoded.insert(0, 0x80 | (length & 0x7f))
    track = (b"\x00\xff\x7f" + bytes(encoded) + b"x" * 400_000
             + b"\x00\x90\x3c\x50\x60\x80\x3c\x00\x00\xff\x2f\x00")
    data = (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
            + b"MTrk" + len(track).to_bytes(4, "big") + track)
    sources = [tmp_path / f"SONG{index}.MID" for index in range(2)]
    for source in sources:
        source.write_bytes(data)
    if image_mode:
        image = tmp_path / "source.img"
        floppy_image.create_floppy_images_from_files(
            [(str(path), path.name) for path in sources], str(image), "img",
            floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"],
        )
        session = floppy_image.FloppyImageSession.load(image)
        window._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    else:
        window._load_regular_files([str(path) for path in sources], "", prepare_destination=False)
    output = tmp_path / "prepared.img"
    output.write_bytes(b"Existing prepared disk")
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file() and path.suffix != ".ini"}
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_options:
                        (str(output), "img", floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]))
    monkeypatch.setattr(window, "_choose_write_image_floppy_target", lambda:
                        pytest.fail("Overflow must not reach a physical drive"))

    window._save_image_and_apply_to_floppy()

    assert len(window._test_errors) == 1
    assert "do not fit on one floppy disk" in str(window._test_errors[0])
    assert {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file() and path.suffix != ".ini"} == before
