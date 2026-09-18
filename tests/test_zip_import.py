"""Dropped archives preserve their files without escaping temporary storage."""

import io
from pathlib import Path
import stat
import struct
import warnings
import zipfile

import pytest

from aps_midi_prep_tool_app import zip_import


def _archive(tmp_path, entries):
    archive = tmp_path / "songs.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as output:
            for name, contents in entries:
                output.writestr(name, contents)
    destination = tmp_path / "extracted"
    destination.mkdir()
    return archive, destination


def test_nested_files_keep_archive_order_contents_and_names(tmp_path):
    entries = [
        ("album/", b""),
        ("album/second.mid", b"second midi"),
        ("first.mid", b"first midi"),
        ("album/catalog.dat", b"catalog metadata"),
        ("other\\third.mid", b"third midi"),
        ("nested.zip", b"a ZIP is not recursively expanded"),
        ("__MACOSX/._first.mid", b"resource fork"),
        ("album/.DS_Store", b"finder metadata"),
    ]
    archive, destination = _archive(tmp_path, entries)
    progress = []

    paths = zip_import.extract_zip(archive, destination, progress=lambda *args: progress.append(args))

    assert [Path(path).relative_to(destination).as_posix() for path in paths] == [
        "album/second.mid", "first.mid", "album/catalog.dat", "other/third.mid", "nested.zip",
    ]
    assert [Path(path).read_bytes() for path in paths] == [entries[index][1] for index in (1, 2, 3, 4, 5)]
    assert progress[0] == (0, 5)
    assert progress[-1] == (5, 5)
    assert not (destination / "__MACOSX").exists()
    assert not (destination / "album" / ".DS_Store").exists()
    assert archive.exists()


@pytest.mark.parametrize("name", [
    "../escaped.mid", "album/../../escaped.mid", "/escaped.mid",
    "C:/escaped.mid", "C:escaped.mid", "\\\\server\\share\\escaped.mid",
    "album\\..\\escaped.mid", "album/./song.mid", "album//song.mid",
    "song.mid:stream", "CON.mid", "album/AUX", "song.mid.", "song.mid ",
])
def test_unsafe_paths_reject_entire_archive_before_extracting(tmp_path, name):
    archive, destination = _archive(tmp_path, [("safe.mid", b"safe"), (name, b"unsafe")])

    with pytest.raises(zip_import.ZipImportError, match="unsafe paths or unsupported entries"):
        zip_import.extract_zip(archive, destination)

    assert list(destination.iterdir()) == []
    assert not (tmp_path / "escaped.mid").exists()


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_symlinks_and_special_entries_are_rejected(tmp_path, kind):
    entry = zipfile.ZipInfo("unsupported.mid")
    entry.create_system = 3
    entry.external_attr = (kind | 0o644) << 16
    archive, destination = _archive(tmp_path, [("safe.mid", b"safe"), (entry, b"../outside")])

    with pytest.raises(zip_import.ZipImportError, match="unsafe paths or unsupported entries"):
        zip_import.extract_zip(archive, destination)

    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("names", [
    ["song.mid", "song.mid"],
    ["song.mid", "SONG.MID"],
    ["album/song.mid", "ALBUM/other.mid"],
    ["album/song.mid", "album"],
    ["album", "album/song.mid"],
    ["album/song.mid", "album\\song.mid"],
    ["café.mid", "cafe\u0301.mid"],
])
def test_colliding_paths_fail_before_any_extraction(tmp_path, names):
    archive, destination = _archive(tmp_path, [(name, b"midi") for name in names])

    with pytest.raises(zip_import.ZipImportError, match="unsafe paths or unsupported entries"):
        zip_import.extract_zip(archive, destination)

    assert list(destination.iterdir()) == []


def test_explicit_directory_after_its_files_is_valid(tmp_path):
    archive, destination = _archive(tmp_path, [("album/song.mid", b"midi"), ("album/", b"")])

    assert zip_import.extract_zip(archive, destination) == [str(destination / "album" / "song.mid")]


@pytest.mark.parametrize("limit", ["bytes", "files"])
def test_declared_limits_reject_entire_archive_before_extraction(tmp_path, monkeypatch, limit):
    archive, destination = _archive(tmp_path, [("one.mid", b"123"), ("two.mid", b"456")])
    if limit == "bytes":
        monkeypatch.setattr(zip_import, "MAX_UNCOMPRESSED_BYTES", 5)
    else:
        monkeypatch.setattr(zip_import, "MAX_FILES", 1)

    with pytest.raises(zip_import.ZipImportError, match="exceeds the extraction limits"):
        zip_import.extract_zip(archive, destination)

    assert list(destination.iterdir()) == []


