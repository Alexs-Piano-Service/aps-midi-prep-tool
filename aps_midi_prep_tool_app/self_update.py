"""Prepare an authenticated release beside a portable application.

This module never replaces the running application. The detached installer must
recheck both recorded hashes before moving any files. GitHub repository access
controls and HTTPS authenticate release metadata; Windows additionally requires
the same Authenticode publisher as the installed, validly signed application.
"""

from dataclasses import dataclass
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import certifi

from .app_info import APP_NAME, APP_VERSION
from .build_info import build_identity


REPOSITORY = "Alexs-Piano-Service/aps-midi-prep-tool"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
SPACE_MARGIN_BYTES = 16 * 1024 * 1024
ALLOWED_HOSTS = frozenset({
    "api.github.com", "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
})


class UpdateError(Exception):
    """An update could not be safely prepared."""


class UpdateCancelled(UpdateError):
    """The user cancelled preparation; the installed application is unchanged."""


@dataclass(frozen=True)
class UpdateTarget:
    path: Path
    kind: str
    architecture: str


@dataclass(frozen=True)
class StagedUpdate:
    target: UpdateTarget
    directory: Path
    payload: Path
    version: str
    sha256: str
    original_sha256: str


def _architecture(value):
    return {"amd64": "x86_64", "x64": "x86_64", "x86_64": "x86_64",
            "arm64": "aarch64", "aarch64": "aarch64"}.get(str(value).lower())


def detect_update_target():
    """Identify supported external packages, never an interpreter or AppDir."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        identity = build_identity()
        if not isinstance(identity, dict):
            return None
        marker = identity.get("package_kind")
        architecture = _architecture(identity.get("architecture", platform.machine()))
        if sys.platform == "win32":
            if architecture != "x86_64" or struct.calcsize("P") != 8:
                return None
            executable = Path(sys.executable).absolute()
            if marker is not None and marker != "windows-onefile":
                return None
            if marker is None:
                extraction = getattr(sys, "_MEIPASS", None)
                if not extraction or not Path(extraction).is_absolute():
                    return None
                # A directory build can keep its runtime either alongside its
                # executable or in _internal. Replacing only that EXE is unsafe.
                if Path(extraction).resolve().is_relative_to(executable.parent.resolve()):
                    return None
            kind = "windows-onefile"
        elif sys.platform.startswith("linux"):
            if architecture not in {"x86_64", "aarch64"}:
                return None
            if marker is not None and marker != "linux-appimage":
                return None
            appimage = os.environ.get("APPIMAGE", "")
            if not appimage or not Path(appimage).is_absolute():
                return None
            executable = Path(appimage)
            kind = "linux-appimage"
        else:
            return None
        if executable.is_symlink() or not executable.is_file():
            return None
        # Resolve parent aliases and Windows path casing once so the detached
        # helper can validate the exact installation directory consistently.
        return UpdateTarget(executable.resolve(), kind, architecture)
    except (OSError, ValueError, TypeError):
        return None


def _check_cancel(cancel_callback):
    if cancel_callback is not None and cancel_callback():
        raise UpdateCancelled("The update was cancelled.")


def _stable_version(value):
    text = str(value).strip()
    if text.startswith("v"):
        text = text[1:]
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", text):
        raise UpdateError("Only stable release versions can be installed automatically.")
    return text, tuple(int(part) for part in text.split("."))


def _validate_https_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
                 and parsed.port in {None, 443} and parsed.username is None
                 and parsed.password is None and not parsed.fragment
                 and not any(ord(character) < 33 for character in url))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise UpdateError("The update server returned an untrusted download address.")


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url):
    _validate_https_url(url)
    context = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(
        _TrustedRedirectHandler(), urllib.request.HTTPSHandler(context=context),
    )
    request = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        "Accept": "application/vnd.github+json" if url.startswith(API_ROOT) else "application/octet-stream",
        "Accept-Encoding": "identity",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    response = opener.open(request, timeout=15)
    try:
        _validate_https_url(response.geturl())
        if response.status != 200:
            raise UpdateError("The update server did not return a complete download.")
    except BaseException:
        response.close()
        raise
    return response


def _asset_for_release(version, target, cancel_callback):
    _check_cancel(cancel_callback)
    with _open_url(f"{API_ROOT}/releases/tags/v{version}") as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    _check_cancel(cancel_callback)
    if len(raw) > MAX_METADATA_BYTES:
        raise UpdateError("The release information is too large.")
    try:
        release = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise UpdateError("The update server returned invalid release information.") from exc
    if (not isinstance(release, dict) or release.get("tag_name") != f"v{version}"
            or release.get("draft") is not False or release.get("prerelease") is not False):
        raise UpdateError("The requested version is not a published stable release.")
    # Published standalone EXEs use both names; setup EXEs are never suitable
    # for replacing the running portable application.
    names = (("APSMIDIPrepTool.exe", "APS.MIDI.Prep.Tool.exe")
             if target.kind == "windows-onefile" else
             (f"APSMidiPrepTool-{version}-{target.architecture}.AppImage",))
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The release has no verified application download.")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") in names]
    if len(matches) != 1:
        raise UpdateError("This release has no unique application download for your platform.")
    asset = matches[0]
    name = asset["name"]
    size = asset.get("size")
    digest = asset.get("digest")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"
    if (asset.get("state") != "uploaded" or type(size) is not int
            or not 0 < size <= MAX_DOWNLOAD_BYTES):
        raise UpdateError("The application download is incomplete or has an invalid size.")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError("The release does not provide a verified SHA-256 checksum.")
    if asset.get("browser_download_url") != expected_url:
        raise UpdateError("The application download does not belong to the official release.")
    return expected_url, size, digest[7:].lower()


def _hash_file(path, cancel_callback=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            _check_cancel(cancel_callback)
            block = stream.read(CHUNK_BYTES)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _verify_binary(path, target):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
        if target.kind == "windows-onefile":
            if len(header) < 64 or header[:2] != b"MZ":
                raise UpdateError("The update is not a Windows application.")
            offset = struct.unpack_from("<I", header, 60)[0]
            if not 64 <= offset <= 1024 * 1024:
                raise UpdateError("The Windows application header is invalid.")
            stream.seek(offset)
            pe = stream.read(26)
            if (len(pe) != 26 or pe[:4] != b"PE\0\0"
                    or struct.unpack_from("<H", pe, 4)[0] != 0x8664
                    or struct.unpack_from("<H", pe, 24)[0] != 0x20B):
                raise UpdateError("The update is not a 64-bit Windows application.")
        else:
            expected_machine = {"x86_64": 62, "aarch64": 183}[target.architecture]
            if (len(header) < 64 or header[:6] != b"\x7fELF\x02\x01"
                    or header[8:11] != b"AI\x02"
                    or struct.unpack_from("<H", header, 18)[0] != expected_machine):
                raise UpdateError("The update is not an AppImage for this computer.")


_SIGNATURE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$installed = Get-AuthenticodeSignature -LiteralPath $env:APS_UPDATE_INSTALLED
$downloaded = Get-AuthenticodeSignature -LiteralPath $env:APS_UPDATE_DOWNLOADED
@{
  installed = @{status = $installed.Status.ToString(); subject = $installed.SignerCertificate.Subject}
  downloaded = @{status = $downloaded.Status.ToString(); subject = $downloaded.SignerCertificate.Subject}
} | ConvertTo-Json -Compress
"""


