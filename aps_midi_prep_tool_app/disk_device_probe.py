"""Read-only device discovery entry point for source and frozen executables."""

from dataclasses import asdict
import json
from pathlib import Path
import sys


DEVICE_PROBE_ARG = "--aps-device-discovery-helper"


def probe_command(kind, result_path):
    args = [DEVICE_PROBE_ARG, kind, str(result_path)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    entry_point = Path(__file__).resolve().parent.parent / "aps_midi_prep_tool.py"
    return [sys.executable, str(entry_point), *args]


def run_device_probe_from_argv(argv):
    """Return None for normal startup, or an exit code for a helper invocation."""
    if len(argv) < 2 or argv[1] != DEVICE_PROBE_ARG:
        return None
    if len(argv) != 4:
        return 2

    from .floppy_image import list_floppy_drives, list_greaseweazle_devices
    from .markiv_device_discovery import mounted_sources

    probes = {"floppy": list_floppy_drives, "greaseweazle": list_greaseweazle_devices,
              "markiv": mounted_sources}
    result = {"devices": [], "error": ""}
    try:
        result["devices"] = [asdict(device) for device in probes[argv[2]]()]
    except Exception as exc:
        result["error"] = str(exc) or type(exc).__name__
    Path(argv[3]).write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    return 1 if result["error"] else 0
