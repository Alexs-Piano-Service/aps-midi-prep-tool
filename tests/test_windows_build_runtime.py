"""A Windows build must retain every file from the verified HFE stage."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.verify_windows_hfe_stage import ARCHIVE_SHA256, RECEIPT, verify_stage


@pytest.fixture
def staged_runtime(tmp_path):
    contents = {"gw.exe": b"standalone executable", "lib/runtime.dll": b"runtime library",
                "licenses/LICENSE": b"redistribution notice"}
    for name, data in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (tmp_path / RECEIPT).write_text(json.dumps({
        "archive_sha256": ARCHIVE_SHA256,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    }), encoding="utf-8-sig")
    return tmp_path


def test_complete_stage_accepts_powershell_utf8_bom_receipt(staged_runtime):
    verify_stage(staged_runtime)


@pytest.mark.parametrize("change", ["missing_dll", "changed_dll", "missing_license", "extra_file", "unverified"])
def test_incomplete_changed_or_unverified_hfe_runtime_is_rejected(staged_runtime, change):
    if change == "missing_dll":
        (staged_runtime / "lib/runtime.dll").unlink()
    elif change == "changed_dll":
        (staged_runtime / "lib/runtime.dll").write_bytes(b"another version")
    elif change == "missing_license":
        (staged_runtime / "licenses/LICENSE").unlink()
    elif change == "extra_file":
        (staged_runtime / "extra.dll").write_bytes(b"unexpected runtime")
    else:
        receipt = staged_runtime / RECEIPT
        data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        data["archive_sha256"] = "0" * 64
        receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify_stage(staged_runtime)


def test_lone_gw_executable_cannot_be_packaged(tmp_path):
    (tmp_path / "gw.exe").write_bytes(b"executable without runtime")
    with pytest.raises(OSError):
        verify_stage(tmp_path)


def test_windows_packaging_scripts_parse(tmp_path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell parser runs in Windows CI")
    parser = tmp_path / "parse.ps1"
    parser.write_text('''param([string]$Repo)
$ErrorActionPreference = 'Stop'
foreach ($file in @('build/build_windows_main.ps1', 'scripts/stage_windows_hfe_tool.ps1',
                    'scripts/stage_windows_lame.ps1', 'scripts/test_windows_package.ps1')) {
    $parseErrors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Repo $file), [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
}
''', encoding="utf-8")
    result = subprocess.run([pwsh, "-NoProfile", "-File", str(parser),
                             str(Path(__file__).resolve().parents[1])], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
