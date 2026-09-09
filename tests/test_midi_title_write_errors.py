import builtins

import pytest

from aps_midi_prep_tool_app import midi_metadata
from aps_midi_prep_tool_app.helpers import atomic_file


def _midi_bytes(track=b"\x00\xff\x03\x03Old\x00\xff\x2f\x00"):
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
    return header + b"MTrk" + len(track).to_bytes(4, "big") + track


def test_malformed_midi_has_a_typed_title_error_and_does_not_touch_destination(tmp_path):
    source = tmp_path / "broken.mid"
    source.write_bytes(_midi_bytes(b"\x00\x3c\x40\x00\xff\x2f\x00"))
    destination = tmp_path / "edited.mid"
    destination.write_bytes(b"existing destination")

    with pytest.raises(midi_metadata.MidiTitleFormatError, match="Invalid running status") as error:
        midi_metadata.write_midi_title_to_path(source, "New title", destination)

    assert isinstance(error.value.__cause__, ValueError)
    assert destination.read_bytes() == b"existing destination"


@pytest.mark.parametrize("failure_phase", ["read", "open_write", "partial_write"])
def test_title_io_errors_propagate_without_becoming_format_errors(tmp_path, monkeypatch, failure_phase):
    source = tmp_path / "source.mid"
    original = _midi_bytes()
    source.write_bytes(original)
    destination = tmp_path / "edited.mid"
    destination.write_bytes(b"existing destination")
    failure = OSError("simulated file I/O failure")
    real_open = builtins.open
    real_fdopen = atomic_file.os.fdopen

    class FailingFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self):
            raise failure

        def write(self, payload):
            self.handle.write(payload[:3])
            raise failure

    def failing_open(path, mode):
        handle = real_open(path, mode)
        if mode == "rb" and failure_phase == "read":
            return FailingFile(handle)
        return handle

    def failing_fdopen(descriptor, mode):
        if failure_phase == "open_write":
            raise failure
        handle = real_fdopen(descriptor, mode)
        return FailingFile(handle) if failure_phase == "partial_write" else handle

    monkeypatch.setattr(midi_metadata, "open", failing_open, raising=False)
    monkeypatch.setattr(atomic_file.os, "fdopen", failing_fdopen)
    with pytest.raises(OSError) as error:
        midi_metadata.write_midi_title_to_path(source, "New title", destination)

    assert error.value is failure
    assert source.read_bytes() == original
    assert destination.read_bytes() == b"existing destination"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["edited.mid", "source.mid"]


def test_non_format_exceptions_from_title_edit_propagate(tmp_path, monkeypatch):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi_bytes())
    destination = tmp_path / "edited.mid"
    failure = RuntimeError("operation cancelled")

    def cancel_edit(*args):
        raise failure

    monkeypatch.setattr(midi_metadata, "_set_first_title_in_midi_bytes", cancel_edit)
    with pytest.raises(RuntimeError) as error:
        midi_metadata.write_midi_title_to_path(source, "New title", destination)

    assert error.value is failure
    assert not destination.exists()


def test_legacy_title_writer_keeps_error_string_and_success_contract(tmp_path):
    source = tmp_path / "source.mid"
    source.write_bytes(_midi_bytes())
    destination = tmp_path / "edited.mid"
    assert midi_metadata.update_midi_title_to_path(source, "New title", destination) is None
    assert midi_metadata.read_first_title_from_midi(destination) == "New title"

    source.write_bytes(_midi_bytes(b"\x00\x3c\x40"))
    message = midi_metadata.update_midi_title_to_path(source, "Another title", destination)
    assert message == "Could not write updated title for source.mid: Invalid running status in track data."
    assert midi_metadata.read_first_title_from_midi(destination) == "New title"

    source.unlink()
    message = midi_metadata.update_midi_title_to_path(source, "Another title", destination)
    assert message.startswith("Could not write updated title for source.mid:")
    assert "No such file or directory" in message
