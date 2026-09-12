"""The CI prerequisite check must report missing and unusable image tools."""

import subprocess
from unittest.mock import Mock

import pytest

from scripts import check_mtools


@pytest.fixture
def commands(monkeypatch):
    paths = {
        name: f"D:/runner temp/msys64/ucrt64/bin/{name}.exe"
        for name in check_mtools.MTOOLS_COMMANDS
    }
    monkeypatch.setattr(check_mtools.shutil, "which", paths.get)
    run = Mock(side_effect=lambda args, **kwargs: subprocess.CompletedProcess(
        args, 0, f"{args[0]} (GNU mtools) 4.0.49\n",
    ))
    monkeypatch.setattr(check_mtools.subprocess, "run", run)
    return paths, run


def test_reports_paths_and_versions_and_runs_only_version_commands(commands, capsys):
    paths, run = commands

    assert check_mtools.main() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert run.call_count == len(paths)
    for call, (name, path) in zip(run.call_args_list, paths.items()):
        assert f"{name}: {path}" in captured.out
        assert f"{path} (GNU mtools) 4.0.49" in captured.out
        assert call.args == ([path, "-V"],)
        assert not call.kwargs.get("shell", False)
        assert call.kwargs["timeout"] == 10
        assert call.kwargs["stdin"] == subprocess.DEVNULL


def test_reports_every_missing_command_and_checks_remaining_tools(commands, capsys):
    paths, run = commands
    del paths["mformat"]
    del paths["mren"]

    assert check_mtools.main() == 1

    captured = capsys.readouterr()
    assert "mformat: not found on PATH" in captured.err
    assert "mren: not found on PATH" in captured.err
    assert "Working mtools commands are required" in captured.err
    assert run.call_count == 3
    assert "mcopy:" in captured.out


def test_nonzero_exit_reports_tool_code_and_command_output(commands, capsys):
    paths, run = commands
    run.side_effect = lambda args, **kwargs: subprocess.CompletedProcess(
        args, 7 if args[0] == paths["mcopy"] else 0,
        "runtime initialization failed\n" if args[0] == paths["mcopy"] else "mtools 4.0.49\n",
    )

    assert check_mtools.main() == 1

    captured = capsys.readouterr()
    assert "mcopy: version check exited with code 7" in captured.err
    assert "runtime initialization failed" in captured.out
    assert run.call_count == len(paths)


@pytest.mark.parametrize("failure", [
    OSError("A required DLL was not found"),
    subprocess.TimeoutExpired(["mcopy.exe", "-V"], 10),
])
def test_launch_error_or_timeout_names_tool_and_continues(commands, capsys, failure):
    paths, run = commands

    def invoke(args, **kwargs):
        if args[0] == paths["mcopy"]:
            raise failure
        return subprocess.CompletedProcess(args, 0, "mtools 4.0.49\n")

    run.side_effect = invoke

    assert check_mtools.main() == 1

    captured = capsys.readouterr()
    assert "mcopy: could not run version check" in captured.err
    assert str(failure) in captured.err
    assert run.call_count == len(paths)
    assert "mren:" in captured.out
