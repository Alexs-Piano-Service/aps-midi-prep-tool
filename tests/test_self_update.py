"""Portable update preparation must leave the installed application untouched."""

import base64
import ctypes
from dataclasses import replace
import hashlib
import http.client
import io
import json
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from aps_midi_prep_tool_app import self_update as updater


VERSION = "9.8.7"


def appimage_bytes(architecture="x86_64", suffix=b"release"):
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[8:11] = b"AI\x02"
    struct.pack_into("<H", header, 18, 62 if architecture == "x86_64" else 183)
    return bytes(header) + suffix


def windows_bytes(machine=0x8664, suffix=b"release"):
    header = bytearray(128)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    header[64:68] = b"PE\0\0"
    struct.pack_into("<H", header, 68, machine)
    struct.pack_into("<H", header, 88, 0x20B)
    return bytes(header) + suffix


class Response(io.BytesIO):
    def __init__(self, data, *, headers=None, url="https://api.github.com/test", status=200):
        super().__init__(data)
        self.headers = headers or {}
        self.url = url
        self.status = status

    def geturl(self):
        return self.url


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    target_path = tmp_path / "Customer's renamed APS.AppImage"
    original = appimage_bytes(suffix=b"old release")
    target_path.write_bytes(original)
    target_path.chmod(0o755)
    target = updater.UpdateTarget(target_path, "linux-appimage", "x86_64")
    payload = appimage_bytes()
    asset_name = f"APSMidiPrepTool-{VERSION}-x86_64.AppImage"
    url = f"https://github.com/{updater.REPOSITORY}/releases/download/v{VERSION}/{asset_name}"
    state = {
        "target": target, "original": original, "payload": payload, "headers": {}, "calls": [],
        "release": {"tag_name": f"v{VERSION}", "draft": False, "prerelease": False, "assets": [{
            "name": asset_name, "state": "uploaded", "size": len(payload),
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(), "browser_download_url": url,
        }]},
    }
    (tmp_path / "APS.json").write_bytes(b"customer configuration")
    (tmp_path / "Music").mkdir()
    (tmp_path / "Music" / "song.mid").write_bytes(b"customer music")

    def open_url(address):
        state["calls"].append(address)
        if address == f"{updater.API_ROOT}/releases/tags/v{VERSION}":
            return Response(state.get("metadata", json.dumps(state["release"]).encode()))
        assert address == state["release"]["assets"][0]["browser_download_url"]
        if state.get("download_error"):
            raise state["download_error"]
        if state.get("change_original"):
            target_path.write_bytes(appimage_bytes(suffix=b"changed elsewhere"))
        return Response(state["payload"], headers=state["headers"])

    monkeypatch.setattr(updater, "_open_url", open_url)
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda _path: SimpleNamespace(free=10**12))
    return state


def assert_untouched(state):
    directory = state["target"].path.parent
    assert state["target"].path.read_bytes() == state["original"]
    assert (directory / "APS.json").read_bytes() == b"customer configuration"
    assert (directory / "Music" / "song.mid").read_bytes() == b"customer music"
    assert not list(directory.glob(".aps-update-*"))


def prepare(state, **kwargs):
    return updater.prepare_update(VERSION, target=state["target"], **kwargs)


