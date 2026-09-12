"""Encode actual audio with the LAME files included in the Windows release."""

from array import array
import math
import os
from pathlib import Path
import shutil
import time
import wave

import pytest

from aps_midi_prep_tool_app import main_window


def test_bundled_lame_exports_mp3_without_msys2_on_path(tmp_path, monkeypatch, request):
    source = os.environ.get("APS_WINDOWS_LAME_DIR")
    if not source or not (Path(source) / "lame.exe").is_file():
        message = "Stage LAME with scripts/stage_windows_lame.ps1 and set APS_WINDOWS_LAME_DIR"
        if request.config.getoption("--windows-require-tools"):
            pytest.fail(message)
        pytest.skip(message)

    bundle = tmp_path / "APS release é" / "_internal"
    target = bundle / "bin"
    shutil.copytree(source, target)
    monkeypatch.setenv("PATH", os.pathsep.join([
        str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"],
    ]))
    monkeypatch.delenv("APS_MIDI_PREP_LAME", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_window.sys, "_MEIPASS", str(bundle), raising=False)
    assert shutil.which("lame") is None
    assert Path(main_window._find_lame_command()) == target / "lame.exe"

    wav_path = tmp_path / "Piano preview é.wav"
    mp3_path = tmp_path / "Piano preview é.mp3"
    sample_rate = 44100
    samples = array("h", (int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate))
                          for i in range(sample_rate)))
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())

    deadline = time.monotonic() + 30
    main_window._convert_wav_for_audio_export(
        str(wav_path), str(mp3_path), "mp3",
        cancel_callback=lambda: time.monotonic() >= deadline,
    )
    encoded = mp3_path.read_bytes()
    assert len(encoded) > 100
    # LAME's default output starts with an MPEG audio frame sync word.
    assert encoded[0] == 0xFF and encoded[1] & 0xE0 == 0xE0
