"""Audio conversion cancellation reaps its helper before removing staged output."""

from pathlib import Path
import subprocess
import threading

import pytest

from aps_midi_prep_tool_app import audio_tools


def test_cancelled_lame_is_terminated_waited_and_partial_output_removed(tmp_path, monkeypatch):
    destination = tmp_path / "partial.mp3"
    cancel = threading.Event()
    calls = []
    class Process:
        returncode = None
        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            if self.returncode is None:
                cancel.set()
                raise subprocess.TimeoutExpired("fake LAME", timeout)
            return "", ""
        def poll(self):
            return self.returncode
        def terminate(self):
            calls.append(("terminate",))
            self.returncode = -15
    def launch(*args, **kwargs):
        destination.write_bytes(b"incomplete")
        return Process()
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "test-lame")
    monkeypatch.setattr(audio_tools.subprocess, "Popen", launch)
    with pytest.raises(InterruptedError):
        audio_tools.encode_mp3(tmp_path / "input.wav", destination, cancel=cancel)
    assert ("terminate",) in calls
    assert calls[-1] == ("communicate", 2)
    assert not destination.exists()


def test_failed_lame_removes_partial_output_and_reports_failure(tmp_path, monkeypatch):
    destination = tmp_path / "partial.wav"
    class Process:
        returncode = 1
        def communicate(self, timeout=None):
            destination.write_bytes(b"incomplete")
            return "", "Invalid MP3 bitstream"
        def poll(self):
            return self.returncode
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "test-lame")
    monkeypatch.setattr(audio_tools.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(RuntimeError, match="Invalid MP3 bitstream"):
        audio_tools.decode_mp3(tmp_path / "broken.mp3", destination)
    assert not destination.exists()


def test_decoder_never_starts_over_an_existing_file(tmp_path, monkeypatch):
    destination = tmp_path / "keep.wav"
    destination.write_bytes(b"keep")
    monkeypatch.setattr(audio_tools, "find_lame_command", lambda: "test-lame")
    monkeypatch.setattr(audio_tools.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Started over existing file"))
    with pytest.raises(FileExistsError):
        audio_tools.decode_mp3(tmp_path / "song.mp3", destination)
    assert destination.read_bytes() == b"keep"
