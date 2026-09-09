import builtins
import errno
import os
import stat

import pytest

from aps_midi_prep_tool_app import eseq_pianodir, midi_metadata
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    convert_midi_bytes_to_eseq_bytes,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.helpers import atomic_file


def _midi_bytes(track=None):
    if track is None:
        track = b"\x00\xff\x03\x03Old\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
    return header + b"MTrk" + len(track).to_bytes(4, "big") + track


def _source_bytes(file_type):
    midi = _midi_bytes()
    if file_type == "midi":
        return midi
    return convert_midi_bytes_to_eseq_bytes(midi)


@pytest.mark.parametrize("file_type", ["midi", "eseq"])
@pytest.mark.parametrize("destination_kind", ["in_place", "existing", "new"])
@pytest.mark.parametrize("phase", ["create", "short_write", "flush", "fsync", "readback", "replace"])
def test_failed_title_writes_preserve_originals_and_remove_temporary_files(
    tmp_path, monkeypatch, file_type, destination_kind, phase,
):
    source = tmp_path / f"source.{file_type}"
    original = _source_bytes(file_type)
    source.write_bytes(original)
    destination = source if destination_kind == "in_place" else tmp_path / "output"
    if destination_kind == "existing":
        destination.write_bytes(b"previous output")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    failure = OSError(f"simulated {phase} failure")

    def fail(*_args, **_kwargs):
        raise failure

    real_fdopen = atomic_file.os.fdopen
    real_open = builtins.open

    class IncompleteFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, payload):
            if phase == "short_write":
                return self.handle.write(payload[:3])
            return self.handle.write(payload)

        def flush(self):
            raise failure

        def read(self):
            return self.handle.read()[:-1]

    if phase == "create":
        monkeypatch.setattr(atomic_file.tempfile, "mkstemp", fail)
    elif phase in {"short_write", "flush"}:
        monkeypatch.setattr(atomic_file.os, "fdopen", lambda *args: IncompleteFile(real_fdopen(*args)))
    elif phase == "fsync":
        monkeypatch.setattr(atomic_file.os, "fsync", fail)
    elif phase == "readback":
        monkeypatch.setattr(atomic_file, "open", lambda *args: IncompleteFile(real_open(*args)), raising=False)
    elif phase == "replace":
        monkeypatch.setattr(atomic_file.os, "replace", fail)

    writer = getattr(midi_metadata, f"write_{file_type}_title_to_path")
    with pytest.raises(OSError):
        writer(source, "New title", destination)

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("file_type", ["midi", "eseq"])
@pytest.mark.parametrize("destination_kind", ["in_place", "existing"])
def test_title_writes_reject_read_only_destinations(tmp_path, file_type, destination_kind):
    source = tmp_path / "source"
    source.write_bytes(_source_bytes(file_type))
    destination = source if destination_kind == "in_place" else tmp_path / "output"
    if destination_kind == "existing":
        destination.write_bytes(b"previous output")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    destination.chmod(0o444)
    try:
        if os.access(destination, os.W_OK):
            pytest.skip("The current user or filesystem does not enforce read-only permissions")

        with pytest.raises(PermissionError):
            getattr(midi_metadata, f"write_{file_type}_title_to_path")(source, "New title", destination)

        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
        assert not os.access(destination, os.W_OK)
    finally:
        destination.chmod(0o644)


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not enforce read permissions")
def test_atomic_replacement_does_not_require_destination_read_access(tmp_path):
    destination = tmp_path / "write_only"
    destination.write_bytes(b"previous output")
    destination.chmod(0o200)
    try:
        atomic_file.atomic_write_bytes(destination, b"new output")
        assert stat.S_IMODE(destination.stat().st_mode) == 0o200
    finally:
        destination.chmod(0o600)

    assert destination.read_bytes() == b"new output"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("file_type", ["midi", "eseq"])
def test_title_output_is_reopened_and_validated_before_atomic_replace(tmp_path, monkeypatch, file_type):
    source = tmp_path / "source"
    original = _source_bytes(file_type)
    source.write_bytes(original)
    source.chmod(0o640)
    real_replace = os.replace
    replacements = []

    def checked_replace(temp_path, dest_path):
        assert os.path.dirname(temp_path) == str(tmp_path)
        assert source.read_bytes() == original
        reader = getattr(midi_metadata, f"read_{'first_title_from_midi' if file_type == 'midi' else 'eseq_title_from_file'}")
        assert reader(temp_path) == "New title"
        replacements.append((temp_path, dest_path))
        real_replace(temp_path, dest_path)

    monkeypatch.setattr(atomic_file.os, "replace", checked_replace)
    getattr(midi_metadata, f"write_{file_type}_title_to_path")(source, "New title", source)

    assert len(replacements) == 1
    assert list(tmp_path.iterdir()) == [source]
    if os.name != "nt":
        assert stat.S_IMODE(source.stat().st_mode) == 0o640
    if file_type == "midi":
        # The title edit leaves the note events and their timing byte-for-byte intact.
        note_events = b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
        assert original.endswith(note_events)
        assert source.read_bytes().endswith(note_events)
    else:
        before, after = parse_eseq_bytes(original), parse_eseq_bytes(source.read_bytes())
        assert (after.events, after.end_tick, after.tempo_events) == (
            before.events, before.end_tick, before.tempo_events,
        )


