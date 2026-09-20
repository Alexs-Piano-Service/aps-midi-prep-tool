"""Identify a packaged build or a development checkout in diagnostic reports."""

from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys


def checkout_identity(root):
    try:
        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
                timeout=3, text=True,
            ).strip()
        return {"commit": git("rev-parse", "HEAD"),
                "dirty": bool(git("status", "--porcelain", "--untracked-files=normal"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}


@lru_cache(maxsize=1)
def build_identity():
    if getattr(sys, "frozen", False):
        try:
            return json.loads(Path(__file__).with_name("build-info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"commit": "unknown", "dirty": None}
    return checkout_identity(Path(__file__).resolve().parents[1])
