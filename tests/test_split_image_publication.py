"""Numbered image names never authorize replacing existing destinations."""

import errno
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.fixture
def songs(tmp_path):
    paths = [tmp_path / "FIRST.BIN", tmp_path / "SECOND.BIN"]
    for path, value in zip(paths, (b"A", b"B")):
        path.write_bytes(value * 400_000)
    return [(str(path), path.name) for path in paths]


def _export(tmp_path, songs):
    return image.create_floppy_images_from_files(
        songs, tmp_path / "Collection.img", "img", image.DISK_FORMAT_BY_KEY["ibm.720"],
    )


@pytest.mark.parametrize("existing", [(1,), (2,), (1, 2)])
def test_all_numbered_collisions_are_rejected_before_any_publication(tmp_path, songs, existing):
    originals = {}
    for number in existing:
        path = tmp_path / f"Collection_{number:02d}.img"
        originals[path] = f"Original disk {number}".encode()
        path.write_bytes(originals[path])

    with pytest.raises(image.FloppyImageError, match="already exists"):
        _export(tmp_path, songs)

    assert {path: path.read_bytes() for path in tmp_path.glob("*.img")} == originals
    assert not list(tmp_path.glob(".aps_capture_*"))


@pytest.mark.parametrize("number", [1, 2])
def test_split_save_as_preserves_unchanged_source_including_excluded_files(tmp_path, songs, number):
    source = tmp_path / f"Collection_{number:02d}.img"
    excluded = tmp_path / "OMITTED.TXT"
    excluded.write_bytes(b"The only copy of a file excluded from this export")
    image.create_floppy_images_from_files(
        songs + [(str(excluded), excluded.name)], source, "img", image.DISK_FORMAT_BY_KEY["ibm.1440"],
    )
    original = source.read_bytes()
    session = image.FloppyImageSession.load(source)
    try:
        with pytest.raises(image.FloppyImageError, match="already exists"):
            session.export_to_images(
                tmp_path / "Collection.img", "img", image.DISK_FORMAT_BY_KEY["ibm.720"],
                deletes={excluded.name},
            )
        assert source.read_bytes() == original
        assert image._read_fat12_file_bytes(source, excluded.name) == excluded.read_bytes()
        assert list(tmp_path.glob("*.img")) == [source]
        assert not list(tmp_path.glob(".aps_capture_*"))
    finally:
        session.cleanup()


@pytest.mark.parametrize("kind", ["directory", "broken_symlink"])
def test_numbered_destination_must_be_absent_even_without_a_regular_file(tmp_path, songs, kind):
    collision = tmp_path / "Collection_02.img"
    if kind == "directory":
        collision.mkdir()
    else:
        try:
            collision.symlink_to(tmp_path / "missing.img")
        except OSError:
            pytest.skip("Symlinks are unavailable on this platform")

    with pytest.raises(image.FloppyImageError, match="already exists"):
        _export(tmp_path, songs)

    assert collision.is_dir() if kind == "directory" else collision.is_symlink()
    assert not (tmp_path / "Collection_01.img").exists()
    assert not list(tmp_path.glob(".aps_capture_*"))


@pytest.mark.parametrize("link_error", [None, errno.EOPNOTSUPP, errno.EXDEV])
def test_destination_appearing_at_publication_is_never_overwritten(tmp_path, songs, monkeypatch, link_error):
    collision = tmp_path / "Collection_01.img"
    link = image.os.link

    def competing_link(staged, destination):
        assert Path(destination) == collision
        collision.write_bytes(b"Created by another export just before publication")
        if link_error is not None:
            raise OSError(link_error, "Cannot link to destination filesystem")
        return link(staged, destination)

    monkeypatch.setattr(image.os, "link", competing_link)

    with pytest.raises(image.FloppyImageError, match="already exists"):
        _export(tmp_path, songs)

    assert collision.read_bytes() == b"Created by another export just before publication"
    assert not (tmp_path / "Collection_02.img").exists()
    assert not list(tmp_path.glob(".aps_capture_*"))


def test_selected_single_image_can_still_be_replaced(tmp_path, songs):
    output = tmp_path / "Collection.img"
    output.write_bytes(b"Previous explicitly selected destination")

    assert _export(tmp_path, songs[:1]) == [str(output)]

    assert image._read_fat12_file_bytes(output, "FIRST.BIN") == Path(songs[0][0]).read_bytes()
    assert not (tmp_path / "Collection_01.img").exists()
