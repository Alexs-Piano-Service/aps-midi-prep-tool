# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Discover already mounted devices without mounting or modifying anything."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MountedDevice:
    device: str
    mountpoint: Path
    filesystem: str = ""
    label: str = ""
    size: str = ""
    read_only: bool = False
    is_mark_iv: bool = False

    @property
    def display_name(self) -> str:
        name = "Disklavier music" if self.is_mark_iv else self.label or self.mountpoint.name
        return f"{name}  —  {self.device}  ({self.mountpoint})"


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def parse_mountinfo(text: str) -> list[MountedDevice]:
    """Parse Linux mountinfo, including its octal-escaped paths."""
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            source = _unescape_mount(fields[separator + 2])
            if not source.startswith("/dev/"):
                continue
            mounts.append(MountedDevice(
                device=source,
                mountpoint=Path(_unescape_mount(fields[4])),
                filesystem=fields[separator + 1],
                read_only="ro" in fields[5].split(","),
            ))
        except (ValueError, IndexError):
            continue
    return mounts


def _block_metadata() -> dict[str, dict]:
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--output", "PATH,LABEL,SIZE"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        roots = json.loads(result.stdout).get("blockdevices", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    metadata = {}
    stack = list(roots)
    while stack:
        item = stack.pop()
        if item.get("path"):
            metadata[item["path"]] = item
        stack.extend(item.get("children", []))
    return metadata


def discover_devices(*, include_system: bool = False) -> list[MountedDevice]:
    """Return block devices mounted in this process's mount namespace."""
    try:
        mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    except OSError:
        return []
    metadata = _block_metadata()
    devices = []
    seen = set()
    for mount in mounts:
        if mount.mountpoint in seen:
            continue
        seen.add(mount.mountpoint)
        if not include_system and (
            mount.mountpoint == Path("/") or
            str(mount.mountpoint).startswith(("/boot", "/snap/", "/var/", "/usr/", "/etc/"))
        ):
            continue
        meta = metadata.get(mount.device, {})
        try:
            is_mark_iv = (mount.mountpoint / "songs").is_dir() and (
                (mount.mountpoint / "tgm4").is_dir() or (mount.mountpoint / "postgres").is_dir()
            )
        except OSError:
            is_mark_iv = False
        devices.append(MountedDevice(
            device=mount.device, mountpoint=mount.mountpoint,
            filesystem=mount.filesystem, label=meta.get("label") or "",
            size=meta.get("size") or "", read_only=mount.read_only,
            is_mark_iv=is_mark_iv,
        ))
    return sorted(devices, key=lambda item: (not item.is_mark_iv, str(item.mountpoint)))


def resolve_source(value: str | Path) -> Path:
    """Accept a mounted directory or the device node for a mounted partition."""
    source = Path(value).expanduser()
    # Windows Path uses backslashes even when given a POSIX device node. Keep
    # the device check independent of the host's path separator.
    if source.as_posix().startswith("/dev/"):
        canonical = source.resolve()
        for mount in discover_devices(include_system=True):
            if Path(mount.device).resolve() == canonical:
                return mount.mountpoint.resolve()
        raise ValueError(
            f"{source} is not mounted. Mount its data partition read-only, "
            "then select the mounted folder. This program does not mount devices."
        )
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"The source is not an accessible directory: {source}")
    return source
