"""Packaged LAME discovery and cancellable music-file conversion.

These helpers do not import the Qt main window. Callers use private staging
paths; public delivery is performed only after the complete output is checked.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from .subprocess_utils import windows_subprocess_kwargs


def resource_roots():
    roots = [str(Path(__file__).resolve().parent)]
    executable = getattr(sys, "executable", "")
    if executable:
        folder = str(Path(executable).parent)
        roots.extend([os.path.join(folder, "aps_midi_prep_tool_app"), folder])
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        roots.extend([os.path.join(bundle, "aps_midi_prep_tool_app"), bundle])
    appdir = os.environ.get("APPDIR", "")
    if appdir:
        roots.extend([os.path.join(appdir, "usr", "share", "aps-midi-prep-tool"),
                      os.path.join(appdir, "usr", "share", "sounds")])
    return roots


def find_lame_command(roots=None):
    override = os.environ.get("APS_MIDI_PREP_LAME", "").strip()
    candidates = [override] if override else []
    for root in resource_roots() if roots is None else roots:
        for folder in ("bin", "", os.path.join("usr", "bin")):
            candidates.extend(os.path.join(root, folder, name) for name in ("lame.exe", "lame"))
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("lame") or shutil.which("lame.exe") or ""


def check_cancel(cancel):
    if cancel is not None:
        stopped = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
        if stopped:
            raise InterruptedError("Audio conversion cancelled.")


def _run_lame(arguments, destination, *, cancel=None):
    check_cancel(cancel)
    command = find_lame_command()
    if not command:
        raise RuntimeError("LAME is required to read or export MP3 audio.")
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(f"Output already exists: {destination}")
    process = subprocess.Popen([command, *map(str, arguments)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               **windows_subprocess_kwargs())
    try:
        while True:
            check_cancel(cancel)
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        check_cancel(cancel)
        if process.returncode:
            detail = (stderr or stdout or "").strip().splitlines()
            raise RuntimeError("LAME audio conversion failed" + (": " + detail[-1] if detail else "."))
        if not destination.is_file() or not destination.stat().st_size:
            raise RuntimeError("LAME did not create an audio file.")
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        # The caller supplies a fresh private staging destination.
        destination.unlink(missing_ok=True)
        raise


def encode_mp3(wav_path, mp3_path, *, cancel=None):
    """Encode 44.1 kHz PCM as MPEG-1 Layer III, CBR 192 kbps.

    Keep the Info/LAME frame: its encoder delay and padding fields allow
    gapless-aware players to retain the PCM timeline. Hardware that ignores
    those fields requires playback calibration, not a universal MIDI offset.
    """
    _run_lame(["--silent", "--cbr", "-b", "192", "-q", "2", wav_path, mp3_path],
              mp3_path, cancel=cancel)


def decode_mp3(mp3_path, wav_path, *, cancel=None):
    """Decode using LAME's encoder-delay/padding handling into private PCM."""
    _run_lame(["--silent", "--decode", mp3_path, wav_path], wav_path, cancel=cancel)
