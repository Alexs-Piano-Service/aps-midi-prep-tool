"""A disk helper remains cancellable after its output pipe closes."""

import subprocess
import sys
import time

import pytest

from aps_midi_prep_tool_app import floppy_image


def test_cancel_after_output_eof_terminates_still_running_helper(tmp_path, monkeypatch):
    marker = tmp_path / "output-closed"
    code = """
import os
from pathlib import Path
import sys
import time
print('Reading floppy', flush=True)
os.close(1)
os.close(2)
Path(sys.argv[1]).write_text('closed')
time.sleep(5)
"""
    processes = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(floppy_image.subprocess, "Popen", popen)
    closed_at = None

    def cancelled():
        nonlocal closed_at
        if marker.exists() and closed_at is None:
            closed_at = time.monotonic()
        # Give the reader time to consume EOF before requesting cancellation;
        # this specifically exercises the subsequent process-exit wait.
        return closed_at is not None and time.monotonic() - closed_at >= 0.3

    lines = []
    started = time.monotonic()
    try:
        with pytest.raises(floppy_image.FloppyOperationCancelled):
            floppy_image._run_streaming_command(
                [sys.executable, "-c", code, str(marker)], "Read failed",
                line_callback=lines.append, cancel_callback=cancelled,
            )
        assert marker.exists()
        assert lines == ["Reading floppy"]
        assert time.monotonic() - started < 3
        assert processes[0].poll() is not None
        assert processes[0].returncode != 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_output_eof_waits_for_actual_exit_status(exit_code):
    code = f"""
import os
import time
print('Final progress', flush=True)
os.close(1)
os.close(2)
time.sleep(0.2)
os._exit({exit_code})
"""
    command = [sys.executable, "-c", code]
    if exit_code:
        with pytest.raises(floppy_image.FloppyImageError, match="Read failed: Final progress"):
            floppy_image._run_streaming_command(command, "Read failed")
    else:
        assert floppy_image._run_streaming_command(command, "Read failed") == "Final progress"
