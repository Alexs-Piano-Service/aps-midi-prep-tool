"""Native Windows integration tests; tool absence is never a passing result."""

import os
from pathlib import Path
import shutil
import tempfile

import pytest

from aps_midi_prep_tool_app import floppy_image


MTOOLS = ("mformat", "mcopy", "mdir", "mdel", "mren")


def pytest_addoption(parser):
    group = parser.getgroup("Windows integration tests")
    group.addoption("--windows-require-tools", action="store_true",
                    help="Fail instead of skipping when real mtools/Greaseweazle/staged LAME are missing.")


def pytest_collection_modifyitems(items):
    for item in items:
        if Path(__file__).parent in Path(item.path).parents:
            item.add_marker(pytest.mark.skipif(os.name != "nt", reason="Requires native Windows"))


def _missing(request, message):
    if request.config.getoption("--windows-require-tools"):
        pytest.fail(message)
    pytest.skip(message + " (use --windows-require-tools for a release gate)")


@pytest.fixture(scope="session")
def windows_tool_sources():
    # Resolve BEFORE the individual test removes development tools from PATH.
    mtools_dir = os.environ.get("APS_WINDOWS_MTOOLS_DIR")
    commands = {name: str(Path(mtools_dir) / (name + ".exe")) if mtools_dir
                else shutil.which(name) or floppy_image._find_bundled_command(name)
                for name in MTOOLS}
    gw_dir = os.environ.get("APS_WINDOWS_GW_DIR")
    return commands, gw_dir, floppy_image._find_gw()


@pytest.fixture
def bundled_mtools(tmp_path, monkeypatch, request, windows_tool_sources):
    commands, _, _ = windows_tool_sources
    missing = [name for name, path in commands.items() if not path or not Path(path).is_file()]
    if missing:
        _missing(request, "Install mtools or set APS_WINDOWS_MTOOLS_DIR: " + ", ".join(missing))
    # Keep the bundle shallow, like PyInstaller's _MEI directory. A frozen
    # helper has deeply nested extension modules subject to Windows DLL limits.
    staging = tempfile.TemporaryDirectory(prefix="aps_win_")
    request.addfinalizer(staging.cleanup)
    bundle = Path(staging.name) / "APS release é" / "_internal"
    target = bundle / "aps_midi_prep_tool_app" / "bin" / "mtools"
    target.mkdir(parents=True)
    for name, source in commands.items():
        shutil.copy2(source, target / (name + ".exe"))
    for directory in {Path(path).parent for path in commands.values()}:
        for dll in directory.glob("*.dll"):
            shutil.copy2(dll, target / dll.name)
    monkeypatch.setenv("PATH", os.pathsep.join([
        str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"],
    ]))
    for name in ("MTOOLSRC", "MTOOLS_SKIP_CHECK", "MTOOLS_NO_VFAT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(floppy_image.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(floppy_image, "__file__", str(bundle / "aps_midi_prep_tool_app/floppy_image.py"))
    assert all(shutil.which(name) is None for name in MTOOLS)
    for name in MTOOLS:
        assert Path(floppy_image._require_command(name)) == target / (name + ".exe")
    yield bundle


@pytest.fixture
def image_extension(request, bundled_mtools, windows_tool_sources, monkeypatch):
    extension = request.param
    if extension == "hfe":
        _, gw_dir, gw_command = windows_tool_sources
        if gw_dir:
            target = bundled_mtools / "aps_midi_prep_tool_app/bin/greaseweazle"
            shutil.copytree(gw_dir, target)
            gw_command = str(target / "gw.exe")
        if not gw_command or not Path(gw_command).is_file():
            _missing(request, "Install Greaseweazle or set APS_WINDOWS_GW_DIR to its extracted release folder")
        # A pip-installed CLI is sufficient for source integration. Set GW_DIR
        # to test the standalone helper and its DLLs with the same restricted PATH.
        monkeypatch.setattr(floppy_image, "_find_gw", lambda: gw_command)
    return extension
