"""A failed numbered export can be retried without removing unrelated files."""

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image
from aps_midi_prep_tool_app.helpers import atomic_file


@pytest.fixture(params=["img", "hfe"])
def collection(tmp_path, monkeypatch, request):
    output_ext = request.param
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    output_dir = tmp_path / "removable"
    output_dir.mkdir()
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.setattr(floppy_image.tempfile, "tempdir", str(working_dir))
    songs = {}
    for index in range(1, 4):
        song = tmp_path / f"SONG{index}.MID"
        songs[song] = bytes([index]) * (400 * 1024)
        song.write_bytes(songs[song])
    selected = output_dir / f"Collection.{output_ext}"
    originals = {
        selected: b"Previously exported image, selected as the base name",
        output_dir / f"Other_01.{output_ext}": b"An unrelated numbered image",
    }
    for path, payload in originals.items():
        path.write_bytes(payload)

    header = b"TEST HFE CONTAINER\0"

    def convert(source, destination, format_key, **_kwargs):
        assert format_key == disk_format.key
        payload = Path(source).read_bytes()
        if Path(destination).suffix == ".hfe":
            payload = header + payload
        else:
            assert payload.startswith(header)
            payload = payload[len(header):]
        Path(destination).write_bytes(payload)
        return ""

    if output_ext == "hfe":
        monkeypatch.setattr(floppy_image, "_gw_convert", convert)

    def export(**kwargs):
        return floppy_image.create_floppy_images_from_files(
            [(str(path), path.name) for path in songs], selected, output_ext,
            disk_format, **kwargs,
        )

    def check_originals():
        for path, payload in {**originals, **songs}.items():
            assert path.read_bytes() == payload
        assert list(working_dir.iterdir()) == []

    outputs = [output_dir / f"Collection_{index:02d}.{output_ext}" for index in range(1, 4)]
    recovery_directories = set()

    def check_retained(error, *, uncertain_capture=False):
        assert error.retained_paths
        recovery_directories.update(map(Path, getattr(error, "recovery_directories", ())))
        for path in map(Path, error.retained_paths):
            assert path.is_file() or (
                uncertain_capture and path.name == "captured" and path.parent in recovery_directories
            )
            assert str(path) in str(error)
            if path.parent.name.startswith(".aps_rollback_"):
                recovery_directories.add(path.parent)
        for directory in recovery_directories:
            assert str(directory) in str(error)
            mapping = json.loads((directory / "recovery.json").read_text())
            assert Path(mapping["destination"]) in outputs

    def check_outputs(expected):
        assert set(output_dir.iterdir()) == set(expected) | recovery_directories

    def check_retry():
        assert export() == [str(path) for path in outputs]
        check_outputs(set(originals) | set(outputs))
        for path, (song, payload) in zip(outputs, songs.items()):
            raw = path
            if output_ext == "hfe":
                raw = tmp_path / "readback.img"
                raw.write_bytes(path.read_bytes()[len(header):])
            assert floppy_image._read_fat12_file_bytes(raw, song.name) == payload
        check_originals()

    return SimpleNamespace(
        output_dir=output_dir, outputs=outputs, originals=originals,
        export=export, check_originals=check_originals, check_retry=check_retry,
        check_retained=check_retained, check_outputs=check_outputs,
    )


@pytest.fixture(params=[True, False], ids=["hardlinks", "exclusive-copy"])
def hardlinks(request, monkeypatch):
    if not request.param:
        def unsupported(*_args, **_kwargs):
            raise OSError(errno.EOPNOTSUPP, "Hard links are unavailable")

        monkeypatch.setattr(atomic_file.os, "link", unsupported)
    return request.param


