"""Root-capacity preflight reads only the metadata needed to count slots."""

import os

import pytest

from aps_midi_prep_tool_app import floppy_image as image


pytestmark = pytest.mark.skipif(not hasattr(os, "pread"), reason="POSIX directory reader")


@pytest.fixture
def root_image(tmp_path):
    path = tmp_path / "floppy.img"
    image.create_blank_floppy_image(path, image.DISK_FORMAT_BY_KEY["ibm.720"])
    with path.open("rb") as handle:
        geometry = image._geometry_from_boot_sector(handle.read(512))
    return path, geometry


def test_root_capacity_reader_stops_after_readable_terminator(root_image, monkeypatch):
    path, geometry = root_image
    pread = os.pread
    reads = []
    descriptors = set()

    def metadata_only(fd, size, offset):
        descriptors.add(fd)
        reads.append((offset, size))
        assert (offset, size) in [(0, 512), (geometry.root_offset, 512)]
        return pread(fd, size, offset)

    monkeypatch.setattr(image.os, "pread", metadata_only)
    directory = image._posix_floppy_root_directory(path)

    assert len(directory) == geometry.root_entries * 32
    assert reads == [(0, 512), (geometry.root_offset, 512)]
    assert directory[512:] == bytes(len(directory) - 512)
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("failure", ["invalid_boot", "unreadable_root", "cancelled"])
def test_root_capacity_reader_fails_closed_and_releases_device(root_image, monkeypatch, failure):
    path, geometry = root_image
    pread = os.pread
    descriptors = set()
    stopped = False

    def read(fd, size, offset):
        nonlocal stopped
        descriptors.add(fd)
        if failure == "invalid_boot":
            return bytes(size)
        if failure == "unreadable_root" and offset >= geometry.root_offset:
            raise OSError("injected directory read failure")
        data = pread(fd, size, offset)
        if failure == "cancelled":
            stopped = True
        return data

    monkeypatch.setattr(image.os, "pread", read)
    expected = image.FloppyOperationCancelled if failure == "cancelled" else image.FloppyImageError
    with pytest.raises(expected):
        image._posix_floppy_root_directory(path, cancel_callback=lambda: stopped)

    assert descriptors
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