def create_symlink_or_skip(path, destination, *, directory=False):
    try:
        path.symlink_to(destination, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("This environment does not support creating symbolic links")
    if not path.is_symlink():
        pytest.skip("This environment did not create the requested symbolic link")


def test_verified_download_is_staged_beside_renamed_app_and_keeps_neighbors(preparation):
    progress = []
    result = prepare(preparation, progress_callback=lambda *pair: progress.append(pair))
    assert result.target == preparation["target"]
    assert result.directory.parent == preparation["target"].path.parent
    assert result.directory.name.startswith(".aps-update-")
    assert result.payload == result.directory / "payload.AppImage"
    assert result.payload.read_bytes() == preparation["payload"]
    assert result.sha256 == hashlib.sha256(preparation["payload"]).hexdigest()
    assert result.original_sha256 == hashlib.sha256(preparation["original"]).hexdigest()
    assert result.payload.stat().st_mode & 0o777 == result.target.path.stat().st_mode & 0o777
    assert progress == [(0, len(preparation["payload"])), (len(preparation["payload"]), len(preparation["payload"]))]
    assert preparation["calls"] == [
        f"{updater.API_ROOT}/releases/tags/v{VERSION}",
        preparation["release"]["assets"][0]["browser_download_url"],
    ]
    updater.cleanup_staged_update(result)
    updater.cleanup_staged_update(result)
    assert_untouched(preparation)


@pytest.mark.parametrize("change", [
    {"draft": True}, {"draft": "false"}, {"prerelease": True}, {"prerelease": 0},
    {"tag_name": "v9.8.8"}, {"assets": None}, {"assets": []},
])
def test_unpublished_wrong_or_missing_release_is_rejected(preparation, change):
    preparation["release"].update(change)
    with pytest.raises(updater.UpdateError):
        prepare(preparation)
    assert len(preparation["calls"]) == 1
    assert_untouched(preparation)


@pytest.mark.parametrize("change", [
    {"state": "starter"}, {"state": "uploading"}, {"size": 0}, {"size": -1}, {"size": True},
    {"size": updater.MAX_DOWNLOAD_BYTES + 1}, {"size": "100"},
    {"digest": None}, {"digest": ""}, {"digest": "sha1:" + "a" * 40},
    {"digest": "sha256:" + "z" * 64}, {"digest": "sha256:1234"},
    {"browser_download_url": "https://evil.example/update.AppImage"},
    {"browser_download_url": "https://github.com/untrusted/aps/releases/download/v9.8.7/app.AppImage"},
    {"name": "APSMIDIPrepTool-9.8.7-Setup.exe"},
    {"name": "APSMidiPrepTool-9.8.7-aarch64.AppImage"},
])
def test_unverified_wrong_architecture_or_arbitrary_asset_never_downloads(preparation, change):
    preparation["release"]["assets"][0].update(change)
    with pytest.raises(updater.UpdateError):
        prepare(preparation)
    assert len(preparation["calls"]) == 1
    assert_untouched(preparation)


def test_duplicate_matching_asset_is_rejected(preparation):
    preparation["release"]["assets"] *= 2
    with pytest.raises(updater.UpdateError, match="unique"):
        prepare(preparation)
    assert_untouched(preparation)


@pytest.mark.parametrize("metadata", [b"not json", b"[]", b"null", b"\xff", b"x" * (updater.MAX_METADATA_BYTES + 1)],
                         ids=["invalid-json", "array", "null", "invalid-encoding", "too-large"])
def test_invalid_or_oversized_metadata_fails_without_touching_application(preparation, metadata):
    preparation["metadata"] = metadata
    with pytest.raises(updater.UpdateError):
        prepare(preparation)
    assert_untouched(preparation)


@pytest.mark.parametrize("version", ["0.0.0", updater.APP_VERSION, "9.8.7-beta1", "9.8.7/../../payload", "v9.8.7?bad", "09.8.7", "9.8"])
def test_invalid_prerelease_equal_and_older_versions_never_contact_server(preparation, version):
    with pytest.raises(updater.UpdateError):
        updater.prepare_update(version, target=preparation["target"])
    assert not preparation["calls"]
    assert_untouched(preparation)


@pytest.mark.parametrize("change", ["checksum", "truncated", "extra_bytes", "wrong_magic", "wrong_architecture", "not_appimage"])
def test_bad_download_is_removed_without_replacing_application(preparation, change):
    if change == "checksum":
        preparation["release"]["assets"][0]["digest"] = "sha256:" + "a" * 64
    elif change == "truncated":
        preparation["payload"] = preparation["payload"][:-1]
    elif change == "extra_bytes":
        preparation["payload"] += b"extra"
    else:
        if change == "wrong_magic":
            preparation["payload"] = b"not an application"
        elif change == "wrong_architecture":
            preparation["payload"] = appimage_bytes("aarch64")
        else:
            preparation["payload"] = appimage_bytes().replace(b"AI\x02", b"\0\0\0")
        preparation["release"]["assets"][0].update(
            digest="sha256:" + hashlib.sha256(preparation["payload"]).hexdigest(), size=len(preparation["payload"]),
        )
    with pytest.raises(updater.UpdateError):
        prepare(preparation)
    assert_untouched(preparation)


@pytest.mark.parametrize("length", ["0", "junk", "-1", "99999"])
def test_wrong_content_length_is_rejected(preparation, length):
    preparation["headers"] = {"Content-Length": length}
    with pytest.raises(updater.UpdateError, match="size"):
        prepare(preparation)
    assert_untouched(preparation)


def test_cancellation_before_network_preserves_everything(preparation):
    with pytest.raises(updater.UpdateCancelled):
        prepare(preparation, cancel_callback=lambda: True)
    assert not preparation["calls"]
    assert_untouched(preparation)


def test_cancellation_during_download_removes_partial_file(preparation, monkeypatch):
    monkeypatch.setattr(updater, "CHUNK_BYTES", 16)
    progress = []
    with pytest.raises(updater.UpdateCancelled):
        prepare(preparation, progress_callback=lambda received, total: progress.append(received),
                cancel_callback=lambda: bool(progress and progress[-1] > 0))
    assert progress == [0, 16]
    assert_untouched(preparation)


@pytest.mark.parametrize("error", [urllib.error.URLError("Network disconnected"),
                                  http.client.IncompleteRead(b"partial", 100)])
def test_network_failure_removes_empty_stage(preparation, error):
    preparation["download_error"] = error
    with pytest.raises(updater.UpdateError, match="could not be prepared"):
        prepare(preparation)
    assert_untouched(preparation)


def test_write_failure_removes_partial_stage(preparation, monkeypatch):
    def fail(_descriptor):
        raise OSError("USB drive disconnected")

    monkeypatch.setattr(updater.os, "fsync", fail)
    with pytest.raises(updater.UpdateError, match="USB drive disconnected"):
        prepare(preparation)
    assert_untouched(preparation)


def test_insufficient_disk_space_does_not_start_download(preparation, monkeypatch):
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    with pytest.raises(updater.UpdateError, match="free space"):
        prepare(preparation)
    assert len(preparation["calls"]) == 1
    assert_untouched(preparation)


def test_read_only_target_is_rejected_before_network(preparation):
    preparation["target"].path.chmod(0o555)
    with pytest.raises(updater.UpdateError, match="read-only"):
        prepare(preparation)
    assert not preparation["calls"]
    assert_untouched(preparation)


def test_changed_installed_application_aborts_without_reverting_external_change(preparation):
    preparation["change_original"] = True
    with pytest.raises(updater.UpdateError, match="changed during"):
        prepare(preparation)
    assert preparation["target"].path.read_bytes() == appimage_bytes(suffix=b"changed elsewhere")
    assert not list(preparation["target"].path.parent.glob(".aps-update-*"))


@pytest.mark.parametrize("url", [
    "http://github.com/file", "https://github.com.evil.example/file", "https://evil.example/file",
    "file:///tmp/update", "https://user@github.com/file", "https://github.com:444/file",
    "https://github.com/file#fragment", "https://github.com/file\n", "https://github.com:/file\t",
])
def test_untrusted_redirect_is_rejected_before_following(url):
    handler = updater._TrustedRedirectHandler()
    request = urllib.request.Request("https://github.com/original")
    with pytest.raises(updater.UpdateError, match="untrusted"):
        handler.redirect_request(request, None, 302, "Found", {}, url)


@pytest.mark.parametrize("host", sorted(updater.ALLOWED_HOSTS))
def test_https_redirect_to_expected_github_delivery_hosts_is_allowed(host):
    handler = updater._TrustedRedirectHandler()
    request = urllib.request.Request("https://github.com/original")
    url = f"https://{host}/signed-asset?token=delivery-token"
    redirected = handler.redirect_request(request, None, 302, "Found", {}, url)
    assert redirected.full_url == url


def test_network_opener_uses_verified_tls_and_rejects_partial_response(monkeypatch):
    response = Response(b"partial", status=206)
    calls = []

    def opener(*handlers):
        assert any(isinstance(handler, updater._TrustedRedirectHandler) for handler in handlers)
        return SimpleNamespace(open=lambda request, timeout: (calls.append((request, timeout)) or response))

    monkeypatch.setattr(updater.urllib.request, "build_opener", opener)
    with pytest.raises(updater.UpdateError, match="complete"):
        updater._open_url("https://api.github.com/test")
    assert calls[0][1] == 15
    assert response.closed


def test_cleanup_rejects_neighbor_stage(preparation, tmp_path):
    result = prepare(preparation)
    neighbor = tmp_path / "Music"
    with pytest.raises(updater.UpdateError, match="safe to remove"):
        updater.cleanup_staged_update(replace(result, directory=neighbor))
    updater.cleanup_staged_update(result)
    assert_untouched(preparation)


def test_cleanup_rejects_symlink_stage(preparation, tmp_path):
    neighbor = tmp_path / "Music"
    symlink = tmp_path / ".aps-update-symlink"
    create_symlink_or_skip(symlink, neighbor, directory=True)
    result = prepare(preparation)
    with pytest.raises(updater.UpdateError, match="safe to remove"):
        updater.cleanup_staged_update(replace(result, directory=symlink))
    assert (neighbor / "song.mid").read_bytes() == b"customer music"
    symlink.unlink()
    updater.cleanup_staged_update(result)
    assert_untouched(preparation)


def test_cleanup_unlinks_internal_symlink_without_following_it(preparation):
    result = prepare(preparation)
    try:
        create_symlink_or_skip(result.directory / "music-link", result.target.path.parent / "Music", directory=True)
    finally:
        updater.cleanup_staged_update(result)
    assert_untouched(preparation)


@pytest.fixture
def frozen_build(tmp_path, monkeypatch):
    installation = tmp_path / "USB"
    installation.mkdir()
    executable = installation / "My APS.exe"
    executable.write_bytes(windows_bytes())
    extraction = tmp_path / "_MEI123"
    extraction.mkdir()
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(executable))
    monkeypatch.setattr(updater.sys, "_MEIPASS", str(extraction), raising=False)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(updater, "build_identity", lambda: {})
    monkeypatch.delenv("APPIMAGE", raising=False)
    return executable