def test_actual_copied_bytes_are_bounded_even_if_reader_exceeds_declared_size(tmp_path, monkeypatch):
    archive, destination = _archive(tmp_path, [("song.mid", b"x")])
    monkeypatch.setattr(zip_import, "MAX_UNCOMPRESSED_BYTES", 4)
    monkeypatch.setattr(zip_import, "COPY_CHUNK_SIZE", 2)
    monkeypatch.setattr(zip_import.zipfile.ZipFile, "open", lambda *args: io.BytesIO(b"123456789"))

    with pytest.raises(zip_import.ZipImportError, match="exceeds the extraction limits"):
        zip_import.extract_zip(archive, destination)

    assert (destination / "song.mid").read_bytes() == b"1234"


def test_cancellation_interrupts_a_large_file_and_closes_handles(tmp_path, monkeypatch):
    archive, destination = _archive(tmp_path, [("song.mid", b"123456789"), ("other.mid", b"other")])
    monkeypatch.setattr(zip_import, "COPY_CHUNK_SIZE", 2)
    progress = []

    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(
            archive, destination,
            progress=lambda *args: progress.append(args),
            is_cancelled=lambda: len(progress) >= 3,
        )

    assert (destination / "song.mid").read_bytes() == b"1234"
    assert not (destination / "other.mid").exists()
    assert progress == [(0, 2), (0, 2), (0, 2)]
    # These operations also require all readers and writers to close on Windows.
    (destination / "song.mid").unlink()
    archive.unlink()


def test_cancellation_before_extraction_writes_nothing(tmp_path):
    archive, destination = _archive(tmp_path, [("song.mid", b"midi")])

    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(archive, destination, is_cancelled=lambda: True)

    assert list(destination.iterdir()) == []


def test_invalid_zip_preserves_library_diagnostic(tmp_path):
    archive, destination = _archive(tmp_path, [])
    archive.write_bytes(b"not a zip")

    with pytest.raises(zipfile.BadZipFile):
        zip_import.extract_zip(archive, destination)

    assert list(destination.iterdir()) == []