def _powershell_path():
    # Never search PATH or interpolate a filename into PowerShell source.
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise UpdateError("Windows could not locate its signature verification tool.")
    path = Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not path.is_file():
        raise UpdateError("Windows signature verification is unavailable.")
    return path


def _launch_signature_process(command, environment):
    environment = environment.copy()
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        root = Path(bundle).resolve()
        entries = environment.get("PATH", "").split(os.pathsep)
        environment["PATH"] = os.pathsep.join(
            entry for entry in entries
            if entry and not Path(entry.strip('"')).resolve().is_relative_to(root)
        )
    kwargs = dict(env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, cwd=str(Path(command[0]).parent),
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if sys.platform != "win32":
        return subprocess.Popen(command, **kwargs)
    # PyInstaller's DLL directory is inherited by system tools too. Reset it
    # only across process creation, then immediately restore APS's own runtime.
    import ctypes
    kernel = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetDllDirectoryW(len(buffer), buffer)
    if length >= len(buffer) or not kernel.SetDllDirectoryW(None):
        raise UpdateError("Windows could not prepare its signature verification tool.")
    try:
        return subprocess.Popen(command, **kwargs)
    finally:
        kernel.SetDllDirectoryW(buffer.value or None)


def _verify_windows_signatures(installed, downloaded, cancel_callback):
    _check_cancel(cancel_callback)
    environment = os.environ.copy()
    environment.update(APS_UPDATE_INSTALLED=str(installed), APS_UPDATE_DOWNLOADED=str(downloaded))
    command = [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive",
               "-EncodedCommand", base64.b64encode(_SIGNATURE_SCRIPT.encode("utf-16le")).decode("ascii")]
    process = _launch_signature_process(command, environment)
    deadline = time.monotonic() + 45
    try:
        while True:
            _check_cancel(cancel_callback)
            if time.monotonic() >= deadline:
                raise UpdateError("Windows signature verification timed out. Please try again later.")
            try:
                stdout, _stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        raise UpdateError("Windows could not verify the application's publisher.")
    try:
        signatures = json.loads(stdout.decode("utf-8-sig"))
        old = signatures["installed"]
        new = signatures["downloaded"]
        valid = (old["status"] == "Valid" and new["status"] == "Valid"
                 and isinstance(old["subject"], str) and bool(old["subject"].strip())
                 and old["subject"] == new["subject"])
    except (ValueError, UnicodeError, KeyError, TypeError):
        valid = False
    if not valid:
        raise UpdateError("The update must have a valid signature from the same publisher as this application.")


def _validate_target(target):
    if (not isinstance(target, UpdateTarget) or not isinstance(target.path, Path)
            or not target.path.is_absolute()
            or (target.kind, target.architecture) not in {
                ("windows-onefile", "x86_64"), ("linux-appimage", "x86_64"),
                ("linux-appimage", "aarch64"),
            }):
        raise UpdateError("This application package cannot update itself.")
    if target.path.is_symlink() or not target.path.is_file():
        raise UpdateError("The installed application is missing or is a symbolic link.")
    for path in (target.path, target.path.parent):
        if not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise UpdateError("This copy is read-only. Move it to a writable folder or USB drive before updating.")
    _verify_binary(target.path, target)


def _cleanup_directory(directory, target):
    if (not directory.name.startswith(".aps-update-")
            or directory.parent != target.path.parent or directory.is_symlink()):
        raise UpdateError("The temporary update folder is not safe to remove.")
    if directory.exists():
        shutil.rmtree(directory)


def cleanup_staged_update(staged):
    """Remove this preparation's sibling staging folder, leaving neighbors alone."""
    _cleanup_directory(staged.directory, staged.target)


def prepare_update(version, *, target=None, progress_callback=None, cancel_callback=None):
    """Download and verify a new stable release without changing installed files."""
    directory = None
    try:
        version, version_key = _stable_version(version)
        if version_key <= _stable_version(APP_VERSION)[1]:
            raise UpdateError("Only a newer version can be installed automatically.")
        target = target if target is not None else detect_update_target()
        _validate_target(target)
        _check_cancel(cancel_callback)
        original_sha256 = _hash_file(target.path, cancel_callback)
        url, size, expected_sha256 = _asset_for_release(version, target, cancel_callback)
        # Space for the payload, detached helper, recovery copy, and journal.
        needed = size + 2 * target.path.stat().st_size + SPACE_MARGIN_BYTES
        if shutil.disk_usage(target.path.parent).free < needed:
            raise UpdateError("There is not enough free space beside this application to download and keep a recovery copy.")
        directory = Path(tempfile.mkdtemp(prefix=".aps-update-", dir=target.path.parent))
        payload = directory / ("payload.exe" if target.kind == "windows-onefile" else "payload.AppImage")
        received = 0
        digest = hashlib.sha256()
        if progress_callback is not None:
            progress_callback(0, size)
        _check_cancel(cancel_callback)
        with _open_url(url) as response, payload.open("xb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and (not content_length.isdigit() or int(content_length) != size):
                raise UpdateError("The download size does not match the published release.")
            while True:
                _check_cancel(cancel_callback)
                block = response.read(min(CHUNK_BYTES, size - received + 1))
                if not block:
                    break
                received += len(block)
                if received > size:
                    raise UpdateError("The download is larger than the published release.")
                output.write(block)
                digest.update(block)
                if progress_callback is not None:
                    progress_callback(received, size)
            output.flush()
            os.fsync(output.fileno())
        _check_cancel(cancel_callback)
        if received != size:
            raise UpdateError("The update download was interrupted or incomplete.")
        if digest.hexdigest() != expected_sha256:
            raise UpdateError("The update checksum does not match the published release.")
        _verify_binary(payload, target)
        if target.kind == "windows-onefile":
            _verify_windows_signatures(target.path, payload, cancel_callback)
        if target.path.is_symlink() or _hash_file(target.path, cancel_callback) != original_sha256:
            raise UpdateError("The installed application changed during the download. Please restart and try again.")
        payload.chmod(stat.S_IMODE(target.path.stat().st_mode))
        _check_cancel(cancel_callback)
        return StagedUpdate(target, directory, payload, version, expected_sha256, original_sha256)
    except BaseException as exc:
        if directory is not None:
            try:
                _cleanup_directory(directory, target)
            except OSError:
                pass
        if isinstance(exc, (OSError, ValueError, urllib.error.URLError,
                            http.client.HTTPException, subprocess.SubprocessError)):
            raise UpdateError(f"The update could not be prepared: {exc}") from exc
        raise