def test_source_checkout_never_targets_python_interpreter(frozen_build, monkeypatch):
    monkeypatch.setattr(updater.sys, "frozen", False)
    assert updater.detect_update_target() is None


def test_legacy_windows_onefile_uses_external_executable(frozen_build):
    assert updater.detect_update_target() == updater.UpdateTarget(frozen_build, "windows-onefile", "x86_64")


@pytest.mark.parametrize("relative_runtime", [".", "_internal", "runtime/subdirectory"])
def test_legacy_directory_bundle_cannot_replace_only_its_exe(frozen_build, monkeypatch, relative_runtime):
    monkeypatch.setattr(updater.sys, "_MEIPASS", str(frozen_build.parent / relative_runtime))
    assert updater.detect_update_target() is None


@pytest.mark.parametrize("identity", [
    {"package_kind": "windows-onedir", "architecture": "x86_64"},
    {"package_kind": "linux-appimage", "architecture": "x86_64"},
    {"package_kind": "windows-onefile", "architecture": "x86"},
    {"package_kind": "windows-onefile", "architecture": "aarch64"},
    {"package_kind": "unknown", "architecture": "x86_64"},
])
def test_explicit_unsupported_package_markers_disable_in_place_update(frozen_build, monkeypatch, identity):
    monkeypatch.setattr(updater, "build_identity", lambda: identity)
    assert updater.detect_update_target() is None