def test_second_image_enospc_rolls_back_and_allows_clean_retry(collection, hardlinks, monkeypatch):
    fail = True
    failures = []
    real_link = atomic_file.os.link
    real_open = open

    def fail_second_link(staged, destination):
        if fail and Path(destination) == collection.outputs[1]:
            assert collection.outputs[0].exists()
            failures.append("link")
            raise OSError(errno.ENOSPC, "No space left on destination")
        return real_link(staged, destination)

    class FailingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, payload):
            assert collection.outputs[0].exists()
            self.handle.write(payload[:1024])
            self.handle.flush()
            failures.append("copy")
            raise OSError(errno.ENOSPC, "No space left on destination")

    def fail_second_copy(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if fail and mode == "xb" and Path(path) == collection.outputs[1]:
            return FailingWriter(handle)
        return handle

    if hardlinks:
        monkeypatch.setattr(atomic_file.os, "link", fail_second_link)
    else:
        monkeypatch.setattr(atomic_file, "open", fail_second_copy, raising=False)

    with pytest.raises(floppy_image.FloppyImageError, match="No space") as caught:
        collection.export()

    collection.check_retained(caught.value)
    assert failures == ["link" if hardlinks else "copy"]
    collection.check_outputs(collection.originals)
    collection.check_originals()
    fail = False
    collection.check_retry()


def test_cancel_during_second_publication_rolls_back_and_allows_clean_retry(collection, hardlinks):
    cancellations = []

    def cancel():
        second = collection.outputs[1]
        if second.exists() and second.stat().st_size:
            assert collection.outputs[0].exists()
            cancellations.append(True)
            return True
        return False

    with pytest.raises(floppy_image.FloppyOperationCancelled) as caught:
        collection.export(cancel_callback=cancel)

    collection.check_retained(caught.value)
    assert cancellations == [True]
    collection.check_outputs(collection.originals)
    collection.check_originals()
    collection.check_retry()


def test_late_second_collision_survives_rollback_of_first_image(collection, hardlinks, monkeypatch):
    real_link = atomic_file.os.link
    collision = collection.outputs[1]
    concurrent_payload = b"Created by another export during publication"

    def competing_link(staged, destination):
        if Path(destination) == collision:
            assert collection.outputs[0].exists()
            collision.write_bytes(concurrent_payload)
        return real_link(staged, destination)

    monkeypatch.setattr(atomic_file.os, "link", competing_link)

    with pytest.raises(floppy_image.FloppyImageError, match="already exists") as caught:
        collection.export()

    collection.check_retained(caught.value)
    assert collision.read_bytes() == concurrent_payload
    collection.check_outputs(set(collection.originals) | {collision})
    collection.check_originals()


def test_report_callback_failure_rolls_back_just_published_image(collection, hardlinks, monkeypatch):
    write_image = floppy_image._write_image_direct

    def write_with_report(*args, **kwargs):
        write_image(*args, **kwargs)
        return {"report": "verified"}

    def reject_report(report):
        assert report == {"report": "verified"}
        assert collection.outputs[0].exists()
        raise RuntimeError("Report callback failed")

    monkeypatch.setattr(floppy_image, "_write_image_direct", write_with_report)

    with pytest.raises(floppy_image.FloppyImageError, match="Report callback failed") as caught:
        collection.export(sector_report_callback=reject_report)

    collection.check_retained(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
    collection.check_outputs(collection.originals)
    collection.check_originals()
    collection.check_retry()


@pytest.mark.parametrize("change", ["edit", "replace", "symlink", errno.ENODEV, errno.EACCES])
def test_unverifiable_or_unremovable_first_output_is_kept_and_reported(
    collection, hardlinks, monkeypatch, change,
):
    first, second, third = collection.outputs
    real_link = atomic_file.os.link
    real_rename = atomic_file.os.rename
    kept_payload = None
    symlink_target = next(path for path in collection.originals if path.name.startswith("Other_"))

    if change == "symlink":
        probe = collection.output_dir.parent / "symlink-probe"
        try:
            probe.symlink_to(symlink_target)
        except OSError:
            pytest.skip("Symlinks are unavailable on this platform")
        probe.unlink()

    def fail_second_link(staged, destination):
        nonlocal kept_payload
        if Path(destination) != second:
            return real_link(staged, destination)

        original_stat = first.stat()
        kept_payload = first.read_bytes()
        if change == "edit":
            kept_payload = bytes([kept_payload[0] ^ 1]) + kept_payload[1:]
            first.write_bytes(kept_payload)
            os.utime(first, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            assert first.stat().st_ino == original_stat.st_ino
            assert first.stat().st_mtime_ns == original_stat.st_mtime_ns
            assert first.stat().st_size == original_stat.st_size
        elif change == "replace":
            # Allocate the replacement while the old file still exists so the
            # filesystem cannot recycle its inode. Identical data and mtime
            # must not be sufficient evidence of ownership.
            replacement = collection.output_dir.parent / "replacement.img"
            replacement.write_bytes(kept_payload)
            os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            replacement.replace(first)
            assert first.stat().st_ino != original_stat.st_ino
        elif change == "symlink":
            first.unlink()
            first.symlink_to(symlink_target)
            kept_payload = symlink_target.read_bytes()
        raise OSError(errno.ENOSPC, "No space left while publishing the second image")

    def guarded_rename(path, *args, **kwargs):
        if Path(path) == first and isinstance(change, int):
            raise OSError(change, "Destination unavailable during cleanup")
        return real_rename(path, *args, **kwargs)

    monkeypatch.setattr(atomic_file.os, "link", fail_second_link)
    monkeypatch.setattr(atomic_file.os, "rename", guarded_rename)

    with pytest.raises(floppy_image.FloppyImageError, match="incomplete") as caught:
        collection.export()

    collection.check_retained(caught.value, uncertain_capture=isinstance(change, int))
    if isinstance(change, int):
        assert len(caught.value.retained_paths) == 2
        assert str(first) in caught.value.retained_paths
    else:
        assert caught.value.retained_paths == (str(first),)
    assert str(first) in str(caught.value)
    assert "No space left while publishing the second image" in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.ENOSPC
    assert first.read_bytes() == kept_payload
    assert not second.exists()
    assert not third.exists()
    collection.check_outputs(set(collection.originals) | {first})
    if change == "symlink":
        assert first.is_symlink()
        # Windows readlink() can return an extended-length (\\?\) path.
        # Check the target's file identity instead of its path spelling.
        assert first.samefile(symlink_target)
    collection.check_originals()


def test_failed_cleanup_of_partial_second_copy_reports_that_path(collection, monkeypatch):
    first, second, third = collection.outputs
    real_open = open
    real_unlink = atomic_file.os.unlink

    def unsupported_links(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, "Hard links are unavailable")

    class FailingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, payload):
            self.handle.write(payload[:1024])
            self.handle.flush()
            raise OSError(errno.ENOSPC, "No space left while copying the second image")

    def fail_copy(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if mode == "xb" and Path(path) == second:
            return FailingWriter(handle)
        return handle

    def guarded_unlink(path, *args, **kwargs):
        if Path(path) == second:
            raise OSError(errno.ENODEV, "Destination unavailable during cleanup")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(atomic_file.os, "link", unsupported_links)
    monkeypatch.setattr(atomic_file, "open", fail_copy, raising=False)
    monkeypatch.setattr(atomic_file.os, "unlink", guarded_unlink)

    with pytest.raises(floppy_image.FloppyImageError, match="incomplete") as caught:
        collection.export()

    collection.check_retained(caught.value)
    assert str(second) in caught.value.retained_paths
    assert len(caught.value.retained_paths) == 2
    assert str(second) in str(caught.value)
    assert "No space left while copying the second image" in str(caught.value)
    assert "Destination unavailable during cleanup" in str(caught.value)
    assert not first.exists()
    assert second.stat().st_size == 1024
    assert not third.exists()
    collection.check_outputs(set(collection.originals) | {second})
    collection.check_originals()
