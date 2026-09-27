"""Write source and package identity for inclusion in a packaged application."""

import argparse
import json
from pathlib import Path
import platform
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aps_midi_prep_tool_app.build_info import checkout_identity


PACKAGE_KINDS = ("windows-onefile", "windows-onedir", "linux-appimage")


def normalize_architecture(value):
    aliases = {"amd64": "x86_64", "x86_64": "x86_64",
               "arm64": "aarch64", "aarch64": "aarch64"}
    try:
        return aliases[value.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported packaged architecture: {value}") from exc


def interpreter_architecture():
    # Use the interpreter's target on Windows: a 64-bit OS can run a 32-bit
    # interpreter, and ARM64 Windows can run an emulated AMD64 interpreter.
    if sys.platform == "win32":
        return normalize_architecture(sysconfig.get_platform().removeprefix("win-"))
    return normalize_architecture(platform.machine())


def write_build_info(destination, *, package_kind=None, architecture=None):
    if package_kind is not None and package_kind not in PACKAGE_KINDS:
        raise ValueError(f"Unsupported package kind: {package_kind}")
    if package_kind is None and architecture is not None:
        raise ValueError("An architecture requires an explicit package kind.")
    identity = checkout_identity(ROOT)
    if package_kind is not None:
        architecture = (interpreter_architecture() if architecture is None
                        else normalize_architecture(architecture))
        identity.update(package_kind=package_kind, architecture=architecture)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(identity) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--package-kind", choices=PACKAGE_KINDS,
                        help="Package format; omit for legacy source identity only")
    parser.add_argument("--architecture", help="Target architecture; defaults to the build interpreter")
    args = parser.parse_args()
    try:
        write_build_info(args.destination, package_kind=args.package_kind,
                         architecture=args.architecture)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