def test_explicit_onefile_marker_handles_extraction_inside_installation_folder(frozen_build, monkeypatch):
    monkeypatch.setattr(updater, "build_identity", lambda: {"package_kind": "windows-onefile", "architecture": "x86_64"})
    monkeypatch.setattr(updater.sys, "_MEIPASS", str(frozen_build.parent / "_MEI123"))
    assert updater.detect_update_target().path == frozen_build


@pytest.mark.parametrize("architecture", ["x86_64", "aarch64"])
def test_appimage_targets_external_file_using_build_architecture(frozen_build, monkeypatch, architecture):
    image = frozen_build.parent / "Custom APS filename"
    image.write_bytes(appimage_bytes(architecture))
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setattr(updater, "build_identity", lambda: {"package_kind": "linux-appimage", "architecture": architecture})
    assert updater.detect_update_target() == updater.UpdateTarget(image, "linux-appimage", architecture)


def test_linux_directory_build_has_no_update_target(frozen_build, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert updater.detect_update_target() is None


def test_symlink_application_is_not_replaced(frozen_build, monkeypatch):
    alias = frozen_build.with_name("Alias.exe")
    create_symlink_or_skip(alias, frozen_build)
    monkeypatch.setattr(updater.sys, "executable", str(alias))
    assert updater.detect_update_target() is None


@pytest.mark.parametrize("machine", [0x14C, 0xAA64])
def test_windows_binary_machine_must_match_supported_architecture(tmp_path, machine):
    path = tmp_path / "update.exe"
    path.write_bytes(windows_bytes(machine))
    with pytest.raises(updater.UpdateError, match="64-bit Windows"):
        updater._verify_binary(path, updater.UpdateTarget(path, "windows-onefile", "x86_64"))


@pytest.fixture
def signature_process(monkeypatch):
    state = {"installed": {"status": "Valid", "subject": "CN=Alex's Piano Service LLC"},
             "downloaded": {"status": "Valid", "subject": "CN=Alex's Piano Service LLC"},
             "returncode": 0, "calls": [], "killed": False}

    class Process:
        @property
        def returncode(self):
            return state["returncode"]

        def communicate(self, timeout=None):
            if state.get("timeout") and timeout is not None:
                raise subprocess.TimeoutExpired("powershell", timeout)
            return json.dumps({key: state[key] for key in ("installed", "downloaded")}).encode(), b""

        def kill(self):
            state["killed"] = True

    def popen(command, **kwargs):
        state["calls"].append((command, kwargs))
        return Process()

    monkeypatch.setattr(updater, "_powershell_path", lambda: Path("/trusted/windows/powershell.exe"))
    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    return state


def test_windows_authenticode_uses_static_script_and_literal_path_environment(signature_process):
    installed = Path("/USB/O'Brien; $(calc).exe")
    downloaded = Path("/USB/update & new.exe")
    updater._verify_windows_signatures(installed, downloaded, None)
    command, kwargs = signature_process["calls"][0]
    assert command[0] == str(Path("/trusted/windows/powershell.exe"))
    script = base64.b64decode(command[-1]).decode("utf-16le")
    assert str(installed) not in script
    assert str(downloaded) not in script
    assert "-LiteralPath $env:APS_UPDATE_INSTALLED" in script
    assert kwargs["env"]["APS_UPDATE_INSTALLED"] == str(installed)
    assert kwargs["env"]["APS_UPDATE_DOWNLOADED"] == str(downloaded)


@pytest.mark.parametrize("launch_fails", [False, True])
def test_system_signature_tool_does_not_inherit_bundled_dll_paths(tmp_path, monkeypatch, launch_fails):
    bundle = tmp_path / "_MEI bundled libraries"
    system = tmp_path / "System32"
    environment = {"PATH": updater.os.pathsep.join([str(bundle), str(bundle / "PySide6"), str(system)]),
                   "APS_UPDATE_INSTALLED": str(tmp_path / "APS.exe")}
    original_environment = environment.copy()
    calls = []
    current = {"directory": str(bundle)}

    class Kernel:
        def GetDllDirectoryW(self, size, buffer):
            assert size > len(current["directory"])
            buffer.value = current["directory"]
            return len(buffer.value)

        def SetDllDirectoryW(self, directory):
            calls.append(directory)
            current["directory"] = directory
            return 1

    def launch(command, **kwargs):
        assert current["directory"] is None
        assert kwargs["env"]["PATH"] == str(system)
        assert kwargs["env"]["APS_UPDATE_INSTALLED"] == environment["APS_UPDATE_INSTALLED"]
        assert kwargs["cwd"] == str(system)
        if launch_fails:
            raise OSError("Windows could not start PowerShell")
        return "process"

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel()), raising=False)
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    command = [str(system / "powershell.exe")]
    if launch_fails:
        with pytest.raises(OSError):
            updater._launch_signature_process(command, environment)
    else:
        assert updater._launch_signature_process(command, environment) == "process"
    assert current["directory"] == str(bundle)
    assert calls == [None, str(bundle)]
    assert environment == original_environment


