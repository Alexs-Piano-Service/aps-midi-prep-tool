"""Package identity must describe the actual binary before enabling updates."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from aps_midi_prep_tool_app import build_info
from scripts import write_build_info


@pytest.mark.parametrize("package_kind", write_build_info.PACKAGE_KINDS)
@pytest.mark.parametrize("architecture,expected", [
    ("AMD64", "x86_64"), ("x86_64", "x86_64"),
    ("ARM64", "aarch64"), ("aarch64", "aarch64"),
])
def test_packaged_identity_retains_source_identity_and_normalizes_architecture(
        tmp_path, monkeypatch, package_kind, architecture, expected):
    monkeypatch.setattr(write_build_info, "checkout_identity", lambda _root: {
        "commit": "a" * 40, "dirty": False,
    })
    destination = tmp_path / "bundle" / "build-info.json"
    write_build_info.write_build_info(destination, package_kind=package_kind,
                                     architecture=architecture)
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "commit": "a" * 40, "dirty": False,
        "package_kind": package_kind, "architecture": expected,
    }


@pytest.mark.parametrize("target,expected", [("win-amd64", "x86_64"), ("win-arm64", "aarch64")])
def test_windows_marker_describes_python_target_on_emulated_architectures(monkeypatch, target, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(write_build_info.sysconfig, "get_platform", lambda: target)
    monkeypatch.setattr(write_build_info.platform, "machine", lambda: "ARM64")
    assert write_build_info.interpreter_architecture() == expected


def test_32_bit_python_cannot_mislabel_itself_as_64_bit_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(write_build_info.sysconfig, "get_platform", lambda: "win32")
    monkeypatch.setattr(write_build_info.platform, "machine", lambda: "AMD64")
    with pytest.raises(ValueError, match="architecture"):
        write_build_info.interpreter_architecture()


@pytest.mark.parametrize("arguments", [
    ["--package-kind", "unknown"], ["--architecture", "x86_64"],
    ["--package-kind", "windows-onefile", "--architecture", "x86"],
])
def test_invalid_packaging_marker_fails_before_overwriting_existing_info(tmp_path, arguments):
    destination = tmp_path / "build-info.json"
    original = '{"commit": "existing"}\n'
    destination.write_text(original, encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(Path(write_build_info.__file__)), str(destination), *arguments,
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert destination.read_text(encoding="utf-8") == original


def test_legacy_cli_writes_only_source_identity_and_removes_stale_package_marker(tmp_path):
    destination = tmp_path / "build-info.json"
    destination.write_text(
        '{"commit": "old", "package_kind": "windows-onefile", "architecture": "x86_64"}',
        encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(Path(write_build_info.__file__)), str(destination),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    identity = json.loads(destination.read_text(encoding="utf-8"))
    assert set(identity) == {"commit", "dirty"}
    assert isinstance(identity["commit"], str)
    assert identity["dirty"] is None or isinstance(identity["dirty"], bool)


def test_legacy_identity_does_not_require_a_supported_package_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(write_build_info, "checkout_identity", lambda _root: {
        "commit": "a" * 40, "dirty": False,
    })
    monkeypatch.setattr(write_build_info, "interpreter_architecture",
                        lambda: pytest.fail("Legacy identity must not inspect the package architecture"))
    destination = tmp_path / "build-info.json"
    write_build_info.write_build_info(destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"commit": "a" * 40, "dirty": False}


def test_packaged_identity_reader_exposes_embedded_update_marker(tmp_path, monkeypatch):
    destination = tmp_path / "build-info.json"
    write_build_info.write_build_info(destination, package_kind="linux-appimage",
                                     architecture="x86_64")
    monkeypatch.setattr(build_info, "__file__", str(tmp_path / "build_info.py"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    build_info.build_identity.cache_clear()
    try:
        identity = build_info.build_identity()
        assert identity["package_kind"] == "linux-appimage"
        assert identity["architecture"] == "x86_64"
    finally:
        build_info.build_identity.cache_clear()


def test_development_checkout_never_inherits_an_old_packaging_marker(tmp_path, monkeypatch):
    (tmp_path / "build-info.json").write_text(
        '{"package_kind": "windows-onefile", "architecture": "x86_64"}', encoding="utf-8")
    monkeypatch.setattr(build_info, "__file__", str(tmp_path / "build_info.py"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(build_info, "checkout_identity", lambda _root: {"commit": "source", "dirty": True})
    build_info.build_identity.cache_clear()
    try:
        assert build_info.build_identity() == {"commit": "source", "dirty": True}
    finally:
        build_info.build_identity.cache_clear()
