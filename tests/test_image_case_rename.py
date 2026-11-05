"""Image preparation publishes requested spelling despite stale mren case bits."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image as image


CASE_FLAGS = {"SONG.MID": 0, "song.MID": 0x08, "SONG.mid": 0x10, "song.mid": 0x18}
CASE_RENAMES = [(before, after) for before in CASE_FLAGS for after in CASE_FLAGS
                if before != after]


def _entry_bytes(path, image_path):
    directory_path, _, filename = image_path.rpartition("/")
    directory, offsets = image._fat12_directory_slots(path, directory_path)
    entry = image._find_fat12_entry(directory, filename, original_path=image_path)
    assert entry is not None
    offset = entry["offset"]
    return directory[offset:offset + 32], offsets[offset // 32]


@pytest.fixture
def case_image(tmp_path, monkeypatch):
    sessions = []

    def prepare(original, *, directory=""):
        source = tmp_path / "source.img"
        payload = b"Song payload retained through both intermediate renames."
        host = tmp_path / "payload.mid"
        host.write_bytes(payload)
        image.create_blank_floppy_image(source, image.DISK_FORMAT_BY_KEY["ibm.720"])
        if directory:
            image._run_command(
                [image._require_command("mmd"), "-i", os.fspath(source), image.mtools_path(directory)],
                "Could not create the test directory",
            )
        source_name = f"{directory}/{original}" if directory else original
        image._copy_host_file_into_image(source, host, source_name)
        original_entry, offset = _entry_bytes(source, source_name)
        # Make metadata preservation observable, including the unrelated bits
        # in the same byte as the lowercase flags.
        with source.open("r+b") as handle:
            handle.seek(offset + 12)
            handle.write(bytes([original_entry[12] | 0x80]))
        original_entry, _ = _entry_bytes(source, source_name)
        session = image.FloppyImageSession.load(source)
        sessions.append(session)
        baseline = Path(session.working_img_path).read_bytes()
        original_bytes = source.read_bytes()
        run_mtools = session._run_mtools
        calls = []

        def preserve_previous_case_flags(args, message, **kwargs):
            if Path(args[0]).stem.lower() != "mren":
                return run_mtools(args, message, **kwargs)
            target = args[args.index("-i") + 1]
            before, after = (name.removeprefix("::").lstrip("/") for name in args[-2:])
            old_entry, _ = _entry_bytes(target, before)
            result = run_mtools(args, message, **kwargs)
            new_entry, entry_offset = _entry_bytes(target, after)
            # Reproduce affected mtools on hosts whose installed version may
            # already regenerate case bits correctly.
            with open(target, "r+b") as handle:
                handle.seek(entry_offset + 12)
                handle.write(bytes([(new_entry[12] & ~0x18) | (old_entry[12] & 0x18)]))
            calls.append((before, after))
            return result

        monkeypatch.setattr(session, "_run_mtools", preserve_previous_case_flags)
        return SimpleNamespace(
            source=source, session=session, original=source_name, payload=payload,
            original_entry=original_entry, baseline=baseline, original_bytes=original_bytes,
            calls=calls,
        )

    yield prepare
    for session in sessions:
        session.cleanup()


def _assert_source_unchanged(case):
    assert case.source.read_bytes() == case.original_bytes
    assert Path(case.session.working_img_path).read_bytes() == case.baseline


@pytest.mark.parametrize("original,renamed", CASE_RENAMES)
@pytest.mark.parametrize("directory", ["", "MUSIC"], ids=["root", "nested"])
def test_case_only_83_rename_corrects_retained_mtools_flags(case_image, original, renamed, directory):
    case = case_image(original, directory=directory)
    target_name = f"{directory}/{renamed}" if directory else renamed

    prepared = case.session.create_modified_image(renames={case.original: target_name})

    assert [entry.path for entry in image.read_image_listing(prepared).entries] == [target_name]
    assert image._read_fat12_file_bytes(prepared, target_name) == case.payload
    raw, _ = _entry_bytes(prepared, target_name)
    assert raw[:11] == b"SONG    MID"
    assert raw[12] & 0x18 == CASE_FLAGS[renamed]
    assert raw[12] & ~0x18 == case.original_entry[12] & ~0x18
    assert raw[11:12] + raw[13:] == case.original_entry[11:12] + case.original_entry[13:]
    assert len(case.calls) == 2
    assert case.calls[0][0] == case.original
    assert case.calls[0][1] == case.calls[1][0]
    assert case.calls[1][1] == target_name
    _assert_source_unchanged(case)


@pytest.mark.parametrize("original,renamed,flags", [
    ("song.mid", "OTHER.MID", 0),
    ("SONG.MID", "other.mid", 0x18),
])
def test_ordinary_83_rename_corrects_retained_mtools_flags(case_image, original, renamed, flags):
    case = case_image(original)

    prepared = case.session.create_modified_image(renames={original: renamed})

    assert [entry.path for entry in image.read_image_listing(prepared).entries] == [renamed]
    assert image._read_fat12_file_bytes(prepared, renamed) == case.payload
    raw, _ = _entry_bytes(prepared, renamed)
    assert raw[:11] == b"OTHER   MID"
    assert raw[12] & 0x18 == flags
    assert len(case.calls) == 1
    _assert_source_unchanged(case)


@pytest.mark.parametrize("parent_reference", ["different-case", "short-alias"])
def test_nested_rename_accepts_existing_parent_spelling_and_alias(case_image, parent_reference):
    case = case_image("SONG.MID", directory="My Songs")
    if parent_reference == "different-case":
        target_parent = "my songs"
        target_name = "song.mid"
    else:
        directory, _ = image._fat12_directory_slots(case.source, "")
        parent = image._find_fat12_entry(directory, "My Songs", original_path="My Songs")
        target_parent = parent["short_name"]
        target_name = "other.mid"

    prepared = case.session.create_modified_image(
        renames={case.original: f"{target_parent}/{target_name}"},
    )

    assert [entry.path for entry in image.read_image_listing(prepared).entries] == [f"My Songs/{target_name}"]
    assert image._read_fat12_file_bytes(prepared, f"My Songs/{target_name}") == case.payload
    raw, _ = _entry_bytes(prepared, f"My Songs/{target_name}")
    assert raw[12] & 0x18 == 0x18
    _assert_source_unchanged(case)


@pytest.mark.parametrize("fail_after", [1, 2], ids=["staging", "final-rename"])
def test_failed_case_rename_discards_modified_image(case_image, monkeypatch, fail_after):
    case = case_image("SONG.MID")
    run_mtools = case.session._run_mtools

    def fail_rename(args, message, **kwargs):
        result = run_mtools(args, message, **kwargs)
        if len(case.calls) == fail_after:
            raise image.FloppyImageError("Injected rename failure")
        return result

    monkeypatch.setattr(case.session, "_run_mtools", fail_rename)
    with pytest.raises(image.FloppyImageError, match="Injected rename failure"):
        case.session.create_modified_image(renames={"SONG.MID": "song.mid"})

    assert len(case.calls) == fail_after
    assert not list(Path(case.session.temp_dir).glob("modified_*.img"))
    _assert_source_unchanged(case)


@pytest.mark.parametrize("bad_result", ["wrong-spelling", "missing-target"])
def test_successful_mren_with_wrong_result_is_rejected(case_image, monkeypatch, bad_result):
    case = case_image("SONG.MID")
    run_mtools = case.session._run_mtools
    injected = []

    def incorrect_final_rename(args, message, **kwargs):
        if Path(args[0]).stem.lower() == "mren" and args[-1] == image.mtools_path("Song.mid"):
            injected.append(True)
            if bad_result == "missing-target":
                return ""
            # Mixed-case spelling needs an LFN and cannot be repaired with
            # the short name's two case bits.
            args = [*args[:-1], image.mtools_path("SONG.MID")]
        return run_mtools(args, message, **kwargs)

    monkeypatch.setattr(case.session, "_run_mtools", incorrect_final_rename)
    with pytest.raises(image.FloppyImageError, match="resulting filename differs"):
        case.session.create_modified_image(renames={"SONG.MID": "Song.mid"})

    assert injected == [True]
    assert not list(Path(case.session.temp_dir).glob("modified_*.img"))
    _assert_source_unchanged(case)


def test_failed_spelling_verification_discards_modified_image(case_image, monkeypatch):
    case = case_image("SONG.MID")
    read_slots = image._fat12_directory_slots
    verification_reads = []

    def stale_spelling_after_correction(path, directory_path):
        directory, offsets = read_slots(path, directory_path)
        if len(case.calls) == 2:
            verification_reads.append(directory)
            if len(verification_reads) == 2:
                corrected = image._find_fat12_entry(directory, "song.mid", original_path="song.mid")
                assert corrected["name"] == "song.mid"
                # Model a readback that still reports the old spelling after
                # the correction, so only the final verification can catch it.
                return verification_reads[0], offsets
        return directory, offsets

    monkeypatch.setattr(image, "_fat12_directory_slots", stale_spelling_after_correction)
    with pytest.raises(image.FloppyImageError, match="resulting filename differs"):
        case.session.create_modified_image(renames={"SONG.MID": "song.mid"})

    assert len(verification_reads) == 2
    assert not list(Path(case.session.temp_dir).glob("modified_*.img"))
    _assert_source_unchanged(case)
