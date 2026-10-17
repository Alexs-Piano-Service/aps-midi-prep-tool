"""Image-backed root probes work without POSIX positional reads."""

import builtins
import os
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


@pytest.fixture
def root_probe(tmp_path, monkeypatch):
    path = tmp_path / "floppy.img"
    image._create_blank_fat12_image_from_layout(
        path, image._PROTECTED_FAT12_LAYOUTS[0], "TARGET",
    )
    with path.open("rb") as handle:
        geometry = image._geometry_from_boot_sector(handle.read(512))

    # Simulate Windows without changing the process-wide os module.
    monkeypatch.setattr(image, "os", SimpleNamespace(**{
        key: value for key, value in vars(os).items() if key != "pread"
    }))
    state = SimpleNamespace(reads=[], handles=[], descriptors=[], stopped=False, failure=None)

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def seek(self, *args):
            return self.handle.seek(*args)

        def read(self, size):
            offset = self.handle.tell()
            state.reads.append((offset, size))
            assert (offset == 0 and size == 512) or (
                geometry.root_offset <= offset < geometry.root_offset + 512
                and offset + size <= geometry.root_offset + 512
            )
            if state.failure == "invalid_boot":
                return bytes(size)
            if state.failure == "unreadable_root" and offset >= geometry.root_offset:
                raise OSError("injected directory read failure")
            data = self.handle.read(size)
            if state.failure == "cancelled":
                state.stopped = True
            return data

    def open_probe(file, mode, *, buffering):
        assert file == path
        assert mode == "rb"
        assert buffering == 0  # Buffered reads could touch unused floppy sectors.
        handle = builtins.open(file, mode, buffering=buffering)
        state.handles.append(handle)
        state.descriptors.append(handle.fileno())
        return Reader(handle)

    monkeypatch.setattr(image, "open", open_probe, raising=False)
    return path, geometry, state


def _assert_closed(state):
    assert state.handles
    assert all(handle.closed for handle in state.handles)
    for descriptor in state.descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_root_probe_without_pread_reads_only_needed_metadata(root_probe):
    path, geometry, state = root_probe

    directory = image._posix_floppy_root_directory(path)

    assert len(directory) == geometry.root_entries * 32
    assert state.reads == [(0, 512), (geometry.root_offset, 512)]
    assert directory[512:] == bytes(len(directory) - 512)
    _assert_closed(state)


@pytest.mark.parametrize("failure", ["invalid_boot", "short_root", "unreadable_root", "cancelled"])
def test_root_probe_without_pread_fails_closed(root_probe, failure):
    path, geometry, state = root_probe
    state.failure = failure
    if failure == "short_root":
        # A terminator in an incomplete sector must not establish a valid root.
        with path.open("r+b") as handle:
            handle.truncate(geometry.root_offset + 256)

    expected = image.FloppyOperationCancelled if failure == "cancelled" else image.FloppyImageError
    with pytest.raises(expected):
        image._posix_floppy_root_directory(path, cancel_callback=lambda: state.stopped)

    _assert_closed(state)
