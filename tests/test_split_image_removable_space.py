"""Split exports must fit removable storage sized for the completed disk set."""

import errno
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image
from aps_midi_prep_tool_app.helpers import atomic_file


@pytest.mark.parametrize("image_count", [2, 3])
@pytest.mark.parametrize("output_ext", ["img", "hfe"])
def test_split_export_needs_only_final_image_space_on_removable_storage(
    tmp_path, monkeypatch, image_count, output_ext,
):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    output_dir = tmp_path / "removable"
    output_dir.mkdir()
    private_temp = tmp_path / "working"
    private_temp.mkdir()
    monkeypatch.setattr(floppy_image.tempfile, "tempdir", str(private_temp))
    specs = []
    songs = []
    for index in range(1, image_count + 1):
        song = tmp_path / f"SONG{index}.MID"
        payload = bytes([index]) * (400 * 1024)
        song.write_bytes(payload)
        specs.append((str(song), song.name))
        songs.append((song, payload))

    container_header = b"TEST HFE CONTAINER\0"
    conversions = []

    def convert(source, destination, format_key, **kwargs):
        assert format_key == disk_format.key
        payload = Path(source).read_bytes()
        if Path(destination).suffix == ".hfe":
            conversions.append("encode")
            payload = container_header + payload
        else:
            conversions.append("decode")
            assert payload.startswith(container_header)
            payload = payload[len(container_header):]
        Path(destination).write_bytes(payload)
        return ""

    if output_ext == "hfe":
        monkeypatch.setattr(floppy_image, "_gw_convert", convert)

    image_size = disk_format.size_bytes + (len(container_header) if output_ext == "hfe" else 0)
    capacity = image_count * image_size
    writes = []
    real_open = open

    def no_hardlinks(source, destination):
        assert Path(destination).parent == output_dir
        raise OSError(errno.EOPNOTSUPP, "Removable filesystem does not support hard links")

    class QuotaWriter:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, payload):
            occupied = sum(path.stat().st_size for path in output_dir.rglob("*") if path.is_file())
            if occupied + len(payload) > capacity:
                raise OSError(errno.ENOSPC, "Removable storage has no space for duplicate images")
            written = self.handle.write(payload)
            self.handle.flush()
            writes.append(written)
            return written

    def quota_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if mode == "xb" and Path(path).parent == output_dir:
            return QuotaWriter(handle)
        return handle

    monkeypatch.setattr(atomic_file.os, "link", no_hardlinks)
    monkeypatch.setattr(atomic_file, "open", quota_open, raising=False)

    output_paths = floppy_image.create_floppy_images_from_files(
        specs, output_dir / f"Collection.{output_ext}", output_ext, disk_format,
    )

    expected_paths = [
        output_dir / f"Collection_{index:02d}.{output_ext}"
        for index in range(1, image_count + 1)
    ]
    assert output_paths == [str(path) for path in expected_paths]
    assert set(output_dir.iterdir()) == set(expected_paths)
    assert sum(path.stat().st_size for path in expected_paths) == capacity
    assert sum(writes) == capacity
    assert list(private_temp.iterdir()) == []
    assert conversions == (["encode", "decode"] * image_count if output_ext == "hfe" else [])
    for image_path, (song, payload) in zip(expected_paths, songs):
        delivered_raw = image_path
        if output_ext == "hfe":
            delivered_raw = tmp_path / "readback.img"
            delivered_raw.write_bytes(image_path.read_bytes()[len(container_header):])
        listing = floppy_image._read_fat12_image_listing(delivered_raw)
        assert [entry.path for entry in listing.entries if not entry.is_directory] == [song.name]
        assert floppy_image._read_fat12_file_bytes(delivered_raw, song.name) == payload
        assert song.read_bytes() == payload
