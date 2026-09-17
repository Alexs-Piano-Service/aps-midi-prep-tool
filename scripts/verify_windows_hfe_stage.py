"""Require the complete runtime extracted by stage_windows_hfe_tool.ps1."""

import hashlib
import json
from pathlib import Path
import sys


ARCHIVE_SHA256 = "f409ae2411506eacecd60915c361c7bfa7293795e3e488fad4927ddd7d146464"
RECEIPT = ".aps-hfe-stage.json"


def verify_stage(directory):
    directory = Path(directory)
    receipt = json.loads((directory / RECEIPT).read_text(encoding="utf-8-sig"))
    if receipt.get("archive_sha256") != ARCHIVE_SHA256:
        raise ValueError("Stage the pinned Greaseweazle archive with scripts/stage_windows_hfe_tool.ps1.")
    expected = receipt["files"]
    actual = {path.relative_to(directory).as_posix(): path for path in directory.rglob("*")
              if path.is_file() and path.name != RECEIPT}
    if set(expected) != set(actual) or "gw.exe" not in actual or len(actual) < 2:
        raise ValueError("The standalone Greaseweazle runtime is incomplete or has changed.")
    for name, path in actual.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[name].lower():
            raise ValueError(f"Staged Greaseweazle file changed: {name}")


if __name__ == "__main__":
    try:
        verify_stage(sys.argv[1])
    except (IndexError, OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Invalid HFE runtime: {exc}")