@pytest.mark.parametrize("side,change", [
    ("installed", {"status": "NotSigned"}), ("downloaded", {"status": "NotSigned"}),
    ("downloaded", {"status": "HashMismatch"}), ("downloaded", {"status": "UnknownError"}),
    ("downloaded", {"subject": "CN=Another Trusted Publisher"}),
    ("installed", {"subject": ""}), ("installed", {"subject": None}),
])
def test_windows_requires_valid_current_and_new_signature_from_same_publisher(signature_process, side, change):
    signature_process[side].update(change)
    with pytest.raises(updater.UpdateError, match="same publisher"):
        updater._verify_windows_signatures(Path("old.exe"), Path("new.exe"), None)


def test_windows_signature_process_failure_is_rejected(signature_process):
    signature_process["returncode"] = 1
    with pytest.raises(updater.UpdateError, match="could not verify"):
        updater._verify_windows_signatures(Path("old.exe"), Path("new.exe"), None)


def test_windows_signature_check_can_be_cancelled(signature_process):
    with pytest.raises(updater.UpdateCancelled):
        updater._verify_windows_signatures(Path("old.exe"), Path("new.exe"), lambda: bool(signature_process["calls"]))
    assert signature_process["killed"]


def test_windows_signature_check_has_a_deadline(signature_process, monkeypatch):
    signature_process["timeout"] = True
    ticks = iter([0, 1, 50])
    monkeypatch.setattr(updater.time, "monotonic", lambda: next(ticks))
    with pytest.raises(updater.UpdateError, match="timed out"):
        updater._verify_windows_signatures(Path("old.exe"), Path("new.exe"), None)
    assert signature_process["killed"]


def test_windows_preparation_runs_signature_check_and_cleans_stage_on_rejection(preparation, monkeypatch):
    old = windows_bytes(suffix=b"old release")
    payload = windows_bytes()
    path = preparation["target"].path
    path.write_bytes(old)
    preparation.update(original=old, payload=payload, target=updater.UpdateTarget(path, "windows-onefile", "x86_64"))
    preparation["release"]["assets"][0].update(
        name="APSMIDIPrepTool.exe", size=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        browser_download_url=f"https://github.com/{updater.REPOSITORY}/releases/download/v{VERSION}/APSMIDIPrepTool.exe",
    )

    def reject(installed, downloaded, _cancel):
        assert installed == path
        assert downloaded.name == "payload.exe"
        assert downloaded.read_bytes() == payload
        raise updater.UpdateError("Publisher does not match")

    monkeypatch.setattr(updater, "_verify_windows_signatures", reject)
    with pytest.raises(updater.UpdateError, match="Publisher"):
        prepare(preparation)
    assert_untouched(preparation)