def test_crc_failure_is_reported_and_closes_handles(tmp_path):
    archive, destination = _archive(tmp_path, [("song.mid", b"midi data")])
    contents = bytearray(archive.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", contents, 26)
    contents[30 + name_length + extra_length] ^= 0xFF
    archive.write_bytes(contents)

    with pytest.raises(zipfile.BadZipFile, match="CRC"):
        zip_import.extract_zip(archive, destination)

    (destination / "song.mid").unlink()
    archive.unlink()


def test_encrypted_zip_preserves_library_diagnostic(tmp_path):
    archive, destination = _archive(tmp_path, [("song.mid", b"midi data")])
    contents = bytearray(archive.read_bytes())
    central_header = contents.index(b"PK\x01\x02")
    for offset in (6, central_header + 8):
        flags = struct.unpack_from("<H", contents, offset)[0]
        struct.pack_into("<H", contents, offset, flags | 1)
    archive.write_bytes(contents)

    with pytest.raises(RuntimeError, match="encrypted"):
        zip_import.extract_zip(archive, destination)

    assert list(destination.iterdir()) == []
    archive.unlink()


def test_existing_destination_files_are_never_overwritten(tmp_path):
    archive, destination = _archive(tmp_path, [("song.mid", b"new")])
    existing = destination / "song.mid"
    existing.write_bytes(b"original")

    with pytest.raises(zip_import.ZipImportError):
        zip_import.extract_zip(archive, destination)

    assert existing.read_bytes() == b"original"


def test_file_created_during_progress_is_never_overwritten(tmp_path):
    archive, destination = _archive(tmp_path, [("song.mid", b"new")])
    existing = destination / "song.mid"

    with pytest.raises(FileExistsError):
        zip_import.extract_zip(archive, destination, progress=lambda *_: existing.write_bytes(b"original"))

    assert existing.read_bytes() == b"original"


def test_empty_archive_reports_completion(tmp_path):
    archive, destination = _archive(tmp_path, [])
    progress = []

    assert zip_import.extract_zip(archive, destination, progress=lambda *args: progress.append(args)) == []

    assert progress == [(0, 0)]


def test_byte_progress_advances_within_a_single_large_file(tmp_path):
    contents = b"x" * (zip_import.COPY_CHUNK_SIZE * 2 + 17)
    archive, destination = _archive(tmp_path, [("large.mid", contents)])
    byte_progress = []

    paths = zip_import.extract_zip(
        archive, destination, byte_progress=lambda *args: byte_progress.append(args),
    )

    assert Path(paths[0]).read_bytes() == contents
    assert byte_progress[0] == (0, 0)
    extraction_progress = byte_progress[1:]
    assert extraction_progress[0] == (0, len(contents))
    assert extraction_progress[-1] == (len(contents), len(contents))
    completed_bytes = [completed for completed, total in extraction_progress]
    assert completed_bytes == sorted(set(completed_bytes))
    assert any(0 < completed < len(contents) for completed in completed_bytes)
    assert all(total == len(contents) for completed, total in extraction_progress)


def test_byte_progress_accumulates_files_and_ignores_metadata(tmp_path, monkeypatch):
    archive, destination = _archive(tmp_path, [
        ("album/", b""),
        ("album/one.mid", b"123"),
        ("album/empty.mid", b""),
        ("album/two.mid", b"45678"),
        ("__MACOSX/._one.mid", b"resource fork"),
        ("album/.DS_Store", b"finder metadata"),
    ])
    monkeypatch.setattr(zip_import, "COPY_CHUNK_SIZE", 2)
    byte_progress = []

    paths = zip_import.extract_zip(
        archive, destination, byte_progress=lambda *args: byte_progress.append(args),
    )

    assert len(paths) == 3
    assert byte_progress == [(0, 0), (0, 8), (2, 8), (3, 8), (3, 8), (5, 8), (7, 8), (8, 8)]


@pytest.mark.parametrize("entries", [[], [("empty.mid", b"")]])
def test_empty_archive_byte_progress_has_zero_total(tmp_path, entries):
    archive, destination = _archive(tmp_path, entries)
    byte_progress = []

    zip_import.extract_zip(
        archive, destination, byte_progress=lambda *args: byte_progress.append(args),
    )

    assert byte_progress == [(0, 0)] * (2 + len(entries))


def test_byte_progress_allows_cancellation_between_empty_files(tmp_path):
    archive, destination = _archive(tmp_path, [("one.mid", b""), ("two.mid", b"")])
    updates = []

    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(
            archive, destination,
            byte_progress=lambda *args: updates.append(args),
            is_cancelled=lambda: len(updates) == 3,
        )

    assert (destination / "one.mid").is_file()
    assert not (destination / "two.mid").exists()


def test_initial_byte_progress_can_cancel_before_opening_archive(tmp_path, monkeypatch):
    archive, destination = _archive(tmp_path, [("song.mid", b"midi")])
    byte_progress = []

    def unexpected_open(*args, **kwargs):
        pytest.fail("Cancellation must prevent opening the archive")

    monkeypatch.setattr(zip_import.zipfile, "ZipFile", unexpected_open)
    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(
            archive, destination,
            byte_progress=lambda *args: byte_progress.append(args),
            is_cancelled=lambda: bool(byte_progress),
        )

    assert byte_progress == [(0, 0)]
    assert list(destination.iterdir()) == []


def test_byte_progress_can_cancel_during_metadata_validation(tmp_path, monkeypatch):
    entries = [(f"album/{index}.mid", b"midi") for index in range(300)]
    archive, destination = _archive(tmp_path, entries)
    byte_progress = []
    validated = []
    original_member_parts = zip_import._member_parts

    def track_validation(info):
        validated.append(info.filename)
        return original_member_parts(info)

    monkeypatch.setattr(zip_import, "_member_parts", track_validation)
    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(
            archive, destination,
            byte_progress=lambda *args: byte_progress.append(args),
            is_cancelled=lambda: len(byte_progress) > 1,
        )

    assert byte_progress == [(0, 0), (0, 0)]
    assert 0 < len(validated) < len(entries)
    assert list(destination.iterdir()) == []
    archive.unlink()


def test_byte_progress_can_cancel_after_validation_before_writing(tmp_path):
    archive, destination = _archive(tmp_path, [("album/song.mid", b"midi")])
    byte_progress = []

    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(
            archive, destination,
            byte_progress=lambda *args: byte_progress.append(args),
            is_cancelled=lambda: bool(byte_progress and byte_progress[-1][1]),
        )

    assert byte_progress == [(0, 0), (0, 4)]
    assert list(destination.iterdir()) == []


def test_byte_progress_can_cancel_between_copy_chunks(tmp_path, monkeypatch):
    archive, destination = _archive(tmp_path, [("song.mid", b"123456789"), ("other.mid", b"other")])
    monkeypatch.setattr(zip_import, "COPY_CHUNK_SIZE", 2)
    byte_progress = []

    with pytest.raises(zip_import.ZipImportCancelled):
        zip_import.extract_zip(
            archive, destination,
            byte_progress=lambda *args: byte_progress.append(args),
            is_cancelled=lambda: bool(byte_progress and byte_progress[-1][0] >= 4),
        )

    assert byte_progress == [(0, 0), (0, 14), (2, 14), (4, 14)]
    assert (destination / "song.mid").read_bytes() == b"1234"
    assert not (destination / "other.mid").exists()
    (destination / "song.mid").unlink()
    archive.unlink()