@pytest.mark.parametrize("track", [
    b"\x00\xff\x03\x03Old\x00\x90\x3c",
    b"\x00\xff\x03\x03Old\x80\x80\x80\x80\x80",
])
def test_invalid_midi_after_existing_title_does_not_replace_destination(tmp_path, track):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi_bytes(track))
    original = source.read_bytes()

    with pytest.raises(midi_metadata.MidiTitleFormatError):
        midi_metadata.write_midi_title_to_path(source, "New", source)

    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("file_type", ["midi", "eseq"])
def test_wrong_prepared_title_fails_validation_before_replace(tmp_path, monkeypatch, file_type):
    source = tmp_path / "source"
    original = _source_bytes(file_type)
    source.write_bytes(original)
    setter_name = "_set_first_title_in_midi_bytes" if file_type == "midi" else "_set_eseq_title_in_bytes"
    monkeypatch.setattr(midi_metadata, setter_name, lambda *_args: original)

    with pytest.raises(ValueError, match="does not match"):
        getattr(midi_metadata, f"write_{file_type}_title_to_path")(source, "New", source)

    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("api", [
    "update_midi_title", "update_midi_title_to_destination", "update_midi_title_to_path",
    "update_eseq_title", "update_eseq_title_to_path",
    "update_eseq_order_key", "update_eseq_order_key_to_path",
])
def test_legacy_metadata_writers_report_replace_failure_without_overwriting(tmp_path, monkeypatch, api):
    source = tmp_path / "source"
    original = _source_bytes("midi" if "midi" in api else "eseq")
    source.write_bytes(original)

    def fail_replace(*_args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(atomic_file.os, "replace", fail_replace)
    module = eseq_pianodir if "order_key" in api else midi_metadata
    args = [source, b"SONG    FIL\x00" if "order_key" in api else "New title"]
    if api.endswith("_to_path"):
        args.append(source)
    elif api.endswith("_to_destination"):
        args.append(tmp_path)
    error = getattr(module, api)(*args)

    assert "simulated replacement failure" in error
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


def test_clavinova_title_save_preserves_file_without_embedded_title(tmp_path):
    source = tmp_path / "SONG.MDA"
    original = convert_midi_bytes_to_eseq_bytes(
        _midi_bytes(
            b"\x00\xff\x58\x04\x04\x02\x18\x08"
            b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
        ),
        container_variant=ESEQ_CONTAINER_CLAVINOVA_MDA,
    )
    source.write_bytes(original)

    assert midi_metadata.update_eseq_title(source, "New title") is None
    assert source.read_bytes() == original


def test_in_place_title_edit_preserves_symlink_and_updates_its_target(tmp_path):
    source = tmp_path / "target.mid"
    source.write_bytes(_midi_bytes())
    link = tmp_path / "linked.mid"
    try:
        link.symlink_to(source.name)
    except OSError:
        pytest.skip("Creating symlinks is not supported in this environment")

    assert midi_metadata.update_midi_title(link, "New title") is None
    assert link.is_symlink()
    assert midi_metadata.read_first_title_from_midi(source) == "New title"


@pytest.mark.skipif(os.name == "nt", reason="Windows uses its exclusive rename API")
def test_exclusive_write_supports_filesystems_without_links_without_overwriting(tmp_path, monkeypatch):
    def no_links(*args):
        raise OSError(errno.EOPNOTSUPP, "No hard links on this filesystem")

    monkeypatch.setattr(atomic_file.os, "link", no_links)
    destination = tmp_path / "output.mid"
    atomic_file.atomic_write_bytes(destination, b"first output", replace_existing=False)
    assert destination.read_bytes() == b"first output"
    with pytest.raises(FileExistsError):
        atomic_file.atomic_write_bytes(destination, b"second output", replace_existing=False)
    assert destination.read_bytes() == b"first output"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.skipif(os.name == "nt", reason="Windows uses its exclusive rename API")
def test_exclusive_fallback_cleans_new_output_after_partial_write(tmp_path, monkeypatch):
    real_open = builtins.open

    def no_links(*args):
        raise OSError(errno.EOPNOTSUPP, "No hard links on this filesystem")

    class PartialFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, data):
            self.handle.write(data[:3])
            raise OSError("disk full")

    def fail_final_write(path, mode):
        handle = real_open(path, mode)
        return PartialFile(handle) if mode == "xb" else handle

    monkeypatch.setattr(atomic_file.os, "link", no_links)
    monkeypatch.setattr(atomic_file, "open", fail_final_write, raising=False)
    with pytest.raises(OSError, match="disk full"):
        atomic_file.atomic_write_bytes(tmp_path / "output.mid", b"new output", replace_existing=False)
    assert list(tmp_path.iterdir()) == []
