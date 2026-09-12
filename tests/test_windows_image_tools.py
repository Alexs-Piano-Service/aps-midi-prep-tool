"""Bundled tools and stalled subprocesses, without requiring physical hardware."""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import floppy_image


@pytest.mark.parametrize("root", ["bin", "aps_midi_prep_tool_app/bin"])
@pytest.mark.parametrize("folder,command", [
    ("mtools", "mformat"), ("7zip", "7z"), ("greaseweazle", "gw"),
    ("fluidsynth", "fluidsynth"), ("lame", "lame"),
])
def test_resolves_nested_tools_in_frozen_bundle(tmp_path, monkeypatch, root, folder, command):
    bundle = tmp_path / "_MEI123"
    tool = bundle / root / folder / f"{command}.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"bundled tool")
    tool.chmod(0o755)
    monkeypatch.setattr(floppy_image.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(floppy_image.sys, "frozen", True, raising=False)
    monkeypatch.setattr(floppy_image.sys, "executable", str(tmp_path / "APS.exe"))
    monkeypatch.setattr(floppy_image, "__file__", str(bundle / "aps_midi_prep_tool_app/floppy_image.py"))
    monkeypatch.setattr(floppy_image.shutil, "which", lambda _name: None)
    monkeypatch.setattr(floppy_image, "_command_name_variants", lambda name: [name, f"{name}.exe"])

    assert floppy_image._require_command(command) == str(tool)


def test_path_tools_take_precedence_over_bundle(monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "which", lambda _name: "installed-mcopy")
    monkeypatch.setattr(floppy_image, "_find_bundled_command", lambda *_: pytest.fail("PATH already resolved"))
    assert floppy_image._require_command("mcopy") == "installed-mcopy"


def test_missing_image_tool_has_actionable_message(monkeypatch):
    monkeypatch.setattr(floppy_image.shutil, "which", lambda _name: None)
    monkeypatch.setattr(floppy_image, "_find_bundled_command", lambda *_: None)
    with pytest.raises(floppy_image.FloppyImageError, match="complete APS build") as error:
        floppy_image._require_command("mformat")
    assert "mformat" in str(error.value)


def test_extraction_fallback_uses_bundled_mcopy(tmp_path, monkeypatch):
    tool = str(tmp_path / "bin/mtools/mcopy.exe")
    monkeypatch.setattr(floppy_image.shutil, "which", lambda _name: None)
    monkeypatch.setattr(floppy_image, "_find_bundled_command", lambda name: tool if name == "mcopy" else None)
    def unreadable(*_args):
        raise floppy_image.FloppyImageError("FAT12 reader could not decode this file")
    monkeypatch.setattr(floppy_image, "_read_fat12_file_bytes", unreadable)
    calls = []
    def run(args, *_args, **_kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"Recovered song")
    session = object.__new__(floppy_image.FloppyImageSession)
    monkeypatch.setattr(session, "_run_mtools", run)
    output = tmp_path / "song.fil"

    session._extract_from_image("image.img", "SONG.FIL", str(output))

    assert calls[0][0] == tool
    assert output.read_bytes() == b"Recovered song"


@pytest.mark.parametrize("cancellable", [False, True])
def test_stalled_tool_is_terminated_and_reaped(monkeypatch, cancellable):
    processes = []
    original_popen = subprocess.Popen
    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            processes.append(process)
        return process
    monkeypatch.setattr(floppy_image.subprocess, "Popen", popen)
    with pytest.raises(floppy_image.FloppyImageError, match="disk-image tool did not finish"):
        floppy_image._run_command(
            [sys.executable, "-c", "import time; time.sleep(60)"], "Image creation failed",
            cancel_callback=(lambda: False) if cancellable else None, timeout=0.2,
        )
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_tool_cancellation_reaps_child(monkeypatch):
    processes = []
    original_popen = subprocess.Popen
    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            processes.append(process)
        return process
    monkeypatch.setattr(floppy_image.subprocess, "Popen", popen)
    with pytest.raises(floppy_image.FloppyOperationCancelled):
        floppy_image._run_command(
            [sys.executable, "-c", "import time; time.sleep(60)"], "Image creation failed",
            cancel_callback=lambda: bool(processes),
        )
    assert len(processes) == 1 and processes[0].poll() is not None


def test_windows_cancellation_targets_the_owned_process_tree(monkeypatch):
    import ntpath

    commands = []
    waits = []
    process = SimpleNamespace(
        pid=12345, poll=lambda: None, wait=lambda **kwargs: waits.append(kwargs),
        terminate=lambda: pytest.fail("Tree shutdown succeeded"),
    )
    monkeypatch.setattr(floppy_image, "os", SimpleNamespace(
        name="nt", path=ntpath, environ={"SystemRoot": r"C:\Windows"},
    ))
    monkeypatch.setattr(floppy_image.subprocess, "run", lambda args, **kwargs: commands.append(args))
    floppy_image._terminate_process(process)
    assert commands == [[r"C:\Windows\System32\taskkill.exe", "/PID", "12345", "/T", "/F"]]
    assert waits == [{"timeout": 2}]


@pytest.mark.parametrize("cancellable", [False, True])
def test_tool_cannot_wait_for_hidden_console_input(cancellable):
    output = floppy_image._run_command(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], "Tool failed",
        cancel_callback=(lambda: False) if cancellable else None, timeout=5,
    )
    assert output.strip() == "''"
